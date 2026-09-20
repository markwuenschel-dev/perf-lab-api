"""Phase 2.1 exit gate: the representation changed, the behaviour did not.

Four claims, each checked over the whole simulation grid rather than a sample:

1. ``exercises[] == project_exercises(structure)`` — the projection is exact, so the flat
   list clients already read is derivable from the blocks rather than parallel to them.
2. Prescription SELECTION is unchanged — the same athlete gets the same session.
3. The production DOSE is unchanged.
4. The athlete-STATE transition is unchanged.

2-4 are checked against `docs/simulations/phase-1.md`, which was generated and committed
BEFORE this change. That file is the before-snapshot; if a representation refactor moved any
number in it, this test says so.
"""
import re
from pathlib import Path

import pytest

from app.schemas.prescription import project_exercises
from app.scripts import simulate_matrix as sm

_REPORT = Path(__file__).resolve().parents[1] / "docs/simulations/phase-1.md"


def _committed_rows() -> dict[tuple[str, str, str, str], tuple[str, int, int, str, str, str]]:
    """The matrix table as committed before phase 2.1, keyed by cell."""
    rows: dict[tuple[str, str, str, str], tuple[str, int, int, str, str, str]] = {}
    for line in _REPORT.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 10 or cells[0] in ("experience", "---"):
            continue
        if cells[0] not in sm.EXPERIENCE:
            continue
        experience, freshness, goal, workload, session, sets, minutes, dose, dcap, dfat = cells
        rows[(experience, freshness, goal, workload)] = (
            re.sub(r"\s*⚠️$", "", session), int(sets), int(minutes), dose, dcap, dfat
        )
    return rows


def test_the_before_snapshot_is_actually_present() -> None:
    """Guard the guard: an unparsed report would make every comparison below vacuous."""
    committed = _committed_rows()

    assert len(committed) == len(sm.EXPERIENCE) * len(sm.FRESHNESS) * len(sm.GOALS) * len(
        sm.WORKLOADS
    ), f"parsed {len(committed)} rows from {_REPORT.name}"


def test_selection_dose_and_state_transition_are_unchanged() -> None:
    """Claims 2-4, over all 54 cells, against the pre-2.1 committed report."""
    committed = _committed_rows()
    drift: list[str] = []
    for cell in sm.build_matrix():
        was = committed[(cell.experience, cell.freshness, cell.goal, cell.workload)]
        now = (
            cell.session,
            cell.sets,
            cell.duration_min,
            f"{cell.dose_total:.2f}",
            f"{cell.capacity_delta:+.3f}",
            f"{cell.fatigue_delta:+.2f}",
        )
        if now != was:
            drift.append(f"{cell.experience}/{cell.freshness}/{cell.goal}/{cell.workload}: {was} -> {now}")

    assert not drift, "phase 2.1 changed behaviour:\n" + "\n".join(drift)


@pytest.mark.parametrize("workload", sm.WORKLOADS)
def test_every_generated_session_projects_back_to_its_own_exercises(workload: str) -> None:
    """Claim 1, driven through the real prescriber for every experience/freshness/goal."""
    from app.logic.prescriber import recommend_next_session

    mismatches: list[str] = []
    for experience, (level_key, _years) in sm.EXPERIENCE.items():
        for freshness, (fatigue, tissue) in sm.FRESHNESS.items():
            for goal, (training_goal, domain, category) in sm.GOALS.items():
                rx = recommend_next_session(
                    sm._state(level_key, fatigue, tissue),
                    goal=training_goal,
                    block_context={
                        "block_goal": training_goal,
                        "session_category": category,
                        "session_domain": domain,
                        "week_number": 2,
                        "duration_weeks": 8,
                        "deload_every_n_weeks": 4,
                        "intensity": workload,
                    },
                )
                if rx.structure is None:
                    mismatches.append(f"{experience}/{freshness}/{goal}: no structure attached")
                elif project_exercises(rx.structure) != rx.exercises:
                    mismatches.append(f"{experience}/{freshness}/{goal}: projection != exercises")

    assert not mismatches, "\n".join(mismatches)


def test_a_prescription_cannot_be_built_with_disagreeing_views() -> None:
    """The invariant that makes structure canonical rather than a copy."""
    from app.schemas.prescription import ExercisePrescription, WorkoutPrescription

    rx = WorkoutPrescription(
        type="Max Strength", focus="f", rationale="r", duration_min=60,
        exercises=[ExercisePrescription(name="Back Squat", sets=4, reps="5")],
    ).with_structure()

    with pytest.raises(ValueError, match="structure and exercises disagree"):
        WorkoutPrescription(
            type=rx.type, focus=rx.focus, rationale=rx.rationale, duration_min=rx.duration_min,
            exercises=[ExercisePrescription(name="Front Squat", sets=4, reps="5")],
            structure=rx.structure,
        )
