"""Non-blocking onboarding state machine (PDR-0010).

The app never gates access on a performance measurement. The **only** hard gate is
"can we prescribe safely at all" — the safety/feasibility basics. Everything that only
improves precision (1RM, 5K, VO₂, skill benchmarks, wearable sync) is non-blocking: a
user who measures nothing still enters on a provisional experience-prior seed, with
unmeasured axes surfaced as measurement debt.

Statuses (persisted on ``AthleteProfile.onboarding_status``):
  not_started → in_progress (basics submitted, twin seeded, app usable) → completed.
``completed_reason`` records how it ended: finished | done_for_now | skipped — a user
may always leave; leaving early is not failure.
"""

from __future__ import annotations

from typing import Any

STATUS_NOT_STARTED = "not_started"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"

REASON_FINISHED = "finished"
REASON_DONE_FOR_NOW = "done_for_now"
REASON_SKIPPED = "skipped"
COMPLETION_REASONS: frozenset[str] = frozenset(
    {REASON_FINISHED, REASON_DONE_FOR_NOW, REASON_SKIPPED}
)


def required_basics_missing(profile: Any) -> list[str]:
    """The safety/feasibility basics still missing — the ONLY hard gate.

    A primary objective and a declared training environment (equipment) are the
    user-supplied minimum to prescribe safely; experience level and available days have
    safe defaults. Precision inputs (1RM/5K/benchmarks) are never listed here — they are
    measurement debt, not a gate. (Age/minor status + contraindications are not modeled
    as profile fields yet; when added they join this list.)
    """
    missing: list[str] = []
    if not getattr(profile, "primary_goal", None):
        missing.append("primary_goal")
    if not getattr(profile, "equipment", None):
        missing.append("equipment")
    days = getattr(profile, "available_days_per_week", None)
    if not days or days < 1:
        missing.append("available_days_per_week")
    return missing


def can_prescribe(profile: Any) -> bool:
    """Safe to prescribe iff the hard-gate basics are all present."""
    return not required_basics_missing(profile)


def status_after_basics(current_status: str) -> str:
    """Advance to in_progress once basics are submitted — unless already completed."""
    if current_status == STATUS_COMPLETED:
        return STATUS_COMPLETED
    return STATUS_IN_PROGRESS


def is_valid_completion_reason(reason: str) -> bool:
    return reason in COMPLETION_REASONS
