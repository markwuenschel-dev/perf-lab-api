"""Build the dose-law calibration training frame (phase 8.4).

Turns logged sessions into a supervised frame for learning WEAK POPULATION PRIORS on the
session dose-law weights (``dose_volume_weights`` and the ``dose_shape_six_by_modality``
multipliers). Each row carries the raw volume-proxy COMPONENTS the weights act on, the
session fields needed to recompute the dose, the dose v1 models for it, and a label.

Label: CAUSAL, from ``app/logic/dose_fit_policy.py``
  The athlete's next logged session RPE (1-4 days later), centred on the mean RPE of that
  athlete's sessions strictly BEFORE this one. A full-trajectory mean would hand a held-out
  athlete's future RPEs to the evaluation; the policy never looks forward, and a session
  without enough earlier history has no label. The same module drives the production
  census (``app/scripts/dose_shadow_report.py``), so the census and the frame count the
  same pairs.

Dose model: v1, at the version the running code implements
  Doses are recomputed with ``dose_engine_v1`` (not v0: the density variable changed). A
  frame is built for exactly ONE ``v1_model_version``, and it must be the version this code
  computes: rows captured under v1.1 cannot be recomputed as v1.2, and the two are never
  pooled.

Features: raw, standardized at fit time
  The component columns are raw magnitudes. Standardization happens inside the fit, on the
  training partition only, so held-out athletes never shape the scaling.

Data sources
  ``load_shadow_frame`` reads the 8A shadow log: eligible rows only (``fit_tier``, which
  consults ``density_fit_eligible``), real athletes only unless ``allow_seeded``. Its
  manifest records the source, versions, pairing rule, exclusions and a fingerprint of the
  frame, and an artifact fitted from it carries that manifest into activation
  (``app/logic/dose_model.py``).
  ``synthesize_sessions`` is a deterministic SYNTHETIC stand-in for tests: shape only,
  never effect sizes, and never activatable.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import pandas as pd

from app.engine.parameters import EngineParameters, default_parameters
from app.logic import dose_engine_v1
from app.logic.dose_fit_policy import (
    FIT_POLICY_VERSION,
    LoggedSession,
    fit_tier,
    is_seeded_email,
    pair_sessions,
    pairing_rule,
)
from app.ml.common.splits import grouped_time_split as _grouped_time_split
from app.schemas.workouts import WorkoutLog

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

#: Bump on ANY change to what a frame row contains or how a feature is computed.
#: 1 (implicit, before phase 8): v0 doses, fabricated set fallback, full-trajectory demeaned
#: label, features z-scored over the whole frame. 2: v1 doses, reported sets only, causal
#: label from the fit policy, raw features standardized at fit time.
FEATURE_SCHEMA_VERSION = "dose-features-2"

GROUP_COLUMN = "user_id"
SESSION_ID_COLUMN = "workout_log_id"
LABEL_COLUMN = "label"
DOSE_COLUMN = "modeled_dose_default"
#: The v1 total recorded at ingest (shadow rows only), to measure recompute fidelity.
INGEST_DOSE_COLUMN = "v1_dose_ingest"

DATA_SOURCE_SHADOW = "dose_model_shadow_log"
DATA_SOURCE_SEEDED = "seeded"
DATA_SOURCE_SYNTHETIC = "synthetic"

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

# Session columns needed both for the linear fit and to REBUILD a WorkoutLog so the dose
# can be recomputed under calibrated parameters.
_SESSION_FIELDS: tuple[str, ...] = (
    "modality", "duration_minutes", "session_rpe", "total_volume_load",
    "estimated_sets", "distance_meters", "novelty", "avg_rir", "sleep_quality",
    "life_stress_inverse",
)

# Features that are FORBIDDEN because they leak the label or are measured post-outcome.
# The label is the NEXT session's RPE; anything from session t+1, or the modeled dose of
# t+1, is measured AFTER the dose being calibrated and would leak the answer.
FORBIDDEN_FEATURES: dict[str, str] = {
    "session_rpe_next": "the label itself — next-session RPE",
    "next_session_rpe": "the label's raw value — next-session RPE",
    "modeled_dose_next": "modeled dose of session t+1 — post-outcome by construction",
    "duration_minutes_next": "next-session (t+1) field — measured after the outcome window",
    "total_volume_load_next": "next-session (t+1) field — post-outcome",
    "sets_next": "next-session (t+1) field — post-outcome",
    "avg_rir_next": "next-session (t+1) field — post-outcome",
    "label": "the supervised target itself",
}


def _missing(val: object) -> bool:
    return val is None or (isinstance(val, float) and np.isnan(val))


def _wellness(row: pd.Series, field: str) -> float | None:
    """A 1-10 wellness self-report from the frame, preserving unknown as ``None``.

    ADR-0049: an absent report is a gap, not a value. The previous ``float(row.get(f)
    or 5.0)`` did two wrong things at once — it fabricated the scale midpoint for a
    missing report, and, because ``0.0 or 5.0`` is ``5.0``, it silently mapped a
    ``0`` reading onto that same midpoint. ``0`` is out of the schema's ``[1, 10]``
    domain, so it now raises a validation error at :func:`build_log` (as ``0.5``
    already did) instead of being laundered into a plausible-looking 5.0. Missing and
    zero are therefore distinct outcomes: missing is carried through as ``None`` and
    dosed with a labelled neutral; zero is rejected as corrupt.
    """
    val = row.get(field)
    if _missing(val):
        return None
    return float(val)


def _optional(row: pd.Series, field: str) -> float | None:
    val = row.get(field)
    return None if _missing(val) else float(val)


def build_log(row: pd.Series) -> WorkoutLog:
    """Rebuild a minimal ``WorkoutLog`` from a frame row for dose recomputation.

    Sets are the REPORTED count or ``None``: v1 never fabricates a set count, so neither
    does the frame (the v0-era ``max(3, duration/12)`` fallback is gone).
    """
    novelty = _optional(row, "novelty")
    return WorkoutLog(
        timestamp=pd.Timestamp(row["date"]).to_pydatetime(),
        modality=row["modality"],
        duration_minutes=float(row["duration_minutes"]),
        session_rpe=float(row["session_rpe"]),
        total_volume_load=_optional(row, "total_volume_load") or 0.0,
        distance_meters=_optional(row, "distance_meters") or 0.0,
        estimated_sets=_optional(row, "sets_eff"),
        novelty=1.0 if novelty is None else novelty,
        avg_rir=_optional(row, "avg_rir"),
        sleep_quality=_wellness(row, "sleep_quality"),
        life_stress_inverse=_wellness(row, "life_stress_inverse"),
    )


def modeled_dose_scalar(row: pd.Series, params: EngineParameters) -> float:
    """Total six-axis session dose under ``params``, by dose model v1."""
    dose = dose_engine_v1.calculate_stress_dose(build_log(row), params)
    six = dose.dose_six
    return float(six.volume + six.intensity + six.density + six.impact + six.skill + six.metabolic)


def modeled_doses(frame: pd.DataFrame, params: EngineParameters) -> np.ndarray:
    """Vector of modeled dose magnitudes for every row under ``params``."""
    return np.array([modeled_dose_scalar(row, params) for _, row in frame.iterrows()], dtype=float)


def _labelled(candidates: pd.DataFrame, sessions: Iterable[LoggedSession]) -> pd.DataFrame:
    """Attach the causal label to candidate rows; keep only labelled pairs.

    ``candidates`` has one row per session that MAY enter the fit (``SESSION_ID_COLUMN``
    keyed). ``sessions`` is every logged session of those athletes: the next session and the
    baseline come from all of them, not only from eligible rows.
    """
    pairs = pair_sessions(sessions)
    df = candidates.copy()
    status = [
        pairs[int(i)].status if int(i) in pairs else "unlinked_workout_log"
        for i in df[SESSION_ID_COLUMN]
    ]
    df["pair_status"] = status
    df["next_session_rpe"] = [
        pairs[int(i)].next_session_rpe if int(i) in pairs else None
        for i in df[SESSION_ID_COLUMN]
    ]
    df["causal_baseline_rpe"] = [
        pairs[int(i)].causal_baseline_rpe if int(i) in pairs else None
        for i in df[SESSION_ID_COLUMN]
    ]
    df[LABEL_COLUMN] = [
        pairs[int(i)].label if int(i) in pairs else None for i in df[SESSION_ID_COLUMN]
    ]
    return df


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    """Keep labelled rows, sort, and add the raw component features and the default dose."""
    out = df[df["pair_status"] == "labelled"].copy()
    out[LABEL_COLUMN] = out[LABEL_COLUMN].astype(float)
    out = out.sort_values([GROUP_COLUMN, "date", SESSION_ID_COLUMN]).reset_index(drop=True)
    out["sets_eff"] = out["estimated_sets"]
    for feat, src in _COMPONENT_SOURCE.items():
        # Raw magnitudes; the fit standardizes on its training partition. An unreported
        # component is 0 work of that kind, which is what V itself uses.
        out[feat] = out[src].astype(float).fillna(0.0)
    out[DOSE_COLUMN] = modeled_doses(out, default_parameters()) if len(out) else []
    return out


def build_frame(sessions: pd.DataFrame) -> pd.DataFrame:
    """Frame from a per-session table (the synthetic stand-in and tests).

    Every session is both a candidate row and part of the athlete's history. Sessions get a
    stable id from ``SESSION_ID_COLUMN`` when present, else their row position.
    """
    df = sessions.copy()
    df["date"] = pd.to_datetime(df["date"])
    if SESSION_ID_COLUMN not in df.columns:
        df[SESSION_ID_COLUMN] = np.arange(1, len(df) + 1)
    for col in _SESSION_FIELDS:
        if col not in df.columns:
            df[col] = None
    logged = [
        LoggedSession(
            workout_log_id=int(i),
            user_id=int(u),
            at=pd.Timestamp(t).to_pydatetime(),
            session_rpe=float(r),
        )
        for i, u, t, r in zip(
            df[SESSION_ID_COLUMN], df[GROUP_COLUMN], df["date"], df["session_rpe"], strict=True
        )
    ]
    return _finish(_labelled(df, logged))


# --- The real frame: the 8A shadow log -------------------------------------------------


class ShadowFrameError(ValueError):
    """The shadow frame was asked for something this code cannot build honestly."""


@dataclass(frozen=True)
class ShadowFrame:
    frame: pd.DataFrame
    #: What the frame is: source, versions, pairing rule, exclusions, fingerprint. An
    #: artifact fitted from it carries this into activation.
    manifest: dict[str, Any]


def frame_fingerprint(frame: pd.DataFrame) -> str:
    """sha256 over the rows a fit learns from, in a canonical order and precision."""
    cols = [GROUP_COLUMN, SESSION_ID_COLUMN, LABEL_COLUMN, *_COMPONENT_SOURCE.values(),
            *_SESSION_FIELDS]
    present = [c for c in dict.fromkeys(cols) if c in frame.columns]
    ordered = frame.sort_values([GROUP_COLUMN, SESSION_ID_COLUMN])[present]

    def canon(v: object) -> object:
        if _missing(v):
            return None
        if isinstance(v, (int, np.integer)):
            return int(v)
        if isinstance(v, (float, np.floating)):
            return round(float(v), 9)
        return str(v)

    rows = [[canon(v) for v in r] for r in ordered.itertuples(index=False, name=None)]
    payload = json.dumps({"columns": present, "rows": rows}, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pairs_per_athlete(frame: pd.DataFrame) -> dict[str, float | int | None]:
    if frame.empty:
        return {"min": None, "median": None, "max": None}
    counts = frame.groupby(GROUP_COLUMN).size()
    return {
        "min": int(counts.min()),
        "median": float(counts.median()),
        "max": int(counts.max()),
    }


def _recompute_fidelity(frame: pd.DataFrame) -> dict[str, Any]:
    """How far the frame's recomputed default dose sits from the dose recorded at ingest.

    The frame rebuilds a session from its logged fields; the ingest dose also saw exercise
    phi vectors, per-set external intensity and novelty, which are not all persisted. A fit
    nudges weights on the RECOMPUTED dose, so the gap is reported, never assumed zero.
    """
    if frame.empty or INGEST_DOSE_COLUMN not in frame.columns:
        return {"n": 0, "median_abs_rel_diff": None, "max_abs_rel_diff": None}
    ingest = frame[INGEST_DOSE_COLUMN].astype(float).to_numpy()
    recomputed = frame[DOSE_COLUMN].astype(float).to_numpy()
    ok = ingest > 0
    if not ok.any():
        return {"n": 0, "median_abs_rel_diff": None, "max_abs_rel_diff": None}
    rel = np.abs(recomputed[ok] / ingest[ok] - 1.0)
    return {
        "n": int(ok.sum()),
        "median_abs_rel_diff": round(float(np.median(rel)), 6),
        "max_abs_rel_diff": round(float(np.max(rel)), 6),
        "not_persisted": ["novelty", "exercise_phi", "per_set_external_intensity"],
    }


def build_shadow_frame(
    shadow_rows: Iterable[Mapping[str, Any]],
    sessions: Iterable[LoggedSession],
    emails: Mapping[int, str | None],
    *,
    model_version: str,
    allow_seeded: bool = False,
) -> ShadowFrame:
    """Pure core of :func:`load_shadow_frame`: select, exclude, label, fingerprint.

    ``shadow_rows`` carry shadow-log columns plus the workout log's ``avg_rir``,
    ``sleep_quality`` and ``life_stress_inverse``. ``sessions`` are ALL logged sessions of
    the athletes concerned. ``emails`` maps user id to email (seeded detection).
    """
    live = dose_engine_v1.WORK_PER_TIME_DENSITY.version
    if model_version != live:
        raise ShadowFrameError(
            f"frame requested for v1_model_version {model_version!r}, but this code computes "
            f"{live!r}: rows from another version cannot be recomputed here, and versions are "
            "never pooled in one fit."
        )

    excluded: dict[str, int] = {}

    def drop(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    kept: list[dict[str, Any]] = []
    any_seeded = False
    for r in shadow_rows:
        if r.get("v1_model_version") != model_version:
            drop("other_v1_model_version")
            continue
        tier = fit_tier(
            v0_volume_used_fabricated_sets=bool(r.get("v0_volume_used_fabricated_sets")),
            v1_density_basis=r.get("v1_density_basis"),
        )
        if tier != "eligible":
            drop(f"tier:{tier}")
            continue
        if r.get("workout_log_id") is None:
            drop("unlinked_workout_log")
            continue
        seeded = is_seeded_email(emails.get(int(r["user_id"])))
        if seeded and not allow_seeded:
            drop("seeded_account")
            continue
        any_seeded = any_seeded or seeded
        kept.append(
            {
                GROUP_COLUMN: int(r["user_id"]),
                SESSION_ID_COLUMN: int(r["workout_log_id"]),
                "date": pd.Timestamp(cast(datetime, r["session_at"])),
                "modality": r["modality"],
                "duration_minutes": float(r["duration_minutes"]),
                "session_rpe": float(r["session_rpe"]),
                "total_volume_load": r.get("total_volume_load"),
                "estimated_sets": r.get("reported_sets"),
                "distance_meters": r.get("distance_meters"),
                "novelty": None,
                "avg_rir": r.get("avg_rir"),
                "sleep_quality": r.get("sleep_quality"),
                "life_stress_inverse": r.get("life_stress_inverse"),
                INGEST_DOSE_COLUMN: r.get("v1_total"),
            }
        )

    columns = [GROUP_COLUMN, SESSION_ID_COLUMN, "date", *_SESSION_FIELDS, INGEST_DOSE_COLUMN]
    candidates = pd.DataFrame(kept, columns=columns)
    labelled = _labelled(candidates, sessions)
    for status, n in labelled["pair_status"].value_counts().items():
        if status != "labelled":
            excluded[f"pair:{status}"] = int(n)
    frame = _finish(labelled)

    source = DATA_SOURCE_SEEDED if any_seeded else DATA_SOURCE_SHADOW
    manifest: dict[str, Any] = {
        "data_source": source,
        "model_version": model_version,
        "fit_policy_version": FIT_POLICY_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "pairing_rule": pairing_rule(),
        "split_unit": GROUP_COLUMN,
        "n_rows": int(len(frame)),
        "n_athletes": int(frame[GROUP_COLUMN].nunique()) if len(frame) else 0,
        "pairs_per_athlete": _pairs_per_athlete(frame),
        "excluded": dict(sorted(excluded.items())),
        "recompute_fidelity": _recompute_fidelity(frame),
        "frame_fingerprint": frame_fingerprint(frame),
    }
    return ShadowFrame(frame=frame, manifest=manifest)


async def load_shadow_frame(
    db: AsyncSession, *, model_version: str, allow_seeded: bool = False
) -> ShadowFrame:
    """The real training frame: one ``v1_model_version`` of the 8A shadow log.

    Read-only. Excludes every row the fit policy excludes (``fit_tier`` →
    ``density_fit_eligible``), seeded accounts unless ``allow_seeded`` (which tags the
    manifest ``data_source="seeded"``, never activatable), and every pair without a causal
    label.
    """
    from sqlalchemy import select

    from app.models.dose_model_shadow import DoseModelShadowLog
    from app.models.user import User
    from app.models.workout_log import WorkoutLog as WorkoutLogORM

    shadow_cols = [
        DoseModelShadowLog.user_id, DoseModelShadowLog.workout_log_id,
        DoseModelShadowLog.session_at, DoseModelShadowLog.v1_model_version,
        DoseModelShadowLog.v1_density_basis, DoseModelShadowLog.v0_volume_used_fabricated_sets,
        DoseModelShadowLog.modality, DoseModelShadowLog.duration_minutes,
        DoseModelShadowLog.session_rpe, DoseModelShadowLog.total_volume_load,
        DoseModelShadowLog.reported_sets, DoseModelShadowLog.distance_meters,
        DoseModelShadowLog.v1_total,
        WorkoutLogORM.avg_rir, WorkoutLogORM.sleep_quality, WorkoutLogORM.life_stress_inverse,
    ]
    result = await db.execute(
        select(*shadow_cols)
        .outerjoin(WorkoutLogORM, WorkoutLogORM.id == DoseModelShadowLog.workout_log_id)
        .where(DoseModelShadowLog.v1_model_version == model_version)
    )
    rows = [dict(m) for m in result.mappings().all()]
    user_ids = sorted({int(r["user_id"]) for r in rows})
    if not user_ids:
        return build_shadow_frame([], [], {}, model_version=model_version,
                                  allow_seeded=allow_seeded)

    logged = await db.execute(
        select(WorkoutLogORM.id, WorkoutLogORM.user_id, WorkoutLogORM.session_timestamp,
               WorkoutLogORM.session_rpe)
        .where(WorkoutLogORM.user_id.in_(user_ids))
    )
    sessions = [
        LoggedSession(workout_log_id=i, user_id=u, at=t, session_rpe=float(rpe))
        for i, u, t, rpe in logged.all()
    ]
    users = await db.execute(select(User.id, User.email).where(User.id.in_(user_ids)))
    emails = {int(uid): email for uid, email in users.all()}
    return build_shadow_frame(rows, sessions, emails, model_version=model_version,
                              allow_seeded=allow_seeded)


def grouped_time_split(
    frame: pd.DataFrame, holdout_frac: float = 0.25
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split holding out whole athletes (grouped) while preserving per-athlete time order.

    Athletes are partitioned by id so no athlete appears in both train and test. Binds this
    pipeline's constants to ``app.ml.common.splits.grouped_time_split``.
    """
    return _grouped_time_split(
        frame,
        group_column=GROUP_COLUMN,
        order_columns=("date", SESSION_ID_COLUMN),
        holdout_frac=holdout_frac,
    )


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
