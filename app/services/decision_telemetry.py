"""Best-effort telemetry capture for prescriber decisions (Workstream B).

Persists first-party decision labels — one :class:`PrescriptionDecision` plus
one :class:`CandidateDecisionLog` per considered candidate — so that offline
scoring-weight research (Q8, see
``app/analysis/feature_builders/scoring_weight_features.py``) has real data to
learn from.

This is DATA-CAPTURE ONLY. It never influences a prescription's content or the
HTTP response, and every write is best-effort: any failure is logged and
swallowed so a telemetry problem can never break a prescription.

IMPORTANT (see the warnings in ``app/models/telemetry.py``): do NOT fabricate
outcome labels such as ``followed_as_prescribed`` here — those belong to
``SessionFeedback`` and are explicitly out of scope for this write-path.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.constraint_engine.candidate import SessionCandidate, score_candidate
from app.models.telemetry import CandidateDecisionLog, PrescriptionDecision
from app.schemas.prescription import WorkoutPrescription

logger = logging.getLogger(__name__)

# The linear scoring axes carried by every SessionCandidate (see
# app.logic.constraint_engine.candidate.DEFAULT_SCORE_WEIGHTS). Captured verbatim
# so offline analysis can re-fit weights against the raw per-candidate signals.
_SCORE_AXES: tuple[str, ...] = (
    "goal_alignment",
    "state_fit",
    "weak_point_coverage",
    "fatigue_penalty",
    "tissue_penalty",
    "novelty_bonus",
    "habit_bonus",
    "template_bias",
)


def _score_components(candidate: SessionCandidate) -> dict[str, float]:
    """Snapshot a candidate's raw scoring axes for offline weight-fitting."""
    return {axis: float(getattr(candidate, axis, 0.0)) for axis in _SCORE_AXES}


async def persist_prescription_decision(
    db: AsyncSession,
    user_id: int,
    prescription: WorkoutPrescription,
    candidate_log: list[SessionCandidate],
    *,
    goal: str,
    decision_mode: str = "adaptive",
    algorithm_version: str | None = None,
    planned_session_id: int | None = None,
    state_snapshot: dict[str, Any] | None = None,
    block_context: dict[str, Any] | None = None,
) -> None:
    """Persist one ``PrescriptionDecision`` + N ``CandidateDecisionLog`` rows.

    Call this AFTER the prescription is finalized (and its planned-session
    content committed). The chosen candidate is ``candidate_log[0]`` — the
    prescriber emits the ranked pool best-first (see
    ``app.logic.prescriber.recommend_next_session``'s ``candidate_log_out``).

    Best-effort / non-blocking: the whole body is wrapped so any exception is
    logged and swallowed. On failure the telemetry rows are rolled back, which
    only affects the uncommitted telemetry inserts (the prescription is already
    committed by the caller).

    Args:
        db: Active async session (the same one the prescription was built on).
        user_id: Athlete id (``prescription_decisions.athlete_id``).
        prescription: The finalized prescription (its ``model_version`` is the
            default ``algorithm_version`` when none is supplied).
        candidate_log: The ranked in-memory pool from the prescriber. May be
            empty (e.g. a hard-safety override clears it) — a decision row is
            still written with no candidate rows.
        goal: Effective training-goal string that drove the prescription.
        decision_mode: ``"adaptive"`` or ``"static"`` (the prescription arm).
        algorithm_version: Override for ``algorithm_version``; defaults to the
            prescription's ``model_version``.
        planned_session_id: Linked planned-session id, if any.
        state_snapshot: JSON-serializable athlete-state snapshot.
        block_context: JSON-serializable block/objective context used for bias.
    """
    try:
        chosen = candidate_log[0] if candidate_log else None
        decision = PrescriptionDecision(
            athlete_id=user_id,
            planned_session_id=planned_session_id,
            goal=str(goal),
            algorithm_version=algorithm_version or prescription.model_version,
            decision_mode=decision_mode,
            state_snapshot_json=state_snapshot,
            block_context_json=block_context,
            chosen_candidate_id=chosen.branch_id if chosen is not None else None,
            chosen_score=score_candidate(chosen) if chosen is not None else None,
        )
        db.add(decision)
        # Flush to assign decision.id for the candidate-log FK, without committing.
        await db.flush()

        for candidate in candidate_log:
            db.add(
                CandidateDecisionLog(
                    prescription_decision_id=decision.id,
                    branch_id=candidate.branch_id,
                    candidate_type=candidate.type,
                    focus=candidate.focus,
                    source=candidate.source,
                    score_components_json=_score_components(candidate),
                    final_score=score_candidate(candidate),
                    # The logged pool is the set of scored *survivors*; the
                    # prescriber applies hard constraints during finalization,
                    # not by flagging pool members — so nothing here hard-failed.
                    hard_failed=False,
                    chosen=candidate is chosen,
                )
            )
        await db.commit()
    except Exception:  # pragma: no cover - telemetry must never break prescribe
        logger.exception(
            "Failed to persist prescription decision telemetry for user %s", user_id
        )
        try:
            await db.rollback()
        except Exception:
            logger.exception("Rollback after telemetry write failure also failed")
