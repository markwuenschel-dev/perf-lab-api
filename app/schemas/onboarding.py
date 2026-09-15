from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.logic.confidence_presentation import ConfidenceStatus
from app.logic.onboarding_state import validate_dob
from app.schemas.benchmarks import StrengthReport, refuse_bare_canonical_lift_numbers


def _validate_optional_dob(v: date | None) -> date | None:
    """Shared field validator: a present DOB must not be future / implausible (PDR-0010)."""
    if v is not None:
        validate_dob(v, date.today())
    return v


class OnboardStrengthReport(StrengthReport):
    """A strength report given during onboarding (S2 decision 3).

    The same characterization semantics as Assess — onboarding gets no weaker rules. It adds
    one input requirement: a tested max or a set must carry the date it was performed, so a
    fact asked for at signup is not recorded as undated. An estimate needs no date; it never
    sizes a load either way.
    """

    @model_validator(mode="after")
    def _dated_when_performed(self) -> "OnboardStrengthReport":
        if self.method in ("tested_max", "rep_set") and self.performed_at is None:
            raise ValueError(f"a {self.method} reported during onboarding needs performed_at")
        return self


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
    # Squat / bench / deadlift, characterized (S2). Each report becomes strength evidence
    # through the same service Assess uses, and the profile's seed value for that lift is
    # derived from it — never written as a bare number.
    strength: list[OnboardStrengthReport] = Field(default_factory=lambda: [])
    # Biometric context (optional)
    bodyweight_kg: float | None = Field(None, gt=0)
    run_5k_seconds: float | None = Field(None, gt=0)

    _dob = field_validator("date_of_birth")(_validate_optional_dob)

    @model_validator(mode="before")
    @classmethod
    def _refuse_bare_lift_numbers(cls, data: Any) -> Any:
        return refuse_bare_canonical_lift_numbers(data, instead="send them in `strength`.")

    @field_validator("strength")
    @classmethod
    def _one_report_per_lift(cls, v: list[OnboardStrengthReport]) -> list[OnboardStrengthReport]:
        codes = [report.benchmark_code for report in v]
        repeated = sorted({code for code in codes if codes.count(code) > 1})
        if repeated:
            raise ValueError(
                f"one strength report per lift during onboarding; repeated: {', '.join(repeated)}"
            )
        return v


class OnboardResponse(BaseModel):
    user_id: int
    profile_id: int
    message: str
    next_step: str = "Call GET /v1/next-session?goal=Strength to get first prescription"


class OnboardingTwinSummary(BaseModel):
    seeded: bool
    seed_status: str  # initial_seed_status_rollup_v1: none|experience_prior_only|benchmark_seeded|mixed
    provisional: bool
    # Worst-axis band over the seeded axes, from live variance. Same canonical
    # band as the assessment card and the per-axis snapshot statuses — typed from
    # the one Literal so all three publish the same enum.
    overall_confidence: ConfidenceStatus | None


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
