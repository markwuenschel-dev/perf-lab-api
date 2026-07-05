"""Pure (non-DB) tests for the Q2 recovery-priors training pipeline (Rail 1).

Covers: (a) build_frame yields the expected columns with no leaked/label-derived
features; (b) train() emits an artifact the frozen loader ACCEPTS and merges; (c) the
learned response beats a neutral (no-recovery-signal) baseline on a held-out fixture
with a planted recovery effect.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.engine.parameter_overrides import (
    OverrideError,
    apply_parameter_overrides,
    load_override_artifact,
)
from app.engine.parameters import default_parameters
from app.ml.q2_recovery.build_training_frame import (
    FEATURE_COLUMNS,
    FORBIDDEN_FEATURES,
    GROUP_COLUMN,
    LABEL_COLUMN,
    build_frame,
)
from app.ml.q2_recovery.train import holdout_mae, train


def _write_synthetic_csv(path: Path, *, n_athletes: int = 4, n_days: int = 45) -> Path:
    """A tiny wellness CSV with the columns build_frame reads, consecutive daily rows."""
    rng = np.random.default_rng(7)
    rows: list[dict[str, object]] = []
    start = pd.Timestamp("2024-01-01")
    for uid in range(1, n_athletes + 1):
        for d in range(n_days):
            rows.append(
                {
                    "user_id": uid,
                    "date": (start + pd.Timedelta(days=d)).date().isoformat(),
                    "sleep_hours": float(rng.normal(7.5, 0.8)),
                    "hrv": float(rng.normal(60, 8)),
                    "resting_hr": float(rng.normal(55, 4)),
                    "fatigue_score": float(rng.normal(5, 1.5)),
                }
            )
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    return path


def _planted_frame(n_athletes: int = 30, n_days: int = 40) -> pd.DataFrame:
    """A frame where the label is a known linear function of the z features + noise."""
    rng = np.random.default_rng(11)
    parts: list[pd.DataFrame] = []
    start = pd.Timestamp("2024-01-01")
    for uid in range(1, n_athletes + 1):
        z_sleep = rng.normal(0, 1, n_days)
        z_hrv = rng.normal(0, 1, n_days)
        z_rhr = rng.normal(0, 1, n_days)
        label = 0.6 * z_sleep + 0.4 * z_hrv - 0.35 * z_rhr + rng.normal(0, 0.3, n_days)
        parts.append(
            pd.DataFrame(
                {
                    GROUP_COLUMN: uid,
                    "date": [start + pd.Timedelta(days=d) for d in range(n_days)],
                    "z_sleep": z_sleep,
                    "z_hrv": z_hrv,
                    "z_rhr": z_rhr,
                    LABEL_COLUMN: label,
                }
            )
        )
    return pd.concat(parts, ignore_index=True)


def test_build_frame_columns_and_no_leakage(tmp_path: Path) -> None:
    csv = _write_synthetic_csv(tmp_path / "wellness.csv")
    frame = build_frame(csv)

    # Expected columns present.
    for col in (GROUP_COLUMN, "date", *FEATURE_COLUMNS, LABEL_COLUMN):
        assert col in frame.columns
    assert len(frame) > 0

    # No leaked / label-derived raw feature is exposed as a model feature. The only
    # fatigue-touching columns allowed are the derived label + its raw clearance.
    leaky = set(FORBIDDEN_FEATURES) - {"label"}
    assert leaky.isdisjoint(set(FEATURE_COLUMNS))
    assert "fatigue_score" not in FEATURE_COLUMNS
    assert "fatigue_score" not in frame.columns  # raw outcome not carried as a feature col

    # Features are imputed (no NaN) so the linear fit is well-posed.
    for feat in FEATURE_COLUMNS:
        assert not frame[feat].isna().any()

    # Label is a per-athlete residual: mean ~ 0 within each athlete.
    per_athlete_mean = frame.groupby(GROUP_COLUMN)[LABEL_COLUMN].mean().abs().max()
    assert per_athlete_mean < 1e-6


def test_train_emits_loader_accepted_artifact(tmp_path: Path) -> None:
    csv = _write_synthetic_csv(tmp_path / "wellness.csv")
    frame = build_frame(csv)
    artifact = train(frame)

    # Frozen loader accepts it.
    loaded = load_override_artifact(artifact)
    assert loaded["version"] == "q2_recovery_priors_v1"
    assert loaded["namespace"] == "q2_recovery"
    assert loaded["shadow_only"] is True

    # Every axis carries the learned/extended signal set within schema bounds.
    for axis, weights in artifact["recovery_clearance_beta"].items():
        assert axis in {"cns", "muscular", "metabolic", "structural", "tendon", "grip"}
        assert {"sleep", "stress", "hrv", "rhr", "soreness"} <= set(weights)

    # It merges onto default parameters via the shadow-only path.
    merged = apply_parameter_overrides(default_parameters(), artifact, allow_shadow=True)
    assert "hrv" in merged.recovery_clearance_beta["cns"]
    assert "rhr" in merged.recovery_clearance_beta["cns"]
    assert merged.recovery_clearance_min == 0.6
    assert merged.recovery_clearance_max == 1.5


def test_shadow_only_blocks_production_apply(tmp_path: Path) -> None:
    csv = _write_synthetic_csv(tmp_path / "wellness.csv")
    artifact = train(build_frame(csv))
    with pytest.raises(OverrideError):
        apply_parameter_overrides(default_parameters(), artifact, allow_shadow=False)


def test_learned_beats_neutral_baseline_on_planted_effect() -> None:
    frame = _planted_frame()
    mae_learned, mae_baseline = holdout_mae(frame)
    assert mae_learned <= mae_baseline
    # A planted effect should give a real (not marginal) improvement.
    assert mae_learned < 0.98 * mae_baseline


def test_planted_effect_recovers_expected_signs() -> None:
    from app.ml.q2_recovery.train import fit_population_response

    fit = fit_population_response(_planted_frame())
    coefs = fit["coefficients"]
    assert coefs["z_sleep"] > 0
    assert coefs["z_hrv"] > 0
    assert coefs["z_rhr"] < 0
