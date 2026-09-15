"""Schemas for the one benchmark assessment surface (ADR-0047)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.logic.confidence_presentation import ConfidenceStatus


class AssessmentBenchmarkCard(BaseModel):
    code: str
    name: str
    domain: str
    domain_lenses: list[str]
    domain_lenses_source: str
    metric_type: str
    unit: str
    # What the benchmark measures, and — separately — how to measure it. Both code-owned
    # (app.scripts.seed_benchmarks.BENCHMARK_EXPLANATIONS); a protocol that is not established
    # reads "Measurement protocol not yet defined." rather than invented instructions.
    description: str | None
    protocol_summary: str | None
    # Capacity axes this benchmark measures (definition.state_targets).
    measures_axes: list[str]
    # Current certainty of the measured axes, from live variance only (ADR-0059);
    # null when the twin has no state yet (a fresh onramp).
    confidence_status: ConfidenceStatus | None
    last_observed_at: datetime | None
    eligible: bool
    recommended: bool
    recommend_rank: int | None
    utility: float
    utility_model_version: str
    # True for a canonical-lift e1RM benchmark: Assess records it as characterized strength
    # evidence (method + performance date) via POST /benchmarks/strength-evidence rather
    # than as a bare number (S2).
    strength_evidence_entry: bool = False


class AssessmentDomainGroup(BaseModel):
    domain: str
    cards: list[AssessmentBenchmarkCard]


class AssessmentSurfaceRead(BaseModel):
    mode: str
    active_domains: list[str]
    groups: list[AssessmentDomainGroup]
    # Ranked benchmark codes recommended to assess next (measurement debt).
    recommended: list[str]
    policy_version: str
