from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.derived_metric_formulas import CUSTOM_FORMULAS
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.derived_metric_definition import DerivedMetricDefinition
from app.models.derived_metric_snapshot import DerivedMetricSnapshot
from app.models.mesocycle import PlannedSession, SessionStatus
from app.models.user import AthleteProfile
from app.models.workout_log import WorkoutLog
from app.schemas.dashboard import AdherenceMetrics, OverviewMetrics, TrainingLoadMetrics
from app.schemas.state import UnifiedStateVector
from app.services.state_service import load_current_state

# --- Overview / dashboard-tile constants -----------------------------------
ACUTE_DAYS = 7
CHRONIC_DAYS = 28
# A meaningful chronic baseline needs history predating the acute window;
# below this span the acute:chronic ratio is dominated by a few recent days.
MIN_HISTORY_DAYS = 14
SWEET_SPOT_LOW = 0.8
SWEET_SPOT_HIGH = 1.3
ADHERENCE_WINDOW_DAYS = 28

# Derived metrics that depend on other KPIs must run after their inputs.
_DERIVED_ORDER: dict[str, int] = {
    "pl_projected_total": 10,
    "run_fatigue_factor": 10,
    "wl_snatch_cj_ratio": 10,
    "gym_pull_support_balance": 10,
    "pl_relative_total": 20,
}


def _order_key(code: str) -> tuple[int, str]:
    return (_DERIVED_ORDER.get(code, 15), code)


async def latest_observation_values_by_code(
    db: AsyncSession,
    user_id: int,
) -> dict[str, tuple[float, int, datetime]]:
    """Latest valid observation per benchmark code → (raw_value, observation_id, observed_at)."""
    q = (
        select(BenchmarkObservation, BenchmarkDefinition.code)
        .join(BenchmarkDefinition)
        .where(
            BenchmarkObservation.user_id == user_id,
            BenchmarkObservation.validity_status == "valid",
        )
        .order_by(BenchmarkObservation.observed_at.desc())
    )
    result = await db.execute(q)
    out: dict[str, tuple[float, int, datetime]] = {}
    for row, code in result.all():
        if code in out:
            continue
        out[code] = (row.raw_value, row.id, row.observed_at)
    return out


async def latest_kpi_values(db: AsyncSession, user_id: int) -> dict[str, float]:
    """Most recent snapshot value per derived metric code."""
    subq = (
        select(
            DerivedMetricSnapshot.derived_metric_definition_id,
            DerivedMetricSnapshot.value,
            DerivedMetricSnapshot.computed_at,
        )
        .where(DerivedMetricSnapshot.user_id == user_id)
        .order_by(DerivedMetricSnapshot.computed_at.desc())
    )
    result = await db.execute(subq)
    seen: set[int] = set()
    by_def: dict[int, float] = {}
    for did, val, _ in result.all():
        if did in seen:
            continue
        seen.add(did)
        by_def[did] = val
    if not by_def:
        return {}
    defs = await db.execute(select(DerivedMetricDefinition))
    id_to_code = {d.id: d.code for d in defs.scalars().all()}
    return {id_to_code[i]: v for i, v in by_def.items() if i in id_to_code}


def _compute_derived_value(
    d: DerivedMetricDefinition,
    obs_by_code: dict[str, tuple[float, int, datetime]],
    kpi_ctx: dict[str, float],
    bodyweight_kg: float | None,
) -> tuple[float | None, list[int], str | None]:
    fc: dict[str, Any] = dict(d.formula_config or {})
    obs_ids: list[int] = []

    if d.formula_type == "sum":
        codes = fc.get("benchmark_codes") or []
        total = 0.0
        for c in codes:
            if c not in obs_by_code:
                return None, [], f"missing benchmark {c}"
            v, oid, _ = obs_by_code[c]
            total += v
            obs_ids.append(oid)
        return total, obs_ids, None

    if d.formula_type == "ratio":
        num_c = fc.get("numerator")
        den_c = fc.get("denominator")
        if not num_c or not den_c or num_c not in obs_by_code or den_c not in obs_by_code:
            return None, [], "missing ratio inputs"
        n, n_id, _ = obs_by_code[num_c]
        de, d_id, _ = obs_by_code[den_c]
        if abs(de) < 1e-9:
            return None, [], "zero denominator"
        obs_ids = [n_id, d_id]
        return 100.0 * n / de, obs_ids, None

    if d.formula_type == "weighted_sum":
        terms = fc.get("terms") or []
        s = 0.0
        for t in terms:
            c = t.get("benchmark_code")
            w = float(t.get("weight", 1.0))
            if not c or c not in obs_by_code:
                return None, [], f"missing {c}"
            v, oid, _ = obs_by_code[c]
            s += w * v
            obs_ids.append(oid)
        return s, obs_ids, None

    if d.formula_type == "custom_python_key":
        fn_name = fc.get("function")
        inputs = fc.get("inputs") or []
        if not fn_name or fn_name not in CUSTOM_FORMULAS:
            return None, [], "unknown custom function"
        ctx: dict[str, Any] = {}
        for key in inputs:
            if key == "bodyweight_kg":
                ctx[key] = bodyweight_kg
                continue
            if key in kpi_ctx:
                ctx[key] = kpi_ctx[key]
                continue
            if key in obs_by_code:
                ctx[key] = obs_by_code[key][0]
                obs_ids.append(obs_by_code[key][1])
                continue
            return None, [], f"missing input {key}"
        try:
            val = float(CUSTOM_FORMULAS[fn_name](ctx))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return None, [], "custom formula failed"
        return val, obs_ids, None

    return None, [], f"unsupported formula_type {d.formula_type}"


async def recompute_derived_metrics(db: AsyncSession, user_id: int) -> tuple[int, list[str]]:
    obs_by_code = await latest_observation_values_by_code(db, user_id)
    prof = await db.execute(
        select(AthleteProfile).where(AthleteProfile.user_id == user_id)
    )
    profile = prof.scalars().first()
    bw = float(profile.bodyweight_kg) if profile and profile.bodyweight_kg else None

    defs_result = await db.execute(select(DerivedMetricDefinition))
    defs = sorted(defs_result.scalars().all(), key=lambda x: _order_key(x.code))

    kpi_ctx: dict[str, float] = {}
    written: list[str] = []
    for d in defs:
        val, oids, err = _compute_derived_value(d, obs_by_code, kpi_ctx, bw)
        if val is None:
            continue
        snap = DerivedMetricSnapshot(
            user_id=user_id,
            derived_metric_definition_id=d.id,
            computed_at=datetime.utcnow(),
            value=val,
            confidence=1.0 if not err else 0.7,
            contributing_observation_ids=oids or None,
            notes=err,
        )
        db.add(snap)
        kpi_ctx[d.code] = val
        written.append(d.code)
    await db.commit()
    return len(written), written


async def dashboard_kpis_bundle(
    db: AsyncSession,
    user_id: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Latest KPI rows + latest primary-anchor observations for dashboard."""
    defs_res = await db.execute(
        select(DerivedMetricDefinition).order_by(
            DerivedMetricDefinition.display_priority,
            DerivedMetricDefinition.code,
        )
    )
    defs = list(defs_res.scalars().all())
    kpi_vals = await latest_kpi_values(db, user_id)

    kpis_out: list[dict[str, Any]] = []
    for d in defs:
        if d.code not in kpi_vals:
            continue
        snap_res = await db.execute(
            select(DerivedMetricSnapshot)
            .where(
                DerivedMetricSnapshot.user_id == user_id,
                DerivedMetricSnapshot.derived_metric_definition_id == d.id,
            )
            .order_by(DerivedMetricSnapshot.computed_at.desc())
            .limit(1)
        )
        snap = snap_res.scalars().first()
        kpis_out.append(
            {
                "code": d.code,
                "name": d.name,
                "domain": d.domain,
                "metric_type": d.metric_type,
                "unit": d.unit,
                "value": kpi_vals[d.code],
                "confidence": float(snap.confidence) if snap and snap.confidence is not None else None,
                "computed_at": snap.computed_at if snap else datetime.utcnow(),
                "is_dashboard_kpi": d.is_dashboard_kpi,
                "can_affect_prescriber_rules": d.can_affect_prescriber_rules,
            }
        )

    anchor_res = await db.execute(
        select(BenchmarkObservation, BenchmarkDefinition)
        .join(BenchmarkDefinition)
        .where(
            BenchmarkObservation.user_id == user_id,
            BenchmarkObservation.validity_status == "valid",
            BenchmarkDefinition.is_primary_anchor == True,  # noqa: E712
        )
        .order_by(BenchmarkObservation.observed_at.desc())
    )
    latest_by_code: dict[str, tuple[BenchmarkObservation, BenchmarkDefinition]] = {}
    for obs, bd in anchor_res.all():
        if bd.code not in latest_by_code:
            latest_by_code[bd.code] = (obs, bd)

    anchors_out: list[dict[str, Any]] = []
    for obs, bd in latest_by_code.values():
        anchors_out.append(
            {
                "benchmark_code": bd.code,
                "name": bd.name,
                "domain": bd.domain,
                "is_primary_anchor": bd.is_primary_anchor,
                "metric_type": bd.metric_type,
                "unit": bd.unit,
                "raw_value": obs.raw_value,
                "observed_at": obs.observed_at,
            }
        )
    anchors_out.sort(key=lambda x: (x["domain"], x["benchmark_code"]))
    return kpis_out, anchors_out


async def domain_summary(
    db: AsyncSession,
    user_id: int,
    domain: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kpis, anchors = await dashboard_kpis_bundle(db, user_id)
    dk = [k for k in kpis if k["domain"] == domain]
    da = [a for a in anchors if a["domain"] == domain]
    return dk, da


async def readiness_payload(
    db: AsyncSession,
    user_id: int,
) -> tuple[UnifiedStateVector | None, dict[str, Any]]:
    state = await load_current_state(db, user_id)
    if state is None:
        return None, {"note": "no_athlete_state"}
    kpi = await latest_kpi_values(db, user_id)
    flags: dict[str, Any] = {}
    ff = kpi.get("run_fatigue_factor")
    if ff is not None and ff > 14.0:
        flags["run_fatigue_factor_elevated"] = True
    rt = kpi.get("pl_relative_total")
    if rt is not None and rt < 3.0:
        flags["pl_relative_total_low"] = True
    ratio = kpi.get("wl_snatch_cj_ratio")
    if ratio is not None and ratio < 72.0:
        flags["wl_snatch_share_low"] = True
    return state, flags


# ---------------------------------------------------------------------------
# Overview tiles: training load / ACWR + adherence / streak
#
# The numeric logic lives in pure helpers (no DB session) so it is unit-testable
# without Postgres — see tests/test_dashboard_overview.py. ``overview_metrics``
# only does the DB fetches and feeds these helpers.
# ---------------------------------------------------------------------------

def daily_load(session_rpe: float | None, duration_minutes: float | None) -> float:
    """Per-session training-load proxy: ``session_rpe * duration_minutes``.

    Falls back to duration alone when RPE is missing/non-positive, and to 0.0
    when both are missing. Session-RPE load (RPE × minutes) is the standard
    field-friendly internal-load estimate.
    """
    dur = duration_minutes or 0.0
    if dur <= 0:
        return 0.0
    if session_rpe and session_rpe > 0:
        return session_rpe * dur
    return dur


def _classify_acwr(acwr: float) -> str:
    if acwr < SWEET_SPOT_LOW:
        return "low"
    if acwr <= SWEET_SPOT_HIGH:
        return "optimal"
    return "high"


def compute_training_load(loads_by_day: Mapping[date, float], today: date) -> TrainingLoadMetrics:
    """Acute:chronic workload ratio from a date→load map.

    - ``acute`` = summed load over the trailing ``ACUTE_DAYS`` (7).
    - ``chronic`` = average weekly load over ``CHRONIC_DAYS`` (28-day sum / 4).
    - ``acwr`` = acute / chronic.

    Returns ``status == "insufficient"`` (with null figures) when there is no
    load in the window, the chronic baseline is zero, or the history span is
    shorter than ``MIN_HISTORY_DAYS`` (so the ratio would be dominated by a few
    recent days rather than a real chronic baseline).
    """
    window = {d: v for d, v in loads_by_day.items() if 0 <= (today - d).days < CHRONIC_DAYS and v > 0}
    insufficient = TrainingLoadMetrics(
        acwr=None, acute=None, chronic=None, status="insufficient",
        sweet_spot_low=SWEET_SPOT_LOW, sweet_spot_high=SWEET_SPOT_HIGH,
    )
    if not window:
        return insufficient

    oldest_days = max((today - d).days for d in window)
    if oldest_days < MIN_HISTORY_DAYS:
        return insufficient

    acute = sum(v for d, v in window.items() if (today - d).days < ACUTE_DAYS)
    chronic_total = sum(window.values())
    chronic_weekly = chronic_total / (CHRONIC_DAYS / 7.0)
    if chronic_weekly <= 0:
        return insufficient

    acwr = acute / chronic_weekly
    return TrainingLoadMetrics(
        acwr=round(acwr, 2),
        acute=round(acute, 1),
        chronic=round(chronic_weekly, 1),
        status=_classify_acwr(acwr),  # type: ignore[arg-type]
        sweet_spot_low=SWEET_SPOT_LOW,
        sweet_spot_high=SWEET_SPOT_HIGH,
    )


def compute_adherence_pct(completed: int, scheduled: int) -> float | None:
    """``completed / scheduled`` as a 0-100 percentage; ``None`` when nothing
    was scheduled in the window (a new user has no plan to adhere to)."""
    if scheduled <= 0:
        return None
    return round(completed / scheduled * 100.0, 1)


def compute_streak(active_days: set[date], today: date) -> int:
    """Consecutive days with training activity (a completed session or a logged
    workout), counting back from today. Today not yet being active does not
    reset the streak — it resumes from yesterday — but a full gap day ends it.
    """
    cursor = today if today in active_days else today - timedelta(days=1)
    streak = 0
    while cursor in active_days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


async def overview_metrics(db: AsyncSession, user_id: int) -> OverviewMetrics:
    """Real training-load/ACWR and adherence/streak tiles for the Overview.

    Degrades gracefully for new users (nulls / ``insufficient`` / empty streak),
    never raising on thin history.
    """
    today = date.today()
    chronic_cutoff = datetime.combine(today - timedelta(days=CHRONIC_DAYS - 1), datetime.min.time())

    # --- Training load: daily load proxy from logged workouts in the window ---
    wo_rows = (
        await db.execute(
            select(WorkoutLog).where(
                WorkoutLog.user_id == user_id,
                WorkoutLog.session_timestamp >= chronic_cutoff,
            )
        )
    ).scalars().all()

    loads_by_day: dict[date, float] = {}
    workout_days: set[date] = set()
    for w in wo_rows:
        d = w.session_timestamp.date()
        loads_by_day[d] = loads_by_day.get(d, 0.0) + daily_load(w.session_rpe, w.duration_minutes)
        workout_days.add(d)
    training_load = compute_training_load(loads_by_day, today)

    # --- Adherence: planned sessions due within the adherence window ----------
    adherence_start = today - timedelta(days=ADHERENCE_WINDOW_DAYS - 1)
    ps_rows = (
        await db.execute(
            select(PlannedSession).where(
                PlannedSession.user_id == user_id,
                PlannedSession.scheduled_date >= adherence_start,
                PlannedSession.scheduled_date <= today,
            )
        )
    ).scalars().all()

    scheduled = len(ps_rows)
    completed = sum(1 for p in ps_rows if p.status == SessionStatus.COMPLETED)
    completed_days = {p.scheduled_date for p in ps_rows if p.status == SessionStatus.COMPLETED}
    adherence = AdherenceMetrics(
        pct=compute_adherence_pct(completed, scheduled),
        streak_days=compute_streak(workout_days | completed_days, today),
        window_days=ADHERENCE_WINDOW_DAYS,
    )

    return OverviewMetrics(training_load=training_load, adherence=adherence)
