"""Policy-derived observation capacity authority (ADR-0058).

The permanent structural fix for the PR1 corruption class: a writer can no longer
assign capacity authority it did not earn. Authority is **derived** from five
orthogonal provenance dimensions, taken as the *minimum* of independent caps, and
a caller may only ever request *less* than the policy allows — never more.

    resolved = narrow_only( meet(source_cap, mode_cap, evidence_cap, protocol_cap),
                            requested )

The output is a ``capacity_effect`` — the state-transition **operator** the
observation may perform — realized as four distinct handlers (see
``app.logic.observation_authority`` consumers), never one residual path behind a
pile of booleans:

    none | initialize_prior | upward_lower_bound | bidirectional_update

Guardrail: provenance is wide; capacity authority is narrow and policy-derived.
Only a server-validated protocol grade unlocks bidirectional regression; ambiguous
history is ``legacy_unknown``, never fabricated into measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.logic import strength_evidence as se

POLICY_VERSION = "authority_policy_v1"

# ---------------------------------------------------------------------------
# Dimension 1 — source_type (origin/actor)
# ---------------------------------------------------------------------------
ST_ATHLETE_ENTRY = "athlete_entry"
ST_WORKOUT_EXTRACTION = "workout_extraction"
ST_LEGACY_UNKNOWN = "legacy_unknown"
# In the taxonomy but REJECTED at the write boundary until a real writer +
# validation + UX + tests ship (ADR-0058). Present here so the resolver can cap
# them to `none` if one ever leaks in, but `create_observation` refuses them.
ST_COACH_ENTRY = "coach_entry"
ST_DEVICE_IMPORT = "device_import"
ST_THIRD_PARTY_IMPORT = "third_party_import"

BUILT_SOURCE_TYPES: frozenset[str] = frozenset(
    {ST_ATHLETE_ENTRY, ST_WORKOUT_EXTRACTION, ST_LEGACY_UNKNOWN}
)
REJECTED_SOURCE_TYPES: frozenset[str] = frozenset(
    {ST_COACH_ENTRY, ST_DEVICE_IMPORT, ST_THIRD_PARTY_IMPORT}
)

# ---------------------------------------------------------------------------
# Dimension 2 — collection_mode (workflow context)
# ---------------------------------------------------------------------------
CM_ONBOARDING_ONRAMP = "onboarding_onramp"
CM_RETEST = "retest"
CM_AD_HOC = "ad_hoc"
CM_WORKOUT = "workout"
CM_LEGACY_UNKNOWN = "legacy_unknown"

BUILT_COLLECTION_MODES: frozenset[str] = frozenset(
    {CM_ONBOARDING_ONRAMP, CM_RETEST, CM_AD_HOC, CM_WORKOUT, CM_LEGACY_UNKNOWN}
)

# ---------------------------------------------------------------------------
# Dimension 4 — protocol identity + validity (server-derived, never client-asserted)
# ---------------------------------------------------------------------------
PV_NOT_EVALUATED = "not_evaluated"
PV_INCOMPLETE = "incomplete"
PV_VALID = "valid"
PV_INVALID = "invalid"

# provenance_operation — migration is NOT a collection_mode.
OP_LIVE_WRITE = "live_write"
OP_SCHEMA_BACKFILL = "schema_backfill"

# ---------------------------------------------------------------------------
# Dimension 5 — capacity_effect: the state-transition operator (a meet-semilattice)
# ---------------------------------------------------------------------------
CE_NONE = "none"
CE_INITIALIZE_PRIOR = "initialize_prior"
CE_UPWARD_LOWER_BOUND = "upward_lower_bound"
CE_BIDIRECTIONAL_UPDATE = "bidirectional_update"

CAPACITY_EFFECTS: tuple[str, ...] = (
    CE_NONE,
    CE_INITIALIZE_PRIOR,
    CE_UPWARD_LOWER_BOUND,
    CE_BIDIRECTIONAL_UPDATE,
)

# The lattice (diamond):
#
#              bidirectional_update
#               /               \
#     initialize_prior      upward_lower_bound
#               \               /
#                    none
#
# `meet(a, b)` is the greatest capacity_effect both dimensions permit. The two
# mid-level operators are incomparable kinds (seed-when-empty vs raise-a-floor),
# so their meet drops to `none` — a dimension that only allows seeding and one
# that only allows a floor share no capacity authority beyond nothing.
_MEET: dict[frozenset[str], str] = {
    frozenset({CE_NONE}): CE_NONE,
    frozenset({CE_INITIALIZE_PRIOR}): CE_INITIALIZE_PRIOR,
    frozenset({CE_UPWARD_LOWER_BOUND}): CE_UPWARD_LOWER_BOUND,
    frozenset({CE_BIDIRECTIONAL_UPDATE}): CE_BIDIRECTIONAL_UPDATE,
    frozenset({CE_NONE, CE_INITIALIZE_PRIOR}): CE_NONE,
    frozenset({CE_NONE, CE_UPWARD_LOWER_BOUND}): CE_NONE,
    frozenset({CE_NONE, CE_BIDIRECTIONAL_UPDATE}): CE_NONE,
    frozenset({CE_INITIALIZE_PRIOR, CE_UPWARD_LOWER_BOUND}): CE_NONE,
    frozenset({CE_INITIALIZE_PRIOR, CE_BIDIRECTIONAL_UPDATE}): CE_INITIALIZE_PRIOR,
    frozenset({CE_UPWARD_LOWER_BOUND, CE_BIDIRECTIONAL_UPDATE}): CE_UPWARD_LOWER_BOUND,
}


def meet(a: str, b: str) -> str:
    """Greatest capacity_effect permitted by both — the semilattice meet."""
    return _MEET[frozenset({a, b})]


def meet_all(caps: list[str]) -> str:
    """Fold ``meet`` over a non-empty list of caps."""
    result = caps[0]
    for cap in caps[1:]:
        result = meet(result, cap)
    return result


def _rank(effect: str) -> int:
    # Height in the lattice, for narrow-only comparison only (NOT a total order —
    # the two mid operators share rank 1 and their meet is `none`, per _MEET).
    return {CE_NONE: 0, CE_INITIALIZE_PRIOR: 1, CE_UPWARD_LOWER_BOUND: 1,
            CE_BIDIRECTIONAL_UPDATE: 2}[effect]


# ---------------------------------------------------------------------------
# Per-dimension caps (each returns the MAX capacity_effect that dimension allows)
# ---------------------------------------------------------------------------

def source_cap(source_type: str) -> str:
    if source_type == ST_ATHLETE_ENTRY:
        return CE_BIDIRECTIONAL_UPDATE
    if source_type == ST_WORKOUT_EXTRACTION:
        return CE_UPWARD_LOWER_BOUND  # hard denial: never bidirectional
    # legacy_unknown, rejected import/coach source_types → no authority
    return CE_NONE


def mode_cap(collection_mode: str) -> str:
    if collection_mode in (CM_RETEST, CM_AD_HOC):
        return CE_BIDIRECTIONAL_UPDATE
    if collection_mode == CM_ONBOARDING_ONRAMP:
        return CE_INITIALIZE_PRIOR  # hard denial: onramp seeds, never regresses
    if collection_mode == CM_WORKOUT:
        return CE_UPWARD_LOWER_BOUND
    return CE_NONE  # legacy_unknown / anything unrecognized


def evidence_cap(evidence_type: str | None, value_semantics: str | None) -> str:
    vs = value_semantics or se.VS_UNKNOWN
    if vs == se.VS_UNKNOWN:
        return CE_NONE  # hard denial: unknown value means nothing
    if vs == se.VS_MEASURED and (evidence_type or se.EV_LEGACY_UNKNOWN) in (
        se.EV_DIRECT_MEASUREMENT,
        se.EV_PROTOCOL_GRADE_ESTIMATE,
    ):
        return CE_BIDIRECTIONAL_UPDATE
    if vs in (se.VS_ESTIMATED, se.VS_LOWER_BOUND):
        return CE_UPWARD_LOWER_BOUND
    # measured value but non-measurement evidence label → floor only
    return CE_UPWARD_LOWER_BOUND


def protocol_cap(protocol_validity: str) -> str:
    if protocol_validity == PV_VALID:
        return CE_BIDIRECTIONAL_UPDATE
    if protocol_validity == PV_INVALID:
        return CE_NONE
    # not_evaluated / incomplete → no server-validated protocol grade, so no
    # bidirectional regression, but a floor/seed is still allowed.
    return CE_UPWARD_LOWER_BOUND


# ---------------------------------------------------------------------------
# Protocol validity — derived server-side from the definition, never a client flag
# ---------------------------------------------------------------------------

def derive_protocol_validity(
    *,
    has_standardization_rules: bool,
    value_semantics: str | None,
    raw_value_present: bool,
) -> str:
    """Server-derived protocol grade.

    A benchmark carries a server-owned scoring protocol iff its definition has
    ``standardization_rules`` (seed data, not a client assertion). Given that, a
    ``measured`` value with the required input present is ``valid``; a present-but-
    non-measured value is ``incomplete``; a missing input is ``incomplete``. A
    benchmark with no server protocol is ``not_evaluated`` (capped below
    bidirectional by ``protocol_cap``). This is the "a label is not validity" rule.
    """
    if not has_standardization_rules:
        return PV_NOT_EVALUATED
    if not raw_value_present:
        return PV_INCOMPLETE
    if value_semantics == se.VS_MEASURED:
        return PV_VALID
    return PV_INCOMPLETE


# ---------------------------------------------------------------------------
# Default provenance derivation for legacy callers (source string only)
# ---------------------------------------------------------------------------

_SOURCE_TO_SOURCE_TYPE: dict[str, str] = {
    se.SOURCE_BENCHMARK_TEST: ST_ATHLETE_ENTRY,
    se.SOURCE_MANUAL: ST_ATHLETE_ENTRY,
    se.SOURCE_MANUAL_ENTRY: ST_ATHLETE_ENTRY,
    se.SOURCE_COACH_VERIFIED: ST_ATHLETE_ENTRY,
    se.SOURCE_WORKOUT_EXTRACTION: ST_WORKOUT_EXTRACTION,
    se.SOURCE_MIGRATION_LEGACY: ST_LEGACY_UNKNOWN,
    se.SOURCE_SYSTEM_BACKFILL: ST_LEGACY_UNKNOWN,
}

_SOURCE_TO_COLLECTION_MODE: dict[str, str] = {
    se.SOURCE_BENCHMARK_TEST: CM_AD_HOC,
    se.SOURCE_MANUAL: CM_AD_HOC,
    se.SOURCE_MANUAL_ENTRY: CM_AD_HOC,
    se.SOURCE_COACH_VERIFIED: CM_AD_HOC,
    se.SOURCE_WORKOUT_EXTRACTION: CM_WORKOUT,
    se.SOURCE_MIGRATION_LEGACY: CM_LEGACY_UNKNOWN,
    se.SOURCE_SYSTEM_BACKFILL: CM_LEGACY_UNKNOWN,
}


def default_source_type(source: str) -> str:
    return _SOURCE_TO_SOURCE_TYPE.get(source, ST_LEGACY_UNKNOWN)


def default_collection_mode(source: str) -> str:
    return _SOURCE_TO_COLLECTION_MODE.get(source, CM_LEGACY_UNKNOWN)


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuthorityResolution:
    capacity_effect: str
    requested_capacity_effect: str
    resolution_reason: str
    policy_version: str
    protocol_validity: str
    source_cap: str
    mode_cap: str
    evidence_cap: str
    protocol_cap: str
    over_request_clamped: bool

    def legacy_flags(self) -> dict[str, bool]:
        """Derive the ADR-0055 booleans FROM the resolved effect (never the reverse)."""
        eff = self.capacity_effect
        return {
            "affects_capacity": eff in (CE_UPWARD_LOWER_BOUND, CE_BIDIRECTIONAL_UPDATE),
            "can_regress_capacity": eff == CE_BIDIRECTIONAL_UPDATE,
        }


def resolve_authority(
    *,
    source_type: str,
    collection_mode: str,
    evidence_type: str | None,
    value_semantics: str | None,
    protocol_validity: str,
    requested_capacity_effect: str | None = None,
) -> AuthorityResolution:
    """Resolve the permitted ``capacity_effect`` as the meet of independent caps,
    narrowed (never elevated) by any caller request.

    Hard denials are already encoded in the individual caps (workout_extraction /
    onboarding_onramp can never reach bidirectional; unknown semantics and invalid
    protocol collapse to ``none``). A caller may request *less*; an over-request
    (more than the policy allows) is clamped down to the policy ceiling and flagged
    — authority elevation can never be requested.
    """
    s_cap = source_cap(source_type)
    m_cap = mode_cap(collection_mode)
    e_cap = evidence_cap(evidence_type, value_semantics)
    p_cap = protocol_cap(protocol_validity)
    ceiling = meet_all([s_cap, m_cap, e_cap, p_cap])

    over_request_clamped = False
    if requested_capacity_effect is None:
        resolved = ceiling
        requested = ceiling
    else:
        requested = requested_capacity_effect
        narrowed = meet(ceiling, requested)
        # narrow-only: if the request is not ≤ ceiling in the lattice, meet drops
        # it below the request → the caller tried to elevate. Clamp + flag.
        if narrowed != requested or _rank(requested) > _rank(ceiling):
            over_request_clamped = requested != narrowed
        resolved = narrowed

    binding = min(
        [("source", s_cap), ("mode", m_cap), ("evidence", e_cap), ("protocol", p_cap)],
        key=lambda kv: _rank(kv[1]),
    )
    reason = (
        f"resolved={resolved}; ceiling={ceiling} bound_by={binding[0]}({binding[1]}); "
        f"caps[source={s_cap},mode={m_cap},evidence={e_cap},protocol={p_cap}]"
        f"{'; over_request_clamped' if over_request_clamped else ''}"
    )
    return AuthorityResolution(
        capacity_effect=resolved,
        requested_capacity_effect=requested,
        resolution_reason=reason,
        policy_version=POLICY_VERSION,
        protocol_validity=protocol_validity,
        source_cap=s_cap,
        mode_cap=m_cap,
        evidence_cap=e_cap,
        protocol_cap=p_cap,
        over_request_clamped=over_request_clamped,
    )


def capacity_effect_of(obs: Any) -> str:
    """Re-derive the capacity_effect for a persisted row from its provenance,
    fail-closed. The stored ``capacity_effect`` is a snapshot; this is the law and
    is used as the belt-and-suspenders gate at state-application time.
    """
    return resolve_authority(
        source_type=getattr(obs, "source_type", None) or default_source_type(obs.source),
        collection_mode=(
            getattr(obs, "collection_mode", None) or default_collection_mode(obs.source)
        ),
        evidence_type=obs.evidence_type,
        value_semantics=obs.value_semantics,
        protocol_validity=getattr(obs, "protocol_validity", None) or PV_NOT_EVALUATED,
    ).capacity_effect
