
from pydantic import BaseModel, Field


class OnboardRequest(BaseModel):
    display_name: str | None = None
    experience_years: float = Field(0.0, ge=0)
    experience_level: str = "intermediate"
    available_days_per_week: int = Field(3, ge=1, le=7)
    session_duration_minutes: int = 60
    equipment: list[str] = Field(default_factory=list)
    self_reported_weak_points: list[str] = Field(default_factory=list)
    goal: str = "Strength"
    # Baseline lift / biometric context (all optional)
    squat_1rm_kg: float | None = Field(None, gt=0)
    deadlift_1rm_kg: float | None = Field(None, gt=0)
    bench_1rm_kg: float | None = Field(None, gt=0)
    bodyweight_kg: float | None = Field(None, gt=0)
    run_5k_seconds: float | None = Field(None, gt=0)

class OnboardResponse(BaseModel):
    user_id: int
    profile_id: int
    message: str
    next_step: str = "Call GET /v1/next-session?goal=Strength to get first prescription"