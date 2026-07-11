"""Record deferred upward_lower_bound floor candidates as shadow evidence (ADR-0058).

Capture-only, mirroring the EKF / dose-routing shadow services. When
``create_observation`` resolves an ``upward_lower_bound`` capacity_effect, the
authority is real but the live floor-ratchet is not promoted — this records the
resolved authority and the *would-be* applied transition **separately** so a future,
observable promotion decision has the evidence it needs. Applies nothing to
production state (``decision_impact = "none_shadow_only"``).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.vectors import CapacityState
from app.logic import observation_authority as oa
from app.models.capacity_floor_shadow import CapacityFloorShadowLog
from app.schemas.state import UnifiedStateVector

logger = logging.getLogger(__name__)


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
    observation: Any,
    benchmark_code: str,
    prior: UnifiedStateVector,
    floored: UnifiedStateVector,
) -> None:
    """Add a shadow candidate row for a deferred floor-ratchet (best-effort).

    The row is added to the session but NOT committed here — it commits atomically
    with the observation it belongs to. Best-effort: a failure logs and is swallowed
    so shadow capture can never break the observation write.
    """
    try:
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
        db.add(row)
    except Exception:
        logger.warning(
            "capacity floor shadow capture failed for user %s (obs %s)",
            user_id, getattr(observation, "id", None), exc_info=True,
        )
