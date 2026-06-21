"""Planning intent + logging wiring (ADR-0030 modality_mix driver, ADR-0031 seed log).

Covers the pure helpers; the DB-bound route/service integration is exercised by the
requires_db route tests.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from app.schemas.workouts import WorkoutLog
from app.services.planning_service import _template_from_modality_mix
from app.services.state_service import (
    _parse_reps,
    _parse_sets,
    _seed_exercises_from_prescription,
)


def _log(**kw) -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime.now(UTC), modality="Strength",
        duration_minutes=60.0, session_rpe=7.0, **kw,
    )


# --- ADR-0031: prescription seeds the log ---

def test_seed_exercises_from_prescription_fills_from_content():
    ps = SimpleNamespace(prescribed_content={"exercises": [
        {"name": "Back Squat", "sets": 4, "reps": "4-6", "load_note": "RPE 8"},
        {"name": "Romanian Deadlift", "sets": 3, "reps": "5-8"},
    ]})
    seeded = _seed_exercises_from_prescription(_log(), ps)
    assert [e.exercise_name for e in seeded.exercises] == ["Back Squat", "Romanian Deadlift"]
    assert seeded.exercises[0].sets == 4.0
    assert seeded.exercises[0].reps == 4.0  # first int of "4-6"


def test_seed_exercises_noop_when_no_content():
    assert _seed_exercises_from_prescription(_log(), SimpleNamespace(prescribed_content=None)).exercises == []


def test_parse_reps_variants():
    assert _parse_reps("4-6") == 4.0
    assert _parse_reps("8-12/side") == 8.0
    assert _parse_reps(10) == 10.0
    assert _parse_reps("AMRAP", default=12.0) == 12.0


def test_parse_sets_variants():
    assert _parse_sets(5) == 5.0
    assert _parse_sets(None) == 3.0
    assert _parse_sets("x") == 3.0


# --- ADR-0030: modality_mix drives template generation ---

def test_template_from_modality_mix_distributes():
    slots = _template_from_modality_mix({"running": 0.5, "strength": 0.3, "conditioning": 0.2}, 5)
    assert slots is not None
    assert len(slots) == 5
    mods = [s.modality for s in slots]
    assert mods.count("Running") >= 2
    assert mods.count("Strength") >= 1
    assert "Conditioning" in mods
    # Days are spread (no two slots collide trivially into one day beyond allocation).
    assert all(1 <= s.day_of_week <= 7 for s in slots)


def test_template_from_modality_mix_empty_returns_none():
    assert _template_from_modality_mix(None, 3) is None
    assert _template_from_modality_mix({}, 3) is None
    assert _template_from_modality_mix({"strength": 0.0}, 3) is None


def test_template_from_modality_mix_aliases_canonicalize():
    slots = _template_from_modality_mix({"CrossFit": 1.0}, 3)  # BlockGoal alias → mixed
    assert slots is not None and len(slots) == 3
    assert all(s.modality == "Mixed" for s in slots)
