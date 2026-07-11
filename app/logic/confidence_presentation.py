"""Confidence + evidence presentation views (ADR-0059, confidence_presentation_policy_v1).

Two orthogonal facts about a capacity axis, never conflated:

- ``evidence_status`` — *provenance* (measured / estimated / inferred / experience_prior
  / unobserved): where the current value came from.
- ``confidence_status`` — *current certainty* (established / provisional / insufficient),
  derived from the **live per-axis variance ONLY**. A ``measured`` stamp can never
  override high live variance: a value may be "measured but provisional".

Runtime-safe: this reads live variance, never the seed snapshot. There is no global
``twin_is_provisional`` — recommendation-level provisionality is a separate aggregation
over the axes material to a given recommendation (deferred to the consuming surface).
"""

from __future__ import annotations

POLICY_VERSION = "confidence_presentation_policy_v1"

# Thresholds on the engine's compressed relative variance scale (measured ≈ 0.08,
# weak prior ≈ 1.0, cap = 1.5). An axis shrunk by a benchmark is "established"; a
# fresh experience prior is "provisional"; an unseeded placeholder is "insufficient".
ESTABLISHED_MAX_VARIANCE = 0.35
PROVISIONAL_MAX_VARIANCE = 1.05

STATUS_ESTABLISHED = "established"
STATUS_PROVISIONAL = "provisional"
STATUS_INSUFFICIENT = "insufficient"


def confidence_status(variance: float) -> str:
    """Derive the certainty band from a live per-axis variance (only)."""
    if variance <= ESTABLISHED_MAX_VARIANCE:
        return STATUS_ESTABLISHED
    if variance <= PROVISIONAL_MAX_VARIANCE:
        return STATUS_PROVISIONAL
    return STATUS_INSUFFICIENT
