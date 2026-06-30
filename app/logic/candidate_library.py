"""Static session content library — what sessions exist per goal (no state dependency).

Separation of concerns
----------------------
This module owns all *static* knowledge: what sessions exist per goal, their
fixed content (type, focus, rationale, branch_id, duration_min,
goal_alignment, tags) and the three optional eligibility predicates that
decide whether a template is active at generation time.

Dynamic scoring (state_fit, fatigue_penalty, tissue_penalty, etc.) lives in
``score_template()`` which converts a CandidateTemplate into a scored
SessionCandidate given the current state vector and KPI dict.

The ``_weak_point_coverage`` helper was moved here from prescriber.py; a
thin re-export alias is kept there for backwards compatibility if needed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.logic.constraint_engine.candidate import (
    SessionCandidate,
    max_tissue_load,
    mean_fatigue,
    overall_readiness,
)
from app.schemas.state import UnifiedStateVector

# ---------------------------------------------------------------------------
# CandidateTemplate — static content unit
# ---------------------------------------------------------------------------

@dataclass
class CandidateTemplate:
    """
    A static description of one possible session type within a goal domain.

    Fields
    ------
    type / focus / rationale / branch_id / duration_min / goal_alignment :
        Descriptive fields — no state dependency.
    tags :
        Movement/capacity tags used by _weak_point_coverage to match against
        flagged athlete deficits.
    domain :
        Used by score_template() to dispatch to the right per-domain scorer.
    kpi_eligible :
        Predicate over the KPI dict.  ``None`` → always eligible.
    state_eligible :
        Predicate over UnifiedStateVector.  ``None`` → always eligible.
    goal_eligible :
        Predicate over the goal string.  ``None`` → always eligible.
    """

    type: str
    focus: str
    rationale: str
    branch_id: str
    duration_min: int
    goal_alignment: float
    tags: list[str] = field(default_factory=list)
    domain: str = ""
    kpi_eligible: Callable[[dict[str, float]], bool] | None = None
    state_eligible: Callable[[UnifiedStateVector], bool] | None = None
    goal_eligible: Callable[[str], bool] | None = None


# ---------------------------------------------------------------------------
# Weak-point coverage (moved from prescriber.py)
# ---------------------------------------------------------------------------

def _weak_point_coverage(
    tags: list[str],
    state: UnifiedStateVector,
    kpi: dict[str, float],
) -> float:
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


# ---------------------------------------------------------------------------
# Template lists — static content per domain
# ---------------------------------------------------------------------------

STRENGTH_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type="Max Strength",
        focus="Back Squat 5×3 @ RPE 8 + Romanian Deadlift 3×5",
        rationale="Primary strength stimulus — high-tension, low-rep compound work.",
        branch_id="strength_max",
        duration_min=65,
        goal_alignment=1.0,
        tags=["squat_pattern", "hip_hinge"],
        domain="strength",
    ),
    CandidateTemplate(
        type="Skill Acquisition",
        focus="Goblet Squats 3×8 (Tempo 3-1-1) + Box Squat Technique",
        rationale="Motor pattern priority — quality reps before load progression.",
        branch_id="strength_skill_acq",
        duration_min=45,
        goal_alignment=0.75,
        tags=["squat_pattern", "barbell_technique"],
        domain="strength",
        state_eligible=lambda s: s.skill_state.get("squat", 0.0) < 0.55,
    ),
    CandidateTemplate(
        type="Strength — Variety",
        focus="Box Squats + Trap Bar Deadlift + Medicine Ball Slams",
        rationale="Habit strength low — enjoyable variation to sustain adherence.",
        branch_id="strength_variety",
        duration_min=45,
        goal_alignment=0.7,
        tags=[],
        domain="strength",
        state_eligible=lambda s: s.habit_strength < 0.45,
    ),
    CandidateTemplate(
        type="Strength — Volume",
        focus="Front Squat 4×6 @ RPE 6–7 + Accessory Pull",
        rationale="Volume accumulation with controlled intensity — good for fatigued states.",
        branch_id="strength_volume",
        duration_min=55,
        goal_alignment=0.8,
        tags=[],
        domain="strength",
    ),
]

HYPERTROPHY_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type="High Volume Hypertrophy",
        focus="Leg Press 4×12 + Hack Squat 3×15 + Leg Curl 3×12 near failure",
        rationale="Metabolic stress and mechanical tension with high proximity to failure.",
        branch_id="hyp_high_vol",
        duration_min=75,
        goal_alignment=1.0,
        tags=["anterior_chain", "posterior_chain"],
        domain="hypertrophy",
    ),
    CandidateTemplate(
        type="Maintenance Volume",
        focus="Machine Isolation 3×10 @ RPE 7 — upper / lower split",
        rationale="Residual fatigue present — accumulate volume without overreaching.",
        branch_id="hyp_maintenance",
        duration_min=45,
        goal_alignment=0.7,
        tags=[],
        domain="hypertrophy",
    ),
]

POWER_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type="Power Development",
        focus="Hang Power Clean 5×3 @ RPE 6–7 + Box Jumps 4×4 (full recovery)",
        rationale="High-velocity compound work — power requires neural freshness.",
        branch_id="power_main",
        duration_min=50,
        goal_alignment=1.0,
        tags=["hip_hinge"],
        domain="power",
    ),
    CandidateTemplate(
        type="Neural Priming",
        focus="Jumps / Throws (Low Volume, Long Rest) @ RPE 6",
        rationale="Brief neural exposures — maintain power quality under partial fatigue.",
        branch_id="power_neural_prime",
        duration_min=30,
        goal_alignment=0.7,
        tags=[],
        domain="power",
    ),
]

# Two technique variants: kpi_eligible disambiguates snatch vs C&J focus.
OLYMPIC_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type="Weightlifting Technique",
        focus="Snatch Complex + Power Snatch 5×2 @ RPE 6–7",
        rationale="Snatch is weak relative to C&J — extra snatch volume and technique.",
        branch_id="wl_technique_snatch",
        duration_min=65,
        goal_alignment=1.0,
        tags=["olympic_lifting"],
        domain="weightlifting",
        kpi_eligible=lambda kpi: (
            kpi.get("wl_snatch_cj_ratio") is not None
            and kpi["wl_snatch_cj_ratio"] < 72.0
        ),
    ),
    CandidateTemplate(
        type="Weightlifting Technique",
        focus="Clean & Jerk Drills + Hang Variations @ RPE 6–7",
        rationale="Classic lifts and complexes — positions, pulls, and turnover.",
        branch_id="wl_technique_cj",
        duration_min=65,
        goal_alignment=1.0,
        tags=["olympic_lifting"],
        domain="weightlifting",
        kpi_eligible=lambda kpi: not (
            kpi.get("wl_snatch_cj_ratio") is not None
            and kpi["wl_snatch_cj_ratio"] < 72.0
        ),
    ),
    CandidateTemplate(
        type="Strength Pulls",
        focus="Snatch Pull + Deadlift from Deficit 4×4 @ RPE 7",
        rationale="Posterior chain strength and pull off the floor — direct carryover.",
        branch_id="wl_strength_pulls",
        duration_min=55,
        goal_alignment=0.75,
        tags=["hip_hinge", "posterior_chain"],
        domain="weightlifting",
    ),
]

# Two SBD variants: volume-bias rationale when relative total is low.
POWERLIFTING_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type="SBD Strength",
        focus="Squat / Bench / Deadlift — top sets + 3–4 back-off sets",
        rationale="Quality reps before intensity ramp.",
        branch_id="pl_sbd_main_volume",
        duration_min=80,
        goal_alignment=1.0,
        tags=["squat_pattern", "hip_hinge", "push_horizontal"],
        domain="powerlifting",
        kpi_eligible=lambda kpi: (
            kpi.get("pl_relative_total") is not None
            and kpi["pl_relative_total"] < 3.0
        ),
    ),
    CandidateTemplate(
        type="SBD Strength",
        focus="Squat / Bench / Deadlift — top sets + 3–4 back-off sets",
        rationale="Competition lift specificity with managed autoregulation.",
        branch_id="pl_sbd_main",
        duration_min=80,
        goal_alignment=1.0,
        tags=["squat_pattern", "hip_hinge", "push_horizontal"],
        domain="powerlifting",
        kpi_eligible=lambda kpi: not (
            kpi.get("pl_relative_total") is not None
            and kpi["pl_relative_total"] < 3.0
        ),
    ),
    CandidateTemplate(
        type="Accessory Focus",
        focus="Paused Squat 3×4 + Close-Grip Bench + Romanian Deadlift 3×6",
        rationale="Technical variations and accessory volume to address weak points.",
        branch_id="pl_accessory",
        duration_min=65,
        goal_alignment=0.8,
        tags=["squat_pattern", "push_horizontal", "hip_hinge"],
        domain="powerlifting",
    ),
]

METCON_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type="Metabolic Conditioning",
        focus="Row / Bike / KB Swings — AMRAP intervals @ sustainable pace",
        rationale="Work capacity and glycolytic tolerance — mixed-modal structured intervals.",
        branch_id="metcon_mixed_modal",
        duration_min=40,
        goal_alignment=1.0,
        tags=["work_capacity", "aerobic_base"],
        domain="mixed",
    ),
    CandidateTemplate(
        type="Engine Work",
        focus="Zone 2 Bike 20 min + Short Threshold Intervals (4×2 min @ RPE 8)",
        rationale="Base aerobic + lactate threshold dual stimulus.",
        branch_id="metcon_engine",
        duration_min=45,
        goal_alignment=0.8,
        tags=["aerobic_base", "lactate_threshold"],
        domain="mixed",
    ),
]

# Mixed = MetCon pool plus the strength-endurance side for concurrent blocks.
MIXED_TEMPLATES: list[CandidateTemplate] = [
    *METCON_TEMPLATES,
    CandidateTemplate(
        type="Strength Endurance",
        focus="Compound lifts in circuit — moderate load, short rest (e.g. 5×8 @ RPE 7)",
        rationale="The strength side of concurrent work — strength expressed under fatigue.",
        branch_id="mixed_strength_endurance",
        duration_min=45,
        goal_alignment=0.9,
        tags=["work_capacity", "max_strength"],
        domain="mixed",
    ),
]

# Running base templates: two aerobic-base variants (threshold vs standard)
# and two threshold-work variants (marathon goal vs high fatigue-factor).
RUNNING_BASE_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type="Aerobic Base",
        focus="Easy–Moderate Run @ Zone 2 (conversational pace)",
        rationale="Threshold durability priority — moderate effort over pure easy volume.",
        branch_id="run_z2_base_threshold",
        duration_min=45,
        goal_alignment=1.0,
        tags=["aerobic_base", "running_economy"],
        domain="running",
        kpi_eligible=lambda kpi: (kpi.get("run_fatigue_factor") or 0.0) > 14.0,
    ),
    CandidateTemplate(
        type="Aerobic Base",
        focus="Easy–Moderate Run @ Zone 2 (conversational pace)",
        rationale="Cardiac output and mitochondrial density via sustained easy effort.",
        branch_id="run_z2_base",
        duration_min=45,
        goal_alignment=1.0,
        tags=["aerobic_base", "running_economy"],
        domain="running",
        kpi_eligible=lambda kpi: not ((kpi.get("run_fatigue_factor") or 0.0) > 14.0),
    ),
    CandidateTemplate(
        type="Threshold Work",
        focus="Tempo Run 20 min @ RPE 7–8 + Progression Miles",
        rationale="Threshold pace improves fractional utilization of VO2max.",
        branch_id="run_threshold",
        duration_min=50,
        goal_alignment=0.9,
        tags=["lactate_threshold", "aerobic_base"],
        domain="running",
        goal_eligible=lambda g: g in ("HalfMarathon", "FullMarathon"),
    ),
    CandidateTemplate(
        type="Threshold Work",
        focus="4×5 min @ threshold pace (RPE 8) / 2 min easy recovery",
        rationale="Threshold pace improves fractional utilization of VO2max.",
        branch_id="run_threshold_ff",
        duration_min=50,
        goal_alignment=0.9,
        tags=["lactate_threshold", "aerobic_base"],
        domain="running",
        kpi_eligible=lambda kpi: (kpi.get("run_fatigue_factor") or 0.0) > 14.0,
        goal_eligible=lambda g: g not in ("HalfMarathon", "FullMarathon"),
    ),
]

SPRINTING_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type="Speed",
        focus="Acceleration 3×30 m + Max-Velocity Flys 4×20 m (full recovery)",
        rationale="Short high-quality sprints — neural freshness required.",
        branch_id="run_sprint",
        duration_min=35,
        goal_alignment=1.0,
        tags=["running_economy"],
        domain="running",
    ),
]

GYMNASTICS_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type="Gymnastics Skill",
        focus="Handstand Progressions + Ring Support Hold + Shaping Drills",
        rationale="Skill and straight-arm strength — quality reps, protect wrists and shoulders.",
        branch_id="gym_skill",
        duration_min=55,
        goal_alignment=1.0,
        tags=["gymnastics_skill", "overhead_stability"],
        domain="gymnastics",
    ),
]

CALISTHENICS_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type="Gymnastics Skill",
        focus="Handstand Progressions + Ring Support Hold + Shaping Drills",
        rationale="Skill and straight-arm strength — quality reps, protect wrists and shoulders.",
        branch_id="gym_skill",
        duration_min=55,
        goal_alignment=1.0,
        tags=["gymnastics_skill", "overhead_stability"],
        domain="calisthenics",
    ),
    CandidateTemplate(
        type="Bodyweight Strength",
        focus="Pull-ups / Dips / Push-up Variations + Skill Progressions",
        rationale="Horizontal and vertical pressing/pulling patterns + straight-arm strength.",
        branch_id="cal_strength",
        duration_min=50,
        goal_alignment=1.0,
        tags=["pull_vertical", "push_vertical"],
        domain="calisthenics",
    ),
]

GRIP_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type="Grip & Support",
        focus="Farmer Carries + Dead Hangs + Pinch Block Hold + Crush Work @ RPE 7–8",
        rationale="Crush, support, and finger flexors with structured volume.",
        branch_id="grip_main",
        duration_min=35,
        goal_alignment=1.0,
        tags=["grip"],
        domain="grip",
    ),
    CandidateTemplate(
        type="Grip Recovery",
        focus="Light Wrist Mobility + Finger Flexor Rehab Circles",
        rationale="Active tissue care when grip fatigue is elevated.",
        branch_id="grip_recovery",
        duration_min=20,
        goal_alignment=0.5,
        tags=[],
        domain="grip",
    ),
]

GENERAL_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type="General Physical Prep",
        focus="Full-Body Circuit @ RPE 6–7 — Squat / Pull / Push / Carry",
        rationale="Balanced GPP — no critical constraints, no specific goal.",
        branch_id="gpp_balanced",
        duration_min=45,
        goal_alignment=0.9,
        tags=[],
        domain="general",
    ),
]


# ---------------------------------------------------------------------------
# Library index
# ---------------------------------------------------------------------------

GOAL_TEMPLATE_LIBRARY: dict[str, list[CandidateTemplate]] = {
    "strength": STRENGTH_TEMPLATES,
    "hypertrophy": HYPERTROPHY_TEMPLATES,
    "power": POWER_TEMPLATES,
    "weightlifting": OLYMPIC_TEMPLATES,
    "powerlifting": POWERLIFTING_TEMPLATES,
    "mixed": MIXED_TEMPLATES,
    "running": RUNNING_BASE_TEMPLATES,
    "sprinting": SPRINTING_TEMPLATES,
    "gymnastics": GYMNASTICS_TEMPLATES,
    "calisthenics": CALISTHENICS_TEMPLATES,
    "grip": GRIP_TEMPLATES,
    "general": GENERAL_TEMPLATES,
}


def get_templates(
    domain: str,
    kpi: dict[str, float],
    goal: str = "",
    state: UnifiedStateVector | None = None,
) -> list[CandidateTemplate]:
    """Return templates for the domain, filtered by all eligibility predicates.

    Sprinting is a sub-domain of running and resolves to its own pool.
    When ``state`` is None, state_eligible predicates are skipped (treated
    as eligible), so callers that do not yet have state can still query the
    static content.
    """
    if domain == "running" and goal == "Sprinting":
        pool = SPRINTING_TEMPLATES
    else:
        pool = GOAL_TEMPLATE_LIBRARY.get(domain, GENERAL_TEMPLATES)

    return [
        t for t in pool
        if (t.kpi_eligible is None or t.kpi_eligible(kpi))
        and (t.state_eligible is None or state is None or t.state_eligible(state))
        and (t.goal_eligible is None or not goal or t.goal_eligible(goal))
    ]


# ---------------------------------------------------------------------------
# Per-domain scoring functions
# ---------------------------------------------------------------------------

def _score_strength(
    t: CandidateTemplate,
    state: UnifiedStateVector,
    kpi: dict[str, float],
    r: float,
) -> SessionCandidate:
    x = state.capacity_x
    f = state.fatigue_f
    habit = state.habit_strength

    if t.branch_id == "strength_max":
        return SessionCandidate(
            type=t.type, focus=t.focus, rationale=t.rationale,
            duration_min=t.duration_min, branch_id=t.branch_id,
            goal_alignment=t.goal_alignment,
            state_fit=r * (x.max_strength / 100.0 + 0.3),
            fatigue_penalty=f.cns / 100.0,
            tissue_penalty=state.tissue_t.lumbar / 100.0 + state.tissue_t.knee / 100.0,
            weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
            habit_bonus=habit,
        )
    if t.branch_id == "strength_skill_acq":
        return SessionCandidate(
            type=t.type, focus=t.focus, rationale=t.rationale,
            duration_min=t.duration_min, branch_id=t.branch_id,
            goal_alignment=t.goal_alignment,
            state_fit=0.9,
            fatigue_penalty=f.cns / 100.0 * 0.5,
            tissue_penalty=0.0,
            weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
            habit_bonus=habit,
        )
    if t.branch_id == "strength_variety":
        return SessionCandidate(
            type=t.type, focus=t.focus, rationale=t.rationale,
            duration_min=t.duration_min, branch_id=t.branch_id,
            goal_alignment=t.goal_alignment,
            state_fit=r,
            fatigue_penalty=f.muscular / 100.0 * 0.5,
            tissue_penalty=state.tissue_t.lumbar / 100.0 * 0.5,
            habit_bonus=0.8,
        )
    # strength_volume (default)
    return SessionCandidate(
        type=t.type, focus=t.focus, rationale=t.rationale,
        duration_min=t.duration_min, branch_id=t.branch_id,
        goal_alignment=t.goal_alignment,
        state_fit=max(0.3, 1.0 - f.muscular / 100.0),
        fatigue_penalty=f.muscular / 100.0 * 0.7,
        tissue_penalty=state.tissue_t.hip / 100.0,
        habit_bonus=habit * 0.5,
    )


def _score_hypertrophy(
    t: CandidateTemplate,
    state: UnifiedStateVector,
    kpi: dict[str, float],
    r: float,
) -> SessionCandidate:
    f = state.fatigue_f
    habit = state.habit_strength

    if t.branch_id == "hyp_high_vol":
        return SessionCandidate(
            type=t.type, focus=t.focus, rationale=t.rationale,
            duration_min=t.duration_min, branch_id=t.branch_id,
            goal_alignment=t.goal_alignment,
            state_fit=r * (1.0 - f.muscular / 100.0),
            fatigue_penalty=f.muscular / 100.0,
            tissue_penalty=(state.tissue_t.knee + state.tissue_t.hip) / 200.0,
            weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
            habit_bonus=habit,
        )
    # hyp_maintenance
    return SessionCandidate(
        type=t.type, focus=t.focus, rationale=t.rationale,
        duration_min=t.duration_min, branch_id=t.branch_id,
        goal_alignment=t.goal_alignment,
        state_fit=r,
        fatigue_penalty=f.muscular / 100.0 * 0.4,
        tissue_penalty=0.0,
        habit_bonus=habit * 0.5,
    )


def _score_power(
    t: CandidateTemplate,
    state: UnifiedStateVector,
    kpi: dict[str, float],
    r: float,
) -> SessionCandidate:
    f = state.fatigue_f
    habit = state.habit_strength

    if t.branch_id == "power_main":
        return SessionCandidate(
            type=t.type, focus=t.focus, rationale=t.rationale,
            duration_min=t.duration_min, branch_id=t.branch_id,
            goal_alignment=t.goal_alignment,
            state_fit=r * (1.0 - f.cns / 100.0),
            fatigue_penalty=f.cns / 100.0,
            tissue_penalty=(state.tissue_t.knee + state.tissue_t.ankle) / 200.0,
            weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
            habit_bonus=habit,
        )
    # power_neural_prime
    return SessionCandidate(
        type=t.type, focus=t.focus, rationale=t.rationale,
        duration_min=t.duration_min, branch_id=t.branch_id,
        goal_alignment=t.goal_alignment,
        state_fit=max(0.4, 1.0 - f.cns / 100.0),
        fatigue_penalty=f.cns / 100.0 * 0.5,
        tissue_penalty=0.0,
        habit_bonus=habit * 0.6,
    )


def _score_weightlifting(
    t: CandidateTemplate,
    state: UnifiedStateVector,
    kpi: dict[str, float],
    r: float,
) -> SessionCandidate:
    f = state.fatigue_f
    habit = state.habit_strength

    if t.branch_id in ("wl_technique_snatch", "wl_technique_cj"):
        return SessionCandidate(
            type=t.type, focus=t.focus, rationale=t.rationale,
            duration_min=t.duration_min, branch_id=t.branch_id,
            goal_alignment=t.goal_alignment,
            state_fit=r * (1.0 - f.cns / 100.0),
            fatigue_penalty=f.cns / 100.0,
            tissue_penalty=state.tissue_t.wrist / 100.0 + state.tissue_t.shoulder / 100.0,
            weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
            habit_bonus=habit,
        )
    # wl_strength_pulls
    return SessionCandidate(
        type=t.type, focus=t.focus, rationale=t.rationale,
        duration_min=t.duration_min, branch_id=t.branch_id,
        goal_alignment=t.goal_alignment,
        state_fit=r,
        fatigue_penalty=(f.muscular + f.cns * 0.5) / 150.0,
        tissue_penalty=state.tissue_t.lumbar / 100.0,
        weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
        habit_bonus=habit * 0.5,
    )


def _score_powerlifting(
    t: CandidateTemplate,
    state: UnifiedStateVector,
    kpi: dict[str, float],
    r: float,
) -> SessionCandidate:
    f = state.fatigue_f
    habit = state.habit_strength

    if t.branch_id in ("pl_sbd_main", "pl_sbd_main_volume"):
        return SessionCandidate(
            type=t.type, focus=t.focus, rationale=t.rationale,
            duration_min=t.duration_min, branch_id=t.branch_id,
            goal_alignment=t.goal_alignment,
            state_fit=r * (1.0 - f.cns / 100.0 * 0.5),
            fatigue_penalty=(f.cns + f.structural * 0.5) / 150.0,
            tissue_penalty=(state.tissue_t.lumbar + state.tissue_t.knee) / 200.0,
            weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
            habit_bonus=habit,
        )
    # pl_accessory
    return SessionCandidate(
        type=t.type, focus=t.focus, rationale=t.rationale,
        duration_min=t.duration_min, branch_id=t.branch_id,
        goal_alignment=t.goal_alignment,
        state_fit=r,
        fatigue_penalty=f.muscular / 100.0 * 0.6,
        tissue_penalty=state.tissue_t.lumbar / 100.0 * 0.5,
        weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
        habit_bonus=habit * 0.7,
    )


def _score_mixed(
    t: CandidateTemplate,
    state: UnifiedStateVector,
    kpi: dict[str, float],
    r: float,
) -> SessionCandidate:
    f = state.fatigue_f
    habit = state.habit_strength

    if t.branch_id == "metcon_mixed_modal":
        return SessionCandidate(
            type=t.type, focus=t.focus, rationale=t.rationale,
            duration_min=t.duration_min, branch_id=t.branch_id,
            goal_alignment=t.goal_alignment,
            state_fit=r,
            fatigue_penalty=f.metabolic / 100.0,
            tissue_penalty=state.tissue_t.knee / 100.0 * 0.5,
            weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
            habit_bonus=habit,
        )
    if t.branch_id == "metcon_engine":
        return SessionCandidate(
            type=t.type, focus=t.focus, rationale=t.rationale,
            duration_min=t.duration_min, branch_id=t.branch_id,
            goal_alignment=t.goal_alignment,
            state_fit=1.0 - f.metabolic / 100.0,
            fatigue_penalty=f.metabolic / 100.0 * 0.8,
            tissue_penalty=0.0,
            weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
            habit_bonus=habit * 0.6,
        )
    # mixed_strength_endurance
    return SessionCandidate(
        type=t.type, focus=t.focus, rationale=t.rationale,
        duration_min=t.duration_min, branch_id=t.branch_id,
        goal_alignment=t.goal_alignment,
        state_fit=r,
        fatigue_penalty=f.muscular / 100.0 * 0.6,
        tissue_penalty=state.tissue_t.lumbar / 100.0 * 0.4,
        weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
        habit_bonus=habit * 0.7,
    )


def _score_running(
    t: CandidateTemplate,
    state: UnifiedStateVector,
    kpi: dict[str, float],
    r: float,
) -> SessionCandidate:
    f = state.fatigue_f
    habit = state.habit_strength

    if t.branch_id in ("run_z2_base", "run_z2_base_threshold"):
        return SessionCandidate(
            type=t.type, focus=t.focus, rationale=t.rationale,
            duration_min=t.duration_min, branch_id=t.branch_id,
            goal_alignment=t.goal_alignment,
            state_fit=r,
            fatigue_penalty=f.structural / 100.0 * 0.5 + f.tendon / 100.0 * 0.5,
            tissue_penalty=(state.tissue_t.ankle + state.tissue_t.knee) / 200.0,
            weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
            habit_bonus=habit,
        )
    if t.branch_id in ("run_threshold", "run_threshold_ff"):
        return SessionCandidate(
            type=t.type, focus=t.focus, rationale=t.rationale,
            duration_min=t.duration_min, branch_id=t.branch_id,
            goal_alignment=t.goal_alignment,
            state_fit=r * 0.9,
            fatigue_penalty=(f.structural + f.tendon) / 200.0,
            tissue_penalty=(state.tissue_t.ankle + state.tissue_t.knee) / 200.0,
            weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
            habit_bonus=habit * 0.7,
        )
    # run_sprint
    return SessionCandidate(
        type=t.type, focus=t.focus, rationale=t.rationale,
        duration_min=t.duration_min, branch_id=t.branch_id,
        goal_alignment=t.goal_alignment,
        state_fit=r * (1.0 - f.cns / 100.0),
        fatigue_penalty=f.cns / 100.0,
        tissue_penalty=(state.tissue_t.ankle + state.tissue_t.hip) / 200.0,
        weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
        habit_bonus=habit,
    )


def _score_gymnastics(
    t: CandidateTemplate,
    state: UnifiedStateVector,
    kpi: dict[str, float],
    r: float,
) -> SessionCandidate:
    f = state.fatigue_f
    habit = state.habit_strength
    return SessionCandidate(
        type=t.type, focus=t.focus, rationale=t.rationale,
        duration_min=t.duration_min, branch_id=t.branch_id,
        goal_alignment=t.goal_alignment,
        state_fit=r * (1.0 - f.cns / 100.0),
        fatigue_penalty=f.cns / 100.0,
        tissue_penalty=(
            state.tissue_t.wrist + state.tissue_t.shoulder + state.tissue_t.elbow
        ) / 300.0,
        weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
        habit_bonus=habit,
    )


def _score_calisthenics(
    t: CandidateTemplate,
    state: UnifiedStateVector,
    kpi: dict[str, float],
    r: float,
) -> SessionCandidate:
    f = state.fatigue_f
    habit = state.habit_strength

    if t.branch_id == "gym_skill":
        return SessionCandidate(
            type=t.type, focus=t.focus, rationale=t.rationale,
            duration_min=t.duration_min, branch_id=t.branch_id,
            goal_alignment=t.goal_alignment,
            state_fit=r * (1.0 - f.cns / 100.0),
            fatigue_penalty=f.cns / 100.0,
            tissue_penalty=(
                state.tissue_t.wrist + state.tissue_t.shoulder + state.tissue_t.elbow
            ) / 300.0,
            weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
            habit_bonus=habit,
        )
    # cal_strength
    return SessionCandidate(
        type=t.type, focus=t.focus, rationale=t.rationale,
        duration_min=t.duration_min, branch_id=t.branch_id,
        goal_alignment=t.goal_alignment,
        state_fit=r,
        fatigue_penalty=(f.cns + f.grip * 0.5) / 150.0,
        tissue_penalty=(state.tissue_t.shoulder + state.tissue_t.elbow) / 200.0,
        weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
        habit_bonus=habit,
    )


def _score_grip(
    t: CandidateTemplate,
    state: UnifiedStateVector,
    kpi: dict[str, float],
    r: float,
) -> SessionCandidate:
    f = state.fatigue_f
    habit = state.habit_strength

    if t.branch_id == "grip_main":
        return SessionCandidate(
            type=t.type, focus=t.focus, rationale=t.rationale,
            duration_min=t.duration_min, branch_id=t.branch_id,
            goal_alignment=t.goal_alignment,
            state_fit=r * (1.0 - f.grip / 100.0),
            fatigue_penalty=f.grip / 100.0,
            tissue_penalty=(state.tissue_t.finger + state.tissue_t.elbow) / 200.0,
            weak_point_coverage=_weak_point_coverage(t.tags, state, kpi),
            habit_bonus=habit,
        )
    # grip_recovery
    return SessionCandidate(
        type=t.type, focus=t.focus, rationale=t.rationale,
        duration_min=t.duration_min, branch_id=t.branch_id,
        goal_alignment=t.goal_alignment,
        state_fit=1.0 - f.grip / 100.0 + 0.3,
        fatigue_penalty=0.0,
        tissue_penalty=0.0,
        habit_bonus=0.4,
    )


def _score_general(
    t: CandidateTemplate,
    state: UnifiedStateVector,
    kpi: dict[str, float],
    r: float,
) -> SessionCandidate:
    habit = state.habit_strength
    return SessionCandidate(
        type=t.type, focus=t.focus, rationale=t.rationale,
        duration_min=t.duration_min, branch_id=t.branch_id,
        goal_alignment=t.goal_alignment,
        state_fit=r,
        fatigue_penalty=mean_fatigue(state) / 100.0 * 0.5,
        tissue_penalty=max_tissue_load(state) / 100.0 * 0.3,
        habit_bonus=habit,
    )


# ---------------------------------------------------------------------------
# Scoring dispatch
# ---------------------------------------------------------------------------

_DOMAIN_SCORERS: dict[
    str,
    Callable[[CandidateTemplate, UnifiedStateVector, dict[str, float], float], SessionCandidate],
] = {
    "strength": _score_strength,
    "hypertrophy": _score_hypertrophy,
    "power": _score_power,
    "weightlifting": _score_weightlifting,
    "powerlifting": _score_powerlifting,
    "mixed": _score_mixed,
    "running": _score_running,
    "gymnastics": _score_gymnastics,
    "calisthenics": _score_calisthenics,
    "grip": _score_grip,
    "general": _score_general,
}


def score_template(
    t: CandidateTemplate,
    state: UnifiedStateVector,
    kpi: dict[str, float],
    *,
    readiness: float | None = None,
) -> SessionCandidate:
    """Convert a static CandidateTemplate into a scored SessionCandidate.

    The ``readiness`` argument allows the caller to pass a pre-computed
    overall_readiness value so it is not recomputed for each template.
    """
    r = readiness if readiness is not None else overall_readiness(state)
    scorer = _DOMAIN_SCORERS.get(t.domain, _score_general)
    return scorer(t, state, kpi, r)
