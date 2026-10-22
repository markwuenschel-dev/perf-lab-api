"""Strict and display-recovery state loading (INT-15 W1-A, slices 2A1/2A2).

Two row->state conversions with **different policies and different return types**:

``unified_from_athlete_row_strict``
    Canonical state or a typed failure. Never reconstructs from legacy scalars.
    Everything that sizes training, gates safety, or mutates canonical state uses this.

``reconstruct_legacy_state_for_display``
    Returns a provenance-marked ``ReadOnlyStateView``. May recover a legacy row. Only for
    surfaces proven to be display-only.

Why the return types differ
---------------------------
The display path must NOT return a bare ``UnifiedStateVector``. If it did, provenance
would die at the loader boundary: a recovered vector is indistinguishable from canonical
state, and any downstream helper could reuse it for prescription, mutation, or benchmark
processing. ``ReadOnlyStateView`` forces the caller to unwrap ``.state`` and see
``.degraded`` on the way past. It deliberately exposes no prescription or mutation
operations.

Why policy is chosen by function name and not a flag
----------------------------------------------------
A ``mode=`` parameter lets a call site select a more permissive policy by accident.
A function name cannot be selected by accident.

Burden of proof: **strict unless the caller is demonstrably display-only.** A service is
classified by what it can *do*, not by what its module is called — readiness gates
prescription, and ``benchmark_service`` reaches ``create_observation`` which writes.

These are additive. ``state_bridge.unified_from_athlete_row`` is unchanged and still
permissive; it is retired in slice 2D once every caller is classified. See
``docs/superpowers/plans/2026-07-15-int-15-strict-state-loading.md``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, cast

from pydantic import ValidationError

from app.domain.vectors import CapacityConfidence
from app.engine.engine_state_codec import (
    MAX_READ_VERSION,
    EngineStateDecodeError,
    MalformedCurrentEngineState,
    MissingEngineState,
    UnsupportedFutureEngineStateVersion,
    decode_engine_state,
)
from app.engine.state_bridge import (
    build_unified_state_vector,
    capacity_from_legacy,
    fatigue_from_legacy,
    tissue_from_legacy,
)
from app.schemas.state import UnifiedStateVector

CODEC_VERSION = f"v{MAX_READ_VERSION}"

# The legacy capacity scalars a reconstruction genuinely needs. The f_* mirrors carry
# column defaults (0.0) and so are always readable; these four are nullable=False on the
# model (app/models/athlete_state.py:32-37) and have no meaningful default — without them
# there is nothing to reconstruct FROM.
_REQUIRED_LEGACY_SCALARS = ("c_met_aerobic", "c_nm_force", "c_struct", "b_met_anaerobic")

_LEGACY_FATIGUE_SCALARS = (
    "f_met_systemic",
    "f_nm_peripheral",
    "f_nm_central",
    "f_struct_damage",
)


class DisplayStateUnavailable(Exception):
    """No view can honestly be produced for a display surface.

    Distinct from "degraded": a degraded view is real data with a caveat, this is the
    absence of anything showable. Raised when legacy reconstruction is impossible
    (incomplete/non-finite scalars) or forbidden (future-version payload).
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ReadOnlyStateView:
    """A state vector carrying its own provenance.

    ``source="legacy_recovery"`` means the vector was reconstructed from the lossy legacy
    scalar projection — it is NOT an originally observed vector state, and must never be
    promoted into a decision or written back.
    """

    state: UnifiedStateVector
    source: Literal["canonical", "legacy_recovery"]
    degraded: bool
    degradation_reason: str | None
    codec_version: str


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _legacy_scalars_usable(row: Any) -> bool:
    """Can this row's legacy scalars support a reconstruction at all?

    Non-finite is treated as unusable, not as a number: a NaN capacity propagates silently
    through every downstream comparison (``nan > x`` is False), which is exactly the class
    of quiet wrongness this workstream exists to remove.
    """
    return all(_finite(getattr(row, name, None)) for name in _REQUIRED_LEGACY_SCALARS)


# The error_code for a row whose payload decodes but whose own columns cannot form a state.
INVALID_ROW_FIELDS = "invalid_row_fields"


def _passthrough_kwargs(row: Any) -> dict[str, Any]:
    """Row fields that live outside the engine_state payload.

    Raises:
        MalformedCurrentEngineState: a field of the wrong kind (``INVALID_ROW_FIELDS``).
    """
    scalars = {name: getattr(row, name, None) for name in ("s_struct_signal", "habit_strength")}
    skill_state = getattr(row, "skill_state", None)
    bad = [name for name, v in scalars.items() if v is not None and not _finite(v)]
    if skill_state is not None and not isinstance(skill_state, dict):
        bad.append("skill_state")
    if bad:
        raise MalformedCurrentEngineState(INVALID_ROW_FIELDS, ",".join(bad))
    skills: dict[str, Any] = (
        dict(cast("dict[str, Any]", skill_state)) if isinstance(skill_state, dict) else {}
    )
    return {
        **{name: float(v or 0.0) for name, v in scalars.items()},
        "skill_state": skills,
    }


def _state_from_row(
    row: Any,
    *,
    x: Any,
    f: Any,
    t: Any,
    capacity_confidence: CapacityConfidence | None = None,
) -> UnifiedStateVector:
    """Assemble the vector from decoded (or reconstructed) x/f/t plus the row's own fields.

    A row's columns are data too: out-of-range or wrongly typed values are a damaged row,
    reported as a decode error so that every loading policy handles them like a damaged
    payload. Only validation failures are converted; anything else is a bug and propagates.

    Raises:
        MalformedCurrentEngineState: ``INVALID_ROW_FIELDS``.
    """
    extra = {} if capacity_confidence is None else {"capacity_confidence": capacity_confidence}
    try:
        return build_unified_state_vector(
            timestamp=row.timestamp, x=x, f=f, t=t, **extra, **_passthrough_kwargs(row)
        )
    except ValidationError as exc:
        fields = ",".join(str(e["loc"][0]) for e in exc.errors() if e.get("loc"))
        raise MalformedCurrentEngineState(INVALID_ROW_FIELDS, fields) from exc


def unified_from_athlete_row_strict(row: Any) -> UnifiedStateVector:
    """Canonical state, or raise.

    Raises:
        MissingEngineState: the row carries no payload (a legacy row).
        MalformedCurrentEngineState: a payload this reader should understand, but cannot,
            or row fields that cannot form a state (``INVALID_ROW_FIELDS``).
        UnsupportedFutureEngineStateVersion: written by a newer node; deploy readers first.

    Never falls back to the legacy scalars. A caller that cannot proceed without state
    must fail closed — that is the point.
    """
    decoded = decode_engine_state(getattr(row, "engine_state", None))
    return _state_from_row(
        row,
        x=decoded.capacity,
        f=decoded.fatigue,
        t=decoded.tissue,
        capacity_confidence=decoded.capacity_confidence,
    )


def reconstruct_legacy_state_for_display(row: Any) -> ReadOnlyStateView:
    """A provenance-marked view for display-only surfaces.

    Canonical payload            -> source="canonical", degraded=False
    Missing payload (legacy row) -> source="legacy_recovery", degraded=True
    Malformed payload            -> source="legacy_recovery", degraded=True (if scalars usable)
    Future-version payload       -> DisplayStateUnavailable (never reconstructed)
    Unusable legacy scalars      -> DisplayStateUnavailable
    Invalid row fields           -> DisplayStateUnavailable (habit, signal, skills: there is
                                    no lossy mirror to recover them from)

    A future-version payload is not damaged data — it is data this reader is too old to
    read. Reconstructing it from the lossy mirror would show the athlete a worse answer
    than the one already persisted, and would hide a deployment-ordering fault behind a
    plausible chart.
    """
    try:
        decoded = decode_engine_state(getattr(row, "engine_state", None))
    except UnsupportedFutureEngineStateVersion as exc:
        raise DisplayStateUnavailable("unsupported_future_version") from exc
    except (MissingEngineState, MalformedCurrentEngineState) as exc:
        reason = (
            "null_engine_state_legacy_row"
            if isinstance(exc, MissingEngineState)
            else getattr(exc, "error_code", "malformed_current")
        )
        return _legacy_recovery_view(row, reason)
    except EngineStateDecodeError as exc:  # defensive: a new subclass must not silently recover
        raise DisplayStateUnavailable(f"undecodable:{type(exc).__name__}") from exc

    try:
        state = _state_from_row(
            row,
            x=decoded.capacity,
            f=decoded.fatigue,
            t=decoded.tissue,
            capacity_confidence=decoded.capacity_confidence,
        )
    except MalformedCurrentEngineState as exc:
        raise DisplayStateUnavailable(exc.error_code) from exc

    return ReadOnlyStateView(
        state=state,
        source="canonical",
        degraded=False,
        degradation_reason=None,
        codec_version=CODEC_VERSION,
    )


def _legacy_recovery_view(row: Any, reason: str) -> ReadOnlyStateView:
    """Rebuild from the legacy scalar projection. Read-only, never written back.

    This is the branch lifted out of ``state_bridge.unified_from_athlete_row``. It lives
    here, behind an explicitly named function, so that no apparently neutral helper can
    reach it — recovery must be *selected*, never inherited.
    """
    if not _legacy_scalars_usable(row):
        raise DisplayStateUnavailable(f"{reason}+unusable_legacy_scalars")

    if not all(_finite(getattr(row, name, None)) for name in _LEGACY_FATIGUE_SCALARS):
        raise DisplayStateUnavailable(f"{reason}+unusable_legacy_fatigue")

    x = capacity_from_legacy(
        row.c_met_aerobic,
        row.c_nm_force,
        row.c_struct,
        row.b_met_anaerobic,
    )
    f = fatigue_from_legacy(
        row.f_met_systemic,
        row.f_nm_peripheral,
        row.f_nm_central,
        row.f_struct_damage,
    )
    t = tissue_from_legacy(row.f_struct_damage)

    try:
        # A reconstructed row has no observed confidence — weak prior, never the decoded
        # value, because there is no decoded value.
        state = _state_from_row(row, x=x, f=f, t=t)
    except MalformedCurrentEngineState as exc:
        raise DisplayStateUnavailable(f"{reason}+{exc.error_code}") from exc

    return ReadOnlyStateView(
        state=state,
        source="legacy_recovery",
        degraded=True,
        degradation_reason=reason,
        codec_version=CODEC_VERSION,
    )
