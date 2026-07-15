"""Strict engine_state decoding (INT-15a, slice 1).

Pure — no database, no fixtures. The codec is policy-free by design, so it is testable
without any of the surfaces that consume it.

Each test in the first section pins one of the three defects the codec replaces. They
would all pass against the old ``state_bridge._migrate_engine_state`` for the wrong
reason — that code never raised at all; it silently degraded instead.
"""

import json

import pytest

from app.engine.engine_state_codec import (
    MAX_READ_VERSION,
    EngineStateV2,
    MalformedCurrentEngineState,
    MissingEngineState,
    UnsupportedFutureEngineStateVersion,
    decode_engine_state,
    inspect_declared_version,
    payload_hash,
)
from app.schemas.engine_vectors import (
    CapacityConfidence,
    CapacityState,
    FatigueState,
    TissueState,
)


def _valid(version: int = MAX_READ_VERSION, **overrides) -> dict:
    payload = {
        "version": version,
        "x": CapacityState().model_dump(),
        "f": FatigueState().model_dump(),
        "t": TissueState().model_dump(),
        "c": CapacityConfidence().model_dump(),
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Defect 1 — the version stamp must be load-bearing
# --------------------------------------------------------------------------- #

def test_future_version_raises_and_is_not_downgraded():
    """A v3 payload on a v2 reader is a reader-capability failure, not bad data.

    The old code sniffed for x/f/t, restamped to 2, and dropped v3-only fields on the
    next write — silent destructive downgrade on a rolling deploy or rollback.
    """
    future = _valid(version=MAX_READ_VERSION + 1, brand_new_field={"kept": True})
    with pytest.raises(UnsupportedFutureEngineStateVersion) as exc:
        decode_engine_state(future)
    assert exc.value.declared_version == MAX_READ_VERSION + 1
    assert exc.value.max_supported == MAX_READ_VERSION
    # The payload we were handed is untouched — nothing restamped it in place.
    assert future["version"] == MAX_READ_VERSION + 1
    assert future["brand_new_field"] == {"kept": True}


def test_future_version_is_not_malformed():
    """The two outcomes must stay distinct: repair a malformed row; deploy readers
    first for a future one. Conflating them sends operators the wrong way."""
    with pytest.raises(UnsupportedFutureEngineStateVersion):
        decode_engine_state(_valid(version=99))
    assert not issubclass(UnsupportedFutureEngineStateVersion, MalformedCurrentEngineState)


def test_upgrade_is_keyed_on_declared_version_not_structure():
    """v1 -> v2 seeds capacity confidence. The old code keyed this on ``"c" not in
    payload``, so a v1 row carrying a stray ``c`` skipped the upgrade entirely and the
    version stamp could hold any value with no effect at all."""
    v1 = _valid(version=1)
    del v1["c"]
    state = decode_engine_state(v1)
    assert state.version == MAX_READ_VERSION
    assert state.capacity_confidence == CapacityConfidence()


def test_unversioned_payload_is_treated_as_v1():
    unversioned = _valid()
    del unversioned["version"]
    del unversioned["c"]
    state = decode_engine_state(unversioned)
    assert state.version == MAX_READ_VERSION
    assert state.capacity_confidence == CapacityConfidence()


@pytest.mark.parametrize("bogus", ["2", 2.0, True, None, {"nested": 1}])
def test_unusable_version_stamp_is_treated_as_unversioned(bogus):
    """A non-int stamp is not a future version — don't let a string "99" masquerade
    as one, and don't let True (which is an int in Python) read as version 1."""
    payload = _valid()
    payload["version"] = bogus
    assert inspect_declared_version(payload) is None
    decode_engine_state(payload)  # falls back to v1 handling, does not raise


# --------------------------------------------------------------------------- #
# Defect 2 — malformed payloads must raise, never degrade silently
# --------------------------------------------------------------------------- #

def test_missing_state_is_its_own_outcome():
    with pytest.raises(MissingEngineState):
        decode_engine_state(None)


def test_unparseable_json_string_raises():
    with pytest.raises(MalformedCurrentEngineState) as exc:
        decode_engine_state("{not json")
    assert exc.value.error_code == "unparseable_payload"


@pytest.mark.parametrize("payload", [42, [1, 2], "null", '"a string"'])
def test_non_object_payload_raises(payload):
    with pytest.raises(MalformedCurrentEngineState):
        decode_engine_state(payload)


@pytest.mark.parametrize("drop", ["x", "f", "t"])
def test_missing_vector_raises_rather_than_falling_back(drop):
    """The old code returned None here, and the caller silently rebuilt state from
    legacy scalars — no log, no metric. Deciding what to do instead is the adapter's
    job; the codec's job is to refuse."""
    payload = _valid()
    del payload[drop]
    with pytest.raises(MalformedCurrentEngineState) as exc:
        decode_engine_state(payload)
    assert exc.value.error_code == "missing_vectors"
    assert drop in str(exc.value)


def test_garbage_vector_raises_typed_not_validation_error():
    """A raw pydantic ValidationError escaping the codec would be a 500 at whatever
    call site happened to trigger it."""
    with pytest.raises(MalformedCurrentEngineState) as exc:
        decode_engine_state(_valid(x="garbage"))
    assert exc.value.error_code == "vector_not_an_object"


def test_error_detail_never_contains_payload_contents():
    """Raw JSONB is athlete data and must never reach a log. The exception carries a
    normalized code, not the payload."""
    secret = {"aerobic": 61.0, "note": "SENSITIVE_ATHLETE_DATA"}
    with pytest.raises(MalformedCurrentEngineState) as exc:
        decode_engine_state(_valid(x={**secret, "aerobic": "not-a-number"}))
    assert "SENSITIVE_ATHLETE_DATA" not in str(exc.value)
    assert exc.value.error_code == "vector_validation_failed"


# --------------------------------------------------------------------------- #
# Defect 3 — empty vectors are missing data, not a healthy athlete
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("key", ["x", "f", "t"])
def test_empty_vector_object_is_rejected(key):
    """``{"x": {}}`` used to validate into a FULL default-valued vector, because every
    field has a default and no schema sets ``extra=``.

    This is worse than it sounds — see ``test_empty_capacity_used_to_mean_max_strength``.
    """
    payload = _valid(**{key: {}})
    with pytest.raises(MalformedCurrentEngineState) as exc:
        decode_engine_state(payload)
    assert exc.value.error_code == "vector_empty"


def test_empty_capacity_used_to_mean_max_strength():
    """An empty capacity vector did not mean "athlete at neutral defaults".

    ``CapacityState()`` defaults ``max_strength`` to 100.0, which is exactly
    ``AXIS_CEILING`` — the top of the axis. So a damaged row silently decoded to an
    athlete at MAXIMUM strength, and the prescriber sized loads off it. Empty data read
    as peak capability, in the direction that loads the athlete hardest.

    This test exists to state that consequence, not just the mechanism.
    """
    from app.services.strength_decline_service import AXIS_CEILING

    assert CapacityState().max_strength == AXIS_CEILING  # the trap, still true today

    with pytest.raises(MalformedCurrentEngineState) as exc:
        decode_engine_state(_valid(x={}))
    assert exc.value.error_code == "vector_empty"


def test_the_exact_shape_the_old_versioning_test_accepted():
    """tests/test_state_bridge_versioning.py:40 passes {"x":{},"f":{},"t":{}} as a
    VALID case and the old code accepted it happily — decoding it to a max-strength
    athlete. It is now a typed failure. That test is updated in the slice that retires
    the old path."""
    with pytest.raises(MalformedCurrentEngineState):
        decode_engine_state({"version": 2, "x": {}, "f": {}, "t": {}})


# --------------------------------------------------------------------------- #
# The happy path still works
# --------------------------------------------------------------------------- #

def test_valid_current_payload_decodes():
    state = decode_engine_state(_valid())
    assert isinstance(state, EngineStateV2)
    assert state.version == MAX_READ_VERSION
    assert isinstance(state.capacity, CapacityState)
    assert isinstance(state.fatigue, FatigueState)
    assert isinstance(state.tissue, TissueState)


def test_json_string_form_decodes():
    """asyncpg can hand back JSONB as a string depending on codec registration."""
    assert decode_engine_state(json.dumps(_valid())).version == MAX_READ_VERSION


def test_decode_does_not_mutate_the_caller_payload():
    payload = _valid(version=1)
    del payload["c"]
    decode_engine_state(payload)
    assert payload["version"] == 1        # not restamped in place
    assert "c" not in payload             # not back-filled in place


# --------------------------------------------------------------------------- #
# payload_hash — repair CAS + telemetry depend on it
# --------------------------------------------------------------------------- #

def test_payload_hash_is_stable_and_key_order_independent():
    assert payload_hash({"a": 1, "b": 2}) == payload_hash({"b": 2, "a": 1})
    assert payload_hash(_valid()) == payload_hash(_valid())


def test_payload_hash_distinguishes_different_payloads():
    assert payload_hash(_valid()) != payload_hash(_valid(version=1))


def test_payload_hash_survives_unserializable_input():
    """It must work on exactly the garbage the repair tool exists to inspect."""
    assert payload_hash(object())
    assert payload_hash("{not json")
