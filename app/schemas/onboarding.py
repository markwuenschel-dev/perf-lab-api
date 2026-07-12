from datetime import date

from pydantic import BaseModel, Field, field_validator

from app.logic.onboarding_state import validate_dob


def _validate_optional_dob(v: date | None) -> date | None:
    """Shared field validator: a present DOB must not be future / implausible (PDR-0010)."""
    if v is not None:
        validate_dob(v, date.today())
    return v


class OnboardRequest(BaseModel):
    display_name: str | None = None
    experience_years: float = Field(0.0, ge=0)
    experience_level: str = "intermediate"
    available_days_per_week: int = Field(3, ge=1, le=7)
    session_duration_minutes: int = 60
    equipment: list[str] = Field(default_factory=list)
    self_reported_weak_points: list[str] = Field(default_factory=list)
    goal: str = "Strength"
    date_of_birth: date | None = None
    # Baseline lift / biometric context (all optional)
    squat_1rm_kg: float | None = Field(None, gt=0)
    deadlift_1rm_kg: float | None = Field(None, gt=0)
    bench_1rm_kg: float | None = Field(None, gt=0)
    bodyweight_kg: float | None = Field(None, gt=0)
    run_5k_seconds: float | None = Field(None, gt=0)

    _dob = field_validator("date_of_birth")(_validate_optional_dob)

class OnboardResponse(BaseModel):
    user_id: int
    profile_id: int
    message: str
    next_step: str = "Call GET /v1/next-session?goal=Strength to get first prescription"


class OnboardingTwinSummary(BaseModel):
    seeded: bool
    seed_status: str  # initial_seed_status_rollup_v1: none|experience_prior_only|benchmark_seeded|mixed
    provisional: bool
    overall_confidence: str | None  # worst-axis band (established|provisional|insufficient), live variance


class OnboardingStateResponse(BaseModel):
    status: str  # not_started | in_progress | completed
    completed_reason: str | None
    can_prescribe: bool  # the ONLY hard gate — safety/feasibility basics present
    missing_basics: list[str]
    is_minor: bool = False  # a flagged limitation (PDR-0010), never a hard lock
    twin: OnboardingTwinSummary
    # Progressive measurement-debt prompts: benchmark codes to assess next (never a gate).
    measurement_debt: list[str]


class CompleteOnboardingRequest(BaseModel):
    # A user may always leave; leaving early is not failure.
    reason: str = "done_for_now"  # finished | done_for_now | skipped