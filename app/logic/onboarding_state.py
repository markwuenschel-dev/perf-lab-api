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

from datetime import date
from typing import Any

# Age policy (PDR-0010). A plausible date of birth is a safety basic; being a minor is a
# *flagged limitation*, never a hard lock — the app never blocks the user.
MIN_AGE_YEARS = 5
MAX_AGE_YEARS = 100
MINOR_AGE_YEARS = 16


def age_from_dob(dob: date, today: date) -> int:
    """Whole years old on ``today``."""
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def validate_dob(dob: date, today: date) -> None:
    """Raise ``ValueError`` if ``dob`` is in the future or implies an implausible age."""
    if dob > today:
        raise ValueError("date_of_birth cannot be in the future")
    age = age_from_dob(dob, today)
    if not (MIN_AGE_YEARS <= age <= MAX_AGE_YEARS):
        raise ValueError(
            f"date_of_birth implies an implausible age ({age}); "
            f"expected {MIN_AGE_YEARS}–{MAX_AGE_YEARS}"
        )


def is_minor(dob: date | None, today: date) -> bool:
    """True iff the athlete is under the minor threshold (a flag, not a gate)."""
    return dob is not None and age_from_dob(dob, today) < MINOR_AGE_YEARS


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

    A primary objective, a declared training environment (equipment), and a date of birth
    (age is a safety input) are the user-supplied minimum to prescribe safely; experience
    level and available days have safe defaults. Precision inputs (1RM/5K/benchmarks) are
    never listed here — they are measurement debt, not a gate. (Contraindications are not
    modeled as a profile field yet; when added they join this list.)
    """
    missing: list[str] = []
    if not getattr(profile, "primary_goal", None):
        missing.append("primary_goal")
    if not getattr(profile, "equipment", None):
        missing.append("equipment")
    days = getattr(profile, "available_days_per_week", None)
    if not days or days < 1:
        missing.append("available_days_per_week")
    if not getattr(profile, "date_of_birth", None):
        missing.append("date_of_birth")
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
