"""
app/schemas/profile.py

Read/update schemas for the athlete profile. Field names mirror OnboardRequest
(``*_kg`` suffix on lifts/biometrics) so the frontend speaks one vocabulary;
the endpoint maps those to the AthleteProfile columns (``squat_1rm`` etc.).
"""

from datetime import date

from pydantic import BaseModel, Field, field_validator

from app.logic.onboarding_state import validate_dob
from app.logic.wellness_registry import coverage_signals


class ProfileRead(BaseModel):
    display_name: str | None
    primary_goal: str | None
    date_of_birth: date | None = None
    experience_years: float
    experience_level: str
    available_days_per_week: int
    session_duration_minutes: int
    equipment: list[str]
    squat_1rm_kg: float | None
    deadlift_1rm_kg: float | None
    bench_1rm_kg: float | None
    overhead_1rm_kg: float | None
    pullup_max_reps: int | None
    run_5k_seconds: float | None
    run_1p5mi_seconds: float | None
    bodyweight_kg: float | None
    height_cm: float | None
    # Wellness signals the athlete explicitly marked "I don't track this" (ADR-0049);
    # hidden from the check-in and never expected. Missing-but-tracked stays an honest gap.
    untracked_wellness_signals: list[str] = Field(default_factory=list)


class ProfileUpdate(BaseModel):
    """Partial update — only fields present in the request body are written.

    Nullable fields (lifts, biometrics) accept an explicit ``null`` to clear a
    previously stored value; omitting a field leaves it untouched.
    """

    display_name: str | None = None
    primary_goal: str | None = None
    date_of_birth: date | None = None
    experience_years: float | None = Field(None, ge=0)
    experience_level: str | None = None
    available_days_per_week: int | None = Field(None, ge=1, le=7)
    session_duration_minutes: int | None = Field(None, ge=1)
    equipment: list[str] | None = None
    squat_1rm_kg: float | None = Field(None, gt=0)
    deadlift_1rm_kg: float | None = Field(None, gt=0)
    bench_1rm_kg: float | None = Field(None, gt=0)
    overhead_1rm_kg: float | None = Field(None, gt=0)
    pullup_max_reps: int | None = Field(None, ge=0)
    run_5k_seconds: float | None = Field(None, gt=0)
    run_1p5mi_seconds: float | None = Field(None, gt=0)
    bodyweight_kg: float | None = Field(None, gt=0)
    height_cm: float | None = Field(None, gt=0)
    # Full replacement of the explicit "don't track" opt-out list when present.
    untracked_wellness_signals: list[str] | None = None

    @field_validator("date_of_birth")
    @classmethod
    def _check_dob(cls, v: date | None) -> date | None:
        if v is not None:
            validate_dob(v, date.today())
        return v

    @field_validator("untracked_wellness_signals")
    @classmethod
    def _check_untracked_wellness_signals(cls, v: list[str] | None) -> list[str] | None:
        """Reject signal names the coverage engine does not recognize.

        Without this the write boundary accepts anything: readiness intersects
        unknown names away against ``coverage_signals()`` while this API echoes the
        stored raw list back, so the client is told about an opt-out the engine
        never honours. Refuse the write rather than report state that isn't real.
        Duplicates are collapsed and order is preserved; ``None`` passes through so
        PATCH semantics (omitted = untouched) are unaffected.
        """
        if v is None:
            return None
        known = set(coverage_signals())
        unknown = [s for s in v if s not in known]
        if unknown:
            raise ValueError(
                f"unknown wellness signal(s) {sorted(set(unknown))}; "
                f"known signals are {sorted(known)}"
            )
        return list(dict.fromkeys(v))
