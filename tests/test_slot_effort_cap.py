"""An authored per-slot effort ceiling (phase 7.3).

``ExerciseSlot.rpe_cap`` is a MAXIMUM, not a target:

    effective cap = min(slot cap, envelope cap after uncertainty conservatism)

so a slot can pull a lift below the week's envelope and never push it above (ADR-0029). The
authored note that states it survives load sizing instead of being overwritten by it.
"""
from __future__ import annotations

from typing import Any

import pytest
from test_effort_resolution_seam import AS_OF, E1RM, _athlete

from app.logic import strength_calibration as sc
from app.logic.candidate_library import GOAL_TEMPLATE_LIBRARY
from app.logic.exercise_slot import CatalogExercise, ExerciseSlot
from app.logic.planning import periodization_envelope
from app.logic.prescriber import (
    DEFAULT_LOAD_NOTE,
    _select_exercises,  # pyright: ignore[reportPrivateUsage]
)
from app.schemas.prescription import ExercisePrescription, WorkoutPrescription
from app.services.prescription_service import (
    _enrich_exercises_with_load,  # pyright: ignore[reportPrivateUsage]
)

pytestmark = pytest.mark.asyncio

_NOTE = "About RPE 7: moderate load, a few reps in reserve."
_WORKING = {"week_number": 5, "duration_weeks": 8, "deload_every_n_weeks": 4}  # 7.5-8.5
_DELOAD = {"week_number": 4, "duration_weeks": 8, "deload_every_n_weeks": 4}   # 5.0-6.5


def _rx(rpe_cap: float | None, note: str | None) -> WorkoutPrescription:
    return WorkoutPrescription(
        type="Strength Endurance", focus="f", rationale="r", duration_min=45,
        exercises=[ExercisePrescription(
            name="Back Squat", sets=5, reps="8", rpe_cap=rpe_cap, load_note=note,
        )],
    )


async def _sized(db: Any, email: str, block: dict[str, Any], rpe_cap: float | None,
                 note: str | None) -> ExercisePrescription:
    user = await _athlete(db, email)
    rx = _rx(rpe_cap, note)
    await _enrich_exercises_with_load(db, user.id, rx, block, as_of=AS_OF)  # type: ignore[union-attr]
    return rx.exercises[0]


async def test_a_slot_ceiling_below_the_envelope_caps_the_load(async_db) -> None:
    envelope = periodization_envelope(8, 5, 4).rpe_high
    assert envelope > 7.0
    ex = await _sized(async_db, "cap-below@test.com", _WORKING, 7.0, _NOTE)

    assert ex.rpe_cap == 7.0
    assert ex.prescribed_load_kg == sc.suggested_load_kg(E1RM, 8, 7.0)
    assert "cap RPE 7" in (ex.load_note or "")
    assert _NOTE in (ex.load_note or ""), "the authored instruction is kept beside the load"


async def test_the_envelope_below_a_slot_ceiling_still_wins(async_db) -> None:
    """A ceiling never raises effort: in a deload week the envelope's 6.5 stands."""
    envelope = periodization_envelope(8, 4, 4).rpe_high
    assert envelope < 7.0
    ex = await _sized(async_db, "cap-above@test.com", _DELOAD, 7.0, _NOTE)

    assert ex.rpe_cap == envelope
    assert ex.prescribed_load_kg == sc.suggested_load_kg(E1RM, 8, envelope)


async def test_without_a_slot_ceiling_the_envelope_decides_as_before(async_db) -> None:
    ex = await _sized(async_db, "cap-none@test.com", _WORKING, None, DEFAULT_LOAD_NOTE)

    assert ex.rpe_cap == periodization_envelope(8, 5, 4).rpe_high
    # The generic default note is still replaced by the load, exactly as before.
    assert ex.load_note is not None and DEFAULT_LOAD_NOTE not in ex.load_note


def test_selection_carries_the_authored_ceiling(catalog_snapshot: list[CatalogExercise]) -> None:
    (template,) = [
        t for t in GOAL_TEMPLATE_LIBRARY["mixed"] if t.branch_id == "mixed_strength_endurance"
    ]
    selection = _select_exercises(template.exercise_slots, None, catalog_snapshot)
    assert [e.rpe_cap for e in selection.exercises] == [7.0, 7.0, 7.0]


@pytest.mark.parametrize("cap", [0.0, 10.5])
def test_a_ceiling_must_be_an_rpe(cap: float) -> None:
    with pytest.raises(ValueError, match="rpe_cap"):
        ExerciseSlot(sets="5", reps="8", rpe_cap=cap)
