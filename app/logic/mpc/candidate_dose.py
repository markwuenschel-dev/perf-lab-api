"""Convert a prescriber ``SessionCandidate`` into a training dose for MPC rollout.

A candidate carries no numeric modality/intensity — only ``type``/``focus``/``domain``/
``duration_min``. We infer a (modality, intensity, scale) intent and reuse the projection
layer's ``session_log_from_intent`` → the real ``calculate_stress_dose``, so the shadow
rollout uses the exact production dose law (session-level, no DB exercise resolution).
"""
from __future__ import annotations

from datetime import datetime

from app.engine.simulate import session_log_from_intent
from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.domain_vocab import canonical_domain
from app.logic.dose_engine_v0 import calculate_stress_dose
from app.schemas.workouts import StressDose, WorkoutLog

# Canonical domain → projection modality (keys of projection_service._BASE_SESSION).
_MODALITY_FOR_DOMAIN: dict[str, str] = {
    "strength": "Strength",
    "powerlifting": "Strength",
    "grip": "Strength",
    "hypertrophy": "Hypertrophy",
    "power": "Power",
    "weightlifting": "Power",
    "sprinting": "Power",
    "running": "Running",
    "endurance": "Running",
    "halfmarathon": "Running",
    "fullmarathon": "Running",
    "metcon": "Mixed",
    "conditioning": "Mixed",
    "mixed": "Mixed",
    "gymnastics": "Mixed",
    "calisthenics": "Mixed",
    "general": "Mixed",
}

# Base session duration per modality (projection_service._BASE_SESSION durations), used to
# turn a candidate's duration_min into a volume scale.
_BASE_DURATION: dict[str, float] = {
    "Strength": 60.0, "Hypertrophy": 60.0, "Power": 55.0, "Running": 50.0, "Mixed": 40.0,
}

_HARD_HINTS = ("max strength", "power", "heavy", "1rm", "peak", "intens", "sprint")
_EASY_HINTS = ("recovery", "deload", "technique", "mobility", "easy", "maintenance", "z2", "aerobic")


def modality_for_domain(domain: str) -> str:
    """Projection modality for a canonical domain (defaults to Mixed)."""
    return _MODALITY_FOR_DOMAIN.get(canonical_domain(domain), "Mixed") if domain else "Mixed"


def modality_for_candidate(candidate: SessionCandidate) -> str:
    """Best-guess projection modality from the candidate's domain, else its type text."""
    if candidate.domain:
        mod = _MODALITY_FOR_DOMAIN.get(canonical_domain(candidate.domain))
        if mod:
            return mod
    text = f"{candidate.type} {candidate.focus}".lower()
    for key, mod in (("strength", "Strength"), ("hypertroph", "Hypertrophy"),
                     ("power", "Power"), ("run", "Running"), ("sprint", "Power")):
        if key in text:
            return mod
    return "Mixed"


def intensity_for_candidate(candidate: SessionCandidate) -> str:
    """Heuristic intensity band (easy | balanced | hard) from the candidate's type/focus."""
    text = f"{candidate.type} {candidate.focus}".lower()
    if any(h in text for h in _EASY_HINTS):
        return "easy"
    if any(h in text for h in _HARD_HINTS):
        return "hard"
    return "balanced"


def candidate_to_log(
    candidate: SessionCandidate,
    when: datetime,
    *,
    recovery: str = "standard",
) -> WorkoutLog:
    """Synthesize the ``WorkoutLog`` for a candidate at time ``when``."""
    modality = modality_for_candidate(candidate)
    intensity = intensity_for_candidate(candidate)
    base_dur = _BASE_DURATION.get(modality, 45.0)
    scale = max(0.3, min(2.0, float(candidate.duration_min) / base_dur)) if candidate.duration_min else 1.0
    return session_log_from_intent(when, modality, scale=scale, intensity=intensity, recovery=recovery)


def candidate_to_dose(candidate: SessionCandidate, when: datetime, *, recovery: str = "standard") -> StressDose:
    """The training dose a candidate would impose, via the real dose engine."""
    return calculate_stress_dose(candidate_to_log(candidate, when, recovery=recovery))
