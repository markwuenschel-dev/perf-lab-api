"""Receding-horizon MPC planner: roll each candidate forward, score, rank (ADR-0042).

For every candidate in the prescriber's pool we apply *today's* candidate dose, then roll a
**fixed goal-typical continuation** forward over the horizon (identical for every candidate,
so score differences isolate today's decision), and score the resulting trajectory with the
risk-aware objective ``J``. The best-J candidate is what MPC would choose; ``pool[0]`` is
the greedy prescriber's choice. Pure / DB-free — the EKF belief trace is passed in.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from app.engine.simulate import Step, run_schedule, session_log_from_intent
from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.logic.mpc.candidate_dose import candidate_to_log, modality_for_domain
from app.logic.mpc.objective import MpcWeights, ObjectiveBreakdown, score_trajectory
from app.logic.state_update_v0 import update_athlete_state
from app.schemas.state import UnifiedStateVector

DEFAULT_HORIZON_DAYS = 14
_CONTINUATION_CADENCE_DAYS = 2.0  # a maintenance session every ~2 days over the horizon


@dataclass
class CandidateEvaluation:
    candidate: SessionCandidate
    breakdown: ObjectiveBreakdown

    @property
    def score(self) -> float:
        return self.breakdown.J


def rollout_candidate(
    state: UnifiedStateVector,
    candidate: SessionCandidate,
    goal: str,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> list[UnifiedStateVector]:
    """Apply today's candidate, then a fixed goal-typical continuation over the horizon."""
    t0 = state.timestamp
    today_log = candidate_to_log(candidate, t0)
    after_today = update_athlete_state(state, calculate_stress_dose(today_log), timedelta(0), today_log)

    modality = modality_for_domain(goal)
    steps: list[Step] = []
    day = _CONTINUATION_CADENCE_DAYS
    while day <= horizon_days:
        when = t0 + timedelta(days=day)
        steps.append(Step(at=when, log=session_log_from_intent(
            when, modality, scale=1.0, intensity="balanced", recovery="standard")))
        day += _CONTINUATION_CADENCE_DAYS

    continuation = run_schedule(after_today, steps)  # [after_today, ...continuation states]
    return [state, *continuation]


def evaluate_candidates(
    state: UnifiedStateVector,
    pool: Sequence[SessionCandidate],
    goal: str,
    belief_trace: float,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    weights: MpcWeights | None = None,
) -> list[CandidateEvaluation]:
    """Score every candidate by horizon-lookahead J, best-first. Empty pool → []."""
    evals = [
        CandidateEvaluation(
            candidate=c,
            breakdown=score_trajectory(
                rollout_candidate(state, c, goal, horizon_days=horizon_days),
                goal, belief_trace, weights,
            ),
        )
        for c in pool
    ]
    evals.sort(key=lambda e: e.breakdown.J, reverse=True)
    return evals
