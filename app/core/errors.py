"""Application-level failures that carry product policy (INT-15 W1-A, slice 2B).

The seam this module exists to hold
-----------------------------------
A strict codec failure and a product refusal are **two different facts**::

    engine_state_codec        "this payload does not decode"        persistence
            │
            ▼   translated HERE, by the service that knows its own authority
    CanonicalStateInvalid     "prescription must be refused"        product policy
            │
            ▼
    global HTTP handler       409 + capability-specific body        transport

The codec knows only the first. It cannot know whether its caller is prescription,
readiness, benchmark mutation, display recovery, a shadow computation, or an offline
integrity job — and those must not share an outcome.

Why the raw codec exception is NOT mapped to 409 directly
---------------------------------------------------------
Mapping every escaped ``EngineStateDecodeError`` straight to an expected 409 would make
*forgotten translations look successful*, concealing:

* an accidental strict-codec call from the wrong surface,
* a missing display adapter,
* a defect in a new route,
* a non-HTTP job whose failure should terminate, not become "capability unavailable".

That is policy at the wrong boundary — the same mistake this workstream exists to undo. An
untranslated ``EngineStateDecodeError`` reaching the transport is an internal defect and
stays an opaque 500 (see ``app/main.py``). The translation must be written where authority
is known.

Why not per-route mapping
-------------------------
Translation happens once, in the service that knows its capability — not repeated in
`prescribe.py`, `planning.py`, and every later route, where one omission is exactly the
accidental 500 the W1-A decision forbids. HTTP policy then stays centralized, so multiple
routes reaching the same capability cannot disagree.
"""

from __future__ import annotations

from typing import Literal

from app.engine.engine_state_codec import (
    INCOMPLETE_ERROR_CODES,
    EngineStateDecodeError,
    MalformedCurrentEngineState,
    MissingEngineState,
    UnsupportedFutureEngineStateVersion,
)

# An authority-bearing capability. Named by what it *does*, not by the module it lives in:
# readiness gates prescription, and benchmark reaches canonical mutation.
Capability = Literal["prescription", "readiness", "benchmark", "onboarding", "assessment"]

# The external body's per-capability availability field. Only the error code and the 409
# status are shared — forcing `prescription_available` onto a readiness endpoint would be
# a lie dressed as consistency.
_AVAILABILITY_FIELD: dict[str, str] = {
    "prescription": "prescription_available",
    "readiness": "readiness_available",
    "benchmark": "benchmark_update_applied",
    "onboarding": "onboarding_state_available",
    "assessment": "assessment_available",
}

ERROR_CODE = "canonical_state_invalid"


def availability_field(capability: str) -> str:
    """The capability's `*_available`-style field name for the refusal body."""
    return _AVAILABILITY_FIELD.get(capability, "capability_available")


def normalize_decode_error(exc: EngineStateDecodeError) -> str:
    """The internal reason taxonomy — precision preserved for observability and repair.

    Externally these collapse to one code; internally they must not. Future-version is
    distinct from malformed because the operational response differs: "deploy readers",
    not "repair the row".
    """
    if isinstance(exc, MissingEngineState):
        return "canonical_state_missing"
    if isinstance(exc, UnsupportedFutureEngineStateVersion):
        return "canonical_state_version_unsupported"
    if isinstance(exc, MalformedCurrentEngineState) and exc.error_code in INCOMPLETE_ERROR_CODES:
        return "canonical_state_incomplete"
    return "canonical_state_malformed"


class CanonicalStateInvalid(Exception):
    """An authority-bearing capability refused: canonical state cannot be trusted.

    A deliberate product decision, not a decode failure. Raised by the service that knows
    its own authority, never by the codec or a loader.

    Carries ``capability`` (what was refused) and ``normalized_reason`` (why, in the
    internal taxonomy — never raw payload content, which is athlete data).
    """

    def __init__(self, capability: Capability, normalized_reason: str) -> None:
        self.capability = capability
        self.normalized_reason = normalized_reason
        super().__init__(f"{capability} refused: {normalized_reason}")

    def to_response_body(self) -> dict[str, object]:
        """The external contract. Internal reason precision is deliberately dropped here —
        it goes to logs and metrics, not to the client."""
        return {
            "error": ERROR_CODE,
            availability_field(self.capability): False,
            # The athlete cannot fix this by retrying or by changing their request; an
            # operator or repair workflow must. Stated explicitly so a client does not
            # build a retry loop around a permanent condition.
            "resolution_available_in_product": False,
        }
