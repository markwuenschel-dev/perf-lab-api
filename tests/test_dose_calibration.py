"""Pure (non-DB) tests for the dose-law calibration pipeline (Rail 1/4).

Covers: (a) build_frame yields the expected columns with no leaked/next-session features;
(b) train() emits an artifact the frozen loader ACCEPTS and merges on the dose path;
(c) the weak-prior mapping clamps a near-zero signal to the engine defaults and never
exceeds the nudge caps; (d) the evaluate gate runs and returns a well-formed verdict.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine.parameter_overrides import (
    apply_parameter_overrides,
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

    # The label is causal: next RPE minus the mean of STRICTLY EARLIER sessions, so it is
    # not forced to average zero per athlete the way a full-trajectory demean would be.
    recomputed = frame["next_session_rpe"] - frame["causal_baseline_rpe"]
    assert np.allclose(frame[LABEL_COLUMN], recomputed)
    assert (frame["pair_status"] == "labelled").all()


def test_train_emits_loader_accepted_artifact() -> None:
    artifact = train(_frame())

    loaded = load_override_artifact(artifact)
    assert loaded["version"] == "dose_calibration_priors_v1"
    assert loaded["namespace"] == "dose_calibration"
    assert loaded["shadow_only"] is True
    assert "dose_volume_weights" in loaded["engine_overrides"]

    # Merges onto default params via the shadow-only dose path.
    merged = apply_parameter_overrides(default_parameters(), artifact, allow_shadow=True)
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
    merged = apply_parameter_overrides(default_parameters(), artifact, allow_shadow=True)
    assert merged.dose_volume_weights == default_parameters().dose_volume_weights
    assert merged.dose_shape_six_by_modality == default_parameters().dose_shape_six_by_modality


def test_evaluate_returns_well_formed_verdict() -> None:
    frame = _frame()
    report = evaluate(frame)
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



def test_a_label_never_changes_when_later_sessions_are_added() -> None:
    """Appending future sessions must leave every earlier label untouched (causal)."""
    sessions = synthesize_sessions(n_athletes=4, n_sessions=20, planted=True, seed=5)
    short = sessions.groupby(GROUP_COLUMN).head(12)
    a = build_frame(short).set_index(["user_id", "date"])[LABEL_COLUMN]
    b = build_frame(sessions).set_index(["user_id", "date"])[LABEL_COLUMN]
    common = a.index.intersection(b.index)
    # The last labelled row of each short history gains nothing; every other shared row
    # must be identical.
    assert len(common) > 0
    assert np.allclose(a.loc[common], b.loc[common])


def test_evaluation_never_trains_on_a_held_out_athlete(monkeypatch) -> None:
    """The prior evaluate() scores must be fitted without the test athletes."""
    import app.ml.dose_calibration.evaluate as ev

    frame = _frame()
    seen: list[set[int]] = []
    real_train = ev.train

    def spy(train_frame: pd.DataFrame, **kw):
        seen.append(set(train_frame[GROUP_COLUMN]))
        return real_train(train_frame, **kw)

    monkeypatch.setattr(ev, "train", spy)
    report = ev.evaluate(frame)
    _, test_df = ev.grouped_time_split(frame)
    assert len(seen) == 1
    assert seen[0].isdisjoint(set(test_df[GROUP_COLUMN]))
    assert report.n_train_athletes + report.n_test_athletes == frame[GROUP_COLUMN].nunique()


def test_the_fit_standardizes_the_features_it_is_given() -> None:
    """Scaling a held-out athlete's raw volumes must not change the fitted response."""
    from app.ml.dose_calibration.train import fit_component_response

    frame = _frame()
    base = fit_component_response(frame)
    # The fit sees only the frame it is given: its coefficients are expressed in that
    # frame's own standardization, so they are scale-free.
    scaled = frame.copy()
    for feat in COMPONENT_FEATURES:
        scaled[feat] = scaled[feat] * 7.0
    again = fit_component_response(scaled)
    for feat in COMPONENT_FEATURES:
        assert again["coefficients"][feat] == pytest.approx(base["coefficients"][feat], abs=1e-9)


def test_the_frame_recomputes_doses_with_v1_and_never_fabricates_sets() -> None:
    from app.logic import dose_engine_v1
    from app.ml.dose_calibration.build_training_frame import build_log, modeled_dose_scalar

    frame = _frame()
    row = frame.iloc[0].copy()
    six = dose_engine_v1.calculate_stress_dose(build_log(row), default_parameters()).dose_six
    assert modeled_dose_scalar(row, default_parameters()) == pytest.approx(
        six.volume + six.intensity + six.density + six.impact + six.skill + six.metabolic
    )
    row["sets_eff"] = np.nan
    assert build_log(row).estimated_sets is None
