"""
Candidate-based prescription engine.

Architecture:
  1. Hard safety overrides  — identical hard stops from v1; always override.
  2. Candidate generation   — goal + state + weak points → pool of session candidates.
  3. Constraint validation  — filter candidates that would violate hard constraints.
  4. Candidate scoring      — rank by goal alignment, state needs, fatigue cost,
                              tissue cost, novelty, habit bias, template bias.
  5. Finalize               — attach explainability and provenance to winner.

The output is still a WorkoutPrescription, but its contents are assembled from
scored candidates rather than hardcoded per-goal branches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.logic.domain_vocab import GOAL_TO_DOMAIN
from app.logic.prescription_finalize import finalize_prescription
from app.schemas.prescription import WorkoutPrescription
from app.schemas.state import UnifiedStateVector
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT, TrainingGoal


# ---------------------------------------------------------------------------
# Candidate data class
# ---------------------------------------------------------------------------

@dataclass
class SessionCandidate:
    type: str
    focus: str
    rationale: str
    duration_min: int
    branch_id: str

    # Scoring axes (0–1 each, higher = better)
    goal_alignment: float = 1.0        # How directly this serves the stated goal
    state_fit: float = 1.0             # Matches current capacity/need
    fatigue_penalty: float = 0.0       # Estimated fatigue cost (penalizes)
    tissue_penalty: float = 0.0        # Estimated tissue stress (penalizes)
    novelty_bonus: float = 0.0         # Variation bonus (mild)
    habit_bonus: float = 0.0           # Adherence alignment
    template_bias: float = 0.0         # Template-guided preference

    # Weak-point coverage: fraction of active weak-point tags addressed
    weak_point_coverage: float = 0.0

    # Set True to mark as a hard-safety override — skips scoring, always wins
    is_safety_override: bool = False


# ---------------------------------------------------------------------------
# Candidate scoring
# ---------------------------------------------------------------------------

_SCORE_WEIGHTS = {
    "goal_alignment": 0.30,
    "state_fit": 0.25,
    "weak_point_coverage": 0.15,
    "fatigue_penalty": -0.15,   # negative: high penalty → lower score
    "tissue_penalty": -0.08,
    "novelty_bonus": 0.04,
    "habit_bonus": 0.03,
    "template_bias": 0.05,       # unused yet; reserved for template-guided bias
}


def _score_candidate(c: SessionCandidate) -> float:
    """Linear weighted score in [0, 1] range."""
    s = 0.0
    for axis, w in _SCORE_WEIGHTS.items():
        s += w * getattr(c, axis)
    return max(0.0, min(1.0, s))


# ---------------------------------------------------------------------------
# Fatigue / tissue readiness helpers
# ---------------------------------------------------------------------------

def _mean_fatigue(state: UnifiedStateVector) -> float:
    f = state.fatigue_f
    values = [f.cns, f.muscular, f.metabolic, f.structural, f.tendon, f.grip]
    return sum(values) / len(values)


def _tissue_max(state: UnifiedStateVector) -> float:
    t = state.tissue_t
    return max(
        t.shoulder, t.elbow, t.wrist, t.lumbar,
        t.hip, t.knee, t.ankle, t.finger,
    )


def _readiness(state: UnifiedStateVector) -> float:
    """Overall readiness 0–1 (1 = fully fresh)."""
    mf = _mean_fatigue(state)
    return max(0.0, 1.0 - mf / 100.0)


# ---------------------------------------------------------------------------
# Candidate generation per goal
# ---------------------------------------------------------------------------

def _weak_point_coverage(tags: list[str], state: UnifiedStateVector, kpi: dict) -> float:
    """
    Fraction of state-flagged capacity deficits covered by this candidate's tags.
    Simple heuristic: low capacity axes → weak point; candidate addresses it.
    """
    if not tags:
        return 0.0

    flagged: set[str] = set()
    x = state.capacity_x
    if x.aerobic < 200.0:
        flagged.add("aerobic_base")
    if x.max_strength < 40.0:
        flagged.add("hip_hinge")
        flagged.add("squat_pattern")
    if state.fatigue_f.grip > 40.0:
        flagged.add("grip")
    if x.skill < 35.0:
        flagged.add("barbell_technique")
        flagged.add("gymnastics_skill")
    if x.mobility < 35.0:
        flagged.add("hip_mobility")
        flagged.add("ankle_mobility")

    if not flagged:
        return 0.0
    hits = sum(1 for t in tags if t in flagged)
    return min(1.0, hits / len(flagged))


def _gen_strength_candidates(
    state: UnifiedStateVector,
    kpi: dict,
    recent: list[dict] | None,
) -> list[SessionCandidate]:
    x = state.capacity_x
    f = state.fatigue_f
    squat_skill = state.skill_state.get("squat", 0.0)
    habit = state.habit_strength
    readiness = _readiness(state)

    candidates = []

    # High-load max strength when ready
    candidates.append(SessionCandidate(
        type="Max Strength",
        focus="Back Squat 5×3 @ RPE 8 + Romanian Deadlift 3×5",
        rationale="Primary strength stimulus — high-tension, low-rep compound work.",
        duration_min=65,
        branch_id="strength_max",
        goal_alignment=1.0,
        state_fit=readiness * (x.max_strength / 100.0 + 0.3),
        fatigue_penalty=f.cns / 100.0,
        tissue_penalty=state.tissue_t.lumbar / 100.0 + state.tissue_t.knee / 100.0,
        weak_point_coverage=_weak_point_coverage(["squat_pattern", "hip_hinge"], state, kpi),
        habit_bonus=habit,
    ))

    # Skill acquisition for low-skill athletes
    if squat_skill < 0.55:
        candidates.append(SessionCandidate(
            type="Skill Acquisition",
            focus="Goblet Squats 3×8 (Tempo 3-1-1) + Box Squat Technique",
            rationale="Motor pattern priority — quality reps before load progression.",
            duration_min=45,
            branch_id="strength_skill_acq",
            goal_alignment=0.75,
            state_fit=0.9,
            fatigue_penalty=f.cns / 100.0 * 0.5,
            tissue_penalty=0.0,
            weak_point_coverage=_weak_point_coverage(["squat_pattern", "barbell_technique"], state, kpi),
            habit_bonus=habit,
        ))

    # Variety / adherence bias when habit is low
    if habit < 0.45:
        candidates.append(SessionCandidate(
            type="Strength — Variety",
            focus="Box Squats + Trap Bar Deadlift + Medicine Ball Slams",
            rationale="Habit strength low — enjoyable variation to sustain adherence.",
            duration_min=45,
            branch_id="strength_variety",
            goal_alignment=0.7,
            state_fit=readiness,
            fatigue_penalty=f.muscular / 100.0 * 0.5,
            tissue_penalty=state.tissue_t.lumbar / 100.0 * 0.5,
            habit_bonus=0.8,
        ))

    # Back-off volume when fatigued
    candidates.append(SessionCandidate(
        type="Strength — Volume",
        focus="Front Squat 4×6 @ RPE 6–7 + Accessory Pull",
        rationale="Volume accumulation with controlled intensity — good for fatigued states.",
        duration_min=55,
        branch_id="strength_volume",
        goal_alignment=0.8,
        state_fit=max(0.3, 1.0 - f.muscular / 100.0),
        fatigue_penalty=f.muscular / 100.0 * 0.7,
        tissue_penalty=state.tissue_t.hip / 100.0,
        habit_bonus=habit * 0.5,
    ))

    return candidates


def _gen_hypertrophy_candidates(
    state: UnifiedStateVector,
    kpi: dict,
    recent: list[dict] | None,
) -> list[SessionCandidate]:
    f = state.fatigue_f
    readiness = _readiness(state)

    return [
        SessionCandidate(
            type="High Volume Hypertrophy",
            focus="Leg Press 4×12 + Hack Squat 3×15 + Leg Curl 3×12 near failure",
            rationale="Metabolic stress and mechanical tension with high proximity to failure.",
            duration_min=75,
            branch_id="hyp_high_vol",
            goal_alignment=1.0,
            state_fit=readiness * (1.0 - f.muscular / 100.0),
            fatigue_penalty=f.muscular / 100.0,
            tissue_penalty=(state.tissue_t.knee + state.tissue_t.hip) / 200.0,
            weak_point_coverage=_weak_point_coverage(["anterior_chain", "posterior_chain"], state, kpi),
            habit_bonus=state.habit_strength,
        ),
        SessionCandidate(
            type="Maintenance Volume",
            focus="Machine Isolation 3×10 @ RPE 7 — upper / lower split",
            rationale="Residual fatigue present — accumulate volume without overreaching.",
            duration_min=45,
            branch_id="hyp_maintenance",
            goal_alignment=0.7,
            state_fit=readiness,
            fatigue_penalty=f.muscular / 100.0 * 0.4,
            tissue_penalty=0.0,
            habit_bonus=state.habit_strength * 0.5,
        ),
    ]


def _gen_power_candidates(
    state: UnifiedStateVector,
    kpi: dict,
    recent: list[dict] | None,
) -> list[SessionCandidate]:
    f = state.fatigue_f
    x = state.capacity_x
    readiness = _readiness(state)

    return [
        SessionCandidate(
            type="Power Development",
            focus="Hang Power Clean 5×3 @ RPE 6–7 + Box Jumps 4×4 (full recovery)",
            rationale="High-velocity compound work — power requires neural freshness.",
            duration_min=50,
            branch_id="power_main",
            goal_alignment=1.0,
            state_fit=readiness * (1.0 - f.cns / 100.0),
            fatigue_penalty=f.cns / 100.0,
            tissue_penalty=(state.tissue_t.knee + state.tissue_t.ankle) / 200.0,
            weak_point_coverage=_weak_point_coverage(["hip_hinge"], state, kpi),
            habit_bonus=state.habit_strength,
        ),
        SessionCandidate(
            type="Neural Priming",
            focus="Jumps / Throws (Low Volume, Long Rest) @ RPE 6",
            rationale="Brief neural exposures — maintain power quality under partial fatigue.",
            duration_min=30,
            branch_id="power_neural_prime",
            goal_alignment=0.7,
            state_fit=max(0.4, 1.0 - f.cns / 100.0),
            fatigue_penalty=f.cns / 100.0 * 0.5,
            tissue_penalty=0.0,
            habit_bonus=state.habit_strength * 0.6,
        ),
    ]


def _gen_olympic_candidates(
    state: UnifiedStateVector,
    kpi: dict,
    recent: list[dict] | None,
) -> list[SessionCandidate]:
    ratio = kpi.get("wl_snatch_cj_ratio")
    f = state.fatigue_f
    readiness = _readiness(state)
    snatch_focus = ratio is not None and ratio < 72.0

    return [
        SessionCandidate(
            type="Weightlifting Technique",
            focus=(
                "Snatch Complex + Power Snatch 5×2 @ RPE 6–7"
                if snatch_focus
                else "Clean & Jerk Drills + Hang Variations @ RPE 6–7"
            ),
            rationale=(
                f"Snatch is weak relative to C&J ({ratio:.0f}%) — extra snatch work."
                if snatch_focus
                else "Classic lifts and complexes — positions, pulls, and turnover."
            ),
            duration_min=65,
            branch_id="wl_technique_snatch" if snatch_focus else "wl_technique_cj",
            goal_alignment=1.0,
            state_fit=readiness * (1.0 - f.cns / 100.0),
            fatigue_penalty=f.cns / 100.0,
            tissue_penalty=state.tissue_t.wrist / 100.0 + state.tissue_t.shoulder / 100.0,
            weak_point_coverage=_weak_point_coverage(["olympic_lifting"], state, kpi),
            habit_bonus=state.habit_strength,
        ),
        SessionCandidate(
            type="Strength Pulls",
            focus="Snatch Pull + Deadlift from Deficit 4×4 @ RPE 7",
            rationale="Posterior chain strength and pull off the floor — direct carryover.",
            duration_min=55,
            branch_id="wl_strength_pulls",
            goal_alignment=0.75,
            state_fit=readiness,
            fatigue_penalty=(f.muscular + f.cns * 0.5) / 150.0,
            tissue_penalty=state.tissue_t.lumbar / 100.0,
            weak_point_coverage=_weak_point_coverage(["hip_hinge", "posterior_chain"], state, kpi),
            habit_bonus=state.habit_strength * 0.5,
        ),
    ]


def _gen_powerlifting_candidates(
    state: UnifiedStateVector,
    kpi: dict,
    recent: list[dict] | None,
) -> list[SessionCandidate]:
    rel = kpi.get("pl_relative_total")
    f = state.fatigue_f
    readiness = _readiness(state)
    volume_bias = rel is not None and rel < 3.0

    return [
        SessionCandidate(
            type="SBD Strength",
            focus="Squat / Bench / Deadlift — top sets + 3–4 back-off sets",
            rationale=(
                "Quality reps before intensity ramp" if volume_bias
                else "Competition lift specificity with managed autoregulation."
            ),
            duration_min=80,
            branch_id="pl_sbd_main",
            goal_alignment=1.0,
            state_fit=readiness * (1.0 - f.cns / 100.0 * 0.5),
            fatigue_penalty=(f.cns + f.structural * 0.5) / 150.0,
            tissue_penalty=(state.tissue_t.lumbar + state.tissue_t.knee) / 200.0,
            weak_point_coverage=_weak_point_coverage(["squat_pattern", "hip_hinge", "push_horizontal"], state, kpi),
            habit_bonus=state.habit_strength,
        ),
        SessionCandidate(
            type="Accessory Focus",
            focus="Paused Squat 3×4 + Close-Grip Bench + Romanian Deadlift 3×6",
            rationale="Technical variations and accessory volume to address weak points.",
            duration_min=65,
            branch_id="pl_accessory",
            goal_alignment=0.8,
            state_fit=readiness,
            fatigue_penalty=f.muscular / 100.0 * 0.6,
            tissue_penalty=state.tissue_t.lumbar / 100.0 * 0.5,
            weak_point_coverage=_weak_point_coverage(["squat_pattern", "push_horizontal", "hip_hinge"], state, kpi),
            habit_bonus=state.habit_strength * 0.7,
        ),
    ]


def _gen_metcon_candidates(
    state: UnifiedStateVector,
    kpi: dict,
    recent: list[dict] | None,
) -> list[SessionCandidate]:
    f = state.fatigue_f
    x = state.capacity_x
    readiness = _readiness(state)

    return [
        SessionCandidate(
            type="Metabolic Conditioning",
            focus="Row / Bike / KB Swings — AMRAP intervals @ sustainable pace",
            rationale="Work capacity and glycolytic tolerance — mixed-modal structured intervals.",
            duration_min=40,
            branch_id="metcon_mixed_modal",
            goal_alignment=1.0,
            state_fit=readiness,
            fatigue_penalty=f.metabolic / 100.0,
            tissue_penalty=state.tissue_t.knee / 100.0 * 0.5,
            weak_point_coverage=_weak_point_coverage(["work_capacity", "aerobic_base"], state, kpi),
            habit_bonus=state.habit_strength,
        ),
        SessionCandidate(
            type="Engine Work",
            focus="Zone 2 Bike 20 min + Short Threshold Intervals (4×2 min @ RPE 8)",
            rationale="Base aerobic + lactate threshold dual stimulus.",
            duration_min=45,
            branch_id="metcon_engine",
            goal_alignment=0.8,
            state_fit=1.0 - f.metabolic / 100.0,
            fatigue_penalty=f.metabolic / 100.0 * 0.8,
            tissue_penalty=0.0,
            weak_point_coverage=_weak_point_coverage(["aerobic_base", "lactate_threshold"], state, kpi),
            habit_bonus=state.habit_strength * 0.6,
        ),
    ]


def _gen_running_candidates(
    state: UnifiedStateVector,
    kpi: dict,
    recent: list[dict] | None,
    goal: TrainingGoal,
) -> list[SessionCandidate]:
    ff = kpi.get("run_fatigue_factor")
    f = state.fatigue_f
    x = state.capacity_x
    readiness = _readiness(state)
    threshold_priority = ff is not None and ff > 14.0

    base_cands = [
        SessionCandidate(
            type="Aerobic Base",
            focus="Easy–Moderate Run @ Zone 2 (conversational pace)",
            rationale=(
                "Threshold durability priority — moderate effort over pure easy volume."
                if threshold_priority
                else "Cardiac output and mitochondrial density via sustained easy effort."
            ),
            duration_min=45,
            branch_id="run_z2_base",
            goal_alignment=1.0,
            state_fit=readiness,
            fatigue_penalty=f.structural / 100.0 * 0.5 + f.tendon / 100.0 * 0.5,
            tissue_penalty=(state.tissue_t.ankle + state.tissue_t.knee) / 200.0,
            weak_point_coverage=_weak_point_coverage(["aerobic_base", "running_economy"], state, kpi),
            habit_bonus=state.habit_strength,
        ),
    ]

    if goal in ("HalfMarathon", "FullMarathon") or threshold_priority:
        base_cands.append(SessionCandidate(
            type="Threshold Work",
            focus=(
                "Tempo Run 20 min @ RPE 7–8 + Progression Miles"
                if goal in ("HalfMarathon", "FullMarathon")
                else "4×5 min @ threshold pace (RPE 8) / 2 min easy recovery"
            ),
            rationale="Threshold pace improves fractional utilization of VO2max.",
            duration_min=50,
            branch_id="run_threshold",
            goal_alignment=0.9,
            state_fit=readiness * 0.9,
            fatigue_penalty=(f.structural + f.tendon) / 200.0,
            tissue_penalty=(state.tissue_t.ankle + state.tissue_t.knee) / 200.0,
            weak_point_coverage=_weak_point_coverage(["lactate_threshold", "aerobic_base"], state, kpi),
            habit_bonus=state.habit_strength * 0.7,
        ))

    if goal == "Sprinting":
        base_cands = [SessionCandidate(
            type="Speed",
            focus="Acceleration 3×30 m + Max-Velocity Flys 4×20 m (full recovery)",
            rationale="Short high-quality sprints — neural freshness required.",
            duration_min=35,
            branch_id="run_sprint",
            goal_alignment=1.0,
            state_fit=readiness * (1.0 - f.cns / 100.0),
            fatigue_penalty=f.cns / 100.0,
            tissue_penalty=(state.tissue_t.ankle + state.tissue_t.hip) / 200.0,
            weak_point_coverage=_weak_point_coverage(["running_economy"], state, kpi),
            habit_bonus=state.habit_strength,
        )]

    return base_cands


def _gen_gymnastics_candidates(
    state: UnifiedStateVector,
    kpi: dict,
    recent: list[dict] | None,
    goal: TrainingGoal,
) -> list[SessionCandidate]:
    f = state.fatigue_f
    readiness = _readiness(state)

    cands = [
        SessionCandidate(
            type="Gymnastics Skill",
            focus="Handstand Progressions + Ring Support Hold + Shaping Drills",
            rationale="Skill and straight-arm strength — quality reps, protect wrists and shoulders.",
            duration_min=55,
            branch_id="gym_skill",
            goal_alignment=1.0,
            state_fit=readiness * (1.0 - f.cns / 100.0),
            fatigue_penalty=f.cns / 100.0,
            tissue_penalty=(state.tissue_t.wrist + state.tissue_t.shoulder + state.tissue_t.elbow) / 300.0,
            weak_point_coverage=_weak_point_coverage(["gymnastics_skill", "overhead_stability"], state, kpi),
            habit_bonus=state.habit_strength,
        ),
    ]

    if goal == "Calisthenics":
        cands.append(SessionCandidate(
            type="Bodyweight Strength",
            focus="Pull-ups / Dips / Push-up Variations + Skill Progressions",
            rationale="Horizontal and vertical pressing/pulling patterns + straight-arm strength.",
            duration_min=50,
            branch_id="cal_strength",
            goal_alignment=1.0,
            state_fit=readiness,
            fatigue_penalty=(f.cns + f.grip * 0.5) / 150.0,
            tissue_penalty=(state.tissue_t.shoulder + state.tissue_t.elbow) / 200.0,
            weak_point_coverage=_weak_point_coverage(["pull_vertical", "push_vertical"], state, kpi),
            habit_bonus=state.habit_strength,
        ))

    return cands


def _gen_grip_candidates(
    state: UnifiedStateVector,
    kpi: dict,
    recent: list[dict] | None,
) -> list[SessionCandidate]:
    f = state.fatigue_f
    readiness = _readiness(state)

    return [
        SessionCandidate(
            type="Grip & Support",
            focus="Farmer Carries + Dead Hangs + Pinch Block Hold + Crush Work @ RPE 7–8",
            rationale="Crush, support, and finger flexors with structured volume.",
            duration_min=35,
            branch_id="grip_main",
            goal_alignment=1.0,
            state_fit=readiness * (1.0 - f.grip / 100.0),
            fatigue_penalty=f.grip / 100.0,
            tissue_penalty=(state.tissue_t.finger + state.tissue_t.elbow) / 200.0,
            weak_point_coverage=_weak_point_coverage(["grip"], state, kpi),
            habit_bonus=state.habit_strength,
        ),
        SessionCandidate(
            type="Grip Recovery",
            focus="Light Wrist Mobility + Finger Flexor Rehab Circles",
            rationale="Active tissue care when grip fatigue is elevated.",
            duration_min=20,
            branch_id="grip_recovery",
            goal_alignment=0.5,
            state_fit=1.0 - f.grip / 100.0 + 0.3,
            fatigue_penalty=0.0,
            tissue_penalty=0.0,
            habit_bonus=0.4,
        ),
    ]


def _gen_general_candidates(
    state: UnifiedStateVector,
    kpi: dict,
    recent: list[dict] | None,
) -> list[SessionCandidate]:
    f = state.fatigue_f
    readiness = _readiness(state)

    return [
        SessionCandidate(
            type="General Physical Prep",
            focus="Full-Body Circuit @ RPE 6–7 — Squat / Pull / Push / Carry",
            rationale="Balanced GPP — no critical constraints, no specific goal.",
            duration_min=45,
            branch_id="gpp_balanced",
            goal_alignment=0.9,
            state_fit=readiness,
            fatigue_penalty=_mean_fatigue(state) / 100.0 * 0.5,
            tissue_penalty=_tissue_max(state) / 100.0 * 0.3,
            habit_bonus=state.habit_strength,
        ),
    ]


# ---------------------------------------------------------------------------
# Safety override candidates (always placed first; skip scoring)
# ---------------------------------------------------------------------------

def _safety_candidates(state: UnifiedStateVector) -> list[SessionCandidate]:
    """Generate hard-stop recovery candidates. Return empty if no safety triggered."""
    overrides: list[SessionCandidate] = []

    if state.tissue_t.lumbar > 65.0 or state.tissue_t.knee > 70.0:
        overrides.append(SessionCandidate(
            type="Recovery",
            focus="Low-Impact Mobility + Swim / Bike Easy",
            rationale=(
                f"Regional tissue stress elevated (lumbar {state.tissue_t.lumbar:.0f}, "
                f"knee {state.tissue_t.knee:.0f}). Deload axial and knee-dominant loading."
            ),
            duration_min=30,
            branch_id="safety_regional_tissue",
            is_safety_override=True,
        ))

    if state.fatigue_f.tendon > 55.0 or state.fatigue_f.structural > 65.0:
        overrides.append(SessionCandidate(
            type="Tissue Deload",
            focus="Isometrics + Blood-Flow Circuits",
            rationale=(
                f"Tendon / structural fatigue high (tendon {state.fatigue_f.tendon:.0f}, "
                f"structural {state.fatigue_f.structural:.0f}). Reduce plyometrics and eccentrics."
            ),
            duration_min=35,
            branch_id="safety_tendon_structural",
            is_safety_override=True,
        ))

    if state.f_struct_damage > 70.0:
        overrides.append(SessionCandidate(
            type="Recovery",
            focus="Mobility / Light Movement",
            rationale=(
                f"Structural fatigue critical ({state.f_struct_damage:.1f}%). "
                "Training now would increase injury risk."
            ),
            duration_min=20,
            branch_id="safety_structural_damage",
            is_safety_override=True,
        ))

    if state.f_met_systemic > 80.0:
        overrides.append(SessionCandidate(
            type="Recovery",
            focus="Passive Rest / Sleep / Nutrition",
            rationale=(
                f"Systemic fatigue very high ({state.f_met_systemic:.1f}%). "
                "Autonomic recovery required before loading again."
            ),
            duration_min=0,
            branch_id="safety_systemic_metabolic",
            is_safety_override=True,
        ))

    return overrides


def _readiness_redirect(
    state: UnifiedStateVector,
    goal: TrainingGoal,
    kpi: dict,
) -> list[SessionCandidate]:
    """
    Soft readiness shifts — not hard stops, but significant fatigue redirects.
    These go into the candidate pool with high state_fit so they tend to win
    when relevant, but they can be overridden by a better-fit candidate.
    """
    redirects: list[SessionCandidate] = []

    if state.f_nm_central > 60.0:
        if state.c_met_aerobic > 0:
            redirects.append(SessionCandidate(
                type="Metabolic Conditioning",
                focus="Zone 2 Cardio (Bike / Row) @ RPE 4–5",
                rationale=(
                    f"CNS fatigue high ({state.f_nm_central:.1f}%). "
                    "Shifting stress toward aerobic system with low neural load."
                ),
                duration_min=45,
                branch_id="readiness_cns_aerobic_shift",
                goal_alignment=0.6,
                state_fit=1.0,
                fatigue_penalty=0.1,
                tissue_penalty=0.0,
            ))
        else:
            redirects.append(SessionCandidate(
                type="Technique / Flow",
                focus="Movement Drills <50% Intensity",
                rationale=(
                    f"CNS fatigue high ({state.f_nm_central:.1f}%). "
                    "Motor patterns without heavy loading."
                ),
                duration_min=30,
                branch_id="readiness_cns_technique",
                goal_alignment=0.5,
                state_fit=1.0,
                fatigue_penalty=0.05,
                tissue_penalty=0.0,
            ))

    if state.f_nm_peripheral > 60.0:
        if goal in ("Power", "OlympicLifts", "Sprinting"):
            redirects.append(SessionCandidate(
                type="Neural Priming",
                focus="Jumps / Throws (Low Volume, Long Rest)",
                rationale=(
                    f"Peripheral fatigue elevated ({state.f_nm_peripheral:.1f}%), "
                    "but CNS available — brief neural exposures only."
                ),
                duration_min=30,
                branch_id="readiness_peripheral_neural_priming",
                goal_alignment=0.65,
                state_fit=0.9,
                fatigue_penalty=0.1,
                tissue_penalty=0.0,
            ))
        else:
            redirects.append(SessionCandidate(
                type="Active Recovery",
                focus="Walking / Light Sled Drag",
                rationale=(
                    f"Local muscular fatigue high ({state.f_nm_peripheral:.1f}%). "
                    "Low-intensity movement to promote clearance."
                ),
                duration_min=30,
                branch_id="readiness_peripheral_active_recovery",
                goal_alignment=0.5,
                state_fit=1.0,
                fatigue_penalty=0.05,
                tissue_penalty=0.0,
            ))

    return redirects


# ---------------------------------------------------------------------------
# Goal → candidate generator dispatch
# ---------------------------------------------------------------------------

def _generate_candidates(
    state: UnifiedStateVector,
    goal: TrainingGoal,
    kpi: dict,
    recent: list[dict] | None,
) -> list[SessionCandidate]:
    if goal == "Strength":
        return _gen_strength_candidates(state, kpi, recent)
    if goal == "Hypertrophy":
        return _gen_hypertrophy_candidates(state, kpi, recent)
    if goal == "Power":
        return _gen_power_candidates(state, kpi, recent)
    if goal == "OlympicLifts":
        return _gen_olympic_candidates(state, kpi, recent)
    if goal == "Powerlifting":
        return _gen_powerlifting_candidates(state, kpi, recent)
    if goal == "MetCon":
        return _gen_metcon_candidates(state, kpi, recent)
    if goal in ("Running", "Sprinting", "HalfMarathon", "FullMarathon"):
        return _gen_running_candidates(state, kpi, recent, goal)
    if goal in ("Gymnastics", "Calisthenics"):
        return _gen_gymnastics_candidates(state, kpi, recent, goal)
    if goal == "Grip":
        return _gen_grip_candidates(state, kpi, recent)
    return _gen_general_candidates(state, kpi, recent)


# ---------------------------------------------------------------------------
# Primary entry point
# ---------------------------------------------------------------------------

def _finalize(
    candidate: SessionCandidate,
    state: UnifiedStateVector,
    goal: TrainingGoal,
    recent_sessions: list[dict] | None,
) -> WorkoutPrescription:
    rx = WorkoutPrescription(
        type=candidate.type,
        focus=candidate.focus,
        rationale=candidate.rationale,
        duration_min=candidate.duration_min,
    )
    return finalize_prescription(rx, state, goal, candidate.branch_id, recent_sessions=recent_sessions)


def recommend_next_session(
    state: UnifiedStateVector,
    goal: TrainingGoal = TRAINING_GOAL_DEFAULT,
    recent_sessions: list[dict] | None = None,
    kpi_summary: dict[str, float] | None = None,
) -> WorkoutPrescription:
    """
    Candidate-based controller.

    Builds a pool of session candidates for the given goal and state, scores
    them, and returns the best valid candidate. Hard safety overrides always
    take priority.

    `kpi_summary` holds latest derived dashboard metrics (codes → values).
    These are soft signals: state vectors are the primary controller.
    """
    kpi = kpi_summary or {}

    # --- 1. Hard safety overrides (always override scoring) ---
    safety = _safety_candidates(state)
    if safety:
        return _finalize(safety[0], state, goal, recent_sessions)

    # --- 2. Build candidate pool: goal-specific + readiness redirects ---
    goal_candidates = _generate_candidates(state, goal, kpi, recent_sessions)
    redirects = _readiness_redirect(state, goal, kpi)

    all_candidates = redirects + goal_candidates   # redirects evaluated first but scored alongside

    # --- 3. Score and sort (no hard filtering needed here; finalize handles constraint override) ---
    scored = sorted(all_candidates, key=_score_candidate, reverse=True)

    if not scored:
        # Fallback — should not happen unless generator returns empty
        scored = _gen_general_candidates(state, kpi, recent_sessions)

    # --- 4. Return best candidate (finalize adds explainability + hard-constraint override) ---
    return _finalize(scored[0], state, goal, recent_sessions)
