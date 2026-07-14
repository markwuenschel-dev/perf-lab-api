"""Strict decoding of the ``engine_state`` JSONB payload (INT-15a).

This module has exactly one job: turn a persisted payload into one valid supported
state, or raise. It is **policy-free**. It does not consult legacy scalar columns, does
not write back, does not skip errors, does not log raw payloads, and does not decide
whether the caller may continue. Those are execution decisions, and they differ per
surface — a history screen may show degraded data that a prescription must refuse.

Callers do not choose a policy by passing a flag. They call the adapter named for the
operation they are performing (``load_state_for_decision``, ``load_state_for_read_only``,
``try_load_state_for_shadow``, ``read_raw_state_for_repair``). A ``mode=`` parameter here
would let a call site select a more permissive policy by accident; a function name
cannot be selected by accident.

Three defects this replaces (all in ``state_bridge._migrate_engine_state``):

1. **The version stamp was decorative.** ``_migrate_engine_state`` never read
   ``eng["version"]``. It sniffed for x/f/t and unconditionally restamped to 2, while its
   own docstring promised ``if version < 2: upgrade``. So a v3 payload written by a newer
   node was restamped to 2 and its v3-only fields dropped on the next write — a silent
   destructive downgrade during a rolling deploy or rollback. Latent only because no v3
   exists yet; it would have fired on the first version bump.
2. **Malformed payloads degraded silently**, falling back to a lossy legacy-scalar
   reconstruction with no log and no metric.
3. **Structurally empty vectors validated clean.** ``{"x": {}}`` produced a full
   default-valued capacity vector, because every field has a default and no schema sets
   ``extra=``. An empty vector is missing data, not a healthy athlete at defaults.

A future-version payload is **not** malformed. It is valid data this reader is too old to
understand — a reader-capability problem, not a data-quality one. The two are distinct
outcomes everywhere, because their operational responses differ: malformed means repair
the row; future-version means deploy readers first.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

from app.schemas.engine_vectors import (
    CapacityConfidence,
    CapacityState,
    FatigueState,
    TissueState,
)

# The newest payload version this reader understands. Bump ONLY together with an upgrade
# branch in `_upgrade_to_current`, and deploy readers before enabling writers of the new
# version — see `UnsupportedFutureEngineStateVersion`.
MAX_READ_VERSION = 2

# Payloads written before the stamp existed are v1 by definition.
_ASSUMED_UNVERSIONED = 1

_VECTOR_KEYS = ("x", "f", "t")


class EngineStateDecodeError(Exception):
    """Base for every decode failure. Callers should catch the specific subclass —
    the distinction between them is the whole point."""


class MissingEngineState(EngineStateDecodeError):
    """No payload at all (NULL column). A legitimate state for a legacy row."""


class MalformedCurrentEngineState(EngineStateDecodeError):
    """A payload this reader should understand, but cannot. The row is damaged.

    Operational response: repair the row (see ``engine_state_repair``).
    """

    def __init__(self, error_code: str, detail: str = "") -> None:
        self.error_code = error_code
        super().__init__(f"{error_code}: {detail}" if detail else error_code)


class UnsupportedFutureEngineStateVersion(EngineStateDecodeError):
    """A payload from a newer writer. The DATA is fine; this reader is too old.

    Never fall back, reconstruct, restamp, downgrade, or write back such a payload —
    an older node doing so destroys fields it cannot see. Operational response: deploy
    readers that understand the new version before enabling its writers.
    """

    def __init__(self, declared_version: int, max_supported: int = MAX_READ_VERSION) -> None:
        self.declared_version = declared_version
        self.max_supported = max_supported
        super().__init__(
            f"engine_state declares version {declared_version}; this reader supports "
            f"at most {max_supported}"
        )


@dataclass(frozen=True)
class EngineStateV2:
    """A fully validated engine state at the current schema version."""

    version: int
    capacity: CapacityState
    fatigue: FatigueState
    tissue: TissueState
    capacity_confidence: CapacityConfidence


def payload_hash(payload: object) -> str:
    """Stable identifier for a payload, for telemetry and repair compare-and-swap.

    Hashing rather than logging the payload itself: raw JSONB is athlete data and must
    never reach the logs. Sorted keys so the same content always hashes the same.
    """
    try:
        canonical = json.dumps(payload, sort_keys=True, default=str)
    except (TypeError, ValueError):
        canonical = repr(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _as_dict(payload: object) -> dict[str, Any] | None:
    """Coerce a stored payload to a dict, tolerating the JSON-string form."""
    if isinstance(payload, dict):
        return cast("dict[str, Any]", payload)
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else None
    return None


def inspect_declared_version(payload: object) -> int | None:
    """The version the payload claims, without validating anything else.

    Returns None when the payload is unreadable or declares no usable version. Used by
    telemetry and by the repair tool, which must reason about payloads that cannot be
    decoded at all.
    """
    raw = _as_dict(payload)
    if raw is None:
        return None
    declared = raw.get("version")
    if isinstance(declared, bool) or not isinstance(declared, int):
        return None
    return declared


def _upgrade_to_current(raw: dict[str, Any], declared: int) -> dict[str, Any]:
    """Migrate an older payload forward, keyed on the DECLARED version.

    Keyed on the version, not on structural sniffing. The old code decided the v1->v2
    upgrade by ``"c" not in payload``, which meant a v1 row carrying a stray ``c`` key
    of the wrong shape skipped the upgrade and went straight to validation, and the
    version stamp could be any value at all with no effect on behaviour.
    """
    upgraded = dict(raw)
    if declared < 2:
        # v1 -> v2: seed a weak-prior capacity confidence (ADR-0036).
        upgraded.setdefault("c", CapacityConfidence().model_dump())
    upgraded["version"] = MAX_READ_VERSION
    return upgraded


def _require_populated_vector(raw: dict[str, Any], key: str) -> dict[str, Any]:
    """A vector must be present AND non-empty.

    ``{"x": {}}`` used to validate into a full default-valued vector, because every
    field has a default and no schema sets ``extra=``. That turns missing data into a
    confident-looking healthy athlete. An empty vector is a damaged row.
    """
    value = raw.get(key)
    if not isinstance(value, dict):
        raise MalformedCurrentEngineState(
            "vector_not_an_object", f"{key!r} is {type(value).__name__}, expected object"
        )
    if not value:
        raise MalformedCurrentEngineState("vector_empty", f"{key!r} is an empty object")
    return cast("dict[str, Any]", value)


def decode_engine_state(payload: object) -> EngineStateV2:
    """Return one valid supported state, or raise.

    Raises:
        MissingEngineState: the payload is NULL/absent.
        UnsupportedFutureEngineStateVersion: declares a version newer than this reader.
        MalformedCurrentEngineState: should be readable, is not.
    """
    if payload is None:
        raise MissingEngineState("engine_state is NULL")

    raw = _as_dict(payload)
    if raw is None:
        raise MalformedCurrentEngineState(
            "unparseable_payload", "not a JSON object or object-encoding string"
        )

    # Version FIRST. A future payload must never be structurally sniffed, partially
    # parsed, or coerced — we cannot know what its fields mean.
    declared = inspect_declared_version(payload)
    if declared is not None and declared > MAX_READ_VERSION:
        raise UnsupportedFutureEngineStateVersion(declared)
    effective = _ASSUMED_UNVERSIONED if declared is None else declared

    missing = [k for k in _VECTOR_KEYS if k not in raw]
    if missing:
        raise MalformedCurrentEngineState(
            "missing_vectors", f"absent: {', '.join(sorted(missing))}"
        )

    migrated = _upgrade_to_current(raw, effective)

    try:
        capacity = CapacityState.model_validate(_require_populated_vector(migrated, "x"))
        fatigue = FatigueState.model_validate(_require_populated_vector(migrated, "f"))
        tissue = TissueState.model_validate(_require_populated_vector(migrated, "t"))
        confidence = CapacityConfidence.model_validate(
            migrated.get("c") or CapacityConfidence().model_dump()
        )
    except MalformedCurrentEngineState:
        raise
    except Exception as exc:  # pydantic ValidationError and friends
        # Normalized code only — never let payload contents reach a caller that logs.
        raise MalformedCurrentEngineState(
            "vector_validation_failed", type(exc).__name__
        ) from exc

    return EngineStateV2(
        version=MAX_READ_VERSION,
        capacity=capacity,
        fatigue=fatigue,
        tissue=tissue,
        capacity_confidence=confidence,
    )
