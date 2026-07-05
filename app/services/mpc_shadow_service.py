"""Shadow MPC planner service (ADR-0042) — capture-only, never a live decision.

After a prescription is finalized, re-rank the prescriber's own candidate pool by
receding-horizon MPC (``evaluate_candidates``) and record what MPC *would* have chosen
versus the greedy prescriber's actual choice (``pool[0]``). Applies NOTHING to the
prescription (``decision_impact="none_shadow_only"``); best-effort — a failure here must
never break or alter ``rx``.

The EKF belief's total uncertainty ``tr(P)`` feeds the objective's conservatism term; when
no belief exists yet (a brand-new athlete) we treat uncertainty as maximal.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.ekf.belief import EkfBelief
from app.logic.mpc.objective import MpcWeights
from app.logic.mpc.planner import DEFAULT_HORIZON_DAYS, evaluate_candidates
from app.models.ekf_shadow import EkfShadowLog
from app.models.mpc_shadow import MpcShadowLog
from app.schemas.state import UnifiedStateVector
from app.services.telemetry_common import best_effort_write


async def _latest_belief_trace(db: AsyncSession, user_id: int, default: float) -> float:
    """Total uncertainty tr(P) from the athlete's latest EKF belief, or ``default``."""
    row = (
        await db.execute(
            select(EkfShadowLog)
            .where(EkfShadowLog.user_id == user_id)
            .order_by(EkfShadowLog.id.desc())
            .limit(1)
        )
    ).scalars().first()
    if row is None:
        return default
    belief = EkfBelief.from_row(
        mean_map=dict(row.mean_json), cov_list=list(row.covariance_json),
        timestamp=row.belief_at, model_version=row.model_version,
    )
    return belief.trace()


async def record_mpc_shadow(
    db: AsyncSession,
    user_id: int,
    state: UnifiedStateVector,
    candidate_log: Sequence[SessionCandidate],
    goal: str,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    weights: MpcWeights | None = None,
) -> None:
    """Write one MPC shadow row for a prescription. Never raises to the caller."""
    pool = list(candidate_log)
    if not pool:
        return  # safety-override / empty pool → nothing to plan over

    w = weights or MpcWeights()
    async with best_effort_write(db, f"mpc shadow log for user {user_id}"):
        belief_trace = await _latest_belief_trace(db, user_id, default=w.uncertainty_ref)
        evals = evaluate_candidates(state, pool, goal, belief_trace, horizon_days=horizon_days, weights=w)

        greedy = pool[0]
        mpc = evals[0].candidate
        candidate_scores = [
            {
                "branch_id": e.candidate.branch_id,
                "type": e.candidate.type,
                "J": round(e.breakdown.J, 6),
                **{k: round(v, 6) for k, v in e.breakdown.terms.items()},
            }
            for e in evals
        ]
        db.add(
            MpcShadowLog(
                user_id=user_id,
                goal=goal,
                horizon_days=horizon_days,
                greedy_branch_id=greedy.branch_id,
                greedy_type=greedy.type,
                mpc_branch_id=mpc.branch_id,
                mpc_type=mpc.type,
                agreement=(greedy.branch_id == mpc.branch_id),
                belief_trace=round(belief_trace, 6),
                candidate_scores_json=candidate_scores,
                weights_json=asdict(w),
                decision_impact="none_shadow_only",
            )
        )
