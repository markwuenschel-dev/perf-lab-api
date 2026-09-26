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
from dataclasses import dataclass, field, replace

from app.domain.vectors import FatigueState, TissueState
from app.logic.constraint_engine.candidate import (
    SessionCandidate,
    WorkloadVolume,
    overall_readiness,
)
from app.logic.exercise_slot import CircuitSpec, ExerciseSlot
from app.logic.planned_session_slots import (
    ACTIVE_RECOVERY_CATEGORY,
    HYROX_SIMULATION_CATEGORY,
    RUNNING_FUNCTIONAL_CATEGORY,
    SPEED_CATEGORY,
    STRENGTH_POTENTIATION_CATEGORY,
    STRENGTH_SKILL_CATEGORY,
    THRESHOLD_CATEGORY,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.workout_structure import (
    CircuitStation,
    ContinuousBlock,
    EMOMScheme,
    FixedRoundsScheme,
    ForTimeScheme,
    IntervalBlock,
)

# ---------------------------------------------------------------------------
# ScoringSpec — per-template dynamic scoring, carried as data
# ---------------------------------------------------------------------------

@dataclass
class ScoringSpec:
    """How one template scores against the current state.

    Only ``state_fit`` varies enough between templates to need a callable; the
    penalties and habit bonus are uniform formulas parameterised by data. This
    mirrors the eligibility predicates already carried on CandidateTemplate, so
    a template's content, eligibility, and scoring all live in one place.

    fatigue_penalty = fatigue_f.<fatigue_axis> / 100 * fatigue_weight, or, when
                      ``fatigue_axes`` names several, their weighted MEAN / 100 * fatigue_weight
    tissue_penalty  = max(tissue_t.<tissue_axes>) / 100 * tissue_weight
                      (the MOST-STRESSED tissue — see _score_from_spec)
    habit_bonus     = habit_fixed, else habit_strength * habit_mult
    weak_point_coverage = _weak_point_coverage(tags) if covers_weak_points else 0

    Every template carries one (phase 4.2). The per-domain scorers it replaced picked a formula
    by matching ``branch_id`` strings and scored any unrecognised template with whichever
    formula happened to be written last; a spec makes the formula part of the template.
    """

    state_fit: Callable[[UnifiedStateVector, float], float]
    fatigue_axis: str = "cns"
    fatigue_weight: float = 1.0
    #: Several fatigue axes with relative weights; when set, replaces ``fatigue_axis``. The
    #: weights are normalised, so ``(("cns", 1.0), ("structural", 0.5))`` is
    #: ``(cns + 0.5·structural) / 150`` — the form the hand-coded scorers wrote out.
    fatigue_axes: tuple[tuple[str, float], ...] = ()
    tissue_axes: tuple[str, ...] = ()
    #: UNCALIBRATED (phase 8, C4). ``max`` decides WHICH tissue the penalty reads; this
    #: weight is a hand-set guess at how much that tissue's load should cost, fitted to nothing.
    tissue_weight: float = 1.0
    habit_mult: float = 1.0
    habit_fixed: float | None = None
    covers_weak_points: bool = False


#: Every fatigue axis at equal weight — ``mean_fatigue`` expressed as a spec.
ALL_FATIGUE_AXES: tuple[tuple[str, float], ...] = tuple(
    (axis, 1.0) for axis in FatigueState.KEYS
)
#: Every tissue axis — ``max_tissue_load`` expressed as a spec.
ALL_TISSUE_AXES: tuple[str, ...] = tuple(TissueState.KEYS)


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
        The canonical domain the template belongs to (see app.logic.domain_vocab).
    scoring :
        How the template scores against the current state. Required, and keyword-only, so a
        template cannot exist without saying how it is scored.
    exercise_slots :
        What each movement in this session must BE, not which one it is
        (ADR-0016). Each slot states requirements — movement pattern, load
        type, skill ceiling, sport domain — and the catalog is searched at
        prescribe time for the best match the athlete can actually perform
        with their equipment, biased toward their flagged weak points.

        Competition lifts pin via ``e1rm_code``: a powerlifter's squat is not
        interchangeable with any other squat-pattern barbell movement, so
        those slots name the benchmark rather than the shape. Everything else
        resolves. Empty (default) still falls back to the equipment map.
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
    tags: list[str] = field(default_factory=lambda: [])
    domain: str = ""
    kpi_eligible: Callable[[dict[str, float]], bool] | None = None
    state_eligible: Callable[[UnifiedStateVector], bool] | None = None
    goal_eligible: Callable[[str], bool] | None = None
    # Dynamic scoring — required. See ScoringSpec.
    scoring: ScoringSpec = field(kw_only=True)
    # Requirement-based movement slots — see class docstring.
    exercise_slots: list[ExerciseSlot] = field(default_factory=lambda: [])
    # "scaled" (default): the block's easy/medium/hard preference moves working sets. "fixed":
    # the authored volume is the session, and the generic scaler skips it (phase 5). A declared
    # property of the session, so no code path keys on a template id.
    workload_volume: WorkloadVolume = "scaled"
    # Phase 6.1: slots performed as one circuit under a scheme. None: every slot is its own
    # block, as before.
    circuit: CircuitSpec | None = None


# ---------------------------------------------------------------------------
# WorkoutFamily — one session design, enumerated variants
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FamilyVariant:
    """One member of a family: its identity, why it exists, and when it applies.

    ``branch_id`` is the variant's identity everywhere downstream — scoring, the planner's slot
    bindings, the golden corpora — so expanding a family must reproduce it exactly.
    """

    branch_id: str
    rationale: str
    kpi_eligible: Callable[[dict[str, float]], bool] | None = None
    state_eligible: Callable[[UnifiedStateVector], bool] | None = None
    goal_eligible: Callable[[str], bool] | None = None
    #: Overrides the family's focus when the variant is the same design written differently —
    #: a tempo run and threshold intervals are both threshold work. None inherits the family's.
    focus: str | None = None
    #: Overrides the family's slots when the variant's work is shaped differently (one
    #: continuous tempo vs four intervals). None inherits the family's.
    exercise_slots: tuple[ExerciseSlot, ...] | None = None


@dataclass(frozen=True)
class WorkoutFamily:
    """A session design shared by several templates, which differ only in their variants.

    ``expand()`` produces ordinary ``CandidateTemplate``s — the same dataclass, the same
    ``branch_id`` identities, in variant order — so a family joins the one template pool rather
    than forming a parallel one, and nothing downstream can tell a family member from a literal.

    Variants are ENUMERATED, never a sweep over parameters: each one is a session someone
    decided should exist.
    """

    family_id: str
    domain: str
    type: str
    focus: str
    duration_min: int
    goal_alignment: float
    tags: tuple[str, ...]
    scoring: ScoringSpec
    exercise_slots: tuple[ExerciseSlot, ...]
    variants: tuple[FamilyVariant, ...]

    def expand(self) -> list[CandidateTemplate]:
        return [
            CandidateTemplate(
                type=self.type,
                focus=self.focus if v.focus is None else v.focus,
                rationale=v.rationale,
                branch_id=v.branch_id,
                duration_min=self.duration_min,
                goal_alignment=self.goal_alignment,
                tags=list(self.tags),
                domain=self.domain,
                kpi_eligible=v.kpi_eligible,
                state_eligible=v.state_eligible,
                goal_eligible=v.goal_eligible,
                scoring=self.scoring,
                # A fresh list per member: templates are mutable, families are not.
                exercise_slots=list(
                    self.exercise_slots if v.exercise_slots is None else v.exercise_slots
                ),
            )
            for v in self.variants
        ]


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
        exercise_slots=[
            ExerciseSlot(sets="5", reps="3", movement_pattern="squat", load_type="barbell",
                         modality="Strength", skill_target=0.70,
                         prefer_tags=("squat_pattern",)),
            ExerciseSlot(sets="3", reps="5", movement_pattern="hinge", load_type="barbell",
                         prefer_tags=("posterior_chain",)),
        ],
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (s.capacity_x.max_strength / 100.0 + 0.3),
            fatigue_axis="cns",
            tissue_axes=("lumbar", "knee"),
            covers_weak_points=True,
        ),
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: 0.9,
            fatigue_axis="cns",
            fatigue_weight=0.5,
            covers_weak_points=True,
        ),
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r,
            fatigue_axis="muscular",
            fatigue_weight=0.5,
            tissue_axes=("lumbar",),
            tissue_weight=0.5,
            habit_fixed=0.8,
        ),
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: max(0.3, 1.0 - s.fatigue_f.muscular / 100.0),
            fatigue_axis="muscular",
            fatigue_weight=0.7,
            tissue_axes=("hip",),
            habit_mult=0.5,
        ),
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.muscular / 100.0),
            fatigue_axis="muscular", tissue_axes=("knee", "hip"),
            covers_weak_points=True,
        ),
        exercise_slots=[
            ExerciseSlot(sets="4", reps="12", movement_pattern="squat", modality="Hypertrophy"),
            ExerciseSlot(sets="3", reps="15", movement_pattern="squat", modality="Hypertrophy"),
            ExerciseSlot(sets="3", reps="12", movement_pattern="hinge", modality="Hypertrophy"),
        ],
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r,
            fatigue_axis="muscular", fatigue_weight=0.4, habit_mult=0.5,
        ),
    ),
    CandidateTemplate(
        type="Upper Body Hypertrophy",
        focus="Bench Press 4×10 + Barbell Row 4×10 + Dumbbell Shoulder Press 3×12 near failure",
        rationale="Upper-body mechanical tension and volume — complements the lower-body-biased high-volume day.",
        branch_id="hyp_upper_split",
        duration_min=60,
        goal_alignment=0.9,
        tags=[],
        domain="hypertrophy",
        # Only when reasonably fresh — keeps hyp_maintenance the pick under elevated
        # muscular fatigue (see tests/test_prescriber_exercise_selection.py).
        state_eligible=lambda s: s.fatigue_f.muscular < 55.0,
        exercise_slots=[
            ExerciseSlot(sets="4", reps="10", movement_pattern="push_horizontal",
                         load_type="barbell", skill_target=0.5),
            ExerciseSlot(sets="4", reps="10", movement_pattern="pull_horizontal",
                         load_type="barbell", skill_target=0.5),
            ExerciseSlot(sets="3", reps="12", movement_pattern="push_vertical",
                         load_type="dumbbell", modality="Hypertrophy", skill_target=0.4),
        ],
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.muscular / 100.0 * 0.5),
            fatigue_axis="muscular",
            fatigue_weight=0.6,
            tissue_axes=("shoulder", "lumbar"),
            tissue_weight=0.5,
            habit_mult=0.8,
        ),
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.cns / 100.0),
            tissue_axes=("knee", "ankle"), covers_weak_points=True,
        ),
        exercise_slots=[
            ExerciseSlot(sets="5", reps="3", movement_pattern="hinge", modality="Power", load_type="barbell"),
            ExerciseSlot(sets="4", reps="4", movement_pattern="jump", modality="Power"),
        ],
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: max(0.4, 1.0 - s.fatigue_f.cns / 100.0),
            fatigue_weight=0.5, habit_mult=0.6,
        ),
    ),
    CandidateTemplate(
        type="Reactive Power",
        focus="Lateral Bounds + Broad Jumps + Rotational Med Ball Work — multi-directional plyometrics",
        rationale="Multi-planar reactive strength — complements the sagittal-only jump work in the main session.",
        branch_id="power_reactive",
        duration_min=40,
        goal_alignment=0.85,
        tags=[],
        domain="power",
        exercise_slots=[
            ExerciseSlot(sets="4", reps="4 each side", movement_pattern="jump",
                         sport_domain="running", skill_target=0.55, prefer_tags=("plyometric",)),
            ExerciseSlot(sets="4", reps="4", movement_pattern="jump",
                         sport_domain="running", skill_target=0.55,
                         prefer_tags=("plyometric", "power")),
            ExerciseSlot(sets="3", reps="8-10", movement_pattern="core",
                         max_skill_demand=0.45, prefer_tags=("rotation",)),
        ],
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.cns / 100.0 * 0.7),
            fatigue_axis="cns",
            fatigue_weight=0.8,
            tissue_axes=("knee", "ankle", "hip"),
            tissue_weight=0.6,
            habit_mult=0.8,
        ),
    ),
]

#: The power block's Strength Potentiation day (phase 5.6), reachable on that day only
#: (``_CATEGORY_POOLS``). Contrast / PAPE: a heavy squat before explosive jumps. The acute
#: effect depends heavily on load, volume and the recovery interval, so this is a PRIMER, not
#: a second strength session: doubles, three sets, full recovery. Fatigue that accumulates
#: before the jumps cancels what the pairing is for.
#:
#: The A/B round order (squat, then jumps, three times) is a fixed-rounds circuit (phase 6.1),
#: so the structure says it rather than the text. The exercises it projects are the same two
#: lines, 3×2 and 3×3, as before it was a circuit. One thing it still cannot say, carried in
#: the text instead:
#: - the effort target: the block envelope owns effort, so the squat's cap is the week's, not
#:   a per-template RPE (``test_effort_resolution_seam``). Per-session effort is phase 7.
POWER_POTENTIATION_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type=STRENGTH_POTENTIATION_CATEGORY,
        focus="3 rounds: Back Squat ×2 (heavy, no grinding) → Broad Jump ×3 — full recovery "
              "between every set",
        rationale="Contrast pairing: a heavy squat primes the jumps that follow. Fixed-volume "
                  "primer: additional rounds may increase fatigue and undermine the intended "
                  "potentiation effect, so workload-specific progression is left to the "
                  "periodization layer.",
        branch_id="power_potentiation",
        duration_min=45,
        goal_alignment=0.9,
        tags=["squat_pattern"],
        domain="power",
        # Three rounds is the authored protocol, not a claimed optimum. What the evidence does
        # support is that conditioning-activity volume and recovery shift the fatigue /
        # potentiation balance (Xu et al. 2025), so "hard = another heavy round" is not a safe
        # generic progression. Neither sets nor load move with the workload preference; phase
        # 7 owns primer-specific progression.
        workload_volume="fixed",
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.cns / 100.0),
            tissue_axes=("knee", "hip"), covers_weak_points=True,
        ),
        exercise_slots=[
            ExerciseSlot(sets="3", reps="2", e1rm_code="pl_e1rm_squat",
                         load_note="Primer, not a strength set: no grinding reps. Full recovery "
                                   "before the jumps."),
            ExerciseSlot(sets="3", reps="3", movement_pattern="jump",
                         prefer_tags=("plyometric", "power"),
                         load_note="Maximal intent. Stop or regress if jump quality clearly "
                                   "drops. Full recovery before the next round."),
        ],
        # No station duration or transition is authored ("full recovery" is not a number),
        # so the structure's duration stays unknown rather than guessed.
        circuit=CircuitSpec(
            scheme=FixedRoundsScheme(rounds=3),
            stations=(CircuitStation(exercise="", reps=2), CircuitStation(exercise="", reps=3)),
            label="Contrast pair",
        ),
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.cns / 100.0),
            tissue_axes=("wrist", "shoulder"), covers_weak_points=True,
        ),
        exercise_slots=[
            ExerciseSlot(sets="5", reps="2", modality="Power", load_type="barbell",
                         movement_pattern="mixed", sport_domain="weightlifting",
                         prefer_tags=("power",)),
            ExerciseSlot(sets="5", reps="2", modality="Power", load_type="barbell",
                         movement_pattern="mixed", sport_domain="weightlifting",
                         prefer_tags=("power",)),
        ],
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.cns / 100.0),
            tissue_axes=("wrist", "shoulder"), covers_weak_points=True,
        ),
        exercise_slots=[
            ExerciseSlot(sets="5", reps="2", modality="Power", load_type="barbell",
                         movement_pattern="mixed", sport_domain="weightlifting",
                         prefer_tags=("power",)),
            ExerciseSlot(sets="4", reps="3", modality="Power", load_type="barbell",
                         movement_pattern="mixed", sport_domain="weightlifting"),
        ],
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r,
            fatigue_axes=(("muscular", 1.0), ("cns", 0.5)), tissue_axes=("lumbar",),
            habit_mult=0.5, covers_weak_points=True,
        ),
    ),
]

# Two SBD variants: volume-bias rationale when relative total is low.
def _pl_total_below_3x(kpi: dict[str, float]) -> bool:
    """Relative total under 3× bodyweight: the athlete still gains from volume at quality."""
    return kpi.get("pl_relative_total") is not None and kpi["pl_relative_total"] < 3.0


#: Competition squat / bench / deadlift, one session design. Which variant is eligible depends
#: only on the relative total, and the two predicates are exact complements: every athlete gets
#: exactly one.
SBD_STRENGTH_FAMILY = WorkoutFamily(
    family_id="pl_sbd",
    domain="powerlifting",
    type="SBD Strength",
    focus="Squat / Bench / Deadlift — top sets + 3–4 back-off sets",
    duration_min=80,
    goal_alignment=1.0,
    tags=("squat_pattern", "hip_hinge", "push_horizontal"),
    scoring=ScoringSpec(
        state_fit=lambda s, r: r * (1.0 - s.fatigue_f.cns / 100.0 * 0.5),
        fatigue_axes=(("cns", 1.0), ("structural", 0.5)),
        tissue_axes=("lumbar", "knee"), covers_weak_points=True,
    ),
    exercise_slots=(
        ExerciseSlot(sets="4", reps="3-5", e1rm_code="pl_e1rm_squat"),
        ExerciseSlot(sets="4", reps="3-5", e1rm_code="pl_e1rm_bench"),
        ExerciseSlot(sets="2", reps="3-5", e1rm_code="pl_e1rm_deadlift"),
        ExerciseSlot(sets="3", reps="6-8", e1rm_code="pl_e1rm_squat", allow_repeat=True),
    ),
    variants=(
        FamilyVariant(
            branch_id="pl_sbd_main_volume",
            rationale="Quality reps before intensity ramp.",
            kpi_eligible=_pl_total_below_3x,
        ),
        FamilyVariant(
            branch_id="pl_sbd_main",
            rationale="Competition lift specificity with managed autoregulation.",
            kpi_eligible=lambda kpi: not _pl_total_below_3x(kpi),
        ),
    ),
)

POWERLIFTING_TEMPLATES: list[CandidateTemplate] = [
    *SBD_STRENGTH_FAMILY.expand(),
    CandidateTemplate(
        type="Accessory Focus",
        focus="Paused Squat 3×4 + Close-Grip Bench + Romanian Deadlift 3×6",
        rationale="Technical variations and accessory volume to address weak points.",
        branch_id="pl_accessory",
        duration_min=65,
        goal_alignment=0.8,
        tags=["squat_pattern", "push_horizontal", "hip_hinge"],
        domain="powerlifting",
        scoring=ScoringSpec(
            state_fit=lambda s, r: r,
            fatigue_axis="muscular", fatigue_weight=0.6,
            tissue_axes=("lumbar",), tissue_weight=0.5, habit_mult=0.7, covers_weak_points=True,
        ),
        exercise_slots=[
            ExerciseSlot(sets="3", reps="4", movement_pattern="squat", load_type="barbell", modality="Strength"),
            ExerciseSlot(sets="3", reps="6", movement_pattern="push_horizontal", load_type="barbell"),
            ExerciseSlot(sets="3", reps="6", movement_pattern="hinge", load_type="barbell",
                         prefer_tags=("posterior_chain",)),
        ],
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r,
            fatigue_axis="metabolic", tissue_axes=("knee",), tissue_weight=0.5,
            covers_weak_points=True,
        ),
        exercise_slots=[
            ExerciseSlot(sets="5", reps="2 min @ sustainable pace", movement_pattern="row",
                         modality="Conditioning"),
            ExerciseSlot(sets="5", reps="2 min @ sustainable pace", movement_pattern="bike",
                         modality="Conditioning"),
            ExerciseSlot(sets="4", reps="20 reps", movement_pattern="hinge", load_type="kettlebell"),
        ],
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: 1.0 - s.fatigue_f.metabolic / 100.0,
            fatigue_axis="metabolic", fatigue_weight=0.8, habit_mult=0.6,
            covers_weak_points=True,
        ),
        # Phase 6.2 (F13a): the focus above, as structure — nothing new but the recovery,
        # which the focus never stated. 2 min easy spin (1:1) is AUTHORED: recovery changes
        # the response to an interval session, so it is stated rather than left to read as
        # back-to-back work. Timed: 20 + 4 x 2 + 3 x 2 = 34 min (no recovery after the last).
        # Any bike; the intervals repeat the bike the steady block chose. Fixed under the
        # workload preference in phase 6 (mixed-domain endurance never scaled); phase 7
        # decides which dimension Engine Work progresses along.
        exercise_slots=[
            ExerciseSlot(sets="1", reps="20 min Zone 2", movement_pattern="bike",
                         endurance=ContinuousBlock(duration_sec=1200, intensity_basis="zone",
                                                   intensity_target=2.0)),
            ExerciseSlot(sets="4", reps="2 min @ RPE 8 / 2 min easy spin",
                         movement_pattern="bike", allow_repeat=True,
                         endurance=IntervalBlock(
                             repetitions=4, work_duration_sec=120,
                             recovery_duration_sec=120, recovery_type="easy",
                             recovery_after_last_rep=False,
                             intensity_basis="rpe", intensity_target=8.0,
                         )),
        ],
    ),
]

_STRENGTH_ENDURANCE_NOTE = (
    "About RPE 7: moderate load, a few reps in reserve. Move to the next station with short "
    "transitions; rest as available."
)

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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r,
            fatigue_axis="muscular", fatigue_weight=0.6,
            tissue_axes=("lumbar",), tissue_weight=0.4, habit_mult=0.7, covers_weak_points=True,
        ),
        # Phase 6.2 (F13b): the focus above, read literally enough to keep its magnitude —
        # "5x8 @ RPE 7" as five circuit rounds of eight reps at each station, about RPE 7. An
        # INTERPRETATION of ambiguous prose, pinned by a characterization test, not a validated
        # HYROX protocol. Three movements alternating lower / push / pull (120 reps): no
        # deadlift beside the squat, because 40 squats + 40 deadlifts under short-rest circuit
        # fatigue is a dose no evidence supports here. An authored lift is never swapped for
        # one with better load-resolution metadata, so the press and row are pinned by name.
        # "Short rest" names no number, so transitions and the duration stay unknown. Fixed
        # under the workload preference; phase 7 owns progression. RPE 7 is carried in the
        # note: per-session effort targets are phase 7 (the block envelope owns effort today).
        workload_volume="fixed",
        exercise_slots=[
            ExerciseSlot(sets="5", reps="8", e1rm_code="pl_e1rm_squat",
                         load_note=_STRENGTH_ENDURANCE_NOTE),
            ExerciseSlot(sets="5", reps="8", exercise="Overhead Press",
                         load_note=_STRENGTH_ENDURANCE_NOTE),
            ExerciseSlot(sets="5", reps="8", exercise="Barbell Row",
                         load_note=_STRENGTH_ENDURANCE_NOTE),
        ],
        circuit=CircuitSpec(
            scheme=FixedRoundsScheme(rounds=5),
            stations=tuple(CircuitStation(exercise="", reps=8) for _ in range(3)),
            label="Strength Endurance",
        ),
    ),
]

# ---------------------------------------------------------------------------
# HYROX and CrossFit planned days (phase 6.2)
# ---------------------------------------------------------------------------
#
# Each owns its planned day (``_CATEGORY_POOLS``) and competes on no other. Every station is an
# EXACT catalog movement (``ExerciseSlot.exercise``), and each circuit is atomic: if a station
# cannot be done with the athlete's equipment, the whole template is ineligible
# (``circuit_resolves``) rather than prescribed in part. Where two variants share a day, which
# one appears when is phase 7's decision; until then the first eligible one wins ties.

#: The official HYROX race, in order: (catalog movement, distance m, reps, carries a load).
#: Source: hyrox.com/the-fitness-race, checked 2026-09-26 -- 8 x (1 km run -> station). Loads
#: and the wall-ball count vary by division; the athlete's division is not recorded, so no
#: load is invented (see ``_HYROX_LOAD_NOTE``). The simulation invariant test reads this table.
HYROX_OFFICIAL_STATIONS: tuple[tuple[str, float | None, int | None, bool], ...] = (
    ("SkiErg", 1000.0, None, False),
    ("Sled Push", 50.0, None, True),
    ("Sled Pull", 50.0, None, True),
    ("Burpee Broad Jump", 80.0, None, False),
    ("Rowing (Ergometer)", 1000.0, None, False),
    ("Farmer Carry", 200.0, None, True),
    ("Sandbag Lunges", 100.0, None, True),
    ("Wall Ball", None, 100, True),
)
HYROX_RUN_M = 1000.0

_HYROX_LOAD_NOTE = (
    "Your HYROX division's standard load. Your division isn't recorded yet, so no weight is "
    "set here."
)


def _volume_text(distance_m: float | None, reps: int | None) -> str:
    return f"{distance_m:g} m" if distance_m is not None else f"{reps}"


def _hyrox_station(
    name: str, distance_m: float | None, reps: int | None, loaded: bool, *, sets: int,
) -> tuple[ExerciseSlot, CircuitStation]:
    """One exact station: the slot that names it and the circuit station that shapes it."""
    slot = ExerciseSlot(
        sets=str(sets), reps=_volume_text(distance_m, reps), exercise=name,
        load_note=_HYROX_LOAD_NOTE if loaded else None,
    )
    return slot, CircuitStation(exercise="", distance_m=distance_m, reps=reps)


def _hyrox_run(*, sets: int) -> tuple[ExerciseSlot, CircuitStation]:
    slot = ExerciseSlot(sets=str(sets), reps="1 km", exercise="Run", allow_repeat=True)
    return slot, CircuitStation(exercise="", distance_m=HYROX_RUN_M)


_HYROX_SCORING = ScoringSpec(
    state_fit=lambda s, r: r * (1.0 - s.fatigue_f.metabolic / 100.0),
    fatigue_axes=(("metabolic", 1.0), ("muscular", 0.5)),
    tissue_axes=("knee", "lumbar"),
    covers_weak_points=True,
)


def _hyrox_half_simulation(half: str, stations: range) -> CandidateTemplate:
    """Half of the race, exactly as raced: 4 x (1 km run -> the next official station)."""
    pairs = [
        pair
        for i in stations
        for pair in (_hyrox_run(sets=1), _hyrox_station(*HYROX_OFFICIAL_STATIONS[i], sets=1))
    ]
    names = " / ".join(HYROX_OFFICIAL_STATIONS[i][0] for i in stations)
    return CandidateTemplate(
        type=f"HYROX Half Simulation \u2014 {half}",
        focus=f"For time: 4 x (1 km run -> station), race order: {names}",
        rationale=(
            "Half of the race in official order and volume: running under station-induced "
            "fatigue, and race transitions. A half rather than the full race: HYROX studies "
            "show high acute demand and progressive fatigue, and do not establish how often a "
            "full simulation should be done. Fixed volume: the race defines it."
        ),
        branch_id=f"hyrox_half_sim_{half.lower()}",
        # A legacy display field, not the structure's duration: a for-time session's duration
        # is the athlete's result and stays unknown in the structure.
        duration_min=50,
        goal_alignment=1.0,
        tags=["work_capacity", "aerobic_base"],
        domain="mixed",
        workload_volume="fixed",
        scoring=_HYROX_SCORING,
        exercise_slots=[slot for slot, _ in pairs],
        circuit=CircuitSpec(
            scheme=ForTimeScheme(rounds=1),
            stations=tuple(station for _, station in pairs),
            label=f"HYROX Half Simulation \u2014 {half}",
        ),
    )


HYROX_SIMULATION_TEMPLATES: list[CandidateTemplate] = [
    _hyrox_half_simulation("A", range(0, 4)),
    _hyrox_half_simulation("B", range(4, 8)),
]


def _running_functional(variant: str, station: str) -> CandidateTemplate:
    """4 x (1 km run -> a quarter of one official station): one race's worth of the station.

    Not a simulation: the point is the run that FOLLOWS a station. Wall Balls are deliberately
    absent -- the race has no run after them. The round count is the volume lever, moved one
    round by the workload preference (3 / 4 / 5): the natural round-scaled form of this
    authored session, not a validated difficulty dose.
    """
    name, distance_m, reps, loaded = next(s for s in HYROX_OFFICIAL_STATIONS if s[0] == station)
    quarter_m = None if distance_m is None else distance_m / 4
    quarter_reps = None if reps is None else reps // 4
    run_slot, run_station = _hyrox_run(sets=4)
    work_slot, work_station = _hyrox_station(name, quarter_m, quarter_reps, loaded, sets=4)
    return CandidateTemplate(
        type=f"Running + Functional \u2014 {variant}",
        focus=f"4 rounds: 1 km run -> {_volume_text(quarter_m, quarter_reps)} {name}",
        rationale=(
            "Repeated race-order run\u2013station work; later running repetitions are "
            "performed under station-induced fatigue. Four quarter-volume rounds add up to one "
            "full station and 4 km of running."
        ),
        branch_id=f"run_functional_{variant.lower()}",
        duration_min=40,
        goal_alignment=1.0,
        tags=["aerobic_base", "work_capacity"],
        domain="mixed",
        scoring=_HYROX_SCORING,
        exercise_slots=[run_slot, work_slot],
        circuit=CircuitSpec(
            scheme=FixedRoundsScheme(rounds=4),
            stations=(run_station, work_station),
            label=f"Running + Functional \u2014 {variant}",
            scales_with_workload=True,
        ),
    )


RUNNING_FUNCTIONAL_TEMPLATES: list[CandidateTemplate] = [
    _running_functional("Ski", "SkiErg"),
    _running_functional("Lunges", "Sandbag Lunges"),
]

_SKILL_NOTE = (
    "Complete only as many quality reps as allow substantial rest before the next minute; "
    "regress the movement rather than training through repeated technical failure."
)


def _strength_skill(variant: str, lift_code: str, sets: int, reps: int) -> CandidateTemplate:
    """A load-resolved lift, then a 10-minute rotating skill EMOM (5 exposures each).

    Skill work is quality-capped and never progressed by forcing technical failure. The whole
    session is fixed volume in phase 6: the lift's scheme is reused from ``strength_max`` and
    the EMOM is skill practice under a clock, not a MetCon; phase 7 owns progressing either.
    """
    return CandidateTemplate(
        type=f"Strength + Skill \u2014 {variant}",
        focus=f"{variant} {sets}x{reps} -> EMOM 10: Double Unders / Toes to Bar",
        rationale=(
            "Strength with a resolved load, then rope and gymnastics skill practised fresh "
            "enough to keep quality: a rotating EMOM spreads the work so technique, not "
            "fatigue, is what is trained."
        ),
        branch_id=f"cf_strength_skill_{variant.lower()}",
        duration_min=50,
        goal_alignment=1.0,
        tags=["max_strength", "squat_pattern" if variant == "Squat" else "hip_hinge"],
        domain="mixed",
        workload_volume="fixed",
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.cns / 100.0 * 0.5),
            fatigue_axes=(("cns", 1.0), ("muscular", 0.5)),
            tissue_axes=("lumbar", "knee"),
            covers_weak_points=True,
        ),
        exercise_slots=[
            ExerciseSlot(sets=str(sets), reps=str(reps), e1rm_code=lift_code),
            ExerciseSlot(sets="5", reps="20\u201330 unbroken; stop before repeated misses",
                         exercise="Double Unders", load_note=_SKILL_NOTE),
            ExerciseSlot(sets="5", reps="4\u20136 clean; stop before form or rhythm breaks",
                         exercise="Toes to Bar", load_note=_SKILL_NOTE),
        ],
        circuit=CircuitSpec(
            scheme=EMOMScheme(intervals=10, interval_sec=60),
            stations=(CircuitStation(exercise=""), CircuitStation(exercise="")),
            first_slot=1,
            label="Skill EMOM",
        ),
    )


STRENGTH_SKILL_TEMPLATES: list[CandidateTemplate] = [
    _strength_skill("Squat", "pl_e1rm_squat", 5, 3),
    _strength_skill("Deadlift", "pl_e1rm_deadlift", 3, 5),
]


# Running base: two families. Aerobic base splits on fatigue factor; threshold work splits on
# the race goal and, off a marathon goal, on fatigue factor.
def _run_high_fatigue_factor(kpi: dict[str, float]) -> bool:
    """Fatigue factor above 14: pace falls off with distance, so durability work comes first."""
    return (kpi.get("run_fatigue_factor") or 0.0) > 14.0


def _marathon_goal(goal: str) -> bool:
    return goal in ("HalfMarathon", "FullMarathon")


#: Zone-2 aerobic base. The two variants are exact complements on fatigue factor: every
#: athlete gets exactly one.
RUN_AEROBIC_FAMILY = WorkoutFamily(
    family_id="run_aerobic_base",
    domain="running",
    type="Aerobic Base",
    focus="Easy–Moderate Run @ Zone 2 (conversational pace)",
    duration_min=45,
    goal_alignment=1.0,
    tags=("aerobic_base", "running_economy"),
    scoring=ScoringSpec(
        state_fit=lambda s, r: r,
        fatigue_axes=(("structural", 1.0), ("tendon", 1.0)),
        tissue_axes=("ankle", "knee"), covers_weak_points=True,
    ),
    exercise_slots=(
        # Zone 2 is the prescribed intensity. 30-40 min is a range, not a duration, so the
        # block's duration stays unknown rather than a picked midpoint.
        ExerciseSlot(sets="1", reps="30-40 min conversational pace", movement_pattern="run",
                     modality="Running",
                     endurance=ContinuousBlock(intensity_basis="zone", intensity_target=2.0)),
    ),
    variants=(
        FamilyVariant(
            branch_id="run_z2_base_threshold",
            rationale="Threshold durability priority — moderate effort over pure easy volume.",
            kpi_eligible=_run_high_fatigue_factor,
        ),
        FamilyVariant(
            branch_id="run_z2_base",
            rationale="Cardiac output and mitochondrial density via sustained easy effort.",
            kpi_eligible=lambda kpi: not _run_high_fatigue_factor(kpi),
        ),
    ),
)

#: Threshold work: a continuous tempo for a half/full-marathon goal, intervals otherwise when
#: fatigue factor is high. NOT a partition — a non-marathon athlete with a low fatigue factor
#: is offered neither on an unplanned day, and gets aerobic base. A PLANNED Threshold day is
#: exhaustive instead (``_threshold_day_pool``).
RUN_THRESHOLD_FAMILY = WorkoutFamily(
    family_id="run_threshold",
    domain="running",
    type="Threshold Work",
    focus="Tempo Run 20 min @ RPE 7–8 + Progression Miles",
    duration_min=50,
    goal_alignment=0.9,
    tags=("lactate_threshold", "aerobic_base"),
    scoring=ScoringSpec(
        state_fit=lambda s, r: r * 0.9,
        fatigue_axes=(("structural", 1.0), ("tendon", 1.0)),
        tissue_axes=("ankle", "knee"), habit_mult=0.7,
        covers_weak_points=True,
    ),
    # One continuous tempo; the interval variant replaces it with repeats.
    exercise_slots=(
        # 20 min is timed. RPE 7-8 is a band, so the basis is recorded and the target left
        # unknown rather than collapsed to 7.5.
        ExerciseSlot(sets="1", reps="20 min @ RPE 7–8",
                     movement_pattern="run", modality="Running",
                     prefer_tags=("lactate_threshold",),
                     endurance=ContinuousBlock(duration_sec=1200, intensity_basis="rpe")),
    ),
    variants=(
        FamilyVariant(
            branch_id="run_threshold",
            rationale="Threshold pace improves fractional utilization of VO2max.",
            goal_eligible=_marathon_goal,
        ),
        FamilyVariant(
            branch_id="run_threshold_ff",
            rationale="Threshold pace improves fractional utilization of VO2max.",
            focus="4×5 min @ threshold pace (RPE 8) / 2 min easy recovery",
            exercise_slots=(
                ExerciseSlot(sets="4", reps="5 min @ threshold pace (RPE 8) / 2 min easy",
                             movement_pattern="run", modality="Running", skill_target=0.5,
                             prefer_tags=("lactate_threshold",),
                             endurance=IntervalBlock(
                                 repetitions=4, work_duration_sec=300,
                                 recovery_duration_sec=120, recovery_type="easy",
                                 intensity_basis="rpe", intensity_target=8.0,
                             )),
            ),
            kpi_eligible=_run_high_fatigue_factor,
            goal_eligible=lambda g: not _marathon_goal(g),
        ),
    ),
)

RUNNING_BASE_TEMPLATES: list[CandidateTemplate] = [
    *RUN_AEROBIC_FAMILY.expand(),
    *RUN_THRESHOLD_FAMILY.expand(),
]

#: The running Active Recovery day (phase 5.6), reachable on that day only
#: (``_CATEGORY_POOLS``). A very-low-load run on the recovery slot, NOT a claim that easy
#: running speeds recovery: easy runs are often loosely called recovery runs, and the evidence
#: for active-recovery interventions is mixed (Haugen et al.). Zone 1, not 1-2: the point is
#: the lowest running load that is still a run. The range stays a range, so its duration stays
#: unknown rather than a picked midpoint.
RUNNING_RECOVERY_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type=ACTIVE_RECOVERY_CATEGORY,
        focus="Very Easy Run 20–30 min @ Zone 1",
        rationale="The lowest running load that is still a run, on the week's recovery slot.",
        branch_id="run_recovery",
        duration_min=30,
        goal_alignment=0.6,
        tags=["aerobic_base"],
        domain="running",
        scoring=ScoringSpec(
            state_fit=lambda s, r: r,
            fatigue_axes=(("structural", 1.0), ("tendon", 1.0)),
            fatigue_weight=0.3,
            tissue_axes=("ankle", "knee"),
            tissue_weight=0.3,
        ),
        exercise_slots=[
            ExerciseSlot(sets="1", reps="20-30 min very easy (Zone 1)", movement_pattern="run",
                         modality="Running", prefer_tags=("aerobic_base",),
                         endurance=ContinuousBlock(intensity_basis="zone", intensity_target=1.0)),
        ],
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.cns / 100.0),
            tissue_axes=("ankle", "hip"), covers_weak_points=True,
        ),
        exercise_slots=[
            # Distance-only: no pace target times them, so their duration stays unknown.
            ExerciseSlot(sets="3", reps="30m", movement_pattern="run", modality="Power",
                         endurance=IntervalBlock(repetitions=3, work_distance_m=30.0)),
            ExerciseSlot(sets="4", reps="20m", movement_pattern="run", modality="Power",
                         endurance=IntervalBlock(repetitions=4, work_distance_m=20.0)),
        ],
    ),
    CandidateTemplate(
        type="Speed Endurance",
        focus="6×300m @ ~90% Effort (Full Recovery) + Bounding Drill Primer",
        rationale="Anaerobic capacity and speed maintenance under accumulating fatigue — distinct demand from max-velocity work.",
        branch_id="run_speed_endurance",
        duration_min=40,
        goal_alignment=0.9,
        tags=[],
        domain="running",
        exercise_slots=[
            # The build-up's 20-30 m is a range: its distance stays unknown.
            ExerciseSlot(sets="3", reps="20-30m build-up", movement_pattern="run", modality="Power",
                         sport_domain="running", skill_target=0.55,
                         endurance=IntervalBlock(repetitions=3)),
            ExerciseSlot(sets="6", reps="300m @ ~90% effort, full recovery", movement_pattern="run",
                         modality="Running", skill_target=0.40,
                         prefer_tags=("lactate_threshold", "running_economy"),
                         endurance=IntervalBlock(repetitions=6, work_distance_m=300.0)),
        ],
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (
                1.0 - (s.fatigue_f.cns * 0.5 + s.fatigue_f.metabolic * 0.5) / 100.0
            ),
            fatigue_axis="metabolic",
            fatigue_weight=0.8,
            tissue_axes=("ankle", "hip"),
            tissue_weight=1.0,
        ),
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.cns / 100.0),
            tissue_axes=("wrist", "shoulder", "elbow"),
            covers_weak_points=True,
        ),
        exercise_slots=[
            ExerciseSlot(sets="4", reps="20-30s", movement_pattern="push_vertical",
                         modality="Calisthenics", load_type="time"),
            ExerciseSlot(sets="3", reps="20-30s", movement_pattern="push_vertical",
                         modality="Calisthenics", load_type="time"),
        ],
    ),
    CandidateTemplate(
        type="Gymnastics Strength",
        focus="Weighted Ring Dips + Chest-to-Bar Pull-Ups + Strict Toes-to-Bar",
        rationale="Base pulling and pressing strength under the skill — quality holds alone don't build it.",
        branch_id="gym_strength",
        duration_min=55,
        goal_alignment=0.95,
        tags=["gymnastics_skill", "overhead_stability"],
        domain="gymnastics",
        exercise_slots=[
            ExerciseSlot(sets="4", reps="5-8", movement_pattern="pull_vertical", modality="Calisthenics",
                         sport_domain="gymnastics", skill_target=0.55, prefer_tags=("gymnastics_skill",)),
            ExerciseSlot(sets="4", reps="6-10", movement_pattern="push_vertical", modality="Calisthenics",
                         sport_domain="gymnastics", skill_target=0.82, prefer_tags=("gymnastics_skill",)),
            ExerciseSlot(sets="3", reps="8-10", movement_pattern="core", modality="Calisthenics",
                         sport_domain="gymnastics", skill_target=0.60, prefer_tags=("gymnastics_skill",)),
        ],
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.cns / 100.0),
            fatigue_axis="cns",
            tissue_axes=("wrist", "shoulder", "elbow"),
            # Was 1.0/3.0 — a hand-compensation for the old summed penalty, proof the
            # convention was understood and unenforced. The max() aggregation needs none.
            tissue_weight=1.0,
            covers_weak_points=True,
        ),
    ),
]

CALISTHENICS_TEMPLATES: list[CandidateTemplate] = [
    CandidateTemplate(
        type="Gymnastics Skill",
        focus="Handstand Progressions + Ring Support Hold + Shaping Drills",
        rationale="Skill and straight-arm strength — quality reps, protect wrists and shoulders.",
        branch_id="cal_skill",
        duration_min=55,
        goal_alignment=1.0,
        tags=["gymnastics_skill", "overhead_stability"],
        domain="calisthenics",
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.cns / 100.0),
            tissue_axes=("wrist", "shoulder", "elbow"),
            covers_weak_points=True,
        ),
        # The same holds as gymnastics' gym_skill, whose content this template has always
        # shared; it had simply never been given slots (phase 4.4).
        exercise_slots=[
            ExerciseSlot(sets="4", reps="20-30s", movement_pattern="push_vertical",
                         modality="Calisthenics", load_type="time"),
            ExerciseSlot(sets="3", reps="20-30s", movement_pattern="push_vertical",
                         modality="Calisthenics", load_type="time"),
        ],
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r,
            fatigue_axes=(("cns", 1.0), ("grip", 0.5)),
            tissue_axes=("shoulder", "elbow"), covers_weak_points=True,
        ),
        exercise_slots=[
            ExerciseSlot(sets="4", reps="6-10", movement_pattern="pull_vertical",
                         modality="Calisthenics", skill_target=0.50),
            ExerciseSlot(sets="3", reps="8-12", movement_pattern="push_horizontal", modality="Calisthenics"),
            ExerciseSlot(sets="3", reps="10-15", movement_pattern="push_horizontal",
                         modality="Calisthenics", load_type="bodyweight"),
        ],
    ),
    CandidateTemplate(
        type="Calisthenics Conditioning",
        focus="Push-up / Pull-up / Step-Up Circuit — high-rep bodyweight conditioning",
        rationale="Work capacity under bodyweight load — complements the low-rep skill and strength sessions.",
        branch_id="cal_conditioning",
        duration_min=35,
        goal_alignment=0.8,
        tags=[],
        domain="calisthenics",
        exercise_slots=[
            ExerciseSlot(sets="5", reps="12-15", movement_pattern="push_horizontal",
                         load_type="bodyweight", modality="Calisthenics", max_skill_demand=0.4),
            ExerciseSlot(sets="5", reps="8-10", movement_pattern="pull_vertical",
                         load_type="bodyweight", modality="Calisthenics", max_skill_demand=0.5),
            ExerciseSlot(sets="5", reps="12 each side", movement_pattern="single_leg",
                         load_type="bodyweight", max_skill_demand=0.45, skill_target=0.40),
        ],
        scoring=ScoringSpec(
            state_fit=lambda s, r: r,
            fatigue_axis="metabolic",
            fatigue_weight=0.6,
            tissue_axes=("shoulder", "elbow"),
            tissue_weight=0.4,
            habit_mult=0.8,
        ),
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.grip / 100.0),
            fatigue_axis="grip", tissue_axes=("finger", "elbow"),
            covers_weak_points=True,
        ),
        exercise_slots=[
            ExerciseSlot(sets="4", reps="40m", movement_pattern="carry", prefer_tags=("grip",)),
            ExerciseSlot(sets="4", reps="30-45s", movement_pattern="pull_vertical", load_type="time"),
        ],
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
        scoring=ScoringSpec(
            # Clamped to the declared 0-1 range; the +0.3 recovery preference used to push
            # it to 1.3 on a fresh grip, outscoring every correctly-bounded template.
            state_fit=lambda s, r: min(1.0, 1.0 - s.fatigue_f.grip / 100.0 + 0.3),
            fatigue_weight=0.0, habit_fixed=0.4,
        ),
    ),
    CandidateTemplate(
        type="Grip Strength",
        focus="Gripper Crush Work + Pinch Block Hold + Thick-Bar Deadlift — max grip strength",
        rationale="Crush and pinch strength near maximal load — a different demand than carry-based endurance.",
        branch_id="grip_crush_pinch",
        duration_min=30,
        goal_alignment=0.95,
        tags=["grip"],
        domain="grip",
        exercise_slots=[
            ExerciseSlot(sets="5", reps="6-8", movement_pattern="pull_vertical", modality="Strength",
                         sport_domain="grip", skill_target=0.35),
            ExerciseSlot(sets="4", reps="20-30s", movement_pattern="carry",
                         sport_domain="grip", skill_target=0.52),
            ExerciseSlot(sets="3", reps="4-6", movement_pattern="hinge", load_type="barbell",
                         skill_target=0.72, prefer_tags=("thick_bar",)),
        ],
        scoring=ScoringSpec(
            state_fit=lambda s, r: r * (1.0 - s.fatigue_f.grip / 100.0 * 0.8),
            fatigue_axis="grip",
            fatigue_weight=0.9,
            tissue_axes=("finger", "elbow"),
            tissue_weight=0.6,
            habit_mult=0.9,
            covers_weak_points=True,
        ),
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
        scoring=ScoringSpec(
            state_fit=lambda s, r: r,
            fatigue_axes=ALL_FATIGUE_AXES, fatigue_weight=0.5,
            tissue_axes=ALL_TISSUE_AXES, tissue_weight=0.3,
        ),
        exercise_slots=[
            ExerciseSlot(sets="3", reps="10", movement_pattern="squat", max_skill_demand=0.45, skill_target=0.30),
            ExerciseSlot(sets="3", reps="8", movement_pattern="pull_vertical",
                         modality="Calisthenics", skill_target=0.50),
            ExerciseSlot(sets="3", reps="12", movement_pattern="push_horizontal",
                         modality="Calisthenics", load_type="bodyweight"),
            ExerciseSlot(sets="2", reps="40m", movement_pattern="carry",
                         prefer_tags=("carry",)),
        ],
    ),
    CandidateTemplate(
        type="General Strength Foundation",
        focus="Back Squat 3×5 + Romanian Deadlift 3×5 + Bench Press 3×5 — loaded basics",
        rationale="External-load strength basics — the loaded counterpart to the bodyweight-first GPP circuit.",
        branch_id="gpp_strength_foundation",
        duration_min=50,
        goal_alignment=0.85,
        tags=["squat_pattern", "hip_hinge"],
        domain="general",
        exercise_slots=[
            ExerciseSlot(sets="3", reps="5", movement_pattern="squat", load_type="barbell",
                         modality="Strength", skill_target=0.70),
            ExerciseSlot(sets="3", reps="5", movement_pattern="hinge", load_type="barbell",
                         skill_target=0.50, prefer_tags=("posterior_chain",)),
            ExerciseSlot(sets="3", reps="5", movement_pattern="push_horizontal", load_type="barbell",
                         modality="Strength", skill_target=0.50),
        ],
        scoring=ScoringSpec(
            state_fit=lambda s, r: r,
            fatigue_axis="muscular",
            fatigue_weight=0.5,
            tissue_axes=("lumbar", "knee"),
            tissue_weight=0.4,
            habit_mult=0.7,
            covers_weak_points=True,
        ),
    ),
    CandidateTemplate(
        type="General Conditioning",
        focus="Bike Intervals + Row Intervals + Plank Circuit — mixed aerobic engine work",
        rationale="Aerobic base and work capacity — no specific goal, no critical constraints.",
        branch_id="gpp_conditioning",
        duration_min=40,
        goal_alignment=0.85,
        tags=["aerobic_base"],
        domain="general",
        exercise_slots=[
            ExerciseSlot(sets="1", reps="15-20 min steady", movement_pattern="bike", modality="Conditioning"),
            ExerciseSlot(sets="1", reps="10 min steady", movement_pattern="row", modality="Conditioning"),
            ExerciseSlot(sets="3", reps="30-45s", movement_pattern="core", modality="Strength",
                         max_skill_demand=0.35),
        ],
        scoring=ScoringSpec(
            state_fit=lambda s, r: r,
            fatigue_axis="metabolic",
            fatigue_weight=0.6,
            habit_mult=0.7,
            covers_weak_points=True,
        ),
    ),
    CandidateTemplate(
        type="General Mobility & Movement Quality",
        focus="Hip Mobility Flow + Single-Leg Control + Calf/Ankle Work",
        rationale="Restorative movement quality session — for when nothing else is critical but capacity work isn't a fit either.",
        branch_id="gpp_mobility",
        duration_min=30,
        goal_alignment=0.65,
        tags=["hip_mobility", "ankle_mobility"],
        domain="general",
        exercise_slots=[
            ExerciseSlot(sets="3", reps="8-10 each side", movement_pattern="single_leg",
                         max_skill_demand=0.5, prefer_tags=("hip_mobility",)),
            ExerciseSlot(sets="2", reps="30-45s each side", movement_pattern="core",
                         max_skill_demand=0.35, prefer_tags=("hip_mobility",)),
            ExerciseSlot(sets="3", reps="12-15 each side", movement_pattern="single_leg",
                         max_skill_demand=0.3),
        ],
        scoring=ScoringSpec(
            state_fit=lambda s, r: r,
            fatigue_axis="structural",
            fatigue_weight=0.3,
            tissue_axes=("ankle", "hip"),
            tissue_weight=0.5,
            habit_mult=0.6,
            covers_weak_points=True,
        ),
    ),
]


# ---------------------------------------------------------------------------
# Library index
# ---------------------------------------------------------------------------

#: Every family whose members are in the pool. Pool = expanded families + remaining literals.
WORKOUT_FAMILIES: tuple[WorkoutFamily, ...] = (
    SBD_STRENGTH_FAMILY,
    RUN_AEROBIC_FAMILY,
    RUN_THRESHOLD_FAMILY,
)

GOAL_TEMPLATE_LIBRARY: dict[str, list[CandidateTemplate]] = {
    "strength": STRENGTH_TEMPLATES,
    "hypertrophy": HYPERTROPHY_TEMPLATES,
    "power": POWER_TEMPLATES,
    "weightlifting": OLYMPIC_TEMPLATES,
    "powerlifting": POWERLIFTING_TEMPLATES,
    "mixed": MIXED_TEMPLATES,
    "running": RUNNING_BASE_TEMPLATES,
    "sprinting": SPRINTING_TEMPLATES,
    # Category-owned pools (``_CATEGORY_POOLS``). Listed so every library-wide guard and golden
    # covers them; no canonical domain has these names, so no ordinary day resolves here.
    "running_recovery": RUNNING_RECOVERY_TEMPLATES,
    "power_potentiation": POWER_POTENTIATION_TEMPLATES,
    "hyrox_simulation": HYROX_SIMULATION_TEMPLATES,
    "running_functional": RUNNING_FUNCTIONAL_TEMPLATES,
    "strength_skill": STRENGTH_SKILL_TEMPLATES,
    "gymnastics": GYMNASTICS_TEMPLATES,
    "calisthenics": CALISTHENICS_TEMPLATES,
    "grip": GRIP_TEMPLATES,
    "general": GENERAL_TEMPLATES,
}


def _threshold_day_pool(goal: str) -> list[CandidateTemplate]:
    """A planned Threshold day: exactly one threshold session is eligible, whatever the KPIs.

    The family's own predicates are NOT a partition: a non-marathon athlete without a high
    fatigue factor, including one who never logged the 400 m + 1 mile benchmarks it needs,
    is offered neither template, and a planned Threshold day became an Easy Run. On the planned
    day the choice becomes exhaustive:

        marathon goal     -> run_threshold (continuous tempo)
        high ff (> 14)    -> run_threshold_ff (intervals)
        otherwise         -> run_threshold

    The ff split stays as it was: an existing coaching heuristic, not a validated partition.
    Only this day widens. On every other running day the family's predicates are unchanged,
    so threshold work does not start competing with aerobic base on ordinary days (phase 5.7).
    """
    tempo, intervals = (
        next(t for t in RUNNING_BASE_TEMPLATES if t.branch_id == bid)
        for bid in ("run_threshold", "run_threshold_ff")
    )
    if _marathon_goal(goal):
        return [replace(tempo, goal_eligible=None, kpi_eligible=None)]

    def unless_intervals(kpi: dict[str, float]) -> bool:
        return not _run_high_fatigue_factor(kpi)

    return [replace(tempo, goal_eligible=None, kpi_eligible=unless_intervals), intervals]


#: Planned categories that OWN their day (phase 5.6): on a day planned as one of these, the
#: domain draws only this pool, and no other day can reach it. That keeps a template written
#: for one planned day from competing on every other day of its domain. The value takes the
#: goal, because a day's family may still choose its member by goal.
_CATEGORY_POOLS: dict[tuple[str, str], Callable[[str], list[CandidateTemplate]]] = {
    ("running", SPEED_CATEGORY): lambda goal: SPRINTING_TEMPLATES,
    ("running", ACTIVE_RECOVERY_CATEGORY): lambda goal: RUNNING_RECOVERY_TEMPLATES,
    ("running", THRESHOLD_CATEGORY): _threshold_day_pool,
    ("power", STRENGTH_POTENTIATION_CATEGORY): lambda goal: POWER_POTENTIATION_TEMPLATES,
    ("mixed", HYROX_SIMULATION_CATEGORY): lambda goal: HYROX_SIMULATION_TEMPLATES,
    ("mixed", RUNNING_FUNCTIONAL_CATEGORY): lambda goal: RUNNING_FUNCTIONAL_TEMPLATES,
    ("mixed", STRENGTH_SKILL_CATEGORY): lambda goal: STRENGTH_SKILL_TEMPLATES,
}

#: Owned days that ARE the pulled-down option. A readiness redirect exists to pull work down;
#: opening these days to the ordinary pool on a bad day would let a longer aerobic-base run
#: beat the very easy one, which is backwards. The redirects still compete with them.
_LOW_LOAD_CATEGORIES: frozenset[tuple[str, str]] = frozenset(
    {("running", ACTIVE_RECOVERY_CATEGORY)}
)


def template_pool(
    domain: str,
    goal: str = "",
    session_category: str | None = None,
    *,
    category_owns_day: bool = True,
) -> list[CandidateTemplate]:
    """The templates a domain draws from, before any eligibility predicate.

    Sprinting, a sub-domain of running, has its own pool for the Sprinting goal; nothing else
    reaches it, so an ordinary running day can never be handed a sprint session.

    A category-owned day (``_CATEGORY_POOLS``) draws its own pool instead. Except when a
    readiness redirect is competing (``category_owns_day=False``): redirects exist to pull
    work down on a bad day, and the plan must not talk over them, so the category's templates
    JOIN the ordinary pool (replacing same-id entries) and scoring chooses, exactly as the
    prescriber already skips plan narrowing on a redirect day. A low-load day
    (``_LOW_LOAD_CATEGORIES``) keeps its pool: it already is the pulled-down session.
    """
    if domain == "running" and goal == "Sprinting":
        ordinary = SPRINTING_TEMPLATES
    else:
        ordinary = GOAL_TEMPLATE_LIBRARY.get(domain, GENERAL_TEMPLATES)
    owned = (
        None if session_category is None else _CATEGORY_POOLS.get((domain, session_category))
    )
    if owned is None:
        return ordinary
    pool = owned(goal)
    if category_owns_day or (domain, session_category) in _LOW_LOAD_CATEGORIES:
        return pool
    ids = {t.branch_id for t in pool}
    return [*pool, *(t for t in ordinary if t.branch_id not in ids)]


def get_templates(
    domain: str,
    kpi: dict[str, float],
    goal: str = "",
    state: UnifiedStateVector | None = None,
    session_category: str | None = None,
    *,
    category_owns_day: bool = True,
) -> list[CandidateTemplate]:
    """Return templates for the domain, filtered by all eligibility predicates.

    The pool is ``template_pool``'s. When ``state`` is None, state_eligible predicates are
    skipped (treated as eligible), so callers that do not yet have state can still query the
    static content.
    """
    pool = template_pool(domain, goal, session_category, category_owns_day=category_owns_day)

    return [
        t for t in pool
        if (t.kpi_eligible is None or t.kpi_eligible(kpi))
        and (t.state_eligible is None or state is None or t.state_eligible(state))
        and (t.goal_eligible is None or not goal or t.goal_eligible(goal))
    ]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _score_from_spec(
    t: CandidateTemplate,
    state: UnifiedStateVector,
    kpi: dict[str, float],
    r: float,
) -> SessionCandidate:
    """The one scorer: every template's formula is its ScoringSpec."""
    spec = t.scoring
    if spec.fatigue_axes:
        weighted = sum(getattr(state.fatigue_f, a) * w for a, w in spec.fatigue_axes)
        fatigue_load = weighted / sum(w for _, w in spec.fatigue_axes)
    else:
        fatigue_load = getattr(state.fatigue_f, spec.fatigue_axis)
    fatigue_penalty = fatigue_load / 100.0 * spec.fatigue_weight
    tissue_penalty = (
        # Weakest link: the most-stressed tissue the template names. The SUM used to be divided
        # by 100 rather than 100·len(axes), so a three-axis template's "0-1" penalty reached
        # 1.8 — listing more tissues multiplied the penalty. Averaging fixes the range but
        # dilutes a single overloaded tissue with healthy ones (a knee at 90 beside two axes at
        # 0 reads as 30), which is what the 13 hand-coded templates did until phase 4.2b.
        max((getattr(state.tissue_t, a) for a in spec.tissue_axes), default=0.0)
        / 100.0
        * spec.tissue_weight
    )
    habit_bonus = (
        spec.habit_fixed
        if spec.habit_fixed is not None
        else state.habit_strength * spec.habit_mult
    )
    wpc = _weak_point_coverage(t.tags, state, kpi) if spec.covers_weak_points else 0.0
    return SessionCandidate(
        type=t.type, focus=t.focus, rationale=t.rationale,
        duration_min=t.duration_min, branch_id=t.branch_id,
        goal_alignment=t.goal_alignment,
        state_fit=spec.state_fit(state, r),
        fatigue_penalty=fatigue_penalty,
        tissue_penalty=tissue_penalty,
        weak_point_coverage=wpc,
        habit_bonus=habit_bonus,
    )


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

    The template's ``exercise_slots`` are carried onto the resulting candidate
    here, not by the scorer, so finalization can prefer them over the equipment
    map without scoring needing to know about it.
    """
    r = readiness if readiness is not None else overall_readiness(state)
    candidate = _score_from_spec(t, state, kpi, r)
    candidate.exercise_slots = t.exercise_slots
    candidate.domain = t.domain
    candidate.workload_volume = t.workload_volume
    candidate.circuit = t.circuit
    return candidate
