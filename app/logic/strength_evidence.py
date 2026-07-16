"""Strength-evidence authority policy (ADR-0055).

The single place that answers "may this observation touch measured capacity?" —
so the capacity-update path can be **hostile to bad evidence** rather than trusting
whatever a writer stamped on a row. Writers drift; this boundary does not.

Core invariant: **non-protocol workout logs may raise a lower-bound floor, but may
never create a negative capacity residual.** Only protocol-grade benchmark
observations update canonical capacity bidirectionally.
"""

from __future__ import annotations

from typing import Any

# --- Provenance vocabularies (persisted as text; enforced in code) -----------

SOURCE_BENCHMARK_TEST = "benchmark_test"
SOURCE_WORKOUT_EXTRACTION = "workout_extraction"
SOURCE_MANUAL = "manual"
SOURCE_MANUAL_ENTRY = "manual_entry"
SOURCE_COACH_VERIFIED = "coach_verified"
SOURCE_MIGRATION_LEGACY = "migration_legacy"
SOURCE_SYSTEM_BACKFILL = "system_backfill"

# Sources permitted to update canonical capacity bidirectionally. Everything else
# is, at most, lower-bound / prescription / tracking evidence. Fail-closed: a source
# not on this list can never regress capacity.
CAPACITY_AUTHORITATIVE_SOURCES: frozenset[str] = frozenset(
    {SOURCE_BENCHMARK_TEST, SOURCE_MANUAL, SOURCE_MANUAL_ENTRY, SOURCE_COACH_VERIFIED}
)

# Evidence types
EV_DIRECT_MEASUREMENT = "direct_measurement"
EV_PROTOCOL_GRADE_ESTIMATE = "protocol_grade_estimate"
EV_ESTIMATED_FROM_TRAINING_SET = "estimated_from_training_set"
EV_LOWER_BOUND = "lower_bound"
EV_LEGACY_UNKNOWN = "legacy_unknown"

CAPACITY_AUTHORITATIVE_EVIDENCE: frozenset[str] = frozenset(
    {EV_DIRECT_MEASUREMENT, EV_PROTOCOL_GRADE_ESTIMATE}
)

# Value semantics — the honesty ladder as data. measured ≠ estimated ≠ lower_bound ≠ unknown.
VS_MEASURED = "measured"
VS_ESTIMATED = "estimated"
VS_LOWER_BOUND = "lower_bound"
VS_UNKNOWN = "unknown"

# Purpose-specific validity ("valid for what?") — never a bare "valid".
VALIDITY_VALID_FOR_CAPACITY = "valid_for_capacity"
VALIDITY_VALID_FOR_PRESCRIPTION = "valid_for_prescription"
VALIDITY_TRACKING_ONLY = "tracking_only"
VALIDITY_QUARANTINED = "quarantined"
VALIDITY_INVALID = "invalid"

# Effort fidelity (ADR-0045) → evidence-authority multiplier. Strictly monotone in the
# documented ladder: set_level > group_level > session_level > missing. Unlike the
# confidence-side table in strength_calibration, `missing` is 0.0 here: absent effort
# confers no *authority* at all.
FIDELITY_MULTIPLIER: dict[str, float] = {
    "set_level": 1.0,
    "group_level": 0.5,
    "session_level": 0.25,
    "missing": 0.0,
}

# The one fidelity rung that is independent, proven, per-set evidence. Everything else
# (cloned quick-entry, a session-wide RPE, absent effort, or an unrecognized label) is
# held to the stricter extraction bar — see `is_e1rm_informative`.
FIDELITY_SET_LEVEL = "set_level"

# Signature default wherever effort fidelity is unstated: assume the weakest provenance.
FIDELITY_UNSTATED = "missing"


# `obs` is any object exposing `source`, `evidence_type`, `affects_capacity`,
# `can_regress_capacity` (a BenchmarkObservation ORM row, or a test stand-in). Typed
# `Any` to match the repo's ORM-reading convention (SQLAlchemy `Mapped[...]` breaks
# structural Protocol matching under pyright).


def capacity_authoritative(obs: Any) -> bool:
    """True only if this observation may update canonical capacity **bidirectionally**.

    Fail-closed: recomputed from provenance (source + evidence_type), *not* trusted
    from the stored ``affects_capacity`` flag alone — a mismarked legacy row must
    still be refused here. The stored flags are a policy snapshot; this is the law.
    """
    return (
        obs.source in CAPACITY_AUTHORITATIVE_SOURCES
        and (obs.evidence_type or EV_LEGACY_UNKNOWN) in CAPACITY_AUTHORITATIVE_EVIDENCE
        and bool(obs.affects_capacity)
        and bool(obs.can_regress_capacity)
    )


def may_regress_capacity(obs: Any, residual: float) -> bool:
    """Whether a capacity correction of sign ``residual`` is permitted.

    A downward correction (``residual < 0``) requires full capacity authority. Any
    non-authoritative source (notably ``workout_extraction``) can never pull capacity
    down — training proves you are stronger, never weaker.
    """
    if residual >= 0:
        return capacity_authoritative(obs) or _may_raise_lower_bound(obs)
    return capacity_authoritative(obs)


def _may_raise_lower_bound(obs: Any) -> bool:
    """Non-authoritative evidence may still ratchet an upward lower-bound floor."""
    return (obs.evidence_type or EV_LEGACY_UNKNOWN) in (
        EV_LOWER_BOUND,
        EV_DIRECT_MEASUREMENT,
        EV_PROTOCOL_GRADE_ESTIMATE,
    )


def is_e1rm_informative(
    reps: float | None,
    rpe: float | None,
    rir: float | None,
    effort_fidelity: str = FIDELITY_UNSTATED,
) -> bool:
    """Extraction gate: only low-rep, high-effort sets yield capacity-relevant e1RM.

    Epley extrapolation is only trustworthy at low reps, and an e1RM is only
    informative near failure.

    Fail-closed on provenance: only *proven* ``set_level`` effort clears the standard
    bar. Every rung below it on the ADR-0045 ladder — ``group_level`` (cloned
    quick-entry), ``session_level``, ``missing`` — plus any unrecognized label must
    clear a stricter bar, because none of them are independent per-set evidence and a
    less-trusted rung may never gate more permissively than a more-trusted one. The
    default is deliberately the weakest rung: a caller with true per-set effort says so.
    """
    if reps is None or reps < 1 or reps > 5:
        return False
    if effort_fidelity != FIDELITY_SET_LEVEL:
        return (rpe is not None and rpe >= 9.0) or (rir is not None and rir <= 1.0)
    return (rpe is not None and rpe >= 8.0) or (rir is not None and rir <= 2.0)
