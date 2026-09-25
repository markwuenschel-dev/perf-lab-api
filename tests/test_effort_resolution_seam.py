"""Effort must be decided before load is resolved, exactly once, under one ceiling (phase 3.4).

Three defects this pins, all found by tracing the live path during the 3.4 review rather than
by a failing test — which is why the seam gets a test now even though no family policy is live:

1. **Effort written after load resolution.** ``_enrich_exercises_with_load`` resolves kilograms
   *from* the RPE cap (``prescription_service``), and ``prescribe_for_athlete`` then re-derives
   the structure from those exercises. A difficulty layer that moved ``rpe_target`` after that
   point would leave the athlete a prescription whose displayed effort and prescribed load
   disagree — the bar would not have moved. The assertion is therefore not "the layers run in
   this order" but the consequence: **the load on the final structure is the load that effort
   target resolves to**. Reordering the pipeline breaks it; so does any late edit.

2. **Two layers moving effort.** The workload preference already shifts the envelope's RPE band
   (``planning._with_intensity``), which is what sizes the load. A family transform that also
   moved effort *without replacing* that shift would apply the preference twice and prescribe a
   session neither policy describes.

3. **Two disagreeing ceilings.** ``INTENSITY_RPE_CEILING`` is the stated bound on what a
   preference may reach. A difficulty layer carrying its own looser ceiling could pass its own
   validation while breaching that bound.

Effort and load are one prescription decision, not a load decision annotated with an effort
label: autoregulatory targets select the load rather than describing it after the fact (Helms
et al. 2020; Larsen et al. 2021; Hickmott et al. 2022).
"""
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.logic import strength_calibration as sc
from app.logic.difficulty_strength import (
    CANDIDATE_TRANSFORMS,
    RIR_CEILING,
    RIR_FLOOR,
    RPE_CEILING,
    RPE_FLOOR,
)
from app.logic.planning import INTENSITY_CHOICES, INTENSITY_RPE_CEILING, periodization_envelope
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.user import User
from app.schemas.prescription import (
    ExercisePrescription,
    WorkoutPrescription,
    structure_from_exercises,
)
from app.schemas.workout_structure import StrengthBlock
from app.services.prescription_service import _enrich_exercises_with_load

SQUAT = "pl_e1rm_squat"
E1RM = 140.0
AS_OF = datetime(2026, 9, 14, 12, 0, 0)
#: wk 5 of 8 with the default 4-week deload cadence: a working week in "intensification", so
#: the band is not already saturated at the ceiling and a preference has room to move it.
BLOCK: dict[str, Any] = {"week_number": 5, "duration_weeks": 8, "deload_every_n_weeks": 4}


async def _athlete(db: Any, email: str) -> None:
    user = User(email=email, hashed_password="hashed", is_active=True)
    db.add(user)
    db.add(Exercise(
        name="Back Squat", modality="Strength", movement_pattern="squat", load_type="barbell",
        is_benchmark=True, e1rm_benchmark_code=SQUAT,
    ))
    definition = BenchmarkDefinition(
        code=SQUAT, name="Squat e1RM", domain="powerlifting", metric_type="load", unit="kg",
        better_direction="higher", observation_weight=1.0,
        standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    db.add(definition)
    await db.commit()
    await db.refresh(user)
    await db.refresh(definition)
    db.add(BenchmarkObservation(
        user_id=user.id, benchmark_definition_id=definition.id, source="manual",
        source_type="athlete_entry", validity_status="valid", affects_prescription=True,
        raw_value=E1RM, performed_at=AS_OF - timedelta(days=2),
        evidence_type="direct_measurement", value_semantics="measured",
    ))
    await db.commit()
    return user


def _rx() -> WorkoutPrescription:
    return WorkoutPrescription(
        type="strength", focus="lower", rationale="x", duration_min=60,
        exercises=[ExercisePrescription(name="Back Squat", sets=5, reps="3")],
    )


def _disagreements(structure: list[Any]) -> list[str]:
    """Every block whose prescribed load is not what its own effort target resolves to.

    The one check both the real pipeline and the planted defect below are graded against, so
    a green result means the check can actually fail rather than that it never fires.
    """
    problems: list[str] = []
    for block in structure:
        if not isinstance(block, StrengthBlock) or block.load_target_kg is None:
            continue
        reps = float(int(str(block.reps).split("-")[0]))
        expected = sc.suggested_load_kg(E1RM, reps, block.rpe_target)
        if expected != block.load_target_kg:
            problems.append(
                f"{block.exercise}: RPE {block.rpe_target} resolves to {expected} kg, "
                f"prescribed {block.load_target_kg} kg"
            )
    return problems


# ── 1. the seam: effort decided before load, on the real pipeline ────────────


@pytest.mark.parametrize("level", INTENSITY_CHOICES)
async def test_the_final_structure_load_is_what_its_own_effort_target_resolves_to(
    async_db, level: str
) -> None:
    """The consequence of correct ordering, asserted end to end through the real resolver."""
    user = await _athlete(async_db, f"seam-{level}@test.com")
    rx = _rx()

    await _enrich_exercises_with_load(
        async_db, user.id, rx, {**BLOCK, "intensity": level}, as_of=AS_OF
    )
    # Exactly what prescribe_for_athlete does after resolution (prescription_service.py).
    rx.structure = structure_from_exercises(rx.exercises, rx.structure)

    assert rx.exercises[0].prescribed_load_kg is not None, "the lift must have resolved a load"
    assert _disagreements(rx.structure) == []


async def test_an_effort_change_made_after_load_resolution_is_detected(async_db) -> None:
    """Test the test: the defect this seam exists to prevent must actually trip the check.

    A difficulty layer running one stage too late looks exactly like this — the RPE moves, the
    kilograms do not.
    """
    user = await _athlete(async_db, "seam-late-effort@test.com")
    rx = _rx()
    await _enrich_exercises_with_load(
        async_db, user.id, rx, {**BLOCK, "intensity": "medium"}, as_of=AS_OF
    )
    rx.structure = structure_from_exercises(rx.exercises, rx.structure)

    late = [rx.structure[0].model_copy(update={"rpe_target": 9.5})]

    problems = _disagreements(late)
    assert len(problems) == 1 and "prescribed" in problems[0]


# ── 2. exactly once: one layer owns the preference ───────────────────────────


@pytest.mark.parametrize("level", INTENSITY_CHOICES)
async def test_exactly_one_layer_moves_effort_for_a_workload_preference(
    async_db, level: str
) -> None:
    """The resolved cap is the envelope's, not the envelope's plus a second delta.

    If a family transform is ever promoted, it must REPLACE this shift for the dimensions it
    owns. Stacking would show up here as a cap the envelope never produced.
    """
    user = await _athlete(async_db, f"once-{level}@test.com")
    rx = _rx()

    await _enrich_exercises_with_load(
        async_db, user.id, rx, {**BLOCK, "intensity": level}, as_of=AS_OF
    )

    expected = periodization_envelope(
        BLOCK["duration_weeks"], BLOCK["week_number"], BLOCK["deload_every_n_weeks"],
        intensity=level,
    ).rpe_high
    assert rx.exercises[0].rpe_cap == expected


# ── 3. one ceiling ───────────────────────────────────────────────────────────


def test_the_difficulty_layer_uses_the_envelope_ceiling_rather_than_its_own() -> None:
    assert RPE_CEILING == INTENSITY_RPE_CEILING
    assert RIR_FLOOR == 10.0 - INTENSITY_RPE_CEILING, "the same bound from the RIR end"
    assert RPE_FLOOR < RPE_CEILING and RIR_FLOOR < RIR_CEILING


@pytest.mark.parametrize("family", sorted(CANDIDATE_TRANSFORMS))
@pytest.mark.parametrize("level", INTENSITY_CHOICES)
@pytest.mark.parametrize("start_rpe", [6.0, 8.0, 9.0, 9.5])
def test_no_candidate_can_prescribe_past_the_preference_ceiling(
    family: str, level: str, start_rpe: float
) -> None:
    """Including from a block already authored at the ceiling — a peak week's 9.5."""
    before = [StrengthBlock(exercise="Back Squat", sets=5, reps="3", rpe_target=start_rpe)]

    after = CANDIDATE_TRANSFORMS[family].apply(before, level)

    assert after[0].rpe_target is not None
    assert after[0].rpe_target <= INTENSITY_RPE_CEILING


@pytest.mark.parametrize("family", sorted(CANDIDATE_TRANSFORMS))
@pytest.mark.parametrize("start_rir", [0.5, 1.0, 3.0])
def test_a_block_carrying_rir_is_bounded_exactly_as_one_carrying_rpe(
    family: str, start_rir: float
) -> None:
    """RIR = 10 − RPE, so the two fields must refuse the same prescriptions."""
    before = [StrengthBlock(exercise="Back Squat", sets=5, reps="3", rir_target=start_rir)]

    after = CANDIDATE_TRANSFORMS[family].apply(before, "hard")

    assert after[0].rir_target is not None
    assert after[0].rir_target >= 10.0 - INTENSITY_RPE_CEILING
