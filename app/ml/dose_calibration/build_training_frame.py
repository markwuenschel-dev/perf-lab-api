"""Build the dose-law calibration training frame (Rail 1, shadow-only).

Turns a per-(athlete, session) workout log into a supervised frame for learning WEAK
POPULATION PRIORS on the session dose-law weights (``dose_volume_weights`` and the
``dose_shape_six_by_modality`` multipliers). Each row carries the raw volume-proxy
COMPONENTS the weights act on (session duration, external volume load, sets), the
engine's CURRENTLY-MODELED dose for that session (via ``calculate_stress_dose`` — so the
calibration is measured against the production dose law, not a re-derivation), and a
NEXT-SESSION outcome proxy the dose is supposed to track.

Outcome proxy (label)
  Next-session ``session_rpe`` residual (per-athlete demeaned). The hypothesis a dose
  law encodes is "a bigger session today leaves more residual fatigue, so the next
  session costs more perceived effort". The label is therefore the athlete's next
  logged session RPE, demeaned per athlete so the model learns each athlete's WITHIN-
  person dose→cost response rather than cross-athlete RPE-reporting styles. Only genuine
  next sessions within ``MAX_SESSION_GAP_DAYS`` produce a label; larger gaps (the residual
  fatigue has cleared) yield no label and are dropped.

Data source
  The production-equivalent path is the workout-logs table joined to each athlete's next
  logged session. There is no first-party dose CSV yet, so ``synthesize_sessions`` emits a
  deterministic SYNTHETIC stand-in that keeps this pipeline runnable and testable without a
  DB (analogous to the Q2 recovery CSV). SYNTHETIC data is good only for learning the
  SHAPE of weak priors, never effect magnitudes — which is exactly why the emitted artifact
  is ``shadow_only`` (see ``model_card``).
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.engine.parameters import EngineParameters, default_parameters
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.schemas.workouts import WorkoutLog

GROUP_COLUMN = "user_id"
LABEL_COLUMN = "label"
DOSE_COLUMN = "modeled_dose_default"

# The volume-proxy components the ``dose_volume_weights`` act on, and the weight each maps
# to. These are the model features; a learned coefficient on a component becomes a weak
# multiplicative nudge to its weight (see train.py).
COMPONENT_FEATURES: tuple[str, ...] = ("f_duration", "f_volume_load", "f_sets")
COMPONENT_TO_WEIGHT: dict[str, str] = {
    "f_duration": "duration",
    "f_volume_load": "volume_load",
    "f_sets": "sets",
}
_COMPONENT_SOURCE: dict[str, str] = {
    "f_duration": "duration_minutes",
    "f_volume_load": "total_volume_load",
    "f_sets": "sets_eff",
}

# WorkoutLog.modality -> the shape_six_by_modality key it is distributed by (mirrors
# _shape_six in dose_engine_v0). Used to attribute per-modality shape calibration.
MODALITY_TO_SHAPE: dict[str, str] = {
    "Running": "Running",
    "Strength": "strength",
    "Hypertrophy": "strength",
    "Power": "strength",
    "Mixed": "strength",
}

# Only consecutive-enough sessions carry a residual-fatigue signal; a longer layoff has
# cleared it, so the next session's RPE no longer reflects today's dose.
MAX_SESSION_GAP_DAYS = 4

# Session columns needed both for the linear fit and to REBUILD a WorkoutLog so the dose
# can be recomputed under calibrated parameters.
_SESSION_FIELDS: tuple[str, ...] = (
    "modality", "duration_minutes", "session_rpe", "total_volume_load",
    "estimated_sets", "novelty", "avg_rir", "sleep_quality", "life_stress_inverse",
)

# Features that are FORBIDDEN because they leak the label or are measured post-outcome.
# The label is the NEXT session's RPE; anything from session t+1, or the modeled dose of
# t+1, is measured AFTER the dose being calibrated and would leak the answer.
FORBIDDEN_FEATURES: dict[str, str] = {
    "session_rpe_next": "the label itself — next-session RPE",
    "modeled_dose_next": "modeled dose of session t+1 — post-outcome by construction",
    "duration_minutes_next": "next-session (t+1) field — measured after the outcome window",
    "total_volume_load_next": "next-session (t+1) field — post-outcome",
    "sets_next": "next-session (t+1) field — post-outcome",
    "avg_rir_next": "next-session (t+1) field — post-outcome",
    "label": "the supervised target itself",
}


def _sets_effective(row: pd.Series) -> float:
    """Sets used by the dose law: ``estimated_sets`` or the engine's duration fallback."""
    est = row.get("estimated_sets")
    if est is not None and not (isinstance(est, float) and np.isnan(est)):
        return float(est)
    return max(3.0, float(row["duration_minutes"]) / 12.0)


def build_log(row: pd.Series) -> WorkoutLog:
    """Rebuild a minimal ``WorkoutLog`` from a frame row for dose recomputation."""
    rir = row.get("avg_rir")
    rir_val = None if rir is None or (isinstance(rir, float) and np.isnan(rir)) else float(rir)
    return WorkoutLog(
        timestamp=pd.Timestamp(row["date"]).to_pydatetime(),
        modality=row["modality"],
        duration_minutes=float(row["duration_minutes"]),
        session_rpe=float(row["session_rpe"]),
        total_volume_load=float(row.get("total_volume_load") or 0.0),
        estimated_sets=float(row["sets_eff"]),
        novelty=float(row.get("novelty") or 1.0),
        avg_rir=rir_val,
        sleep_quality=float(row.get("sleep_quality") or 5.0),
        life_stress_inverse=float(row.get("life_stress_inverse") or 5.0),
    )


def modeled_dose_scalar(row: pd.Series, params: EngineParameters) -> float:
    """Total six-axis session dose under ``params`` — the engine's modeled dose magnitude."""
    dose = calculate_stress_dose(build_log(row), params)
    six = dose.dose_six
    return float(six.volume + six.intensity + six.density + six.impact + six.skill + six.metabolic)


def modeled_doses(frame: pd.DataFrame, params: EngineParameters) -> np.ndarray:
    """Vector of modeled dose magnitudes for every row under ``params``."""
    return np.array([modeled_dose_scalar(row, params) for _, row in frame.iterrows()], dtype=float)


def build_frame(sessions: pd.DataFrame) -> pd.DataFrame:
    """Build the supervised dose-calibration frame from per-session workout logs.

    Returns one row per (athlete, session) that has a valid next-session label, carrying
    the standardized component features, the raw session fields (for dose recomputation),
    the default modeled dose, and the per-athlete-residualized next-session RPE label.
    """
    df = sessions.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values([GROUP_COLUMN, "date"]).reset_index(drop=True)
    df["sets_eff"] = df.apply(_sets_effective, axis=1)

    # --- Modeled dose under the current (default) engine weights ---
    df[DOSE_COLUMN] = modeled_doses(df, default_parameters())

    # --- Label: next logged session's RPE, consecutive-enough sessions only ---
    rpe_next = df.groupby(GROUP_COLUMN)["session_rpe"].shift(-1)
    date_next = df.groupby(GROUP_COLUMN)["date"].shift(-1)
    gap_days = (date_next - df["date"]).dt.days
    next_rpe = rpe_next.where((gap_days >= 1) & (gap_days <= MAX_SESSION_GAP_DAYS))

    keep_cols = [GROUP_COLUMN, "date", "sets_eff", DOSE_COLUMN, *_SESSION_FIELDS]
    out = df[keep_cols].copy()
    out["next_session_rpe"] = next_rpe
    out = out[out["next_session_rpe"].notna()].reset_index(drop=True)

    # Residualize the label per athlete (remove each athlete's mean next-session RPE).
    athlete_mean = out.groupby(GROUP_COLUMN)["next_session_rpe"].transform("mean")
    out[LABEL_COLUMN] = out["next_session_rpe"] - athlete_mean

    # --- Component features: population z-score so ridge coefficients are comparable ---
    for feat, src in _COMPONENT_SOURCE.items():
        raw = out[src].astype(float)
        std = float(raw.std(ddof=0)) or 1.0
        out[feat] = (raw - float(raw.mean())) / std

    return out


def grouped_time_split(
    frame: pd.DataFrame, holdout_frac: float = 0.25
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split holding out whole athletes (grouped) while preserving per-athlete time order.

    Athletes are partitioned by id so no athlete appears in both train and test — this
    prevents the per-athlete residualization from leaking across the split.
    """
    ids = np.sort(frame[GROUP_COLUMN].unique())
    n_holdout = max(1, int(round(len(ids) * holdout_frac)))
    test_ids = set(ids[-n_holdout:].tolist())
    is_test = frame[GROUP_COLUMN].isin(test_ids)
    train_df = frame[~is_test].sort_values([GROUP_COLUMN, "date"]).reset_index(drop=True)
    test_df = frame[is_test].sort_values([GROUP_COLUMN, "date"]).reset_index(drop=True)
    return train_df, test_df


def synthesize_sessions(
    *, n_athletes: int = 40, n_sessions: int = 30, planted: bool = True, seed: int = 19
) -> pd.DataFrame:
    """Deterministic SYNTHETIC workout-log stand-in (no DB / no CSV).

    When ``planted`` is True the next-session RPE is driven by a known combination of the
    session's volume components (so the calibration signal is recoverable in tests and the
    gate can demonstrably fire); otherwise the components are pure noise and the honest
    verdict is ``stay_shadow``. SYNTHETIC — shape only, never magnitudes.
    """
    rng = np.random.default_rng(seed)
    modalities = ("Running", "Strength", "Hypertrophy", "Power", "Mixed")
    start = pd.Timestamp("2024-01-01")
    rows: list[dict[str, Any]] = []
    for uid in range(1, n_athletes + 1):
        day = 0
        prev_signal = 0.0
        for _ in range(n_sessions):
            day += int(rng.integers(1, 3))  # 1-2 day gaps -> mostly within MAX gap
            modality = str(rng.choice(modalities))
            duration = float(np.clip(rng.normal(55, 15), 15, 120))
            vol_load = float(max(0.0, rng.normal(4000, 1500)))
            sets = float(np.clip(rng.normal(18, 6), 4, 40))
            # Planted residual-fatigue signal: heavier volume today -> higher next RPE.
            signal = 0.010 * duration + 0.0004 * vol_load + 0.06 * sets
            base_rpe = 5.0 + (0.5 * prev_signal if planted else 0.0) + rng.normal(0, 0.6)
            rows.append(
                {
                    "user_id": uid,
                    "date": (start + pd.Timedelta(days=day)).date().isoformat(),
                    "modality": modality,
                    "duration_minutes": duration,
                    "session_rpe": float(np.clip(base_rpe, 1.0, 10.0)),
                    "total_volume_load": vol_load,
                    "estimated_sets": sets,
                    "novelty": float(np.clip(rng.normal(1.0, 0.2), 0.1, 3.0)),
                    "avg_rir": float(np.clip(rng.normal(2.5, 1.0), 0.0, 6.0)),
                    "sleep_quality": float(np.clip(rng.normal(6.0, 1.5), 1.0, 10.0)),
                    "life_stress_inverse": float(np.clip(rng.normal(6.0, 1.5), 1.0, 10.0)),
                }
            )
            prev_signal = signal - 4.0  # center so it swings the next RPE both ways
    return pd.DataFrame(rows)
