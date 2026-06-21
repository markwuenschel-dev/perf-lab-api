"""
Training planning layer: phase/block structure above single-session prescription.

Provides:
- TrainingPhase enum with block type semantics
- TrainingBlock: a structured 1–6 week block with session distribution and intent
- PlanTemplate: a sequence of blocks for a domain-specific training cycle
- get_current_phase(): determine which block phase fits current athlete state
- next_block(): advance to the next block in a template
- deload_triggered(): check if deload is warranted
- retest_due(): check if a benchmark retest is due
- weekly_session_distribution(): how many sessions of each type per week in a block

Templates currently seeded for:
- Powerlifting: accumulation → intensification → peak → deload
- Olympic Lifting: technique → strength → peak
- Running: base → threshold → race-specific → taper
- Gymnastics / Grip: prerequisites → static strength → dynamic skill

Phase 1 implementation: template-guided, not full optimization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.schemas.state import UnifiedStateVector
from app.schemas.training_goals import TrainingGoal

# ---------------------------------------------------------------------------
# Block types
# ---------------------------------------------------------------------------

class BlockType(str, Enum):
    ACCUMULATION = "accumulation"       # High volume, moderate intensity
    INTENSIFICATION = "intensification" # Lower volume, higher intensity
    PEAK = "peak"                       # Competition prep, very high intensity
    DELOAD = "deload"                   # Recovery week, reduced load
    TECHNIQUE = "technique"             # Skill / motor learning focus
    BASE = "base"                       # Aerobic / general foundation
    THRESHOLD = "threshold"             # Lactate threshold development
    RACE_SPECIFIC = "race_specific"     # Event-pace, sport-specific load
    TAPER = "taper"                     # Pre-competition reduction
    SKILL = "skill"                     # Gymnastics / static strength skill
    PREREQUISITES = "prerequisites"     # Mobility + connective tissue prep
    STRENGTH_FOCUS = "strength_focus"   # Hypertrophy / max-effort strength
    GRIP_TISSUE = "grip_tissue"         # Connective tissue / tendon care


# ---------------------------------------------------------------------------
# Single block description
# ---------------------------------------------------------------------------

@dataclass
class TrainingBlock:
    block_type: BlockType
    name: str
    duration_weeks: int
    description: str

    # Session distribution: type → sessions per week
    weekly_distribution: dict[str, int] = field(default_factory=dict)

    # RPE / intensity envelope
    target_rpe_range: tuple[float, float] = (6.0, 8.0)

    # Volume guideline (relative: 1.0 = normal, 0.5 = deload, 1.2 = accumulation peak)
    volume_modifier: float = 1.0

    # Whether this block should include a benchmark retest at end
    retest_at_end: bool = False

    # Tags for exercise selection bias in this block
    exercise_bias_tags: list[str] = field(default_factory=list)

    # Notes for the athlete / coach
    coaching_notes: str = ""


# ---------------------------------------------------------------------------
# Full training plan template
# ---------------------------------------------------------------------------

@dataclass
class PlanTemplate:
    name: str
    domain: str                   # Canonical domain (from domain_vocab)
    goal: TrainingGoal
    blocks: list[TrainingBlock]
    description: str = ""

    def total_weeks(self) -> int:
        return sum(b.duration_weeks for b in self.blocks)

    def block_at_week(self, week: int) -> TrainingBlock | None:
        """Return the block containing the given week (1-indexed)."""
        cumulative = 0
        for block in self.blocks:
            cumulative += block.duration_weeks
            if week <= cumulative:
                return block
        return self.blocks[-1] if self.blocks else None


# ---------------------------------------------------------------------------
# Template library
# ---------------------------------------------------------------------------

def _pl_template() -> PlanTemplate:
    """Classic 12-week powerlifting accumulation → intensification → peak."""
    return PlanTemplate(
        name="Powerlifting 12-Week Cycle",
        domain="powerlifting",
        goal="Powerlifting",
        description="Accumulation + intensification + peak progression for total improvement.",
        blocks=[
            TrainingBlock(
                block_type=BlockType.ACCUMULATION,
                name="Accumulation (Weeks 1–4)",
                duration_weeks=4,
                description="Higher rep ranges (4–6), moderate intensity, volume emphasis.",
                weekly_distribution={"SBD Strength": 3, "Accessory Focus": 1, "Metabolic Conditioning": 1},
                target_rpe_range=(6.5, 7.5),
                volume_modifier=1.2,
                exercise_bias_tags=["squat_pattern", "hip_hinge", "push_horizontal"],
                coaching_notes="Build work capacity in competition lifts. Track bar speed.",
            ),
            TrainingBlock(
                block_type=BlockType.INTENSIFICATION,
                name="Intensification (Weeks 5–9)",
                duration_weeks=5,
                description="Lower rep ranges (2–4), higher intensity, volume drops 20–25%.",
                weekly_distribution={"SBD Strength": 3, "Accessory Focus": 1},
                target_rpe_range=(7.5, 9.0),
                volume_modifier=0.85,
                retest_at_end=False,
                exercise_bias_tags=["squat_pattern", "hip_hinge", "push_horizontal"],
                coaching_notes="Autoregulate: hit top set then back-off. No grinding.",
            ),
            TrainingBlock(
                block_type=BlockType.PEAK,
                name="Peak (Weeks 10–11)",
                duration_weeks=2,
                description="Competition-specific singles and doubles, maximal intent.",
                weekly_distribution={"SBD Strength": 2, "Neural Priming": 1},
                target_rpe_range=(8.5, 9.5),
                volume_modifier=0.65,
                retest_at_end=True,
                exercise_bias_tags=["squat_pattern", "hip_hinge", "push_horizontal"],
                coaching_notes="Trust the process. Openers should feel easy.",
            ),
            TrainingBlock(
                block_type=BlockType.DELOAD,
                name="Deload (Week 12)",
                duration_weeks=1,
                description="Reduce volume 50%, keep intensity moderate. Active recovery.",
                weekly_distribution={"Accessory Focus": 1, "Active Recovery": 1},
                target_rpe_range=(5.0, 6.5),
                volume_modifier=0.5,
                coaching_notes="Restore CNS and structural readiness. No new PRs.",
            ),
        ],
    )


def _wl_template() -> PlanTemplate:
    """Olympic lifting 9-week technique → strength → peak."""
    return PlanTemplate(
        name="Olympic Lifting 9-Week Cycle",
        domain="weightlifting",
        goal="OlympicLifts",
        description="Technique development into strength phase into competition peak.",
        blocks=[
            TrainingBlock(
                block_type=BlockType.TECHNIQUE,
                name="Technique (Weeks 1–3)",
                duration_weeks=3,
                description="Position work, power variations, and pulls at 60–80% of max.",
                weekly_distribution={"Weightlifting Technique": 3, "Strength Pulls": 1},
                target_rpe_range=(5.5, 7.5),
                volume_modifier=1.0,
                exercise_bias_tags=["olympic_lifting", "hip_hinge"],
                coaching_notes="Prioritize positions over weights. Film every session.",
            ),
            TrainingBlock(
                block_type=BlockType.STRENGTH_FOCUS,
                name="Strength (Weeks 4–7)",
                duration_weeks=4,
                description="Full lifts + back squat / front squat intensity, higher % work.",
                weekly_distribution={"Weightlifting Technique": 2, "Strength Pulls": 2},
                target_rpe_range=(7.5, 8.5),
                volume_modifier=1.1,
                retest_at_end=False,
                exercise_bias_tags=["olympic_lifting", "squat_pattern", "hip_hinge"],
                coaching_notes="Strength blocks should improve pull speed and catch confidence.",
            ),
            TrainingBlock(
                block_type=BlockType.PEAK,
                name="Peak (Weeks 8–9)",
                duration_weeks=2,
                description="Heavy singles and doubles, competition-day timing rehearsal.",
                weekly_distribution={"Weightlifting Technique": 2, "Neural Priming": 1},
                target_rpe_range=(8.5, 9.5),
                volume_modifier=0.65,
                retest_at_end=True,
                coaching_notes="Attempt weights that feel 'heavy but not maximal' on first contact.",
            ),
        ],
    )


def _running_base_template(goal: TrainingGoal = "Running") -> PlanTemplate:
    """Running 10-week base → threshold → race-specific → taper."""
    names = {
        "Running": "Running Base-to-Race 10-Week Cycle",
        "HalfMarathon": "Half Marathon 10-Week Cycle",
        "FullMarathon": "Marathon Build 10-Week Cycle",
        "Sprinting": "Sprint Development 8-Week Cycle",
    }
    return PlanTemplate(
        name=names.get(goal, "Running Cycle"),
        domain="running",
        goal=goal,
        description="Aerobic base building into threshold and race-specific work.",
        blocks=[
            TrainingBlock(
                block_type=BlockType.BASE,
                name="Base (Weeks 1–4)",
                duration_weeks=4,
                description="Zone 2 volume, easy effort, structural adaptation.",
                weekly_distribution={"Aerobic Base": 3, "Active Recovery": 1},
                target_rpe_range=(4.0, 6.0),
                volume_modifier=1.0,
                exercise_bias_tags=["aerobic_base", "running_economy"],
                coaching_notes="Keep all runs truly easy — 'could hold a conversation'.",
            ),
            TrainingBlock(
                block_type=BlockType.THRESHOLD,
                name="Threshold (Weeks 5–7)",
                duration_weeks=3,
                description="Tempo runs and threshold intervals at RPE 7–8.",
                weekly_distribution={"Threshold Work": 2, "Aerobic Base": 1, "Active Recovery": 1},
                target_rpe_range=(6.5, 8.0),
                volume_modifier=1.05,
                exercise_bias_tags=["lactate_threshold", "aerobic_base"],
                coaching_notes="Threshold pace: you can speak in fragments, not sentences.",
            ),
            TrainingBlock(
                block_type=BlockType.RACE_SPECIFIC,
                name="Race-Specific (Weeks 8–9)",
                duration_weeks=2,
                description="Goal-pace segments, race simulation, VO2max work.",
                weekly_distribution={"Threshold Work": 2, "Aerobic Base": 1},
                target_rpe_range=(7.0, 8.5),
                volume_modifier=0.9,
                retest_at_end=True,
                exercise_bias_tags=["lactate_threshold", "running_economy"],
                coaching_notes="Race efforts should feel hard but controlled.",
            ),
            TrainingBlock(
                block_type=BlockType.TAPER,
                name="Taper (Week 10)",
                duration_weeks=1,
                description="Reduce mileage 40–50%. Keep short fast segments.",
                weekly_distribution={"Aerobic Base": 2, "Speed": 1},
                target_rpe_range=(5.0, 7.5),
                volume_modifier=0.55,
                coaching_notes="Trust the training. Tired legs early taper = legs are adapting.",
            ),
        ],
    )


def _gymnastics_template() -> PlanTemplate:
    """Gymnastics 8-week prerequisites → static strength → dynamic skill."""
    return PlanTemplate(
        name="Gymnastics 8-Week Skill Cycle",
        domain="gymnastics",
        goal="Gymnastics",
        description="Foundation mobility → static strength → dynamic skill acquisition.",
        blocks=[
            TrainingBlock(
                block_type=BlockType.PREREQUISITES,
                name="Prerequisites (Weeks 1–2)",
                duration_weeks=2,
                description="Wrist prep, shoulder mobility, core activation, thoracic work.",
                weekly_distribution={"Gymnastics Skill": 2, "Active Recovery": 1},
                target_rpe_range=(4.0, 6.5),
                volume_modifier=0.8,
                exercise_bias_tags=["overhead_stability", "hip_mobility", "thoracic_mobility"],
                coaching_notes="No pain in wrists or shoulders. Prehab first.",
            ),
            TrainingBlock(
                block_type=BlockType.STRENGTH_FOCUS,
                name="Static Strength (Weeks 3–5)",
                duration_weeks=3,
                description="Handstand holds, ring support, planche progressions, L-sit.",
                weekly_distribution={"Gymnastics Skill": 3, "Bodyweight Strength": 1},
                target_rpe_range=(6.0, 8.0),
                volume_modifier=1.0,
                exercise_bias_tags=["gymnastics_skill", "overhead_stability"],
                coaching_notes="Quality positions > duration. 15 sec quality > 30 sec broken.",
            ),
            TrainingBlock(
                block_type=BlockType.SKILL,
                name="Dynamic Skill (Weeks 6–8)",
                duration_weeks=3,
                description="Muscle-ups, kip transitions, bar skills, tempo work.",
                weekly_distribution={"Gymnastics Skill": 3, "Bodyweight Strength": 1},
                target_rpe_range=(6.5, 8.0),
                volume_modifier=1.0,
                retest_at_end=True,
                exercise_bias_tags=["gymnastics_skill", "pull_vertical"],
                coaching_notes="Speed of movement matters — pull hard through transitions.",
            ),
        ],
    )


def _grip_template() -> PlanTemplate:
    """Grip 6-week tissue prep → strength → support cycle."""
    return PlanTemplate(
        name="Grip 6-Week Cycle",
        domain="grip",
        goal="Grip",
        description="Connective tissue conditioning → crush / support / pinch strength → peak.",
        blocks=[
            TrainingBlock(
                block_type=BlockType.GRIP_TISSUE,
                name="Tissue Prep (Weeks 1–2)",
                duration_weeks=2,
                description="Low-load tendon conditioning, blood-flow work, eccentric finger flexion.",
                weekly_distribution={"Grip Recovery": 2, "Grip & Support": 1},
                target_rpe_range=(4.0, 6.0),
                volume_modifier=0.7,
                exercise_bias_tags=["grip"],
                coaching_notes="No pain in fingers or pulleys. Start low.",
            ),
            TrainingBlock(
                block_type=BlockType.STRENGTH_FOCUS,
                name="Strength Phase (Weeks 3–5)",
                duration_weeks=3,
                description="Farmer carries, dead hangs, pinch block, hub loading.",
                weekly_distribution={"Grip & Support": 3, "Active Recovery": 1},
                target_rpe_range=(6.5, 8.0),
                volume_modifier=1.0,
                exercise_bias_tags=["grip", "carry"],
                coaching_notes="Track weights and times. Grip fatigues fast — rest fully between sets.",
            ),
            TrainingBlock(
                block_type=BlockType.PEAK,
                name="Peak Week (Week 6)",
                duration_weeks=1,
                description="Max effort holds and carries. Retest grip maxes.",
                weekly_distribution={"Grip & Support": 2},
                target_rpe_range=(8.0, 9.5),
                volume_modifier=0.65,
                retest_at_end=True,
                coaching_notes="Test fresh — at least 72h after last grip session.",
            ),
        ],
    )


# Registry
_TEMPLATES: dict[TrainingGoal, PlanTemplate] = {
    "Powerlifting": _pl_template(),
    "OlympicLifts": _wl_template(),
    "Running": _running_base_template("Running"),
    "HalfMarathon": _running_base_template("HalfMarathon"),
    "FullMarathon": _running_base_template("FullMarathon"),
    "Gymnastics": _gymnastics_template(),
    "Calisthenics": _gymnastics_template(),  # shared template
    "Grip": _grip_template(),
}


def get_plan_template(goal: TrainingGoal) -> PlanTemplate | None:
    """Return the plan template for a goal, or None if no template exists."""
    return _TEMPLATES.get(goal)


# ---------------------------------------------------------------------------
# Phase / deload / retest logic
# ---------------------------------------------------------------------------

def deload_triggered(state: UnifiedStateVector) -> bool:
    """
    Return True if current athlete state warrants a deload block.

    Criteria (any one sufficient):
    - Any fatigue axis > 60
    - Mean fatigue > 45
    - Any tissue axis > 55
    """
    f = state.fatigue_f
    t = state.tissue_t

    fatigue_values = [f.cns, f.muscular, f.metabolic, f.structural, f.tendon, f.grip]
    tissue_values = [
        t.shoulder, t.elbow, t.wrist, t.lumbar,
        t.hip, t.knee, t.ankle, t.finger,
    ]

    if any(v > 60.0 for v in fatigue_values):
        return True
    if sum(fatigue_values) / len(fatigue_values) > 45.0:
        return True
    if any(v > 55.0 for v in tissue_values):
        return True
    return False


def retest_due(
    block: TrainingBlock,
    sessions_completed_in_block: int,
    sessions_per_week: int = 4,
) -> bool:
    """
    Return True if it's time for a benchmark retest based on block completion.
    """
    if not block.retest_at_end:
        return False
    target_sessions = block.duration_weeks * sessions_per_week
    return sessions_completed_in_block >= int(target_sessions * 0.85)


@dataclass
class BlockProgress:
    """Current position within a training cycle."""
    goal: TrainingGoal
    template: PlanTemplate
    current_block: TrainingBlock
    week_in_block: int
    total_week: int
    deload_recommended: bool
    retest_recommended: bool
    sessions_this_week: dict[str, int]   # session type → count recommended


def get_block_progress(
    state: UnifiedStateVector,
    goal: TrainingGoal,
    cycle_week: int = 1,
    sessions_completed_in_block: int = 0,
) -> BlockProgress | None:
    """
    Return the athlete's current position in a template-guided training cycle.

    `cycle_week`: week number within the overall cycle (1-indexed).
    `sessions_completed_in_block`: number of main sessions done in current block.

    Returns None if no template exists for the goal.
    """
    template = get_plan_template(goal)
    if template is None:
        return None

    block = template.block_at_week(cycle_week)
    if block is None:
        block = template.blocks[-1]

    # Which week within this block?
    week_offset = 0
    for b in template.blocks:
        if b is block:
            break
        week_offset += b.duration_weeks
    week_in_block = max(1, cycle_week - week_offset)

    deload = deload_triggered(state)
    retest = retest_due(block, sessions_completed_in_block)

    return BlockProgress(
        goal=goal,
        template=template,
        current_block=block,
        week_in_block=week_in_block,
        total_week=cycle_week,
        deload_recommended=deload,
        retest_recommended=retest,
        sessions_this_week=dict(block.weekly_distribution),
    )


def weekly_session_distribution(
    state: UnifiedStateVector,
    goal: TrainingGoal,
    cycle_week: int = 1,
) -> dict[str, int]:
    """
    Return recommended session types and counts for the current week.

    Falls back to a simple default if no template exists for the goal.
    """
    progress = get_block_progress(state, goal, cycle_week)
    if progress is not None:
        if progress.deload_recommended:
            return {"Active Recovery": 2, "Technique / Flow": 1}
        return progress.sessions_this_week

    # Generic fallback distribution
    return {
        "Strength": 2,
        "Metabolic Conditioning": 1,
        "Active Recovery": 1,
    }


# ---------------------------------------------------------------------------
# Live periodization envelope (ADR-0029)
#
# The single source of truth the prescriber uses to make `week_number` actually
# shape a prescription: it maps a week within a block to a phase + volume modifier
# + RPE target. The PlanTemplate machinery above is goal-specific reference
# structure; this function is what the live engine consults.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PhaseEnvelope:
    phase: str
    volume_modifier: float
    rpe_low: float
    rpe_high: float


def periodization_envelope(
    duration_weeks: int,
    week_number: int,
    deload_every_n_weeks: int = 4,
) -> PhaseEnvelope:
    """Resolve a block week to its periodization envelope (ADR-0029).

    A generic accumulation → intensification → peak progression with periodic
    deloads and an end taper. The prescriber applies ``volume_modifier`` to the
    session and targets ``rpe_low..rpe_high``; state may pull the prescription
    *down* within this envelope but never above it.
    """
    weeks = max(1, int(duration_weeks))
    wk = max(1, int(week_number))
    deload_n = max(0, int(deload_every_n_weeks))

    if wk >= weeks and weeks >= 3:
        return PhaseEnvelope("taper", 0.55, 6.0, 8.0)
    if deload_n and wk % deload_n == 0:
        return PhaseEnvelope("deload", 0.5, 5.0, 6.5)

    frac = wk / weeks
    if frac <= 0.4:
        return PhaseEnvelope("accumulation", 1.15, 6.5, 7.5)
    if frac <= 0.75:
        return PhaseEnvelope("intensification", 0.9, 7.5, 8.5)
    return PhaseEnvelope("peak", 0.7, 8.5, 9.5)
