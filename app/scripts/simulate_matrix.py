"""Simulation matrix — what the engine actually prescribes across the athlete space.

Phase-1 exit gate. A formula can be locally sensible and globally ridiculous: every unit test
passes, and then a fatigued novice on an easy week is told to do a peak-intensity session. The
only way to see that is to run the whole grid and read it.

    experience  novice | intermediate | advanced
    freshness   fresh | fatigued
    goal        strength | endurance | mixed
    workload    easy | medium | hard

54 cells. For each, the REAL production path is used end to end — no numbers are invented
here: baseline capacities come from ``state_service._BASELINE_CAPACITIES``, the session from
``recommend_next_session``, the dose from the production dose engine, and the state delta from
``update_athlete_state``.

The dose is computed from what the session actually PRESCRIBES — its working sets and its
duration. The MPC intent bridge (``candidate_to_dose``) was tried first and rejected for this
report: it synthesizes a session from modality and duration alone, so easy/medium/hard all
produced an identical dose while the prescribed sets differed 8/11/14. A matrix whose dose
column cannot see the thing under test would have hidden exactly what it exists to show.

Each row reports the prescribed session, its working sets and duration, the dose it carries,
and the resulting one-session state delta. IMPLAUSIBLE cells are flagged by name with the rule
they broke — the flags are heuristics for a human to read, not assertions:

* a fatigued athlete prescribed MORE work than the same fresh athlete
* an easy week carrying more dose than the hard week for the same athlete
* a session built from the generic equipment fallback rather than its own template — the
  "a Running day prescribes Air Squat" symptom (phases 4-6)
* a session with no exercises at all
* zero or negative dose on a real session

Matrix #2 (phase-5 exit) adds the planned running and power days phase 5 made real, and two
corrections to #1:

* the movement catalog is passed, built from the seeder source exactly as the test suite's
  ``catalog_snapshot`` is. #1 passed none, so every cell fell back to the generic equipment
  map (``prescriber._select_exercises``: ``catalog is None``) and slot selection was never
  exercised;
* a per-cell ``plan-replaced`` flag: a planned day that prescribed something else (the
  prescriber's own ``plan:session_replaced=`` code), and ``redirect`` for a readiness /
  safety session that is not a library template.

Matrix #3 (phase-6 exit) adds the six HYROX and CrossFit planned days, each as an active
HYROX or CrossFit block passes it. Before phase 6, three of them bound no template and two
bound one with no exercise slots.

Run:
    uv run python -m app.scripts.simulate_matrix
    uv run python -m app.scripts.simulate_matrix --grid phase-5         --out docs/simulations/phase-5.md --title "Simulation matrix #2 — phase 5 exit"
    uv run python -m app.scripts.simulate_matrix --grid phase-6         --out docs/simulations/phase-6.md --title "Simulation matrix #3 — phase 6 exit"
    uv run python -m app.scripts.simulate_matrix --grid phase-7         --out docs/simulations/phase-7.md --title "Simulation matrix #4 — phase 7 exit"

Matrix #4 (phase-7 exit) keeps the phase-6 grid and adds a week axis: every week of an 8-week
block for each kind of block phase 7 distinguishes (a distance and a sprint-primary Running
block, Calisthenics, and generic Strength and HYROX blocks).
"""
from __future__ import annotations

import argparse
import itertools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic import dose_engine_v0 as _v0
from app.logic import dose_engine_v1 as _v1
from app.logic.candidate_library import GOAL_TEMPLATE_LIBRARY
from app.logic.dose_engine import PRODUCTION_DOSE_MODEL_NAME, calculate_stress_dose
from app.logic.exercise_slot import CatalogExercise
from app.logic.mpc.candidate_dose import modality_for_domain
from app.logic.prescriber import recommend_next_session
from app.logic.state_update_v0 import update_athlete_state
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.schemas.workouts import WorkoutLog
from app.services.state_service import _BASELINE_CAPACITIES, _SKILL_BY_LEVEL

_WHEN = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)

#: Branch ids whose template names no exercises of its own. Derived from the library rather
#: than listed here, so it cannot go stale as phases 4-6 close the gaps.
_SLOTLESS_BRANCHES: frozenset[str] = frozenset(
    t.branch_id
    for pool in GOAL_TEMPLATE_LIBRARY.values()
    for t in pool
    if not getattr(t, "exercise_slots", None)
)

#: experience label -> (state_service level key, experience years)
EXPERIENCE = {
    "novice": ("beginner", 0.5),
    "intermediate": ("intermediate", 3.0),
    "advanced": ("advanced", 8.0),
}

#: freshness label -> (fatigue on every axis, tissue on every axis)
FRESHNESS = {
    "fresh": (5.0, 5.0),
    # Deliberately below every safety threshold (lumbar 65 / knee 70 / systemic 80): the
    # matrix is about ordinary programming, not the hard-stop paths, which have their own tests.
    "fatigued": (55.0, 45.0),
}

#: Every template id in the library. A winning branch outside it is a readiness redirect, a
#: safety override or the infeasible fallback: not a planned session.
_TEMPLATE_BRANCHES: frozenset[str] = frozenset(
    t.branch_id for pool in GOAL_TEMPLATE_LIBRARY.values() for t in pool
)

#: goal label -> (training goal, canonical domain, planned slot category). The phase-1 grid,
#: kept exactly: ``tests/test_structure_projection_identity.py`` compares it to the committed
#: ``docs/simulations/phase-1.md``.
GOALS = {
    "strength": ("Strength", "strength", "Max Strength"),
    "endurance": ("Running", "running", "Aerobic Base"),
    "mixed": ("CrossFit", "mixed", "Metabolic Conditioning"),
}

#: The phase-5 grid: goal label -> (training goal as a block passes it, canonical domain,
#: planned slot category, KPIs). The running days use goal "Running", as an active Running
#: block does (``prescription_service.resolve_effective_goal``). Threshold runs twice: without
#: the fatigue-factor KPI (most athletes) and with a high one, the two members of the family.
PHASE_5_GOALS: dict[str, tuple[str, str, str, dict[str, float]]] = {
    **{label: (*spec, {}) for label, spec in GOALS.items()},
    "threshold": ("Running", "running", "Threshold Work", {}),
    "threshold_ff20": ("Running", "running", "Threshold Work", {"run_fatigue_factor": 20.0}),
    "speed": ("Running", "running", "Speed", {}),
    "recovery": ("Running", "running", "Active Recovery", {}),
    "potentiation": ("Power", "power", "Strength Potentiation", {}),
}

#: The phase-6 grid: the phase-5 grid plus every HYROX and CrossFit planned day
#: (``planning_service._DEFAULT_TEMPLATES``), with the goal an active block passes.
PHASE_6_GOALS: dict[str, tuple[str, str, str, dict[str, float]]] = {
    **PHASE_5_GOALS,
    "hyrox_strength_endurance": ("Hyrox", "mixed", "Strength Endurance", {}),
    "hyrox_running_functional": ("Hyrox", "mixed", "Running + Functional", {}),
    "hyrox_simulation": ("Hyrox", "mixed", "Hyrox Simulation", {}),
    "crossfit_strength_skill": ("CrossFit", "mixed", "Strength + Skill", {}),
    "crossfit_metcon": ("CrossFit", "mixed", "MetCon", {}),
    "crossfit_engine_work": ("CrossFit", "mixed", "Engine Work", {}),
}


def _catalog() -> list[CatalogExercise]:
    """The movement catalog as plain data, from the seeder source: no database."""
    from app.data.exercise_bulk import bulk_exercises
    from app.scripts.seed_exercises import EXERCISES

    return [
        CatalogExercise(
            name=row["name"],
            modality=row["modality"],
            movement_pattern=row["movement_pattern"],
            load_type=row["load_type"],
            pattern_family=row.get("pattern_family"),
            equipment_required=tuple(row.get("equipment_required") or ()),
            sport_domains=tuple(row.get("sport_domains") or ()),
            weak_point_tags=tuple(row.get("weak_point_tags") or ()),
            skill_demand=row.get("skill_demand") or 0.5,
            e1rm_benchmark_code=row.get("e1rm_benchmark_code"),
        )
        for row in list(EXERCISES) + list(bulk_exercises())
    ]


#: grid name -> (goals, catalog). Phase 1 ran without a catalog; see the module docstring.
GRIDS: dict[str, tuple[dict[str, tuple[str, str, str, dict[str, float]]], list[CatalogExercise] | None]] = {
    "phase-1": ({label: (*spec, {}) for label, spec in GOALS.items()}, None),
    "phase-5": (PHASE_5_GOALS, _catalog()),
    "phase-6": (PHASE_6_GOALS, _catalog()),
    # Matrix #4 (phase-7 exit): the phase-6 grid at week 2, plus the per-week periodization
    # section (``build_periodization``) that phase 7 is actually about.
    "phase-7": (PHASE_6_GOALS, _catalog()),
}

#: Matrix #4's week axis: one athlete (novice, fresh, medium workload) through an 8-week block,
#: deload every 4, for each kind of block phase 7 distinguishes. label ->
#: (block goal, canonical domain, planned category, block modality mix).
PERIODIZATION_BLOCKS: dict[str, tuple[str, str, str, dict[str, float]]] = {
    "running (distance)": ("Running", "running", "Aerobic Base", {}),
    "running (sprint-primary)": ("Running", "running", "Speed", {"sprinting": 1.0}),
    "calisthenics": ("Calisthenics", "calisthenics", "Bodyweight Strength", {}),
    "strength": ("Strength", "strength", "Max Strength", {}),
    "hyrox": ("Hyrox", "mixed", "Hyrox Simulation", {}),
}
PERIODIZATION_WEEKS = 8


@dataclass(frozen=True)
class WeekRow:
    block: str
    week: int
    phase: str
    rpe_target: str
    source: str
    session: str
    branch: str
    duration_min: int


WORKLOADS = ("easy", "medium", "hard")


def _state(level_key: str, fatigue: float, tissue: float) -> UnifiedStateVector:
    """A plausible athlete at this experience level and freshness.

    Capacities come from the same table onboarding seeds from, so the matrix describes
    athletes the product can actually produce.
    """
    caps = _BASELINE_CAPACITIES[level_key]
    x = CapacityState(
        aerobic=caps["c_met_aerobic"],
        max_strength=(caps["c_nm_force"] - 400.0) / 21.0,
        hypertrophy=caps["c_struct"] / 2.0,
        power=caps["c_struct"] / 2.2,
        skill=_SKILL_BY_LEVEL[level_key] * 100.0,
        mobility=50.0,
        work_capacity=caps["c_struct"] / 2.0,
        glycolytic=caps["b_met_anaerobic"] / 400.0,
    )
    f = FatigueState(
        cns=fatigue, muscular=fatigue, metabolic=fatigue, structural=fatigue, tendon=fatigue
    )
    t = TissueState()
    for axis in t.KEYS:
        setattr(t, axis, tissue)
    legacy = sync_legacy_from_vectors(x, f, t)
    return UnifiedStateVector(
        timestamp=_WHEN,
        capacity_x=x,
        fatigue_f=f,
        tissue_t=t,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={},
        **legacy,
    )


@dataclass
class Cell:
    experience: str
    freshness: str
    goal: str
    workload: str
    session: str
    exercises: str
    sets: int
    duration_min: int
    dose_total: float
    capacity_delta: float
    fatigue_delta: float
    flags: list[str]

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.experience, self.freshness, self.goal)


def _run_cell(
    experience: str, freshness: str, goal: str, workload: str, grid: str = "phase-1"
) -> Cell:
    level_key, _years = EXPERIENCE[experience]
    fatigue, tissue = FRESHNESS[freshness]
    goals, catalog = GRIDS[grid]
    training_goal, domain, category, kpi = goals[goal]
    state = _state(level_key, fatigue, tissue)

    rx = recommend_next_session(
        state,
        goal=training_goal,  # type: ignore[arg-type]
        kpi_summary=kpi,
        catalog=catalog,
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

    sets = sum(e.sets or 0 for e in rx.exercises)
    dose = calculate_stress_dose(_dose_log(rx, domain, sets))
    after = update_athlete_state(state, dose, timedelta(days=1), _log(rx))

    capacity_before = sum(getattr(state.capacity_x, k) for k in state.capacity_x.KEYS)
    capacity_after = sum(getattr(after.capacity_x, k) for k in after.capacity_x.KEYS)
    fatigue_before = sum(getattr(state.fatigue_f, k) for k in state.fatigue_f.KEYS)
    fatigue_after = sum(getattr(after.fatigue_f, k) for k in after.fatigue_f.KEYS)

    branch = rx.why.prescription_branch if rx.why is not None else ""
    flags: list[str] = []
    if not rx.exercises:
        flags.append("no-exercises")
    elif branch in _SLOTLESS_BRANCHES:
        # The winning template declares no exercise slots, so the session was assembled from
        # the generic equipment map: it is labelled as the sport and built from something else.
        # This is the "a Running day prescribes Air Squat" defect — phases 4-6.
        flags.append("template-has-no-exercises")
    dose_total = float(sum(dose.dose_six.model_dump().values()))
    if dose_total <= 0.0:
        flags.append("zero-dose")
    applied = rx.why.constraints_applied if rx.why is not None else []
    for code in applied:
        if code.startswith("plan:session_replaced="):
            flags.append(f"plan-replaced({code.split('=', 1)[1]})")
    if branch and branch not in _TEMPLATE_BRANCHES:
        flags.append(f"redirect({branch})")

    return Cell(
        experience=experience,
        freshness=freshness,
        goal=goal,
        workload=workload,
        session=rx.type,
        exercises=", ".join(e.name for e in rx.exercises),
        sets=sets,
        duration_min=rx.duration_min,
        dose_total=dose_total,
        capacity_delta=capacity_after - capacity_before,
        fatigue_delta=fatigue_after - fatigue_before,
        flags=flags,
    )


#: Effort assumed for every cell. Fixed on purpose: the matrix varies experience, freshness,
#: goal and workload, so holding RPE constant keeps the dose differences attributable to what
#: the engine PRESCRIBED rather than to an effort number chosen here. The prescribed RPE cap
#: lives in prescription_service and needs a database, which this offline script has none of.
ASSUMED_SESSION_RPE = 7.0


def _dose_log(rx, domain: str, sets: int) -> WorkoutLog:
    """The session as logged, built from what was actually prescribed."""
    return WorkoutLog(
        timestamp=_WHEN,
        modality=modality_for_domain(domain),
        duration_minutes=max(1.0, float(rx.duration_min)),
        session_rpe=ASSUMED_SESSION_RPE,
        estimated_sets=float(sets) if sets > 0 else None,
        sleep_quality=7.0,
        life_stress_inverse=7.0,
    )


def _log(rx) -> WorkoutLog:
    """A neutral log for the state step — the dose carries the session, this carries recovery."""
    return WorkoutLog(
        timestamp=_WHEN,
        modality="Strength",
        duration_minutes=max(1.0, float(rx.duration_min)),
        session_rpe=ASSUMED_SESSION_RPE,
        sleep_quality=7.0,
        life_stress_inverse=7.0,
    )


def _code_value(codes: list[str], prefix: str) -> str:
    return next((c[len(prefix):] for c in codes if c.startswith(prefix)), "")


def build_periodization() -> list[WeekRow]:
    """Every week of an 8-week block for each block kind, through the real prescriber."""
    level_key, _years = EXPERIENCE["novice"]
    fatigue, tissue = FRESHNESS["fresh"]
    catalog = _catalog()
    rows: list[WeekRow] = []
    for label, (goal, domain, category, mix) in PERIODIZATION_BLOCKS.items():
        for week in range(1, PERIODIZATION_WEEKS + 1):
            rx = recommend_next_session(
                _state(level_key, fatigue, tissue),
                goal=goal,  # type: ignore[arg-type]
                catalog=catalog,
                block_context={
                    "block_goal": goal, "modality_mix": mix, "session_category": category,
                    "session_domain": domain, "week_number": week,
                    "duration_weeks": PERIODIZATION_WEEKS, "deload_every_n_weeks": 4,
                    "intensity": "medium",
                },
            )
            codes = rx.why.constraints_applied if rx.why is not None else []
            rows.append(WeekRow(
                block=label, week=week,
                phase=_code_value(codes, "block:phase="),
                rpe_target=_code_value(codes, "block:rpe_target="),
                source=_code_value(codes, "block:periodization="),
                session=rx.type,
                branch=rx.why.prescription_branch or "" if rx.why is not None else "",
                duration_min=rx.duration_min,
            ))
    return rows


def render_periodization(rows: list[WeekRow]) -> str:
    lines = [
        "",
        "## Periodization by week (matrix #4)",
        "",
        "One athlete (novice, fresh, medium workload), every week of an 8-week block with a "
        "deload every 4 weeks. `source` is the block's periodization: an authored template, or "
        "`generic`.",
        "",
        "| block | week | phase (×length) | RPE target | source | session | min |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r.block} | {r.week} | {r.phase} | {r.rpe_target} | {r.source} | "
            f"{r.session} | {r.duration_min} |"
        )
    return "\n".join(lines) + "\n"


def build_matrix(grid: str = "phase-1") -> list[Cell]:
    goals, _catalog_rows = GRIDS[grid]
    return [
        _run_cell(experience, freshness, goal, workload, grid)
        for experience, freshness, goal, workload in itertools.product(
            EXPERIENCE, FRESHNESS, goals, WORKLOADS
        )
    ]


def cross_cell_flags(cells: list[Cell]) -> list[str]:
    """Checks that only make sense ACROSS cells — the globally-ridiculous ones."""
    findings: list[str] = []
    by_key = {(c.experience, c.freshness, c.goal, c.workload): c for c in cells}
    goals = list(dict.fromkeys(c.goal for c in cells))

    for experience, goal, workload in itertools.product(EXPERIENCE, goals, WORKLOADS):
        fresh = by_key[(experience, "fresh", goal, workload)]
        tired = by_key[(experience, "fatigued", goal, workload)]
        if tired.sets > fresh.sets:
            findings.append(
                f"fatigued > fresh WORK: {experience}/{goal}/{workload} — "
                f"{tired.sets} sets fatigued vs {fresh.sets} fresh"
            )
        if tired.dose_total > fresh.dose_total * 1.05:
            findings.append(
                f"fatigued > fresh DOSE: {experience}/{goal}/{workload} — "
                f"{tired.dose_total:.2f} vs {fresh.dose_total:.2f}"
            )

    for experience, freshness, goal in itertools.product(EXPERIENCE, FRESHNESS, goals):
        easy = by_key[(experience, freshness, goal, "easy")]
        hard = by_key[(experience, freshness, goal, "hard")]
        if easy.dose_total > hard.dose_total * 1.05:
            findings.append(
                f"easy > hard DOSE: {experience}/{freshness}/{goal} — "
                f"{easy.dose_total:.2f} vs {hard.dose_total:.2f}"
            )
        if easy.sets > hard.sets:
            findings.append(
                f"easy > hard WORK: {experience}/{freshness}/{goal} — "
                f"{easy.sets} vs {hard.sets} sets"
            )
    return findings


def _session_dose(engine, sets: int, duration: float = 75.0) -> float:
    """Total dose for one fixed session under a given engine — the easy/hard comparison."""
    log = WorkoutLog(
        timestamp=_WHEN,
        modality="Strength",
        duration_minutes=duration,
        session_rpe=ASSUMED_SESSION_RPE,
        estimated_sets=float(sets),
        sleep_quality=7.0,
        life_stress_inverse=7.0,
    )
    return float(sum(engine.calculate_stress_dose(log).dose_six.model_dump().values()))


def render(cells: list[Cell], title: str = "Simulation matrix #1 — phase 1 exit") -> str:
    cross = cross_cell_flags(cells)
    per_cell = [c for c in cells if c.flags]

    lines = [
        f"# {title}",
        "",
        "Generated by `app/scripts/simulate_matrix.py`. Every number comes from the production",
        "path: baseline capacities from `state_service._BASELINE_CAPACITIES`, the session from",
        "`recommend_next_session`, the dose from the production dose engine, the delta from",
        "`update_athlete_state`.",
        "",
        f"- production dose model: **{PRODUCTION_DOSE_MODEL_NAME}** "
        "(`app/logic/dose_model.py`; v1 is shadow-only until phase 8C)",
        f"- cells: **{len(cells)}** ({len(EXPERIENCE)} experience × {len(FRESHNESS)} freshness "
        f"× {len({c.goal for c in cells})} goal/day × {len(WORKLOADS)} workload)",
        f"- cells with a per-cell flag: **{len(per_cell)}**",
        f"- cross-cell findings: **{len(cross)}**",
        "",
        "## Findings",
        "",
    ]
    if not cross and not per_cell:
        lines.append("None — no cell tripped a plausibility check.")
    else:
        for finding in cross:
            lines.append(f"- **{finding}**")
        for cell in per_cell:
            lines.append(
                f"- **{'/'.join(cell.key)}/{cell.workload}**: {', '.join(cell.flags)} "
                f"(session {cell.session!r})"
            )

    if any("easy > hard DOSE" in f for f in cross):
        lines += [
            "",
            "### Why easy carries more dose than hard, in production",
            "",
            "Not a workload bug: the workload preference does prescribe more sets for hard.",
            "It is the v0 density variable. Production density is elapsed MINUTES PER SET, so",
            "packing more sets into the same session lowers it and therefore lowers the dose —",
            "adding work reduces the recorded training stress. Measured, 75-minute session:",
            "",
            "| engine | 8 sets | 14 sets | ordering |",
            "|---|---|---|---|",
        ]
        for label, engine in (("v0 (production)", _v0), ("v1 (shadow)", _v1)):
            light = _session_dose(engine, 8)
            heavy = _session_dose(engine, 14)
            verdict = "easy > hard — inverted" if light > heavy else "hard > easy — correct"
            lines.append(f"| {label} | {light:.2f} | {heavy:.2f} | {verdict} |")
        lines += [
            "",
            "v1 orders this example correctly and is uncalibrated, so for it the fix is phase",
            "8B/8C activation rather than another change to the dose law. The example is a",
            "Strength log: it does not show how v1 orders any other flagged session.",
        ]

    lines += [
        "",
        "## Matrix",
        "",
        "| experience | freshness | goal | workload | session | exercises | sets | min | dose | Δcapacity | Δfatigue |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in cells:
        flag = " ⚠️" if c.flags else ""
        lines.append(
            f"| {c.experience} | {c.freshness} | {c.goal} | {c.workload} | {c.session}{flag} | "
            f"{c.exercises} | {c.sets} | {c.duration_min} | {c.dose_total:.2f} | "
            f"{c.capacity_delta:+.3f} | {c.fatigue_delta:+.2f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Simulation matrix.")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--title", default="Simulation matrix #1 — phase 1 exit")
    ap.add_argument("--grid", choices=sorted(GRIDS), default="phase-1")
    args = ap.parse_args()

    report = render(build_matrix(args.grid), args.title)
    if args.grid == "phase-7":
        report += render_periodization(build_periodization())
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"[simulate_matrix] wrote {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
