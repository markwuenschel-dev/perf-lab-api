"""Pure (non-DB) tests for the dose-law calibration pipeline (Rail 1/4).

Covers: (a) build_frame yields the expected columns with no leaked/next-session features;
(b) train() emits an artifact the frozen loader ACCEPTS and merges on the dose path;
(c) the weak-prior mapping clamps a near-zero signal to the engine defaults and never
exceeds the nudge caps; (d) the evaluate gate runs and returns a well-formed verdict.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine.parameter_overrides import (
    apply_dose_overrides,
    load_override_artifact,
)
from app.engine.parameters import default_parameters
from app.ml.dose_calibration.build_training_frame import (
    COMPONENT_FEATURES,
    FORBIDDEN_FEATURES,
    GROUP_COLUMN,
    LABEL_COLUMN,
    build_frame,
    synthesize_sessions,
)
from app.ml.dose_calibration.evaluate import evaluate
from app.ml.dose_calibration.train import (
    _MAX_WEIGHT_NUDGE,
    map_response_to_volume_weights,
    placeholder_artifact,
    train,
)


def _frame(*, planted: bool = True, n_athletes: int = 30, n_sessions: int = 28) -> pd.DataFrame:
    sessions = synthesize_sessions(
        n_athletes=n_athletes, n_sessions=n_sessions, planted=planted, seed=3
    )
    return build_frame(sessions)


def test_build_frame_columns_and_no_leakage() -> None:
    frame = _frame()

    for col in (GROUP_COLUMN, "date", *COMPONENT_FEATURES, LABEL_COLUMN):
        assert col in frame.columns
    assert len(frame) > 0

    # No forbidden / next-session field is exposed as a model feature.
    leaky = set(FORBIDDEN_FEATURES) - {"label"}
    assert leaky.isdisjoint(set(COMPONENT_FEATURES))
    for banned in ("session_rpe_next", "next_session_rpe", "modeled_dose_next"):
        assert banned not in COMPONENT_FEATURES

    # Features imputed / finite so the linear fit is well-posed.
    for feat in COMPONENT_FEATURES:
        assert np.isfinite(frame[feat].to_numpy(dtype=float)).all()

    # Label is a per-athlete residual: mean ~ 0 within each athlete.
    per_athlete_mean = frame.groupby(GROUP_COLUMN)[LABEL_COLUMN].mean().abs().max()
    assert per_athlete_mean < 1e-6


def test_train_emits_loader_accepted_artifact() -> None:
    artifact = train(_frame())

    loaded = load_override_artifact(artifact)
    assert loaded["version"] == "dose_calibration_priors_v1"
    assert loaded["namespace"] == "dose_calibration"
    assert loaded["shadow_only"] is True
    assert "dose_volume_weights" in loaded["engine_overrides"]

    # Merges onto default params via the shadow-only dose path.
    merged = apply_dose_overrides(default_parameters(), artifact, allow_shadow=True)
    assert set(merged.dose_volume_weights) == {"duration", "volume_load", "sets"}


def test_trained_weights_stay_within_nudge_caps() -> None:
    artifact = train(_frame())
    defaults = default_parameters().dose_volume_weights
    for name, weight in artifact["engine_overrides"]["dose_volume_weights"].items():
        lo = defaults[name] * (1 - _MAX_WEIGHT_NUDGE) - 1e-9
        hi = defaults[name] * (1 + _MAX_WEIGHT_NUDGE) + 1e-9
        assert lo <= weight <= hi, f"{name} weight escaped the weak-prior clamp"


def test_weak_prior_clamps_near_zero_signal_to_defaults() -> None:
    defaults = default_parameters().dose_volume_weights
    # A zero learned response must reproduce the literature defaults exactly.
    zeroed = map_response_to_volume_weights(dict.fromkeys(COMPONENT_FEATURES, 0.0))
    assert zeroed == {k: round(v, 6) for k, v in defaults.items()}

    # A large positive effect saturates at exactly +MAX_WEIGHT_NUDGE (never beyond).
    strong = map_response_to_volume_weights(dict.fromkeys(COMPONENT_FEATURES, 5.0))
    for name, weight in strong.items():
        assert weight == round(defaults[name] * (1 + _MAX_WEIGHT_NUDGE), 6)


def test_placeholder_artifact_is_zero_change() -> None:
    artifact = placeholder_artifact()
    load_override_artifact(artifact)  # frozen loader accepts it
    merged = apply_dose_overrides(default_parameters(), artifact, allow_shadow=True)
    assert merged.dose_volume_weights == default_parameters().dose_volume_weights
    assert merged.dose_shape_six_by_modality == default_parameters().dose_shape_six_by_modality


def test_evaluate_returns_well_formed_verdict() -> None:
    frame = _frame()
    report = evaluate(frame, artifact=train(frame))
    d = report.as_dict()
    assert report.verdict in {"promote", "stay_shadow"}
    assert d["n_test_rows"] > 0
    # improvement is default - calibrated; a real number either way.
    assert np.isfinite(d["improvement"])
    assert 0.0 <= d["saturation_fraction"] <= 1.0
    if report.verdict == "stay_shadow":
        assert report.reasons  # a stay must name at least one failing guardrail


def test_planted_signal_moves_weights_off_default() -> None:
    # With a planted volume->next-RPE relationship, at least one weight should be nudged.
    artifact = train(_frame(planted=True))
    defaults = default_parameters().dose_volume_weights
    moved = any(
        abs(artifact["engine_overrides"]["dose_volume_weights"][k] - v) > 1e-6
        for k, v in defaults.items()
    )
    assert moved
