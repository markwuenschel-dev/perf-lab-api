"""Benchmark-specific validity profiles for capacity assimilation.

Each profile encodes measurement variance, mapping strength per capacity axis,
and sensitivity to fatigue/tissue/skill state. These feed into effective_variance()
which modulates the Kalman gain in _apply_capacity_residual.

These are priors, not validated truths. Refine after Q5 dataset analysis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.domain.vectors import FatigueState, TissueState
from app.schemas.state import UnifiedStateVector


@dataclass
class BenchmarkValidityProfile:
    benchmark_code: str
    measurement_variance: float
    protocol_variance: float = 0.0
    mapping_strength: dict[str, float] = field(default_factory=lambda: {})
    fatigue_sensitivity: dict[str, float] = field(default_factory=lambda: {})
    tissue_sensitivity: dict[str, float] = field(default_factory=lambda: {})
    skill_sensitivity: float = 0.0
    reliability_prior: float = 1.0
    min_attempts_for_strong_update: int = 1
    classification: Literal[
        "capacity_dominant", "fatigue_sensitive", "skill_sensitive", "noise_prone"
    ] = "capacity_dominant"


def _default_profiles() -> dict[str, BenchmarkValidityProfile]:
    return {
        "1rm": BenchmarkValidityProfile(
            benchmark_code="1rm",
            measurement_variance=0.04,
            protocol_variance=0.01,
            mapping_strength={"max_strength": 0.95, "hypertrophy": 0.30, "power": 0.25},
            fatigue_sensitivity={"cns": 0.50, "muscular": 0.35, "structural": 0.20},
            classification="capacity_dominant",
        ),
        "e1rm": BenchmarkValidityProfile(
            benchmark_code="e1rm",
            measurement_variance=0.07,
            protocol_variance=0.03,
            mapping_strength={"max_strength": 0.80, "hypertrophy": 0.35},
            fatigue_sensitivity={"cns": 0.40, "muscular": 0.50},
            classification="capacity_dominant",
        ),
        "rep_max": BenchmarkValidityProfile(
            benchmark_code="rep_max",
            measurement_variance=0.10,
            protocol_variance=0.04,
            mapping_strength={"hypertrophy": 0.75, "max_strength": 0.50, "work_capacity": 0.30},
            fatigue_sensitivity={"muscular": 0.70, "metabolic": 0.40, "cns": 0.30},
            classification="fatigue_sensitive",
        ),
        "mile": BenchmarkValidityProfile(
            benchmark_code="mile",
            measurement_variance=0.05,
            protocol_variance=0.03,
            mapping_strength={"aerobic": 0.85, "work_capacity": 0.40, "glycolytic": 0.30},
            fatigue_sensitivity={"metabolic": 0.30, "structural": 0.25},
            classification="capacity_dominant",
        ),
        "5k": BenchmarkValidityProfile(
            benchmark_code="5k",
            measurement_variance=0.04,
            protocol_variance=0.04,
            mapping_strength={"aerobic": 0.90, "work_capacity": 0.45},
            fatigue_sensitivity={"metabolic": 0.25, "structural": 0.20},
            classification="capacity_dominant",
        ),
        "400m": BenchmarkValidityProfile(
            benchmark_code="400m",
            measurement_variance=0.08,
            protocol_variance=0.03,
            mapping_strength={"glycolytic": 0.80, "power": 0.50, "aerobic": 0.30},
            fatigue_sensitivity={"metabolic": 0.60, "cns": 0.40, "muscular": 0.35},
            classification="fatigue_sensitive",
        ),
        "vertical_jump": BenchmarkValidityProfile(
            benchmark_code="vertical_jump",
            measurement_variance=0.08,
            protocol_variance=0.04,
            mapping_strength={"power": 0.85, "max_strength": 0.40},
            fatigue_sensitivity={"cns": 0.65, "muscular": 0.50, "structural": 0.30},
            classification="fatigue_sensitive",
        ),
        "grip": BenchmarkValidityProfile(
            benchmark_code="grip",
            measurement_variance=0.12,
            protocol_variance=0.05,
            mapping_strength={"max_strength": 0.50, "work_capacity": 0.25},
            fatigue_sensitivity={"grip": 0.80, "muscular": 0.40},
            tissue_sensitivity={"finger": 0.60, "wrist": 0.40},
            classification="noise_prone",
        ),
        "mobility": BenchmarkValidityProfile(
            benchmark_code="mobility",
            measurement_variance=0.15,
            protocol_variance=0.08,
            mapping_strength={"mobility": 0.70, "skill": 0.20},
            skill_sensitivity=0.30,
            classification="noise_prone",
        ),
        "technical_skill": BenchmarkValidityProfile(
            benchmark_code="technical_skill",
            measurement_variance=0.18,
            protocol_variance=0.06,
            mapping_strength={"skill": 0.80, "power": 0.20},
            fatigue_sensitivity={"cns": 0.50},
            skill_sensitivity=0.60,
            classification="skill_sensitive",
        ),
    }


_PROFILES: dict[str, BenchmarkValidityProfile] = _default_profiles()

_NOISE_PRONE_DEFAULT = BenchmarkValidityProfile(
    benchmark_code="unknown",
    measurement_variance=0.18,
    protocol_variance=0.05,
    mapping_strength={},
    classification="noise_prone",
)


def get_validity_profile(benchmark_code: str) -> BenchmarkValidityProfile:
    """Return the validity profile for a benchmark code. Defaults to noise_prone."""
    return _PROFILES.get(benchmark_code, _NOISE_PRONE_DEFAULT)


def effective_variance(
    profile: BenchmarkValidityProfile,
    state: UnifiedStateVector,
) -> float:
    """Compute R_eff = base variance + state-dependent uncertainty.

    Higher R_eff → smaller Kalman gain → less capacity update.
    Fatigue/tissue uncertainty is scaled by profile sensitivity, not state magnitude,
    so a low-sensitivity benchmark is not penalized by athlete fatigue.

    These are priors, not validated truths. Refine after Q5 dataset analysis.
    """
    base = profile.measurement_variance + profile.protocol_variance

    # Fatigue uncertainty: how much does current fatigue cloud the result?
    fat_contrib = 0.0
    for k in FatigueState.KEYS:
        sens = profile.fatigue_sensitivity.get(k, 0.0)
        val = getattr(state.fatigue_f, k, 0.0) / 100.0
        fat_contrib += sens * val
    fat_contrib *= 0.20  # scale factor: max sensitivity=1 × max fatigue=1 → +0.20

    # Tissue uncertainty
    tis_contrib = 0.0
    for k in TissueState.KEYS:
        sens = profile.tissue_sensitivity.get(k, 0.0)
        val = getattr(state.tissue_t, k, 0.0) / 100.0
        tis_contrib += sens * val
    tis_contrib *= 0.10

    skill_contrib = profile.skill_sensitivity * 0.08

    return base + fat_contrib + tis_contrib + skill_contrib
