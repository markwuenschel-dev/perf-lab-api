"""
app/schemas/profile.py

Read/update schemas for the athlete profile. Field names mirror OnboardRequest
(``*_kg`` suffix on lifts/biometrics) so the frontend speaks one vocabulary;
the endpoint maps those to the AthleteProfile columns (``squat_1rm`` etc.).
"""

from pydantic import BaseModel, Field


class ProfileRead(BaseModel):
    display_name: str | None
    primary_goal: str | None
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


class ProfileUpdate(BaseModel):
    """Partial update — only fields present in the request body are written.

    Nullable fields (lifts, biometrics) accept an explicit ``null`` to clear a
    previously stored value; omitting a field leaves it untouched.
    """

    display_name: str | None = None
    primary_goal: str | None = None
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
