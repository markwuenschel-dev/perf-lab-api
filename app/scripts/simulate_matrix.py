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

Run:
    uv run python -m app.scripts.simulate_matrix
    uv run python -m app.scripts.simulate_matrix --out docs/simulations/phase-1.md
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

#: goal label -> (training goal, canonical domain, planned slot category)
GOALS = {
    "strength": ("Strength", "strength", "Max Strength"),
    "endurance": ("Running", "running", "Aerobic Base"),
    "mixed": ("CrossFit", "mixed", "Metabolic Conditioning"),
}

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
    sets: int
    duration_min: int
    dose_total: float
    capacity_delta: float
    fatigue_delta: float
    flags: list[str]

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.experience, self.freshness, self.goal)


def _run_cell(experience: str, freshness: str, goal: str, workload: str) -> Cell:
    level_key, _years = EXPERIENCE[experience]
    fatigue, tissue = FRESHNESS[freshness]
    training_goal, domain, category = GOALS[goal]
    state = _state(level_key, fatigue, tissue)

    rx = recommend_next_session(
        state,
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

    return Cell(
        experience=experience,
        freshness=freshness,
        goal=goal,
        workload=workload,
        session=rx.type,
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


def build_matrix() -> list[Cell]:
    return [
        _run_cell(experience, freshness, goal, workload)
        for experience, freshness, goal, workload in itertools.product(
            EXPERIENCE, FRESHNESS, GOALS, WORKLOADS
        )
    ]


def cross_cell_flags(cells: list[Cell]) -> list[str]:
    """Checks that only make sense ACROSS cells — the globally-ridiculous ones."""
    findings: list[str] = []
    by_key = {(c.experience, c.freshness, c.goal, c.workload): c for c in cells}

    for experience, goal, workload in itertools.product(EXPERIENCE, GOALS, WORKLOADS):
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

    for experience, freshness, goal in itertools.product(EXPERIENCE, FRESHNESS, GOALS):
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


def render(cells: list[Cell]) -> str:
    cross = cross_cell_flags(cells)
    per_cell = [c for c in cells if c.flags]

    lines = [
        "# Simulation matrix #1 — phase 1 exit",
        "",
        "Generated by `app/scripts/simulate_matrix.py`. Every number comes from the production",
        "path: baseline capacities from `state_service._BASELINE_CAPACITIES`, the session from",
        "`recommend_next_session`, the dose from the production dose engine, the delta from",
        "`update_athlete_state`.",
        "",
        f"- production dose model: **{PRODUCTION_DOSE_MODEL_NAME}** "
        "(`app/logic/dose_model.py`; v1 is shadow-only until phase 8C)",
        f"- cells: **{len(cells)}** (3 experience × 2 freshness × 3 goal × 3 workload)",
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
            "v1 already orders these correctly and is uncalibrated, so the fix is phase 8B/8C",
            "activation rather than another change to the dose law.",
        ]

    lines += [
        "",
        "## Matrix",
        "",
        "| experience | freshness | goal | workload | session | sets | min | dose | Δcapacity | Δfatigue |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in cells:
        flag = " ⚠️" if c.flags else ""
        lines.append(
            f"| {c.experience} | {c.freshness} | {c.goal} | {c.workload} | {c.session}{flag} | "
            f"{c.sets} | {c.duration_min} | {c.dose_total:.2f} | {c.capacity_delta:+.3f} | "
            f"{c.fatigue_delta:+.2f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase-1 simulation matrix.")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    report = render(build_matrix())
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"[simulate_matrix] wrote {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
