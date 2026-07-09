"""Workout prescription + structured explainability (backward compatible)."""

from typing import Any

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

    # ADR-0045: strength prescriptions speak in load. When the athlete has a current
    # e1RM for this lift, the service resolves %e1RM → a suggested working kg against
    # the ADR-0029 intensity envelope, plus an RPE cap. Absent an e1RM these stay null
    # and the lift degrades to RPE-only autoregulation (the ``load_note`` fallback).
    prescribed_load_kg: float | None = Field(
        default=None, description="Suggested working load in kg (pre-fills the log)."
    )
    percent_e1rm: float | None = Field(
        default=None, description="Fraction of estimated 1RM the suggested load targets (0–1)."
    )
    rpe_cap: float | None = Field(
        default=None, description="Upper RPE bound for the working sets (ADR-0029 envelope)."
    )
    e1rm_basis_kg: float | None = Field(
        default=None, description="The current e1RM the suggestion was resolved against."
    )


class WorkoutPrescription(BaseModel):
    """
    Next-session recommendation. Legacy fields required; `why` optional for old clients.
    """

    type: str
    focus: str
    rationale: str
    duration_min: int
    model_version: str = Field(default="v0.3", description="Prescription engine version")
    exercises: list[ExercisePrescription] = Field(default_factory=lambda: [])
    why: PrescriptionExplanation | None = None

    def to_prescribed_content(self) -> dict[str, Any]:
        """Serialize for persistence into ``PlannedSession.prescribed_content``.

        The single source of truth for that JSON shape — the prescribe-and-persist
        seam (service + planning route) writes it, and state_service reads it back
        by string key (ADR-0031). Keeping it here means a new field flows to all
        three sites from one place.
        """
        return self.model_dump()
