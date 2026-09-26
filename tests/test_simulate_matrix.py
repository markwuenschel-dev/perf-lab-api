"""The phase-1 simulation matrix runs, and its plausibility checks actually fire.

A report generator that silently produced an empty grid, or whose checks never triggered,
would be worse than no matrix: it would look like evidence of health. These tests pin the
shape of the run and prove the cross-cell checks can detect an implausible cell.
"""
from app.scripts import simulate_matrix as sm


def test_the_matrix_covers_the_declared_grid() -> None:
    cells = sm.build_matrix()

    assert len(cells) == len(sm.EXPERIENCE) * len(sm.FRESHNESS) * len(sm.GOALS) * len(sm.WORKLOADS)
    assert all(c.session for c in cells), "every cell must name the session it prescribed"
    assert all(c.duration_min > 0 for c in cells)


def test_every_cell_carries_a_real_dose() -> None:
    """A zero dose would make the state deltas meaningless."""
    assert all(c.dose_total > 0.0 for c in sm.build_matrix())


def test_the_cross_cell_checks_detect_an_implausible_cell() -> None:
    """Proof the checks are load-bearing: a planted fatigued>fresh cell is reported."""
    cells = sm.build_matrix()
    by_key = {(c.experience, c.freshness, c.goal, c.workload): c for c in cells}
    fresh = by_key[("novice", "fresh", "strength", "medium")]
    tired = by_key[("novice", "fatigued", "strength", "medium")]
    tired.sets = fresh.sets + 5  # a fatigued athlete given MORE work

    findings = sm.cross_cell_flags(cells)

    assert any("fatigued > fresh WORK" in f for f in findings), findings


def test_the_report_names_its_findings_rather_than_only_counting_them() -> None:
    report = sm.render(sm.build_matrix())

    assert "## Findings" in report and "## Matrix" in report
    assert "easy > hard DOSE" in report, "the known v0 density inversion should be reported"
    assert "v1 (shadow)" in report, "and explained against the corrected engine"


def test_every_planned_day_is_prescribed_as_planned() -> None:
    """The phase-5 exit claim, over the whole grid: no planned day is replaced by something
    else, and no cell is a generic redirect."""
    flagged = [
        (c.experience, c.freshness, c.goal, c.workload, c.flags)
        for c in sm.build_matrix("phase-5")
        if any(f.startswith(("plan-replaced", "redirect")) for f in c.flags)
    ]

    assert not flagged, flagged


#: Every HYROX and CrossFit planned day, and what it prescribes at medium workload. The grid runs
#: week 2 of 8, so days with two variants show the SECOND (phase 7.2 rotates tied variants by
#: block week): Half Simulation B, Running + Functional — Lunges, Strength + Skill — Deadlift.
PHASE_6_DAYS = {
    "hyrox_strength_endurance": "Back Squat, Overhead Press, Barbell Row",
    "hyrox_running_functional": "Run, Sandbag Lunges",
    "hyrox_simulation": (
        "Run, Rowing (Ergometer), Run, Farmer Carry, Run, Sandbag Lunges, Run, Wall Ball"
    ),
    "crossfit_strength_skill": "Conventional Deadlift, Double Unders, Toes to Bar",
    "crossfit_engine_work": "Assault Bike, Assault Bike",
}


def test_every_hyrox_and_crossfit_day_is_prescribed_as_planned() -> None:
    """The phase-6 exit claim, over the whole phase-6 grid: no planned day is replaced, no
    cell is a generic redirect, and no HYROX / CrossFit day is built from the generic
    equipment fallback."""
    cells = sm.build_matrix("phase-6")
    flagged = [
        (c.experience, c.freshness, c.goal, c.workload, c.flags)
        for c in cells
        if any(f.startswith(("plan-replaced", "redirect")) for f in c.flags)
        or (c.goal in sm.PHASE_6_GOALS and c.goal not in sm.PHASE_5_GOALS
            and "template-has-no-exercises" in c.flags)
    ]
    by_goal = {c.goal: c for c in cells if c.workload == "medium" and c.experience == "novice"
               and c.freshness == "fresh"}

    assert not flagged, flagged
    assert {g: by_goal[g].exercises for g in PHASE_6_DAYS} == PHASE_6_DAYS


def test_the_plan_replaced_flag_fires(monkeypatch) -> None:
    """Test the test: a Sprinting goal on an Aerobic Base day draws the sprint pool, so the
    bound aerobic templates are unavailable and the plan is replaced."""
    monkeypatch.setitem(sm.PHASE_5_GOALS, "planted", ("Sprinting", "running", "Aerobic Base", {}))

    cell = sm._run_cell("novice", "fresh", "planted", "medium", "phase-5")

    assert "plan-replaced(running_base(unavailable))" in cell.flags, cell.flags


def test_the_matrix_uses_the_real_catalog_not_the_equipment_fallback() -> None:
    """#1 passed no catalog, so every session came from the generic equipment map. The
    phase-1 grid still does, on purpose: it is a committed before-snapshot."""
    by_goal = {c.goal: c for c in sm.build_matrix("phase-5") if c.workload == "medium"}

    assert by_goal["threshold"].exercises == "Tempo Run"
    assert by_goal["recovery"].exercises == "Easy Run"
    assert by_goal["potentiation"].exercises == "Back Squat, Broad Jump"


def test_matrix_4_shows_each_block_periodized_by_its_own_data() -> None:
    """The phase-7 exit claim, over the week axis."""
    rows = sm.build_periodization()
    by_block: dict[str, list[sm.WeekRow]] = {}
    for r in rows:
        by_block.setdefault(r.block, []).append(r)

    def phases(block: str) -> list[str]:
        return [r.phase.split("(")[0] for r in by_block[block]]

    assert phases("running (distance)") == [
        "base", "base", "base", "deload", "threshold", "threshold", "race_specific", "taper",
    ]
    assert {r.source for r in by_block["running (distance)"]} == {"running"}
    # Sprint-primary: generic, not a distance runner's base / threshold / race shape.
    assert {r.source for r in by_block["running (sprint-primary)"]} == {"generic"}
    assert {r.source for r in by_block["calisthenics"]} == {"calisthenics"}
    generic = [
        "accumulation", "accumulation", "accumulation", "deload",
        "intensification", "intensification", "peak", "taper",
    ]
    for block in ("running (sprint-primary)", "strength", "hyrox"):
        assert phases(block) == generic, block
    # The HYROX simulation day rotates its two halves by block week.
    assert [r.branch for r in by_block["hyrox"]] == ["hyrox_half_sim_a", "hyrox_half_sim_b"] * 4
