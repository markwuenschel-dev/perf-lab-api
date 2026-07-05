"""Build the Q2 recovery-priors training frame (Rail 1, shadow-only).

Turns a per-(athlete, day) wellness log into a supervised frame for learning WEAK
POPULATION PRIORS on the fatigue-clearance recovery modifier. Every feature is a
z-score of a recovery signal against that athlete's own trailing baseline (mirroring
``app.services.readiness_service``'s 28-day personal-baseline intent), and the label
is a NEXT-DAY recovery proxy *residual* — never a raw next-day value and never a
signal derived from the label itself.

The primary source is the synthetic Kaggle google-fit CSV
(``data/kaggle/google-fit-data/hamon_googlefit_medical_realistic.csv``). Because that
data is SYNTHETIC, the frame is only good for learning the *shape* of weak priors
(see ``model_card``). The production-equivalent, DB-backed path is
``app.analysis.feature_builders.fatigue_recovery`` over ``wellness_samples``; the CSV
keeps this pipeline runnable and testable without Postgres.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.ml.common.splits import grouped_time_split as _grouped_time_split
from app.ml.common.standardize import zscore_vs_trailing_baseline

# Trailing personal-baseline window, mirroring readiness_service.BASELINE_WINDOW_DAYS.
WINDOW_DAYS = 28
# Minimum prior observations before a z-score is trusted (else the signal reads neutral).
MIN_BASELINE_DAYS = 7

# The recovery signals this slice can learn from the CSV. rhr is "lower is better", so
# a z-scored coefficient learned against it is expected to come out NEGATIVE.
FEATURE_COLUMNS: tuple[str, ...] = ("z_sleep", "z_hrv", "z_rhr")
LABEL_COLUMN = "label"
GROUP_COLUMN = "user_id"

# Map each learned feature back to the engine recovery signal name it represents.
FEATURE_TO_SIGNAL: dict[str, str] = {"z_sleep": "sleep", "z_hrv": "hrv", "z_rhr": "rhr"}

# Source CSV columns feeding each z-scored feature.
_RAW_SOURCE: dict[str, str] = {
    "z_sleep": "sleep_hours",
    "z_hrv": "hrv",
    "z_rhr": "resting_hr",
}

# Features that are FORBIDDEN because they leak the label or are measured post-outcome.
# The label is the overnight change in fatigue_score, so anything touching fatigue_score
# (same-day or next-day) or any next-day (t+1) signal would leak the answer.
FORBIDDEN_FEATURES: dict[str, str] = {
    "fatigue_score": "label is derived from fatigue_score(t) - fatigue_score(t+1); using it as a feature leaks the label directly",
    "fatigue_score_next": "next-day (t+1) outcome — post-outcome by construction",
    "hrv_next": "next-day (t+1) signal is measured AFTER the recovery window being predicted",
    "resting_hr_next": "next-day (t+1) signal is post-outcome",
    "sleep_hours_next": "next-day (t+1) signal is post-outcome",
    "cardiometabolic_risk_state": "downstream health-state label, not a pre-recovery input",
    "label": "the supervised target itself",
}


def build_frame(csv_path: str | Path) -> pd.DataFrame:
    """Build the supervised recovery-priors frame from a wellness CSV.

    Returns one row per (athlete, day) that has a valid next-day recovery label, with
    columns ``user_id``, ``date``, the z-scored ``FEATURE_COLUMNS`` and the residualized
    ``label``. The label is the overnight fatigue *clearance* (``fatigue(t) -
    fatigue(t+1)``, positive = recovered) residualized against each athlete's mean
    clearance — a per-athlete residual, so the model learns within-athlete response
    rather than cross-athlete fatigue levels. Days that are not consecutive (a gap in the
    log) produce no label and are dropped.
    """
    path = Path(csv_path)
    usecols = ["user_id", "date", "sleep_hours", "hrv", "resting_hr", "fatigue_score"]
    df = pd.read_csv(path, usecols=usecols, parse_dates=["date"])
    df = df.sort_values([GROUP_COLUMN, "date"]).reset_index(drop=True)

    # --- Features: per-signal z-score vs trailing personal baseline (pre-outcome) ---
    for feat, raw_col in _RAW_SOURCE.items():
        df[feat] = zscore_vs_trailing_baseline(
            df,
            raw_col,
            group_column=GROUP_COLUMN,
            window_days=WINDOW_DAYS,
            min_baseline_days=MIN_BASELINE_DAYS,
        )

    # --- Label: next-day fatigue clearance, consecutive days only, then residualize ---
    grp_f = df.groupby(GROUP_COLUMN)["fatigue_score"]
    fatigue_next = grp_f.shift(-1)
    date_next = df.groupby(GROUP_COLUMN)["date"].shift(-1)
    gap_days = (date_next - df["date"]).dt.days
    clearance = df["fatigue_score"] - fatigue_next  # >0 = fatigue fell overnight
    clearance = clearance.where(gap_days == 1)  # only genuine next-day transitions

    out = df[[GROUP_COLUMN, "date", *FEATURE_COLUMNS]].copy()
    out["recovery_clearance"] = clearance
    # Impute missing z (no baseline / missing signal) to neutral before dropping on label.
    for feat in FEATURE_COLUMNS:
        out[feat] = out[feat].fillna(0.0)
    out = out[out["recovery_clearance"].notna()].reset_index(drop=True)

    # Residualize the label per athlete (remove each athlete's mean clearance).
    athlete_mean = out.groupby(GROUP_COLUMN)["recovery_clearance"].transform("mean")
    out[LABEL_COLUMN] = out["recovery_clearance"] - athlete_mean

    return out[[GROUP_COLUMN, "date", *FEATURE_COLUMNS, "recovery_clearance", LABEL_COLUMN]]


def grouped_time_split(
    frame: pd.DataFrame, holdout_frac: float = 0.25
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split holding out whole athletes (grouped) while preserving per-athlete time order.

    Athletes are partitioned by id so no athlete appears in both train and test — this
    prevents the per-athlete residualization and trailing baselines from leaking across
    the split. Rows stay in ``(user_id, date)`` order within each partition. Binds this
    pipeline's constants to ``app.ml.common.splits.grouped_time_split``.
    """
    return _grouped_time_split(
        frame,
        group_column=GROUP_COLUMN,
        order_columns=("date",),
        holdout_frac=holdout_frac,
    )
