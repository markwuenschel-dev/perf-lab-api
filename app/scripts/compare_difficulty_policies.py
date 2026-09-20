"""What the candidate strength difficulty policies would prescribe, and what it would cost.

Phase 3.2 evidence. For each family × difficulty this reports the prescription (sets, reps,
effort target, the load that effort RESOLVES to, rest), which dimensions the family declares
it may touch versus which the transform actually moved, and the consequences under both dose
engines plus the resulting state delta.

Nothing here is live. The point is to see where the two engines disagree about ORDERING and
why, before 3.4 decides what to promote. It is expected that v0 punishes added sets: its
density is minutes-per-set, so more work in a fixed session reads as less dose. The policies
are not contorted to make v0 happy.

Run:
    uv run python -m app.scripts.compare_difficulty_policies
    uv run python -m app.scripts.compare_difficulty_policies --out docs/simulations/phase-3-2.md
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic import dose_engine_v0 as v0
from app.logic import dose_engine_v1 as v1
from app.logic import strength_calibration as sc
from app.logic.difficulty import POLICIES, DifficultyDimension, dimensions_changed, violations
from app.logic.difficulty_strength import CANDIDATE_TRANSFORMS
from app.logic.state_update_v0 import update_athlete_state
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.schemas.workout_structure import StrengthBlock, WorkoutStructure
from app.schemas.workouts import WorkoutLog

_WHEN = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)

#: The athlete every cell is measured on — one athlete, so differences are the policy's.
E1RM_KG = 140.0
SESSION_MINUTES = 75.0

#: One baseline session per family, as its templates author them today: sets, reps and an RPE
#: target, with load left to resolution.
BASELINES: dict[str, WorkoutStructure] = {
    "strength": [
        StrengthBlock(exercise="Back Squat", sets=5, reps="5", rpe_target=8.0, rest_sec=180),
        StrengthBlock(exercise="Romanian Deadlift", sets=3, reps="8", rpe_target=8.0, rest_sec=120),
    ],
    "hypertrophy": [
        StrengthBlock(exercise="Incline Press", sets=4, reps="10", rpe_target=8.0, rest_sec=90),
        StrengthBlock(exercise="Cable Row", sets=4, reps="12", rpe_target=8.0, rest_sec=90),
    ],
    "max_strength": [
        StrengthBlock(exercise="Back Squat", sets=5, reps="3", rpe_target=8.0, rest_sec=240),
    ],
}

LEVELS = ("easy", "medium", "hard")


def _athlete() -> UnifiedStateVector:
    x = CapacityState(aerobic=300.0, max_strength=60.0, hypertrophy=50.0, work_capacity=50.0)
    f = FatigueState(cns=20.0, muscular=20.0, metabolic=20.0, structural=20.0, tendon=20.0)
    t = TissueState()
    legacy = sync_legacy_from_vectors(x, f, t)
    return UnifiedStateVector(
        timestamp=_WHEN, capacity_x=x, fatigue_f=f, tissue_t=t, s_struct_signal=0.0,
        habit_strength=0.5, skill_state={}, **legacy,
    )


def _first_int(reps: str | None) -> float:
    import re

    match = re.search(r"\d+", reps or "")
    return float(match.group()) if match else 5.0


def _resolved_load(block: StrengthBlock) -> tuple[float, float]:
    """(%e1RM, kg) the block's EFFORT target resolves to — derived, never set by a transform."""
    reps = _first_int(block.reps)
    pct = sc.percent_1rm_for_prescription(reps, block.rpe_target).value
    return pct, sc.suggested_load_kg(E1RM_KG, reps, block.rpe_target)


def _log_for(structure: WorkoutStructure) -> WorkoutLog:
    sets = sum(b.sets or 0 for b in structure if isinstance(b, StrengthBlock))
    return WorkoutLog(
        timestamp=_WHEN,
        modality="Strength",
        duration_minutes=SESSION_MINUTES,
        session_rpe=7.5,
        estimated_sets=float(sets) if sets else None,
        total_volume_load=sum(
            (b.sets or 0) * _first_int(b.reps) * _resolved_load(b)[1]
            for b in structure
            if isinstance(b, StrengthBlock)
        ),
        sleep_quality=7.0,
        life_stress_inverse=7.0,
    )


def _dose_total(engine, structure: WorkoutStructure) -> float:
    return float(sum(engine.calculate_stress_dose(_log_for(structure)).dose_six.model_dump().values()))


def _state_delta(structure: WorkoutStructure) -> tuple[float, float]:
    """(Δcapacity, Δfatigue) under the PRODUCTION engine — what v0 would record today."""
    before = _athlete()
    dose = v0.calculate_stress_dose(_log_for(structure))
    after = update_athlete_state(before, dose, timedelta(days=1), _log_for(structure))
    cap = sum(getattr(after.capacity_x, k) - getattr(before.capacity_x, k) for k in before.capacity_x.KEYS)
    fat = sum(getattr(after.fatigue_f, k) - getattr(before.fatigue_f, k) for k in before.fatigue_f.KEYS)
    return cap, fat


def _describe(structure: WorkoutStructure) -> str:
    parts = []
    for block in structure:
        if not isinstance(block, StrengthBlock):
            continue
        pct, kg = _resolved_load(block)
        effort = f"RPE {block.rpe_target:g}" if block.rpe_target is not None else "—"
        if block.rir_target is not None:
            effort = f"RIR {block.rir_target:g}"
        parts.append(
            f"{block.exercise} {block.sets}×{block.reps} @ {effort} "
            f"→ {pct * 100:.0f}% / {kg:g}kg, rest {block.rest_sec}s"
        )
    return "<br>".join(parts)


def render() -> str:
    lines = [
        "# Candidate strength difficulty policies — phase 3.2 (NOT LIVE)",
        "",
        "Generated by `app/scripts/compare_difficulty_policies.py`. Nothing here is in the",
        "prescription path: the live transform is still the legacy ±1 set rule. 3.4 decides",
        "what gets promoted, from these numbers.",
        "",
        f"One athlete throughout: e1RM {E1RM_KG:g} kg, {SESSION_MINUTES:g}-minute session, moderate",
        "fatigue. Load is DERIVED from the effort target by the existing resolution, never set",
        "by a transform — a lower RIR already means a heavier bar.",
        "",
    ]

    for family, baseline in BASELINES.items():
        policy = POLICIES[family]
        transform = CANDIDATE_TRANSFORMS[family]
        declared = sorted(
            d.value for d in DifficultyDimension if not policy.forbids(d)
        )
        lines += [
            f"## {family}",
            "",
            f"*Declared movable:* {', '.join(declared)}",
            "",
            "| level | prescription | moved | v0 dose | v1 dose | Δcapacity (v0) | Δfatigue (v0) |",
            "|---|---|---|---|---|---|---|",
        ]
        for level in LEVELS:
            after = transform.apply(baseline, level)
            moved = sorted(d.value for d in dimensions_changed(baseline, after)) or ["—"]
            cap, fat = _state_delta(after)
            lines.append(
                f"| **{level}** | {_describe(after)} | {', '.join(moved)} | "
                f"{_dose_total(v0, after):.2f} | {_dose_total(v1, after):.2f} | "
                f"{cap:+.3f} | {fat:+.2f} |"
            )
            broken = violations(baseline, after, policy)
            if broken:
                lines.append(f"| | **POLICY VIOLATION: {'; '.join(broken)}** | | | | | |")

        v0_order = [_dose_total(v0, transform.apply(baseline, level)) for level in LEVELS]
        v1_order = [_dose_total(v1, transform.apply(baseline, level)) for level in LEVELS]
        lines += [
            "",
            f"- v0 orders easy → hard as {v0_order[0]:.2f} → {v0_order[1]:.2f} → {v0_order[2]:.2f}"
            f" ({'monotonic' if v0_order[0] < v0_order[1] < v0_order[2] else 'NOT monotonic'})",
            f"- v1 orders easy → hard as {v1_order[0]:.2f} → {v1_order[1]:.2f} → {v1_order[2]:.2f}"
            f" ({'monotonic' if v1_order[0] < v1_order[1] < v1_order[2] else 'NOT monotonic'})",
            "",
        ]

    lines += [
        "## How much difficulty each engine actually registers",
        "",
        "hard ÷ easy total dose. A ratio near 1.00 means the engine barely distinguishes the",
        "two sessions at all.",
        "",
        "| family | what difficulty moves | v0 hard/easy | v1 hard/easy |",
        "|---|---|---|---|",
    ]
    for family, baseline in BASELINES.items():
        transform = CANDIDATE_TRANSFORMS[family]
        easy, hard = transform.apply(baseline, "easy"), transform.apply(baseline, "hard")
        moved = sorted(d.value for d in dimensions_changed(easy, hard))
        lines.append(
            f"| {family} | {', '.join(moved)} | "
            f"{_dose_total(v0, hard) / _dose_total(v0, easy):.2f} | "
            f"{_dose_total(v1, hard) / _dose_total(v1, easy):.2f} |"
        )
    lines += [
        "",
        "Where difficulty moves EFFORT ONLY, both engines move barely at all: the dose law",
        "weights duration 1.0 and sets 2.0 against volume-load 0.02, so a heavier bar at the",
        "same set count is nearly invisible to it. That is a calibration gap in both engines,",
        "not an argument against the policy — it is what a max-strength session IS.",
        "",
        "## Reading this",
        "",
        "Where the engines disagree about ORDER, the cause is v0's density: it is elapsed",
        "minutes per set, so adding sets to a fixed-length session lowers the recorded dose.",
        "That is the known inversion, not a property of these policies, and the policies are",
        "deliberately not contorted to satisfy it.",
        "",
        "Max strength is expected to be non-monotonic in set count by design: its hard level",
        "keeps volume and moves proximity to failure, so the load rises while the set count",
        "does not. A session can be harder with fewer total reps.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3.2 candidate difficulty comparison.")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    report = render()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"[compare_difficulty_policies] wrote {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
