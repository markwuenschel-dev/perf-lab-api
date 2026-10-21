"""engine_state schema versioning: writers stamp the current version, rows round-trip strictly.

Version upgrades on read are the codec's job (tests/test_engine_state_codec.py); the old
permissive migrate-on-read helper was deleted with the permissive row converter (INT-15 2D).
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.engine.state_bridge import (
    ENGINE_STATE_SCHEMA_VERSION,
    athlete_state_kwargs_from_unified,
    default_engine_state_dict,
)
from app.engine.state_loading import unified_from_athlete_row_strict
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


def test_round_trip_through_engine_state():
    kwargs = athlete_state_kwargs_from_unified(_state())
    row = SimpleNamespace(**kwargs)
    rebuilt = unified_from_athlete_row_strict(row)
    # Vectors survive the round trip via engine_state (not the legacy fallback).
    assert rebuilt.skill_state == {"squat": 0.7}
    assert rebuilt.habit_strength == 0.6
