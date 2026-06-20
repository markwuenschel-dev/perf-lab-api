"""engine_state schema versioning + lazy migrate-on-read (§1.2)."""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.engine.state_bridge import (
    ENGINE_STATE_SCHEMA_VERSION,
    _migrate_engine_state,
    athlete_state_kwargs_from_unified,
    default_engine_state_dict,
    unified_from_athlete_row,
)
from app.schemas.state import UnifiedStateVector


def _state() -> UnifiedStateVector:
    return UnifiedStateVector(
        timestamp=datetime.now(UTC),
        c_met_aerobic=300.0,
        c_nm_force=500.0,
        c_struct=50.0,
        b_met_anaerobic=50.0,
        f_met_systemic=20.0,
        f_nm_peripheral=15.0,
        f_nm_central=20.0,
        f_struct_damage=10.0,
        s_struct_signal=20.0,
        habit_strength=0.6,
        skill_state={"squat": 0.7},
    )


def test_default_and_written_payload_carry_current_version():
    assert default_engine_state_dict()["version"] == ENGINE_STATE_SCHEMA_VERSION
    kwargs = athlete_state_kwargs_from_unified(_state())
    assert kwargs["engine_state"]["version"] == ENGINE_STATE_SCHEMA_VERSION


def test_migrate_stamps_unversioned_payload():
    legacy = {"x": {}, "f": {}, "t": {}}  # written before the version stamp existed
    migrated = _migrate_engine_state(legacy)
    assert migrated is not None
    assert migrated["version"] == ENGINE_STATE_SCHEMA_VERSION


def test_migrate_rejects_payload_without_vectors():
    assert _migrate_engine_state({"version": 1}) is None


def test_round_trip_through_engine_state():
    kwargs = athlete_state_kwargs_from_unified(_state())
    row = SimpleNamespace(**kwargs)
    rebuilt = unified_from_athlete_row(row)
    # Vectors survive the round trip via engine_state (not the legacy fallback).
    assert rebuilt.skill_state == {"squat": 0.7}
    assert rebuilt.habit_strength == 0.6
