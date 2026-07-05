"""Shadow model-predictive-control (MPC) planner (ADR-0042).

A *parallel, shadow-only* planner. For each real prescription it re-ranks the prescriber's
own candidate pool by horizon-lookahead: roll each candidate forward under the deterministic
twin, score the trajectory with a risk-aware objective ``J`` (goal progress traded against
fatigue, tissue/injury risk, deload need, and EKF uncertainty), and record what MPC *would*
have chosen versus the greedy prescriber's actual choice.

It changes nothing an athlete sees — the prescriber stays greedy; this only logs
(``decision_impact="none_shadow_only"``), following the Q-shadow / EKF-shadow discipline.

Deterministic single-trajectory rollout (v1); stochastic/Monte-Carlo MPC and learned cost
weights are deferred. The EKF belief enters only as a ``λ_U·tr(P)`` conservatism term.
"""
from __future__ import annotations
