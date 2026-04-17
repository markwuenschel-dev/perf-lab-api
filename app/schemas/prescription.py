"""Workout prescription + structured explainability (backward compatible)."""

from pydantic import BaseModel, Field


class ValidationSummary(BaseModel):
    """Result of validate_session checks."""

    passed: bool
    failed_checks: list[str] = Field(default_factory=list)
    hard_violations: list[str] = Field(default_factory=list)


class PrescriptionExplanation(BaseModel):
    """Why this session — state drivers, constraints, sources."""

    state_drivers: list[str] = Field(default_factory=list)
    goal_alignment: str = ""
    constraints_applied: list[str] = Field(default_factory=list)
    source_alignment: list[str] = Field(
        default_factory=list,
        description="Human-readable: templates + primitives + models",
    )
    template_id: str | None = None
    prescription_branch: str | None = Field(
        default=None,
        description="Internal prescriber branch id (safety, readiness, goal path)",
    )
    validation: ValidationSummary | None = None
    warnings: list[str] = Field(
        default_factory=list,
        description="Soft constraint / template warnings (non-blocking)",
    )
    score: float | None = Field(
        default=None,
        description="Template-aligned fit score vs twin state (0–1)",
    )
    structured_template_name: str | None = Field(
        default=None,
        description="Display name for structured coaching template (v2)",
    )


class ExercisePrescription(BaseModel):
    """A single prescribed exercise within a session."""
    name: str
    sets: int | None = None
    reps: str | None = None
    load_note: str | None = None
    weak_point_tags: list[str] = Field(default_factory=list)


class WorkoutPrescription(BaseModel):
    """
    Next-session recommendation. Legacy fields required; `why` optional for old clients.
    """

    type: str
    focus: str
    rationale: str
    duration_min: int
    model_version: str = Field("v0.3", description="Prescription engine version")
    exercises: list[ExercisePrescription] = Field(default_factory=list)
    why: PrescriptionExplanation | None = None
