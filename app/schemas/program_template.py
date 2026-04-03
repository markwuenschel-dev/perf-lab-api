"""Encoded coaching decomposition (not a verbatim copyrighted program)."""

from pydantic import BaseModel, Field


class ProgramTemplate(BaseModel):
    id: str
    name: str
    domain: str
    goals: list[str] = Field(
        default_factory=list,
        description="TrainingGoal values this template applies to",
    )
    principles: list[str] = Field(default_factory=list)
    load_distribution: dict = Field(default_factory=dict)
    fatigue_profile: dict = Field(default_factory=dict)
    exercise_bias: dict = Field(default_factory=dict)
    constraint_rule_ids: list[str] = Field(default_factory=list)
    source_name: str = Field(
        ...,
        description='Attribution e.g. "Hinshaw-style aerobic principles"',
    )
    provenance_primitive_ids: list[str] = Field(default_factory=list)
    evidence_level: str = Field(
        "heuristic",
        description="high | moderate | heuristic — narrative only in v1",
    )
