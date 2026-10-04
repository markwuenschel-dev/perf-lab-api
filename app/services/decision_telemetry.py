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

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.constraint_engine.candidate import SessionCandidate, score_candidate
from app.models.telemetry import CandidateDecisionLog, PrescriptionDecision
from app.schemas.prescription import WorkoutPrescription
from app.services.telemetry_common import best_effort_write

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


#: Early exits that prescribe without ranking a pool, by the branch id they finalize under
#: (``app.logic.prescriber``). Each is its own outcome, never ``as_ranked``.
_UNRANKED_EXITS: dict[str, str] = {
    "equipment_unavailable": "equipment_unavailable",
    "constraint_infeasible": "constraint_infeasible",
}


def final_outcome(prescription: WorkoutPrescription, ranked_winner: str | None) -> str:
    """What was prescribed, relative to the ranking — from the prescription the athlete got.

    ``ranked_winner`` is the branch id of the top-ranked candidate, or None when no candidate
    was ranked (a safety override clears the pool; the early exits never build one).

    ``safety_unevaluated_rest`` — a hard rule could not run, so the session became rest;
    ``hard_violation_replaced`` — an evaluated hard rule failed and replaced the session;
    ``safety_override`` — a pre-scoring safety branch prescribed;
    ``equipment_unavailable`` / ``constraint_infeasible`` — an early exit prescribed;
    ``as_ranked`` — the ranked winner itself was prescribed;
    ``unknown`` — none of the above can be established. Never a guess.
    """
    why = prescription.why
    validation = why.validation if why is not None else None
    if validation is not None and validation.unevaluated_hard:
        return "safety_unevaluated_rest"
    if validation is not None and validation.hard_violations:
        return "hard_violation_replaced"
    branch = why.prescription_branch if why is not None else None
    if branch is not None and branch.startswith("safety_"):
        return "safety_override"
    if branch is not None and branch in _UNRANKED_EXITS:
        return _UNRANKED_EXITS[branch]
    if ranked_winner is not None and branch == ranked_winner:
        return "as_ranked"
    return "unknown"


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
    async with best_effort_write(db, f"prescription decision telemetry for user {user_id}") as tx:
        tdb = tx.db  # F1: the telemetry transaction's own session
        chosen = candidate_log[0] if candidate_log else None
        validation = prescription.why.validation if prescription.why is not None else None
        hard_violations = list(validation.hard_violations) if validation is not None else []
        unevaluated_hard = list(validation.unevaluated_hard) if validation is not None else []
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
            # The ranking above is evidence; this is what was actually prescribed (W1-c).
            final_outcome=final_outcome(
                prescription, chosen.branch_id if chosen is not None else None
            ),
            final_prescription_type=prescription.type,
            final_duration_min=prescription.duration_min,
            hard_violations_json=hard_violations,
            unevaluated_hard_json=unevaluated_hard,
        )
        tdb.add(decision)
        # Flush to assign decision.id for the candidate-log FK, without committing.
        await tdb.flush()

        for candidate in candidate_log:
            tdb.add(
                CandidateDecisionLog(
                    prescription_decision_id=decision.id,
                    branch_id=candidate.branch_id,
                    candidate_type=candidate.type,
                    focus=candidate.focus,
                    source=candidate.source,
                    score_components_json=_score_components(candidate),
                    final_score=score_candidate(candidate),
                    # The logged pool is the set of scored *survivors*; the prescriber
                    # applies hard constraints only to the WINNER, during finalization.
                    # So only the chosen candidate can have hard-failed, and only when an
                    # evaluated rule rejected it. A rule that could not run judged nothing:
                    # that is recorded on the decision (unevaluated_hard_json), not here.
                    hard_failed=candidate is chosen and bool(hard_violations),
                    hard_fail_reasons_json=(
                        hard_violations if candidate is chosen and hard_violations else None
                    ),
                    chosen=candidate is chosen,
                )
            )
    # commit / on-failure log-and-rollback handled by best_effort_write
