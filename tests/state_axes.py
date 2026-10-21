"""Build test states by the decomposed axes, addressed by their legacy names.

INT-05 C4: decision code reads ``fatigue_f`` / ``capacity_x``, never the legacy scalar mirror.
A test that set only ``f_met_systemic=85`` built a state no engine path can produce (the
mirror disagreeing with the vector it mirrors) and now proves nothing. These helpers set the
axis itself and re-derive the mirror, so every test state is one the engine could hold.
"""
from __future__ import annotations

from typing import Any

from app.engine.state_bridge import sync_legacy_from_vectors
from app.schemas.state import UnifiedStateVector

#: The exact, invertible part of the mirror (state_bridge.sync_legacy_from_vectors).
LEGACY_AXIS: dict[str, tuple[str, str]] = {
    "f_met_systemic": ("fatigue_f", "metabolic"),
    "f_nm_peripheral": ("fatigue_f", "muscular"),
    "f_nm_central": ("fatigue_f", "cns"),
    "c_met_aerobic": ("capacity_x", "aerobic"),
}


def with_axes(state: UnifiedStateVector, **legacy_named: float) -> UnifiedStateVector:
    """A copy of ``state`` with each named axis set, and the whole mirror re-derived."""
    s = state.model_copy(deep=True)
    for name, value in legacy_named.items():
        vector, key = LEGACY_AXIS[name]
        setattr(getattr(s, vector), key, float(value))
    for name, value in sync_legacy_from_vectors(s.capacity_x, s.fatigue_f, s.tissue_t).items():
        setattr(s, name, value)
    return s


def split_legacy(over: dict[str, Any]) -> tuple[dict[str, float], dict[str, Any]]:
    """Separate legacy-named axis overrides from every other field."""
    axes = {k: float(v) for k, v in over.items() if k in LEGACY_AXIS}
    rest = {k: v for k, v in over.items() if k not in LEGACY_AXIS}
    return axes, rest
