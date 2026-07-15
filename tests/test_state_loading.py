"""INT-15 W1-A slices 2A1/2A2 — strict and display-recovery loading.

The load-bearing assertion in this file is `test_empty_vectors_are_not_a_maxed_athlete`:
it pins the defect that motivated the whole slice. Everything else guards the boundary
around it.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.engine.engine_state_codec import (
    MalformedCurrentEngineState,
    MissingEngineState,
    UnsupportedFutureEngineStateVersion,
)
from app.engine.state_bridge import default_engine_state_dict, unified_from_athlete_row
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


def test_valid_current_payload_matches_the_permissive_loader() -> None:
    """2A is additive: on a healthy row, strict must agree with what ships today."""
    row = _row(default_engine_state_dict())

    strict = unified_from_athlete_row_strict(row)
    legacy_path = unified_from_athlete_row(row)

    assert strict.capacity_x == legacy_path.capacity_x
    assert strict.fatigue_f == legacy_path.fatigue_f
    assert strict.tissue_t == legacy_path.tissue_t
    assert strict.c_nm_force == legacy_path.c_nm_force
    assert strict.timestamp == legacy_path.timestamp


def test_empty_vectors_are_not_a_maxed_athlete() -> None:
    """The defect this slice exists to kill.

    `{"x":{},"f":{},"t":{}}` decoded to CapacityState() whose max_strength default is
    100.0 — AXIS_CEILING. A damaged row read as an athlete at MAXIMUM strength, and the
    prescriber sized loads off it. Empty data read as peak capability, in the direction
    that loads the athlete hardest.

    The permissive loader still does this today, which is why it is being retired.
    """
    row = _row({"version": 2, "x": {}, "f": {}, "t": {}})

    with pytest.raises(MalformedCurrentEngineState) as exc:
        unified_from_athlete_row_strict(row)
    assert exc.value.error_code == "vector_empty"

    # The behaviour being replaced, pinned so the contrast is not theoretical:
    assert unified_from_athlete_row(row).capacity_x.max_strength == 100.0


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
