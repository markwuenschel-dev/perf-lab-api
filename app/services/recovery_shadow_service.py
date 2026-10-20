"""Recovery shadow service (Q2 recovery priors, Rail 3).

For a wellness ingest, compute the baseline (production defaults) vs learned (shadow
override) fatigue-clearance multipliers for the athlete's current fatigue state and
record them in ``recovery_shadow_log``. Applies NOTHING to production state — this is
capture-only. Best-effort: a failure here must never break wellness ingest.

The learned prior is loaded via the override loader with ``allow_shadow=True`` (the only
caller permitted to apply a ``shadow_only`` artifact), keeping learned values off every
production decision path until validated and promoted.

P7: each row joins its sample's correction chain (``wellness_shadow_chain``). The row pairs the
day's wellness with the athlete's CURRENT fatigue state, which is the right pairing only for a
sample of the as-of UTC day. Any other day (a backdated check-in, the earlier days of a wearable
sync) records an explicit skip instead, which also retires an earlier computed row for that
sample from current evidence. The wellness sample itself is always kept.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.vectors import FatigueState
from app.engine.parameter_overrides import apply_parameter_overrides, load_namespace_override
from app.engine.parameters import default_parameters
from app.logic.athlete_calendar import AthleteCalendar
from app.logic.recovery_telemetry import multipliers_by_axis, wellness_snapshot
from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot
from app.models.recovery_shadow import RecoveryShadowLog
from app.services.athlete_calendar_service import calendar_for
from app.services.shadow_evidence import RECORDED, SKIPPED_NOT_AS_OF_DAY
from app.services.state_service import load_current_state
from app.services.telemetry_common import best_effort_write
from app.services.wellness_shadow_chain import (
    WellnessSampleRef,
    append_to_chain,
    head_holds,
    is_as_of_day,
    lock_current_inputs,
)

logger = logging.getLogger(__name__)

_NAMESPACE = "q2_recovery"


async def _append(
    tdb: AsyncSession, ref: WellnessSampleRef, snapshot: WellnessTelemetrySnapshot,
    as_of: datetime,
    calendar: AthleteCalendar | None,
) -> str:
    digest = await lock_current_inputs(tdb, ref, snapshot)
    if digest is None:
        return "stale"
    params = default_parameters()
    artifact = load_namespace_override(_NAMESPACE)
    if artifact is not None:
        learned = apply_parameter_overrides(params, artifact, allow_shadow=True)
        model_version = str(artifact["version"])
    else:
        learned = params
        model_version = "none"
    # B1: the as-of day is the athlete's calendar day of the as-of instant (UTC while their zone
    # is unknown): the caller's calendar when it has one, else read in this telemetry session
    # so the caller's session is never touched (F1).
    if calendar is None:
        calendar = await calendar_for(tdb, ref.user_id)
    as_of_day = calendar.day_of(as_of)
    kind = RECORDED if is_as_of_day(ref, as_of, as_of_day) else SKIPPED_NOT_AS_OF_DAY
    if await head_holds(tdb, RecoveryShadowLog, ref, model_version, digest, kind):
        return "unchanged"

    if kind == SKIPPED_NOT_AS_OF_DAY:
        appended = await append_to_chain(
            tdb, ref, snapshot,
            lambda: RecoveryShadowLog(
                user_id=ref.user_id,
                model_version=model_version,
                wellness=wellness_snapshot(snapshot),
                fatigue_before={},
                baseline_clearance_multiplier={},
                learned_clearance_multiplier={},
                decision_impact=SKIPPED_NOT_AS_OF_DAY,
            ),
            model=RecoveryShadowLog, model_version=model_version, kind=kind,
        )
        if appended != "appended":  # satisfied meanwhile, or the sample changed
            return appended
        logger.info("recovery shadow skipped for sample %s: %s is not the as-of day of %s",
                    ref.sample_id, ref.sample_date, as_of.isoformat())
        return SKIPPED_NOT_AS_OF_DAY

    state = await load_current_state(tdb, ref.user_id)
    fatigue_before = (
        {a: round(float(getattr(state.fatigue_f, a)), 2) for a in FatigueState.KEYS}
        if state is not None
        else {}
    )
    appended = await append_to_chain(
        tdb, ref, snapshot,
        lambda: RecoveryShadowLog(
            user_id=ref.user_id,
            model_version=model_version,
            wellness=wellness_snapshot(snapshot),
            fatigue_before=fatigue_before,
            baseline_clearance_multiplier=multipliers_by_axis(params, snapshot),
            learned_clearance_multiplier=multipliers_by_axis(learned, snapshot),
            decision_impact=RECORDED,
        ),
        model=RecoveryShadowLog, model_version=model_version, kind=kind,
    )
    return "recorded" if appended == "appended" else appended


async def record_recovery_shadow(
    db: AsyncSession,
    ref: WellnessSampleRef,
    snapshot: WellnessTelemetrySnapshot,
    *,
    as_of: datetime,
    calendar: AthleteCalendar | None = None,
) -> str:
    """Append this sample's recovery shadow row to its chain when its inputs changed. Never
    raises to the caller.

    Takes an immutable snapshot, never a live WellnessSample ORM instance (AUD-C24). Returns
    ``recorded`` | ``skipped_not_as_of_day`` | ``unchanged`` | ``stale`` | ``failed``.
    """
    outcome = "failed"
    async with best_effort_write(db, f"recovery shadow log for user {ref.user_id}") as tx:
        outcome = await _append(tx.db, ref, snapshot, as_of, calendar)  # F1: the telemetry session
    return "failed" if tx.failed else outcome
