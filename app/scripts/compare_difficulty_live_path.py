"""Candidate difficulty policies against the LIVE rule — phase 3.4 evidence.

Supersedes the comparison in `compare_difficulty_policies.py` (phase 3.2) for the purpose of
deciding promotion. That script applied a candidate transform to a baseline that already
carried an RPE target and never modelled what the live path does with `easy|medium|hard`. The
live rule is TWO changes from one preference, applied in two modules:

* **sets** — `LEGACY_TRANSFORM` in the prescriber (±1 working set);
* **effort** — `planning._with_intensity` shifts the periodization envelope's RPE band by
  ±0.5, and `prescription_service` resolves the load FROM that shifted cap.

Comparing a candidate against the sets half alone overstates the candidate: it reads as
"legacy only adds volume", when legacy already moves effort and re-resolves load.

Both paths here therefore run the same stages in production order:

    base template
    -> effort target        (legacy: envelope shifted by the preference
                             candidate: envelope at MEDIUM, then the family policy)
    -> load resolution      (strength_calibration, from that effort target)
    -> final structure
    -> v0 / v1 dose
    -> v0 state transition

The candidate path reads the envelope at MEDIUM deliberately. A family policy that owns
volume and effort must REPLACE the preference's envelope shift, not stack on top of it;
stacking would compare `legacy + candidate` against `legacy` and flatter the candidate.

Run:
    uv run python -m app.scripts.compare_difficulty_live_path
    uv run python -m app.scripts.compare_difficulty_live_path --out docs/simulations/phase-3-4.md
"""
from __future__ import annotations

import argparse
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.engine.state_bridge import sync_legacy_from_vectors
from app.logic import dose_engine_v0 as v0
from app.logic import dose_engine_v1 as v1
from app.logic import strength_calibration as sc
from app.logic.difficulty import LEGACY_TRANSFORM
from app.logic.difficulty_strength import CANDIDATE_TRANSFORMS
from app.logic.planning import (
    INTENSITY_MEDIUM,
    INTENSITY_RPE_CEILING,
    periodization_envelope,
)
from app.logic.state_update_v0 import update_athlete_state
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.schemas.workout_structure import StrengthBlock, WorkoutStructure
from app.schemas.workouts import WorkoutLog

_WHEN = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)

#: One athlete throughout, so every difference is the policy's.
E1RM_KG = 140.0
SESSION_MINUTES = 75.0

#: The representative block week. Week 5 of 8 on the default 4-week deload cadence lands in
#: "intensification" (band 7.5–8.5), a WORKING week whose band is not already saturated at the
#: ceiling — so a preference has room to move effort in both directions. Deload and taper
#: weeks ignore the preference entirely (`periodization_envelope`), which is why neither is
#: the right week to compare policies on.
WEEKS_TOTAL, WEEK_NUMBER, DELOAD_EVERY = 8, 5, 4

#: Baselines carry NO effort target: in the live path effort comes from the envelope, and the
#: load resolver reads one session-wide cap. Authoring an RPE here is what made the 3.2
#: comparison unable to see the legacy rule's effort half.
BASELINES: dict[str, WorkoutStructure] = {
    "strength": [
        StrengthBlock(exercise="Back Squat", sets=5, reps="5", rest_sec=180),
        StrengthBlock(exercise="Romanian Deadlift", sets=3, reps="8", rest_sec=120),
    ],
    "hypertrophy": [
        StrengthBlock(exercise="Incline Press", sets=4, reps="10", rest_sec=90),
        StrengthBlock(exercise="Cable Row", sets=4, reps="12", rest_sec=90),
    ],
    "max_strength": [
        StrengthBlock(exercise="Back Squat", sets=5, reps="3", rest_sec=240),
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
    match = re.search(r"\d+", reps or "")
    return float(match.group()) if match else 5.0


def _envelope_cap(intensity: str, *, week: int = WEEK_NUMBER, weeks: int = WEEKS_TOTAL) -> float:
    """`prescription_service._envelope_rpe_cap`: the band's high end for this preference."""
    return periodization_envelope(weeks, week, DELOAD_EVERY, intensity=intensity).rpe_high


def _at_effort(structure: WorkoutStructure, rpe: float) -> WorkoutStructure:
    """One session-wide cap onto every strength block, as the load resolver applies it."""
    return [
        block.model_copy(update={"rpe_target": rpe}) if isinstance(block, StrengthBlock) else block
        for block in structure
    ]


def legacy_path(family: str, level: str) -> WorkoutStructure:
    """Today's live rule: sets from the legacy transform, effort from the shifted envelope."""
    return _at_effort(LEGACY_TRANSFORM.apply(BASELINES[family], level), _envelope_cap(level))


def candidate_path(family: str, level: str) -> WorkoutStructure:
    """The family policy owning both dimensions: envelope read at MEDIUM, policy applied to it.

    Calling the real transform (rather than re-deriving its arithmetic) means the policy's own
    clamp is the one exercised — which, since phase 3.4, is the envelope's ceiling.
    """
    anchored = _at_effort(BASELINES[family], _envelope_cap(INTENSITY_MEDIUM))
    return CANDIDATE_TRANSFORMS[family].apply(anchored, level)


def _resolved_load(block: StrengthBlock) -> tuple[float, float]:
    reps = _first_int(block.reps)
    pct = sc.percent_1rm_for_prescription(reps, block.rpe_target).value
    return pct, sc.suggested_load_kg(E1RM_KG, reps, block.rpe_target)


def _log_for(structure: WorkoutStructure) -> WorkoutLog:
    sets = sum(b.sets or 0 for b in structure if isinstance(b, StrengthBlock))
    return WorkoutLog(
        timestamp=_WHEN, modality="Strength", duration_minutes=SESSION_MINUTES, session_rpe=7.5,
        estimated_sets=float(sets) if sets else None,
        total_volume_load=sum(
            (b.sets or 0) * _first_int(b.reps) * _resolved_load(b)[1]
            for b in structure if isinstance(b, StrengthBlock)
        ),
        sleep_quality=7.0, life_stress_inverse=7.0,
    )


def _dose_total(engine, structure: WorkoutStructure) -> float:
    log = _log_for(structure)
    return float(sum(engine.calculate_stress_dose(log).dose_six.model_dump().values()))


def _state_delta(structure: WorkoutStructure) -> tuple[float, float]:
    before = _athlete()
    log = _log_for(structure)
    after = update_athlete_state(before, v0.calculate_stress_dose(log), timedelta(days=1), log)
    cap = sum(
        getattr(after.capacity_x, k) - getattr(before.capacity_x, k)
        for k in before.capacity_x.KEYS
    )
    fat = sum(
        getattr(after.fatigue_f, k) - getattr(before.fatigue_f, k) for k in before.fatigue_f.KEYS
    )
    return cap, fat


def _totals(structure: WorkoutStructure) -> tuple[int, float, float]:
    blocks = [b for b in structure if isinstance(b, StrengthBlock)]
    sets = sum(b.sets or 0 for b in blocks)
    reps = sum((b.sets or 0) * _first_int(b.reps) for b in blocks)
    vl = sum((b.sets or 0) * _first_int(b.reps) * _resolved_load(b)[1] for b in blocks)
    return sets, reps, vl


def _describe(structure: WorkoutStructure) -> str:
    parts = []
    for block in structure:
        if not isinstance(block, StrengthBlock):
            continue
        pct, kg = _resolved_load(block)
        parts.append(
            f"{block.exercise} {block.sets}×{block.reps} @ RPE {block.rpe_target:g} "
            f"→ {pct * 100:.0f}% / {kg:g}kg"
        )
    return "<br>".join(parts)


def render() -> str:
    phase = periodization_envelope(WEEKS_TOTAL, WEEK_NUMBER, DELOAD_EVERY).phase
    lines = [
        "# Difficulty policies against the live rule — phase 3.4 (NOTHING PROMOTED)",
        "",
        "Generated by `app/scripts/compare_difficulty_live_path.py`.",
        "",
        "**This supersedes `phase-3-2.md` as promotion evidence.** That report compared each",
        "candidate against a baseline, not against the rule production actually runs. The live",
        "`easy|medium|hard` preference makes TWO changes from one setting: ±1 working set in",
        "the prescriber, AND a ±0.5 shift of the periodization envelope's RPE band, from which",
        "the load is resolved. Measuring only the first overstates every candidate.",
        "",
        f"One athlete throughout: e1RM {E1RM_KG:g} kg, {SESSION_MINUTES:g}-minute session, moderate",
        f"fatigue. Block week {WEEK_NUMBER} of {WEEKS_TOTAL} (deload every {DELOAD_EVERY}) — "
        f"phase **{phase}**, medium",
        f"cap RPE {_envelope_cap(INTENSITY_MEDIUM):g}. Load is always DERIVED from the effort "
        "target, never set by a",
        "transform.",
        "",
        "`legacy` is what production prescribes today. `candidate` is the phase-3.2 family",
        "policy REPLACING the envelope shift for the dimensions it owns — not added on top of",
        "it, which would compare `legacy + candidate` against `legacy`.",
        "",
    ]

    for family in BASELINES:
        lines += [
            f"## {family}",
            "",
            "| path | level | prescription | sets | reps | volume-load | v0 | v1 "
            "| Δcapacity (v0) | Δfatigue (v0) |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for path_name, build in (("legacy", legacy_path), ("candidate", candidate_path)):
            for level in LEVELS:
                structure = build(family, level)
                sets, reps, vl = _totals(structure)
                cap, fat = _state_delta(structure)
                lines.append(
                    f"| {path_name} | **{level}** | {_describe(structure)} | {sets} | {reps:.0f} "
                    f"| {vl:.0f} | {_dose_total(v0, structure):.2f} "
                    f"| {_dose_total(v1, structure):.2f} | {cap:+.3f} | {fat:+.2f} |"
                )

        legacy_hard, cand_hard = legacy_path(family, "hard"), candidate_path(family, "hard")
        same_sets = _totals(legacy_hard)[0] == _totals(cand_hard)[0]
        lines += [
            "",
            f"- medium is identical on both paths: {_describe(legacy_path(family, 'medium')) == _describe(candidate_path(family, 'medium'))}",
            f"- at hard the two paths prescribe the same set count: **{same_sets}**"
            + (
                " — so for this baseline the candidate reduces to a half-point of RPE"
                if same_sets
                else " — the candidate trades volume for load"
            ),
            f"- v1 reads legacy hard as {_dose_total(v1, legacy_hard):.2f} and candidate hard as "
            f"{_dose_total(v1, cand_hard):.2f}, against medium "
            f"{_dose_total(v1, legacy_path(family, 'medium')):.2f}",
            "",
        ]

    lines += [
        "## Effort target by phase, and the ceiling",
        "",
        "The envelope saturates: `INTENSITY_RPE_CEILING` bounds what any preference may reach,",
        "so by peak week the legacy band is already at the ceiling and `hard` degenerates to",
        "+1 set. Before phase 3.4 the candidate layer carried its own looser ceiling (10.0) and",
        "could prescribe a maximal triple taken to failure past that bound; it now consumes the",
        "same constant, which is what the last column shows.",
        "",
        "| phase (wk/total) | legacy easy/medium/hard | candidate easy/medium/hard |",
        "|---|---|---|",
    ]
    for week, weeks in ((2, 8), (5, 8), (7, 8)):
        label = periodization_envelope(weeks, week, DELOAD_EVERY).phase
        leg = [_envelope_cap(lv, week=week, weeks=weeks) for lv in LEVELS]
        anchored = _at_effort(
            BASELINES["max_strength"], _envelope_cap(INTENSITY_MEDIUM, week=week, weeks=weeks)
        )
        cand = [
            CANDIDATE_TRANSFORMS["max_strength"].apply(anchored, lv)[0].rpe_target
            for lv in LEVELS
        ]
        lines.append(
            f"| {label} {week}/{weeks} | {' / '.join(f'{v:g}' for v in leg)} "
            f"| {' / '.join(f'{v:g}' for v in cand)} |"
        )
    lines += [
        "",
        f"Ceiling in force on both paths: **RPE {INTENSITY_RPE_CEILING:g}**.",
        "",
        "## Decision",
        "",
        "```text",
        "Phase 3.4 decision",
        "",
        "general_strength:",
        "    dormant",
        "    reason: candidate adds little over live policy and v0 still inverts",
        "    added-volume difficulty.",
        "",
        "hypertrophy:",
        "    dormant",
        "    reason: candidate adds only another 0.5 RPE at representative",
        "    set counts; no demonstrated athlete-facing improvement, while v0",
        "    remains directionally wrong.",
        "",
        "max_strength:",
        "    dormant",
        "    reason: corrected live comparison shows legacy already changes both",
        "    effort and volume. Candidate trades meaningful volume for a small",
        "    load increase that neither dose model currently represents well.",
        "    No sufficient evidence of net improvement.",
        "",
        "cross-cutting:",
        "    preference RPE ceiling = 9.5 everywhere",
        "    effort changes must occur before load resolution exactly once",
        "",
        "promotion:",
        "    none",
        "```",
        "",
        "Phase 3 delivered the thing worth having: family-aware semantics, enforceable",
        "dimensional contracts, and evidence showing where the dose models cannot yet support",
        "better policy. Further tuning of these candidates would be optimisation against",
        "uncalibrated dose behaviour — see C1 in `docs/calibration-backlog.md`.",
        "",
        "Proximity to failure, accumulated volume and relative load are distinct programming",
        "variables rather than interchangeable routes to a single scalar \"hardness\" (Helms et",
        "al. 2020; Larsen et al. 2021; Hickmott et al. 2022; Pelland et al. 2022). The",
        "candidates remain registered, tested and dormant so a calibrated engine can be",
        "re-measured against them without rebuilding the contract.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 3.4 live-path difficulty comparison.")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    report = render()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"[compare_difficulty_live_path] wrote {args.out}")
    else:
        print(report)


if __name__ == "__main__":
    main()
