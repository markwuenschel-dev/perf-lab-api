"""
Canonical domain / modality / goal vocabulary and mapping utilities.

All other modules should import from here to prevent drift between exercise
selection, benchmark lookup, dose modeling, and prescription templates.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical domain names (snake_case, lowercase)
# ---------------------------------------------------------------------------

DOMAINS = {
    "powerlifting",
    "weightlifting",
    "strength",
    "hypertrophy",
    "power",
    "running",
    "gymnastics",
    "calisthenics",
    "grip",
    "mixed",
    "general",
}

# ---------------------------------------------------------------------------
# Alias → canonical domain
# ---------------------------------------------------------------------------

DOMAIN_ALIASES: dict[str, str] = {
    # Goal literals (from TrainingGoal)
    "Powerlifting": "powerlifting",
    "powerlifting": "powerlifting",
    "OlympicLifts": "weightlifting",
    "weightlifting": "weightlifting",
    "Strength": "strength",
    "strength": "strength",
    "Hypertrophy": "hypertrophy",
    "hypertrophy": "hypertrophy",
    "Power": "power",
    "power": "power",
    "Running": "running",
    "running": "running",
    "HalfMarathon": "running",
    "FullMarathon": "running",
    "Sprinting": "running",
    "sprinting": "running",
    "Gymnastics": "gymnastics",
    "gymnastics": "gymnastics",
    "Calisthenics": "calisthenics",
    "calisthenics": "calisthenics",
    "Grip": "grip",
    "grip": "grip",
    "MetCon": "mixed",
    "metcon": "mixed",
    "Mixed": "mixed",
    "mixed": "mixed",
    "General": "general",
    "general": "general",
    # Sport domain tags from Exercise model
    "crossfit": "mixed",
    "hyrox": "mixed",
    "triathlon": "running",
    "track": "running",
    # Block goals (MesocycleBlock.BlockGoal) not 1:1 with TrainingGoal
    "Hyrox": "mixed",
    "CrossFit": "mixed",
    "Recomp": "general",
}


def canonical_domain(value: str) -> str:
    """Return canonical domain for any alias; falls back to lowercase input."""
    return DOMAIN_ALIASES.get(value, value.lower())


# ---------------------------------------------------------------------------
# DomainCode: one vocabulary, three roles (ADR-0057)
# ---------------------------------------------------------------------------
#
# A ``DomainCode`` is a member of ``DOMAINS`` filling one of three distinct,
# non-interchangeable semantic roles that must never be conflated as the same
# field:
#   1. Home domain          — a benchmark's/template's canonical specialist
#                             domain (``benchmark_definition.domain``,
#                             ``coaching_template.domain``, operational
#                             ``Objective.domain``).
#   2. Surfacing lens        — athlete-domain lenses under which a benchmark is
#                             *eligible to surface* in the onramp
#                             (``benchmark_definition.domain_lenses``).
#                             Discoverability metadata ONLY.
#   3. Prescription capability — ``PRESCRIPTION_SUPPORTED_DOMAINS`` below.
#
# Only canonical ``DOMAINS`` values are ever persisted or serialized; aliases
# live only at inbound boundaries and are never written back out.

# Explicit, reviewed capability declaration — the domains for which
# seed/exercise/constraint/onramp/dose/test support actually exists. It is a
# ``frozenset`` == DOMAINS for v1, NOT a live alias of ``DOMAINS``: a future
# domain added to the vocabulary must be added here *deliberately* once its
# prescription support ships, never automatically. (ADR-0057)
PRESCRIPTION_SUPPORTED_DOMAINS: frozenset[str] = frozenset(
    {
        "powerlifting",
        "weightlifting",
        "strength",
        "hypertrophy",
        "power",
        "running",
        "gymnastics",
        "calisthenics",
        "grip",
        "mixed",
        "general",
    }
)

# `domain_lenses_source` values distinguishing a curated lens list from a lens
# list defaulted to `[domain]` (ADR-0057 surfacing-lens role).
DOMAIN_LENSES_SOURCE_EXPLICIT = "explicit_curated"
DOMAIN_LENSES_SOURCE_DEFAULT = "home_domain_default"

# Invariants (checked at import so a drifted edit fails fast, and asserted again
# in tests/test_domain_vocab_canonical.py).
assert PRESCRIPTION_SUPPORTED_DOMAINS <= DOMAINS, (
    "PRESCRIPTION_SUPPORTED_DOMAINS must be a subset of DOMAINS"
)
assert set(DOMAIN_ALIASES.values()) <= DOMAINS, (
    "every DOMAIN_ALIASES target must be a canonical DOMAINS value"
)


def is_canonical_domain(value: str) -> bool:
    """True iff ``value`` is a canonical ``DomainCode`` (member of ``DOMAINS``)."""
    return value in DOMAINS


def is_prescription_supported(domain: str) -> bool:
    """True iff ``domain`` is in the explicit prescription-capability set."""
    return domain in PRESCRIPTION_SUPPORTED_DOMAINS


def normalize_domain_at_boundary(value: str | None) -> str | None:
    """Canonicalize an inbound (external/user) domain, raising on non-canonical.

    Used at write boundaries for operational ``DomainCode`` fields (e.g.
    ``Objective.domain``): aliases are folded to canonical, then the result is
    validated as a member of ``DOMAINS``. ``None`` passes through. Per ADR-0057
    the operational field *validates* as canonical rather than being renamed;
    the canonical value is what gets persisted, never the alias.
    """
    if value is None:
        return None
    canonical = canonical_domain(value)
    if canonical not in DOMAINS:
        raise ValueError(
            f"{value!r} is not a canonical DomainCode "
            f"(canonicalized to {canonical!r}, not in DOMAINS)"
        )
    return canonical


def block_goal_to_domain(block_goal: str) -> str:
    """Canonical domain for a MesocycleBlock goal (a `BlockGoal` enum value).

    Bridges the planning vocabulary (BlockGoal) to the single canonical domain
    taxonomy the prescriber dispatches on. See ADR-0038.
    """
    return canonical_domain(block_goal)


# ---------------------------------------------------------------------------
# Goal → canonical domain
# ---------------------------------------------------------------------------

GOAL_TO_DOMAIN: dict[str, str] = {
    "Strength": "strength",
    "Hypertrophy": "hypertrophy",
    "Power": "power",
    "General": "general",
    "OlympicLifts": "weightlifting",
    "Powerlifting": "powerlifting",
    "MetCon": "mixed",
    "Calisthenics": "calisthenics",
    "Gymnastics": "gymnastics",
    "Grip": "grip",
    "Running": "running",
    "Sprinting": "running",
    "HalfMarathon": "running",
    "FullMarathon": "running",
}

# ---------------------------------------------------------------------------
# Canonical modality names (match WorkoutLog.modality + Exercise.modality)
# ---------------------------------------------------------------------------

MODALITY_ALIASES: dict[str, str] = {
    "Strength": "Strength",
    "strength": "Strength",
    "Hypertrophy": "Hypertrophy",
    "hypertrophy": "Hypertrophy",
    "Power": "Power",
    "power": "Power",
    "Running": "Running",
    "running": "Running",
    "Mixed": "Mixed",
    "mixed": "Mixed",
    "Conditioning": "Mixed",
    "conditioning": "Mixed",
    "MetCon": "Mixed",
    "metcon": "Mixed",
    "Calisthenics": "Calisthenics",
    "calisthenics": "Calisthenics",
    "Gymnastics": "Calisthenics",
    "gymnastics": "Calisthenics",
}


def canonical_modality(value: str) -> str:
    """Return canonical modality string; falls back to title-case input."""
    return MODALITY_ALIASES.get(value, value.title())


# ---------------------------------------------------------------------------
# Goal → primary modality for exercise candidate filtering
# ---------------------------------------------------------------------------

GOAL_TO_MODALITY: dict[str, str] = {
    "Strength": "Strength",
    "Hypertrophy": "Hypertrophy",
    "Power": "Power",
    "General": "Mixed",
    "OlympicLifts": "Power",
    "Powerlifting": "Strength",
    "MetCon": "Mixed",
    "Calisthenics": "Calisthenics",
    "Gymnastics": "Calisthenics",
    "Grip": "Strength",
    "Running": "Running",
    "Sprinting": "Running",
    "HalfMarathon": "Running",
    "FullMarathon": "Running",
}

# ---------------------------------------------------------------------------
# Movement pattern families
# ---------------------------------------------------------------------------

MOVEMENT_FAMILIES: dict[str, list[str]] = {
    "squat_family": ["squat", "single_leg", "lunge", "step_up"],
    "hinge_family": ["hinge", "deadlift", "rdl", "good_morning", "swing"],
    "press_family": ["push_horizontal", "push_vertical", "press", "dip"],
    "pull_family": ["pull_horizontal", "pull_vertical", "row", "curl"],
    "carry_family": ["carry", "loaded_carry", "farmer"],
    "locomotion": ["run", "walk", "sprint", "skip"],
    "rotation_core": ["rotation", "core", "anti_rotation"],
    "jump_family": ["jump", "bound", "hop", "throw"],
}


def movement_family(pattern: str) -> str | None:
    """Return family name for a movement pattern, or None."""
    p = pattern.lower()
    for family, patterns in MOVEMENT_FAMILIES.items():
        if any(pat in p for pat in patterns):
            return family
    return None


# ---------------------------------------------------------------------------
# CapacityKey aliases (phi_adapt keys → CapacityState fields)
# ---------------------------------------------------------------------------

PHI_ADAPT_TO_CAPACITY: dict[str, str] = {
    "aerobic": "aerobic",
    "anaerobic": "glycolytic",
    "glycolytic": "glycolytic",
    "strength": "max_strength",
    "max_strength": "max_strength",
    "hypertrophy": "hypertrophy",
    "power": "power",
    "skill": "skill",
    "mobility": "mobility",
    "work_capacity": "work_capacity",
    "endurance": "aerobic",
}


def phi_adapt_key_to_capacity(phi_key: str) -> str | None:
    """Map phi_adapt dict key to CapacityState field name."""
    return PHI_ADAPT_TO_CAPACITY.get(phi_key)
