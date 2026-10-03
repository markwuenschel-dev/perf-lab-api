"""Record deferred upward_lower_bound floor candidates as shadow evidence (ADR-0058).

Capture-only, isolated exactly like the EKF / dose-routing shadow services: the row is
written after the observation commits, in its own best-effort transaction. When
``create_observation`` resolves an ``upward_lower_bound`` capacity_effect, the
authority is real but the live floor-ratchet is not promoted — this records the
resolved authority and the *would-be* applied transition **separately** so a future,
observable promotion decision has the evidence it needs. Applies nothing to
production state (``decision_impact = "none_shadow_only"``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.vectors import CapacityState
from app.logic import observation_authority as oa
from app.models.capacity_floor_shadow import CapacityFloorShadowLog
from app.schemas.state import UnifiedStateVector
from app.services.telemetry_common import best_effort_write


@dataclass(frozen=True)
class FloorObservationSnapshot:
    """The committed observation's fields this writer records — plain values, taken by the
    caller BEFORE the telemetry transaction, never the live ORM row (F1: a writer reading a
    request-session object reaches across into a transaction it does not own)."""

    id: int
    observed_at: datetime
    capacity_effect: str | None
    authority_policy_version: str | None
    authority_resolution_reason: str | None

    @classmethod
    def of(cls, observation: Any) -> FloorObservationSnapshot:
        return cls(
            id=int(observation.id),
            observed_at=observation.observed_at,
            capacity_effect=observation.capacity_effect,
            authority_policy_version=observation.authority_policy_version,
            authority_resolution_reason=observation.authority_resolution_reason,
        )


def floor_candidate_payload(
    prior: UnifiedStateVector, floored: UnifiedStateVector, *, eps: float = 1e-9
) -> dict[str, Any]:
    """Pure: the proposed floor + projected uplift a floor-ratchet would apply.

    ``proposed_floor`` is the per-axis capacity the non-regressing ratchet clamps up
    to; ``projected_uplift`` is the per-axis positive delta over the prior (empty when
    the lower bound lands below the current watermark — it would raise nothing).
    """
    proposed_floor: dict[str, float] = {}
    uplift: dict[str, float] = {}
    for key in CapacityState.KEYS:
        floor_v = float(getattr(floored.capacity_x, key))
        prior_v = float(getattr(prior.capacity_x, key))
        proposed_floor[key] = round(floor_v, 6)
        if floor_v - prior_v > eps:
            uplift[key] = round(floor_v - prior_v, 6)
    would_raise = bool(uplift)
    return {
        "proposed_floor": proposed_floor,
        "projected_uplift": uplift,
        "projected_uplift_total": round(sum(uplift.values()), 6),
        "would_raise": would_raise,
        "not_applied_reason": (
            oa.FLOOR_NOT_APPLIED_DEFERRED if would_raise else oa.FLOOR_NOT_APPLIED_BELOW_WATERMARK
        ),
    }


async def record_floor_candidate(
    db: AsyncSession,
    user_id: int,
    *,
    observation: FloorObservationSnapshot,
    benchmark_code: str,
    prior: UnifiedStateVector,
    floored: UnifiedStateVector,
) -> None:
    """Write a shadow candidate row for a deferred floor-ratchet (best-effort).

    Call this **after** the observation has been committed. The row is written in its
    own transaction via :func:`telemetry_common.best_effort_write`, so a failure to
    persist shadow evidence can never abort the benchmark observation it describes.

    That isolation is load-bearing, not stylistic. ``db.add`` only stages a row in
    memory and performs no I/O, so a guard wrapped around it catches nothing: the INSERT
    — and any constraint violation it raises — executes at commit time, inside whichever
    transaction flushes it. While this rode the live transaction, a bad shadow row failed
    the primary write. The cost of isolating it is that the candidate is no longer atomic
    with its observation; losing one piece of evidence is the intended best-effort
    trade, and ``benchmark_observation_id`` is nullable with ``ondelete=CASCADE``.
    """
    async with best_effort_write(db, f"capacity floor shadow candidate (user {user_id})") as tx:
        tdb = tx.db  # F1: the telemetry transaction's own session
        payload = floor_candidate_payload(prior, floored)
        row = CapacityFloorShadowLog(
            user_id=user_id,
            benchmark_observation_id=observation.id,
            benchmark_code=benchmark_code,
            observed_at=observation.observed_at,
            capacity_effect=observation.capacity_effect or oa.CE_UPWARD_LOWER_BOUND,
            authority_policy_version=observation.authority_policy_version or oa.POLICY_VERSION,
            authority_resolution_reason=observation.authority_resolution_reason,
            application_policy_version=oa.FLOOR_APPLY_POLICY_VERSION,
            not_applied_reason=payload["not_applied_reason"],
            proposed_floor_json=payload["proposed_floor"],
            projected_uplift_json=payload["projected_uplift"],
            projected_uplift_total=payload["projected_uplift_total"],
            would_raise=payload["would_raise"],
            decision_impact="none_shadow_only",
        )
        tdb.add(row)
