"""INT-15 W1-A slices 2A1/2A2 — strict and display-recovery loading.

The load-bearing assertion in this file is `test_empty_vectors_are_not_a_maxed_athlete`:
it pins the defect that motivated the whole slice. Everything else guards the boundary
around it.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.engine import state_loading
from app.engine.engine_state_codec import (
    MalformedCurrentEngineState,
    MissingEngineState,
    UnsupportedFutureEngineStateVersion,
)
from app.engine.state_bridge import default_engine_state_dict
from app.engine.state_loading import (
    DisplayStateUnavailable,
    ReadOnlyStateView,
    reconstruct_legacy_state_for_display,
    unified_from_athlete_row_strict,
)

TS = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _row(engine_state: object, **overrides: object) -> SimpleNamespace:
    """An AthleteState-shaped row with healthy legacy scalars unless overridden."""
    fields: dict[str, object] = {
        "timestamp": TS,
        "engine_state": engine_state,
        "c_met_aerobic": 50.0,
        "c_nm_force": 1200.0,
        "c_struct": 60.0,
        "b_met_anaerobic": 9000.0,
        "f_met_systemic": 10.0,
        "f_nm_peripheral": 12.0,
        "f_nm_central": 8.0,
        "f_struct_damage": 15.0,
        "s_struct_signal": 0.0,
        "habit_strength": 0.5,
        "skill_state": {},
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


# --------------------------------------------------------------------------------------
# strict loader
# --------------------------------------------------------------------------------------


def test_empty_vectors_are_not_a_maxed_athlete() -> None:
    """The defect this slice exists to kill.

    `{"x":{},"f":{},"t":{}}` decoded to CapacityState() whose max_strength default is
    100.0 — AXIS_CEILING. A damaged row read as an athlete at MAXIMUM strength, and the
    prescriber sized loads off it. Empty data read as peak capability, in the direction
    that loads the athlete hardest.

    The permissive loader did this; INT-15 2D deleted it.
    """
    row = _row({"version": 2, "x": {}, "f": {}, "t": {}})

    with pytest.raises(MalformedCurrentEngineState) as exc:
        unified_from_athlete_row_strict(row)
    assert exc.value.error_code == "vector_empty"



def test_partial_vectors_fail_closed() -> None:
    row = _row({"version": 2, "x": {"aerobic": 50.0}, "f": {}})

    with pytest.raises(MalformedCurrentEngineState) as exc:
        unified_from_athlete_row_strict(row)
    assert exc.value.error_code == "missing_vectors"


def test_malformed_current_payload_fails_closed() -> None:
    row = _row({"version": 2, "x": "not-an-object", "f": {}, "t": {}})

    with pytest.raises(MalformedCurrentEngineState) as exc:
        unified_from_athlete_row_strict(row)
    assert exc.value.error_code == "vector_not_an_object"


def test_future_version_is_a_distinct_failure_from_malformed() -> None:
    """Not the same outcome, because the operational responses differ: repair the row
    vs. deploy readers before writers."""
    payload = default_engine_state_dict() | {"version": 99}
    row = _row(payload)

    with pytest.raises(UnsupportedFutureEngineStateVersion) as exc:
        unified_from_athlete_row_strict(row)
    assert exc.value.declared_version == 99
    assert not isinstance(exc.value, MalformedCurrentEngineState)


def test_null_payload_raises_missing_not_malformed() -> None:
    with pytest.raises(MissingEngineState):
        unified_from_athlete_row_strict(_row(None))


def test_strict_never_reconstructs_from_legacy_scalars() -> None:
    """Healthy legacy scalars must not rescue a damaged canonical payload."""
    row = _row({"version": 2, "x": {}, "f": {}, "t": {}}, c_nm_force=2000.0)

    with pytest.raises(MalformedCurrentEngineState):
        unified_from_athlete_row_strict(row)


# --------------------------------------------------------------------------------------
# display loader
# --------------------------------------------------------------------------------------


def test_canonical_payload_is_marked_canonical_not_degraded() -> None:
    view = reconstruct_legacy_state_for_display(_row(default_engine_state_dict()))

    assert isinstance(view, ReadOnlyStateView)
    assert view.source == "canonical"
    assert view.degraded is False
    assert view.degradation_reason is None


def test_malformed_payload_with_usable_scalars_degrades_with_provenance() -> None:
    view = reconstruct_legacy_state_for_display(_row({"version": 2, "x": {}, "f": {}, "t": {}}))

    assert view.source == "legacy_recovery"
    assert view.degraded is True
    assert view.degradation_reason == "vector_empty"


def test_null_payload_recovers_as_a_legacy_row() -> None:
    """A NULL engine_state is a legacy-migration row, not corruption."""
    view = reconstruct_legacy_state_for_display(_row(None))

    assert view.source == "legacy_recovery"
    assert view.degradation_reason == "null_engine_state_legacy_row"


def test_future_version_is_never_reconstructed_for_display() -> None:
    """The data is fine and already persisted; the reader is too old. Reconstructing from
    the lossy mirror would show a WORSE answer than the truth and hide a deployment fault
    behind a plausible chart."""
    payload = default_engine_state_dict() | {"version": 99}

    with pytest.raises(DisplayStateUnavailable) as exc:
        reconstruct_legacy_state_for_display(_row(payload))
    assert exc.value.reason == "unsupported_future_version"


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_nonfinite_legacy_scalars_are_unusable_not_a_number(bad: float) -> None:
    """NaN propagates silently through every downstream comparison (`nan > x` is False).
    Treating it as a value is the quiet wrongness this workstream removes."""
    with pytest.raises(DisplayStateUnavailable) as exc:
        reconstruct_legacy_state_for_display(_row(None, c_nm_force=bad))
    assert "unusable_legacy_scalars" in exc.value.reason


def test_missing_legacy_scalars_yield_unavailable_not_defaults() -> None:
    with pytest.raises(DisplayStateUnavailable):
        reconstruct_legacy_state_for_display(_row(None, c_met_aerobic=None))


def test_display_recovery_does_not_mutate_the_row() -> None:
    """Recovery is read-only: no writeback, ever. A recovered vector is not observed data
    and must never be persisted as though it were."""
    row = _row({"version": 2, "x": {}, "f": {}, "t": {}})
    before = dict(vars(row))

    reconstruct_legacy_state_for_display(row)

    assert vars(row) == before
    assert row.engine_state == {"version": 2, "x": {}, "f": {}, "t": {}}


def test_display_view_exposes_no_mutation_surface() -> None:
    """ReadOnlyStateView is frozen and carries no prescription/mutation operations."""
    view = reconstruct_legacy_state_for_display(_row(default_engine_state_dict()))

    with pytest.raises(FrozenInstanceError):
        view.source = "canonical"  # type: ignore[misc]

    public = {n for n in dir(view) if not n.startswith("_")}
    assert public == {"state", "source", "degraded", "degradation_reason", "codec_version"}


def test_only_validation_failures_become_damaged_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review P1: damaged row columns are a typed decode error, but a programming error inside
    the conversion is not data damage and must still raise as itself."""
    row = SimpleNamespace(
        id=1, timestamp=datetime(2026, 7, 1, 12, 0), engine_state=default_engine_state_dict(),
        s_struct_signal=0.0, habit_strength=2.0, skill_state={},
    )
    with pytest.raises(MalformedCurrentEngineState) as damaged:
        state_loading.unified_from_athlete_row_strict(row)
    assert damaged.value.error_code == state_loading.INVALID_ROW_FIELDS

    def _bug(**_k: Any) -> Any:
        raise TypeError("a bug, not data")

    monkeypatch.setattr(state_loading, "build_unified_state_vector", _bug)
    row.habit_strength = 0.5
    with pytest.raises(TypeError):
        state_loading.unified_from_athlete_row_strict(row)
    with pytest.raises(TypeError):
        state_loading.reconstruct_legacy_state_for_display(row)


def _healthy_row(**fields: Any) -> SimpleNamespace:
    return SimpleNamespace(**{
        "id": 1, "timestamp": datetime(2026, 7, 1, 12, 0),
        "engine_state": default_engine_state_dict(),
        "c_met_aerobic": 300.0, "c_nm_force": 1400.0, "c_struct": 100.0,
        "b_met_anaerobic": 15000.0, "f_met_systemic": 5.0, "f_nm_peripheral": 5.0,
        "f_nm_central": 5.0, "f_struct_damage": 5.0,
        "s_struct_signal": 0.0, "habit_strength": 0.5, "skill_state": {}, **fields,
    })


def _infinite_payload() -> dict[str, Any]:
    payload = default_engine_state_dict()
    payload["x"]["aerobic"] = "Infinity"  # a JSON string the vector validator coerces to inf
    return payload


@pytest.mark.parametrize(
    "fields",
    [{"skill_state": {"squat": "NaN"}}, {"skill_state": {"squat": "Infinity"}},
     {"skill_state": {"squat": "-Infinity"}}, {"engine_state": _infinite_payload()}],
    ids=["skill-nan", "skill-inf", "skill-neg-inf", "payload-inf"],
)
def test_a_non_finite_value_anywhere_is_damaged_state(fields: dict[str, Any]) -> None:
    """Review round 2: these pass validation as floats, then break JSON serialization."""
    row = _healthy_row(**fields)
    with pytest.raises(MalformedCurrentEngineState) as damaged:
        unified_from_athlete_row_strict(row)
    assert damaged.value.error_code == state_loading.NON_FINITE_VALUES
    with pytest.raises(DisplayStateUnavailable):
        reconstruct_legacy_state_for_display(row)


@pytest.mark.parametrize(
    "legacy",
    [{"c_met_aerobic": -1.0}, {"f_met_systemic": -1.0}, {"f_struct_damage": -1.0}],
    ids=["negative-capacity", "negative-fatigue", "negative-tissue-damage"],
)
@pytest.mark.parametrize(
    "payload", [None, {"version": 2, "x": "not-an-object", "f": {}, "t": {}}],
    ids=["missing-payload", "malformed-payload"],
)
def test_invalid_legacy_values_are_unavailable_for_display(
    legacy: dict[str, Any], payload: Any
) -> None:
    """Finite, out-of-range legacy values fail inside the vector constructors, before the
    row's own conversion: still typed, never a raw ValidationError (a 500 on history)."""
    with pytest.raises(DisplayStateUnavailable) as unavailable:
        reconstruct_legacy_state_for_display(_healthy_row(engine_state=payload, **legacy))
    assert unavailable.value.reason.endswith("+invalid_legacy_values")


def test_a_programming_error_in_legacy_reconstruction_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _bug(*_a: Any, **_k: Any) -> Any:
        raise TypeError("a bug, not data")

    monkeypatch.setattr(state_loading, "capacity_from_legacy", _bug)
    with pytest.raises(TypeError):
        reconstruct_legacy_state_for_display(_healthy_row(engine_state=None))
