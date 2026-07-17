"""Persist Model B per-exercise dose routing as shadow telemetry (ADR-0054).

Capture-only, mirroring ``ekf_shadow_service``: computes the raw Σφ·D routed dose + its
0–100 compatibility-scaled control-space values via the pure :mod:`app.logic.dose_routing`
and writes one ``dose_routing_shadow_log`` row per ingested workout. It applies **nothing**
to production state or prescriptions (``decision_impact="none_shadow_only"``). Best-effort:
a failure here never breaks workout ingestion.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.logic import dose_routing as dr
from app.models.dose_routing_shadow import DoseRoutingShadowLog
from app.models.exercise import Exercise
from app.schemas.workouts import ExternalIntensity, WorkoutLog
from app.services.telemetry_common import best_effort_write


async def _e1rm_by_exercise_key(
    db: AsyncSession, user_id: int, log: WorkoutLog
) -> dict[str, float]:
    """Map each resolved exercise (``id:<n>`` / ``name:<name>``) to its pre-log e1RM.

    Uses the same uncorrupted, prescription-grade denominator read as the dose intensity
    path (ADR-0055/0056), so shadow routing and the live dose agree on the denominator.
    """
    ids = [e.exercise_id for e in log.exercises if e.exercise_id is not None]
    names = [e.exercise_name for e in log.exercises if e.exercise_name and e.exercise_id is None]
    if not ids and not names:
        return {}

    code_by_id: dict[int, str] = {}
    code_by_name: dict[str, str] = {}
    if ids:
        res = await db.execute(
            select(Exercise.id, Exercise.e1rm_benchmark_code).where(
                Exercise.id.in_(ids), Exercise.e1rm_benchmark_code.isnot(None)
            )
        )
        code_by_id = {i: c for i, c in res.all() if c}
    if names:
        res = await db.execute(
            select(Exercise.name, Exercise.e1rm_benchmark_code).where(
                Exercise.name.in_(names), Exercise.e1rm_benchmark_code.isnot(None)
            )
        )
        code_by_name = {n: c for n, c in res.all() if c}

    codes = set(code_by_id.values()) | set(code_by_name.values())
    if not codes:
        return {}

    from app.services.state_service import prelog_e1rm_denominators

    denoms = await prelog_e1rm_denominators(db, user_id, codes)
    out: dict[str, float] = {}
    for e in log.exercises:
        code = (
            code_by_id.get(e.exercise_id)
            if e.exercise_id is not None
            else code_by_name.get(e.exercise_name or "")
        )
        if code and code in denoms:
            key = (
                f"id:{e.exercise_id}"
                if e.exercise_id is not None
                else f"name:{e.exercise_name}"
            )
            out[key] = denoms[code]["value"]
    return out


async def record_dose_routing(
    db: AsyncSession,
    user_id: int,
    log: WorkoutLog,
    workout_log_id: int | None,
    *,
    external_intensity: ExternalIntensity | None = None,
    routed_at: datetime | None = None,
) -> None:
    """Compute + persist the Model B shadow routing for one workout (best-effort)."""
    async with best_effort_write(db, f"dose routing shadow for user {user_id}"):
        e1rm_by_key = await _e1rm_by_exercise_key(db, user_id, log)
        r = dr.build_routing(
            log, e1rm_by_key=e1rm_by_key, external_intensity=external_intensity
        )
        row = DoseRoutingShadowLog(
            user_id=user_id,
            workout_log_id=workout_log_id,
            routed_at=(routed_at or log.timestamp).replace(tzinfo=None),
            model_version=r.model_version,
            calibration_basis=r.calibration_basis,
            routing_basis=r.routing_basis,
            n_units=r.n_units,
            n_resolved_phi=r.n_resolved_phi,
            n_unresolved=r.n_unresolved,
            raw_fatigue_total=sum(r.raw_fatigue.values()),
            raw_tissue_total=sum(r.raw_tissue.values()),
            raw_struct=r.raw_struct,
            fatigue_compat_total=sum(r.fatigue_compat_0_100.values()),
            tissue_compat_total=sum(r.tissue_compat_0_100.values()),
            struct_compat=r.struct_compat,
            raw_json={
                "capacity": r.raw_capacity,
                "fatigue": r.raw_fatigue,
                "tissue": r.raw_tissue,
                "struct": r.raw_struct,
            },
            compat_json={
                "capacity": r.capacity_compat,
                "fatigue": r.fatigue_compat_0_100,
                "tissue": r.tissue_compat_0_100,
                "struct": r.struct_compat,
            },
            k_json=r.k,
            contributions_json=[asdict(c) for c in r.contributions],
            decision_impact="none_shadow_only",
        )
        db.add(row)
