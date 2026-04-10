from pydantic import BaseModel, Field
from typing import List, Optional

class OnboardRequest(BaseModel):
    email: str
    experience_years: float = Field(0.0, ge=0)
    experience_level: str = "intermediate"
    available_days_per_week: int = Field(3, ge=1, le=7)
    session_duration_minutes: int = 60
    equipment: List[str] = Field(default_factory=list)
    self_reported_weak_points: List[str] = Field(default_factory=list)
    goal: str = "Strength"

class OnboardResponse(BaseModel):
    user_id: int
    profile_id: int
    message: str
    next_step: str = "Call GET /v1/next-session?goal=Strength to get first prescription"