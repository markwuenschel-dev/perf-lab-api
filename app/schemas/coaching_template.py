"""Unified structured coaching template schema (public-principle abstractions)."""

from typing import Any, Literal

from pydantic import BaseModel, Field

SourceType = Literal["coach_system", "literature", "derived"]
EvidenceLevel = Literal["high", "moderate", "heuristic"]
IntensityBias = Literal["low", "moderate", "high", "wave"]
VolumeBias = Literal["low", "moderate", "high", "wave"]
DensityBias = Literal["low", "moderate", "high"]
SpecificityBias = Literal["low", "moderate", "high"]


class GoalBias(BaseModel):
    strength: float = 0.0
    power: float = 0.0
    hypertrophy: float = 0.0
    aerobic: float = 0.0
    glycolytic: float = 0.0
    skill: float = 0.0
    tissue_resilience: float = 0.0


class WeeklyStructure(BaseModel):
    days_per_week: int = 0
    primary_days: list[str] = Field(default_factory=list)
    secondary_days: list[str] = Field(default_factory=list)
    recovery_days: list[str] = Field(default_factory=list)


class LoadDistributionStructured(BaseModel):
    intensity_bias: IntensityBias = "moderate"
    volume_bias: VolumeBias = "moderate"
    density_bias: DensityBias = "moderate"
    specificity_bias: SpecificityBias = "moderate"


class FatigueProfileStructured(BaseModel):
    cns: float = 0.0
    muscular: float = 0.0
    metabolic: float = 0.0
    structural: float = 0.0
    tendon: float = 0.0
    grip: float = 0.0


class ExerciseBiasEntry(BaseModel):
    exercise_family: str
    weight: float


class StructuredCoachingTemplate(BaseModel):
    """Single schema for all domains — loaded from JSON."""

    template_id: str
    name: str
    domain: str = Field(
        ...,
        description="e.g. olympic_lifting, running, powerlifting, gymnastics",
    )
    source_type: SourceType
    source_name: str
    evidence_level: EvidenceLevel
    goal_bias: GoalBias = Field(default_factory=GoalBias)
    weekly_structure: WeeklyStructure = Field(default_factory=WeeklyStructure)
    load_distribution: LoadDistributionStructured = Field(
        default_factory=LoadDistributionStructured
    )
    state_targets: list[str] = Field(default_factory=list)
    fatigue_profile: FatigueProfileStructured = Field(
        default_factory=FatigueProfileStructured
    )
    hard_constraints: list[str] = Field(default_factory=list)
    soft_constraints: list[str] = Field(default_factory=list)
    exercise_bias: list[ExerciseBiasEntry] = Field(default_factory=list)
    benchmark_preferences: list[str] = Field(default_factory=list)
    progression_rules: list[str] = Field(default_factory=list)
    progression_gates: list[str] = Field(default_factory=list)
    deload_rules: list[str] = Field(default_factory=list)
    explain_tags: list[str] = Field(default_factory=list)

    def as_template_dict(self) -> dict[str, Any]:
        """For ConstraintContext.template (mutable dict)."""
        return self.model_dump(mode="json")
