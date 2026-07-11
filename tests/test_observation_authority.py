"""ADR-0058 — policy-derived observation capacity authority.

Pure, DB-free unit tests for the resolver: min-of-caps, hard denials, narrow-only
(no elevation), the four capacity_effect operators, and legacy-flag derivation.
"""
from __future__ import annotations

from app.logic import observation_authority as oa
from app.logic import strength_evidence as se

# --------------------------------------------------------------------------
# The meet-semilattice
# --------------------------------------------------------------------------

def test_meet_is_greatest_lower_bound() -> None:
    assert oa.meet(oa.CE_BIDIRECTIONAL_UPDATE, oa.CE_UPWARD_LOWER_BOUND) == oa.CE_UPWARD_LOWER_BOUND
    assert oa.meet(oa.CE_BIDIRECTIONAL_UPDATE, oa.CE_INITIALIZE_PRIOR) == oa.CE_INITIALIZE_PRIOR
    # incomparable mid operators drop to none
    assert oa.meet(oa.CE_INITIALIZE_PRIOR, oa.CE_UPWARD_LOWER_BOUND) == oa.CE_NONE
    assert oa.meet(oa.CE_NONE, oa.CE_BIDIRECTIONAL_UPDATE) == oa.CE_NONE
    # idempotent + commutative
    for a in oa.CAPACITY_EFFECTS:
        assert oa.meet(a, a) == a
        for b in oa.CAPACITY_EFFECTS:
            assert oa.meet(a, b) == oa.meet(b, a)


# --------------------------------------------------------------------------
# Full-authority happy path: an athlete retest of a protocol-valid benchmark
# --------------------------------------------------------------------------

def test_athlete_valid_protocol_retest_is_bidirectional() -> None:
    r = oa.resolve_authority(
        source_type=oa.ST_ATHLETE_ENTRY,
        collection_mode=oa.CM_RETEST,
        evidence_type=se.EV_DIRECT_MEASUREMENT,
        value_semantics=se.VS_MEASURED,
        protocol_validity=oa.PV_VALID,
    )
    assert r.capacity_effect == oa.CE_BIDIRECTIONAL_UPDATE
    assert r.legacy_flags() == {"affects_capacity": True, "can_regress_capacity": True}


# --------------------------------------------------------------------------
# Hard denials — no dimension can be talked past
# --------------------------------------------------------------------------

def test_workout_extraction_never_bidirectional() -> None:
    r = oa.resolve_authority(
        source_type=oa.ST_WORKOUT_EXTRACTION,
        collection_mode=oa.CM_WORKOUT,
        evidence_type=se.EV_DIRECT_MEASUREMENT,  # even if mislabeled measured...
        value_semantics=se.VS_MEASURED,
        protocol_validity=oa.PV_VALID,  # ...and even with a valid protocol
    )
    assert r.capacity_effect == oa.CE_UPWARD_LOWER_BOUND  # source caps it
    assert r.legacy_flags()["can_regress_capacity"] is False


def test_onboarding_onramp_never_bidirectional() -> None:
    r = oa.resolve_authority(
        source_type=oa.ST_ATHLETE_ENTRY,
        collection_mode=oa.CM_ONBOARDING_ONRAMP,
        evidence_type=se.EV_DIRECT_MEASUREMENT,
        value_semantics=se.VS_MEASURED,
        protocol_validity=oa.PV_VALID,
    )
    assert r.capacity_effect == oa.CE_INITIALIZE_PRIOR  # mode caps it to seeding


def test_unknown_semantics_collapses_to_none() -> None:
    r = oa.resolve_authority(
        source_type=oa.ST_ATHLETE_ENTRY,
        collection_mode=oa.CM_RETEST,
        evidence_type=None,
        value_semantics=se.VS_UNKNOWN,
        protocol_validity=oa.PV_VALID,
    )
    assert r.capacity_effect == oa.CE_NONE


def test_invalid_protocol_collapses_to_none() -> None:
    r = oa.resolve_authority(
        source_type=oa.ST_ATHLETE_ENTRY,
        collection_mode=oa.CM_RETEST,
        evidence_type=se.EV_DIRECT_MEASUREMENT,
        value_semantics=se.VS_MEASURED,
        protocol_validity=oa.PV_INVALID,
    )
    assert r.capacity_effect == oa.CE_NONE


def test_not_evaluated_protocol_caps_below_bidirectional() -> None:
    # A measured athlete retest of a benchmark with NO server protocol can only
    # raise a floor — a label is not validity.
    r = oa.resolve_authority(
        source_type=oa.ST_ATHLETE_ENTRY,
        collection_mode=oa.CM_RETEST,
        evidence_type=se.EV_DIRECT_MEASUREMENT,
        value_semantics=se.VS_MEASURED,
        protocol_validity=oa.PV_NOT_EVALUATED,
    )
    assert r.capacity_effect == oa.CE_UPWARD_LOWER_BOUND


# --------------------------------------------------------------------------
# Narrow-only: a caller may request less, never more
# --------------------------------------------------------------------------

def test_caller_may_narrow_down() -> None:
    r = oa.resolve_authority(
        source_type=oa.ST_ATHLETE_ENTRY,
        collection_mode=oa.CM_RETEST,
        evidence_type=se.EV_DIRECT_MEASUREMENT,
        value_semantics=se.VS_MEASURED,
        protocol_validity=oa.PV_VALID,
        requested_capacity_effect=oa.CE_UPWARD_LOWER_BOUND,
    )
    assert r.capacity_effect == oa.CE_UPWARD_LOWER_BOUND
    assert r.over_request_clamped is False


def test_over_request_is_clamped_and_flagged() -> None:
    # Policy ceiling is upward_lower_bound (workout source); a request to elevate to
    # bidirectional is clamped back down and flagged — elevation is never granted.
    r = oa.resolve_authority(
        source_type=oa.ST_WORKOUT_EXTRACTION,
        collection_mode=oa.CM_WORKOUT,
        evidence_type=se.EV_ESTIMATED_FROM_TRAINING_SET,
        value_semantics=se.VS_ESTIMATED,
        protocol_validity=oa.PV_NOT_EVALUATED,
        requested_capacity_effect=oa.CE_BIDIRECTIONAL_UPDATE,
    )
    assert r.capacity_effect == oa.CE_UPWARD_LOWER_BOUND
    assert r.over_request_clamped is True


# --------------------------------------------------------------------------
# Protocol validity derivation
# --------------------------------------------------------------------------

def test_derive_protocol_validity() -> None:
    assert oa.derive_protocol_validity(
        has_standardization_rules=True, value_semantics=se.VS_MEASURED, raw_value_present=True
    ) == oa.PV_VALID
    assert oa.derive_protocol_validity(
        has_standardization_rules=True, value_semantics=se.VS_ESTIMATED, raw_value_present=True
    ) == oa.PV_INCOMPLETE
    assert oa.derive_protocol_validity(
        has_standardization_rules=False, value_semantics=se.VS_MEASURED, raw_value_present=True
    ) == oa.PV_NOT_EVALUATED


# --------------------------------------------------------------------------
# Legacy caller default derivation (source string → source_type / mode)
# --------------------------------------------------------------------------

def test_source_string_defaults() -> None:
    assert oa.default_source_type("manual") == oa.ST_ATHLETE_ENTRY
    assert oa.default_source_type(se.SOURCE_WORKOUT_EXTRACTION) == oa.ST_WORKOUT_EXTRACTION
    assert oa.default_source_type("something_weird") == oa.ST_LEGACY_UNKNOWN
    assert oa.default_collection_mode("manual") == oa.CM_AD_HOC
    assert oa.default_collection_mode(se.SOURCE_WORKOUT_EXTRACTION) == oa.CM_WORKOUT
