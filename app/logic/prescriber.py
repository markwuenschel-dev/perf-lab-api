"""
Candidate-based prescription engine (orchestrator).

Responsibilities:
- Generate goal-aware + state-aware SessionCandidate pools
- Apply hard safety overrides
- Orchestrate scoring + block context + equipment/weak-point logic
- Finalize the chosen prescription with explainability

Core domain types (`SessionCandidate`, scoring primitives, readiness helpers)
live in `app.logic.constraint_engine.candidate`.

Static session content (what sessions exist per goal) lives in
`app.logic.candidate_library` as `CandidateTemplate` definitions.
Dynamic scoring (how templates are scored for an athlete) also lives there
as `score_template()`.

The constraint_engine package also provides template-driven validation
(`SessionValidator`) used by the coaching template system.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from app.logic.candidate_library import get_templates, score_template
from app.logic.constraint_engine.candidate import (
    SessionCandidate,
    WorkloadVolume,
)
from app.logic.constraint_engine.candidate import (
    overall_readiness as _readiness,
)
from app.logic.constraint_engine.candidate import (
    score_candidate as _score_candidate,
)
from app.logic.constraint_labels import (
    EQUIPMENT_BODYWEIGHT_ONLY,
    EQUIPMENT_FALLBACK_BODYWEIGHT,
    EQUIPMENT_FILTERED,
    EQUIPMENT_UNCONFIGURED,
    STRUCTURE_CIRCUIT_UNREALIZED,
    describe_constraints,
)
from app.logic.deload_need import compute_deload_need
from app.logic.difficulty import LEGACY_TRANSFORM
from app.logic.domain_vocab import GOAL_TO_DOMAIN, canonical_domain
from app.logic.exercise_slot import (
    CatalogExercise,
    CircuitSpec,
    ExerciseSlot,
    equipment_available,
    preferred_load_types,
    resolve_slots,
)
from app.logic.planned_session_slots import SlotBinding, binding_for
from app.logic.planning import (
    INTENSITY_MEDIUM,
    normalize_intensity,
    periodization_envelope,
)
from app.logic.planning_constraints import (
    ConstraintApplication,
    ResolvedPlanningConstraint,
    apply_constraints,
)
from app.logic.prescription_finalize import finalize_prescription
from app.schemas.prescription import (
    ExercisePrescription,
    WorkoutPrescription,
    circuit_station_for,
    endurance_block_for,
    project_exercises,
    structure_from_exercises,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT, TrainingGoal
from app.schemas.workout_structure import CircuitBlock, WorkoutStructure

# Note: SessionCandidate, scoring, and readiness helpers now live in
# app.logic.constraint_engine.candidate for better separation of concerns.
# Static content + scoring dispatch lives in app.logic.candidate_library.

# Default volume multiplier applied to a deload session's prescribed duration
# when the active block does not carry its own `deload_volume_factor`. Mirrors
# the MesocycleBlock.deload_volume_factor column default (app.models.mesocycle).
DEFAULT_DELOAD_VOLUME_FACTOR = 0.6

# When adherence friction reaches this level, bias the prescription toward
# lighter/variety/recovery work to rebuild adherence.
RECENT_SKIPS_BIAS_THRESHOLD = 2
_ADHERENCE_FRIENDLY_TYPE_KEYWORDS = ("variety", "recovery", "maintenance", "skill")

# A session the athlete modified is weaker evidence of friction than one they did
# not do at all, so it counts for less rather than the same (ADR-0070 precedence).
# The two counts are disjoint by construction — the aggregate that produces them
# credits any session to exactly one — so combining them cannot double-penalise.
# At weight 0 this collapses to the historical skips-only behaviour, which is why
# a block with no reported modifications prescribes exactly what it did before.
MODIFICATION_FRICTION_WEIGHT = 0.5

# Block session-preference bounds (Phase 3a). A block's explicit
# `target_session_minutes` overrides the periodization-scaled duration, but is
# clamped to a sane band and never allowed to inflate far past the winning
# template's own length.
TARGET_DURATION_MIN_MINUTES = 30
TARGET_DURATION_MAX_MINUTES = 120
TARGET_DURATION_TEMPLATE_CAP_FACTOR = 1.5
# A target session shorter than the template's own duration shouldn't pile on
# accessories — cap appended accessories at this many even under "high" emphasis.
SHORT_TARGET_ACCESSORY_CAP = 1

# Objective taper + domain-emphasis (Phase 4a — goal-anchored program). The
# window itself (whether the nearest active objective's target_date qualifies)
# is evaluated upstream by app.services.objective_service.active_objective_signals
# — the prescriber only consumes the resulting `objective_taper` bool. Both
# entry points (prescribe_for_athlete and /planning/today) must populate these
# block_context keys identically (Phase 0/3a lesson).
OBJECTIVE_TAPER_FACTOR = 0.7
# Mirrors the existing block-category boost magnitude (see _score_with_context).
OBJECTIVE_DOMAIN_BOOST = 0.15


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structural / tendon fatigue safety policy (INT-05)
# ---------------------------------------------------------------------------
# Explicit component bands + a conjunctive joint rule. Structural and tendon are
# NOT added — their additive commensurability is unestablished; one component cannot
# arithmetically compensate for the other. Thresholds are provisional expert-prior
# PLANNING thresholds, not validated injury-risk cutoffs (Soligard 2016; Thorpe 2017).
# Held in a versioned object so logs/tests/retunes identify the exact policy.


@dataclass(frozen=True)
class StructuralFatigueSafetyPolicy:
    structural_critical: float
    tendon_critical: float
    joint_structural_high: float
    joint_tendon_high: float
    version: str


STRUCTURAL_FATIGUE_SAFETY_POLICY_V1 = StructuralFatigueSafetyPolicy(
    structural_critical=80.0,
    tendon_critical=70.0,
    joint_structural_high=70.0,
    joint_tendon_high=60.0,
    version="structural_fatigue_safety_policy_v1",
)


def _structural_recovery_trigger(
    structural: float,
    tendon: float,
    policy: StructuralFatigueSafetyPolicy = STRUCTURAL_FATIGUE_SAFETY_POLICY_V1,
) -> str | None:
    """Return the hard-stop Recovery trigger reason, or None.

    Reads ONLY the structural and tendon fatigue components — grip, tissue averages,
    and the legacy ``f_struct_damage`` blend have no authority. Inclusive (``>=``)
    bands; the individual-critical checks precede the conjunctive joint rule, and no
    component is converted into another by addition.
    """
    if structural >= policy.structural_critical:
        return "structural_critical"
    if tendon >= policy.tendon_critical:
        return "tendon_critical"
    if structural >= policy.joint_structural_high and tendon >= policy.joint_tendon_high:
        return "jointly_high"
    return None


def _structural_recovery_rationale(reason: str, structural: float, tendon: float) -> str:
    """Trigger-specific rationale naming the component/combination that fired. These
    are precautionary planning signals — no injury-prediction claim."""
    if reason == "structural_critical":
        return (
            f"Structural fatigue critical ({structural:.1f}%). "
            "Prioritize recovery before further structural loading."
        )
    if reason == "tendon_critical":
        return (
            f"Tendon fatigue critical ({tendon:.1f}%). "
            "Prioritize recovery before further tendon loading."
        )
    return (
        f"Structural and tendon fatigue jointly elevated "
        f"(structural {structural:.1f}%, tendon {tendon:.1f}%). "
        "Prioritize recovery before further loading."
    )


def _classify_counterfactual(legacy_trigger: bool, component_trigger: bool) -> str:
    """Agreement class between the legacy blend and the component decision."""
    if legacy_trigger and component_trigger:
        return "both"
    if legacy_trigger:
        return "legacy_only"
    if component_trigger:
        return "component_only"
    return "neither"


def _log_structural_safety_counterfactual(
    state: UnifiedStateVector, component_reason: str | None
) -> None:
    """Counterfactual telemetry (INT-05): the legacy ``f_struct_damage > 70`` blend
    outcome vs the component decision. The legacy result is telemetry ONLY — it has
    no veto. Structured-logging seam only (no schema); INFO on disagreement, else DEBUG.
    """
    tissue_vals = state.tissue_t.model_dump().values()
    tissue_avg = sum(tissue_vals) / max(1, len(tissue_vals))
    legacy_blend = (
        state.fatigue_f.structural + state.fatigue_f.tendon
        + 0.15 * state.fatigue_f.grip + 0.1 * tissue_avg
    )
    legacy_trigger = legacy_blend > 70.0
    component_trigger = component_reason is not None
    classification = _classify_counterfactual(legacy_trigger, component_trigger)
    emit = logger.info if legacy_trigger != component_trigger else logger.debug
    emit(
        "structural-safety counterfactual: class=%s legacy_blend=%.1f legacy_trigger=%s "
        "component_trigger=%s reason=%s structural=%.1f tendon=%.1f grip=%.1f "
        "tissue_avg=%.1f policy=%s",
        classification, legacy_blend, legacy_trigger, component_trigger, component_reason,
        state.fatigue_f.structural, state.fatigue_f.tendon, state.fatigue_f.grip,
        tissue_avg, STRUCTURAL_FATIGUE_SAFETY_POLICY_V1.version,
    )


# ---------------------------------------------------------------------------
# Safety override candidates (always placed first; skip scoring)
# ---------------------------------------------------------------------------

#: Clinical severity of each hard-stop override, ranked by how much training it still PERMITS
#: — lower permits less. The winner used to be whichever rule happened to be written first
#: (``_safety_candidates(state)[0]``), so an athlete who was systemically overloaded AND had a
#: sore knee was sent to swim/bike because the knee rule sat higher in the source file.
#:
#: Physiological assumption: systemic autonomic overload outranks a regional substitution —
#: you can swim on a sore knee, you cannot swim your way out of systemic fatigue. Complete
#: rest permits nothing, so it is the most restrictive instruction and wins whenever it fires.
SAFETY_SEVERITY: dict[str, int] = {
    "safety_systemic_metabolic": 4,  # passive rest — no training at all
    "safety_structural_damage": 3,   # 20 min mobility / light movement
    "safety_regional_tissue": 2,     # 30 min low-impact substitution
    "safety_tendon_structural": 1,   # 35 min isometrics + blood-flow work
}


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

    # INT-05: the structural/tendon safety decision reads the authoritative fatigue
    # COMPONENTS, not the lossy f_struct_damage blend. The critical Recovery branch is
    # evaluated first and SUPPRESSES the milder Tissue Deload — at most one
    # structural/tendon safety candidate is emitted (never both, leaving downstream
    # ranking to guess which safety instruction wins).
    struct_reason = _structural_recovery_trigger(
        state.fatigue_f.structural, state.fatigue_f.tendon
    )
    _log_structural_safety_counterfactual(state, struct_reason)
    if struct_reason is not None:
        overrides.append(SessionCandidate(
            type="Recovery",
            focus="Mobility / Light Movement",
            rationale=_structural_recovery_rationale(
                struct_reason, state.fatigue_f.structural, state.fatigue_f.tendon
            ),
            duration_min=20,
            branch_id="safety_structural_damage",
            is_safety_override=True,
        ))
    elif state.fatigue_f.tendon > 55.0 or state.fatigue_f.structural > 65.0:
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

    # Ranked by declared severity, most restrictive first. Stable for equal severity, and
    # independent of the order the rules above happen to be written or evaluated in.
    return sorted(overrides, key=lambda c: SAFETY_SEVERITY[c.branch_id], reverse=True)


def _readiness_redirect(
    state: UnifiedStateVector,
    goal: TrainingGoal,
    kpi: dict[str, float],
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

def _candidate_domain(goal: str) -> str:
    """Resolve a goal / block-goal to the canonical domain the prescriber keys on (ADR-0038)."""
    return GOAL_TO_DOMAIN.get(goal) or canonical_domain(str(goal))


def _generate_candidates(
    state: UnifiedStateVector,
    goal: TrainingGoal,
    kpi: dict[str, float],
    recent: list[dict[str, Any]] | None,
    readiness_override: float | None = None,
    domain_override: str | None = None,
    session_category: str | None = None,
    category_owns_day: bool = True,
) -> list[SessionCandidate]:
    """Build the goal-specific candidate pool via the CandidateTemplate library.

    Dispatches on canonical domain, not the raw goal, so BlockGoal-derived
    intent (e.g. Hyrox/CrossFit → "mixed") reaches a real candidate pool.
    Running and gymnastics use the original goal for sub-distinctions.

    ``readiness_override`` (ADR-0052) lets the caller pass the wellness-combined readiness
    *score* (0–1) so a bad night transparently nudges candidate scoring; when ``None`` the
    modeled-only ``overall_readiness`` is used. This is the score channel only — confidence
    never enters here.
    """
    # ``domain_override`` is today's planned slot speaking for itself (ADR-0030 concurrent
    # blocks): a Strength block with a running day builds that day from running templates.
    # Without it the pool always came from the block goal, so a modality mix could only
    # relabel days.
    domain = domain_override or _candidate_domain(goal)
    r = readiness_override if readiness_override is not None else _readiness(state)
    # A planned category may own its day's pool whatever the block goal (phase 5.6), unless a
    # readiness redirect is competing (see ``template_pool``).
    templates = get_templates(
        domain, kpi, goal=str(goal), state=state, session_category=session_category,
        category_owns_day=category_owns_day,
    )
    return [score_template(t, state, kpi, readiness=r) for t in templates]


# ---------------------------------------------------------------------------
# Thin wrappers — kept so existing test imports remain valid
# ---------------------------------------------------------------------------

def _gen_strength_candidates(
    state: UnifiedStateVector,
    kpi: dict[str, float],
    recent: list[dict[str, Any]] | None,
) -> list[SessionCandidate]:
    """Thin wrapper around the template library for the strength domain."""
    r = _readiness(state)
    templates = get_templates("strength", kpi, state=state)
    return [score_template(t, state, kpi, readiness=r) for t in templates]


def _gen_running_candidates(
    state: UnifiedStateVector,
    kpi: dict[str, float],
    recent: list[dict[str, Any]] | None,
    goal: TrainingGoal,
) -> list[SessionCandidate]:
    """Thin wrapper around the template library for the running domain."""
    r = _readiness(state)
    templates = get_templates("running", kpi, goal=str(goal), state=state)
    return [score_template(t, state, kpi, readiness=r) for t in templates]


def _gen_mixed_candidates(
    state: UnifiedStateVector,
    kpi: dict[str, float],
    recent: list[dict[str, Any]] | None,
) -> list[SessionCandidate]:
    """Thin wrapper around the template library for the mixed domain.

    Conditioning-primary (the MetCon candidates) plus one strength-endurance
    option, so concurrent blocks have a real strength day. The planned-session
    category boost (ADR-0030) selects the right one per scheduled slot.
    """
    r = _readiness(state)
    templates = get_templates("mixed", kpi, state=state)
    return [score_template(t, state, kpi, readiness=r) for t in templates]


# ---------------------------------------------------------------------------
# Primary entry point
# ---------------------------------------------------------------------------

# Domains whose prescriptions carry countable working sets, so a workload preference has
# something honest to move. Endurance, conditioning and GPP sessions express work as duration
# and free-text targets; adding a "set" to a Zone-2 run would be noise dressed as a setting.
# Widening this set means giving those domains a real target first (see ADR-0062's AU ledger).
INTENSITY_SET_DOMAINS: frozenset[str] = frozenset(
    {"strength", "powerlifting", "hypertrophy", "power", "weightlifting"}
)


def _is_recovery_week(block: dict[str, Any], week_n: int, weeks_total: int) -> bool:
    """A flagged deload, or the taper week the envelope reserves at the end of a block."""
    if block.get("is_deload"):
        return True
    return bool(week_n and weeks_total and week_n >= weeks_total and weeks_total >= 3)


def _apply_intensity_sets(
    rx: WorkoutPrescription,
    intensity: str,
    domain: str,
    *,
    is_recovery_week: bool,
    workload_volume: WorkloadVolume = "scaled",
) -> None:
    """Move working sets by the block's workload preference, and always say what happened.

    A session whose template declares ``workload_volume="fixed"`` keeps its authored volume:
    its volume is the protocol, not a knob (the phase-5 potentiation primer).

    Every no-op is reported with its reason. A preference that silently does nothing is the
    defect this whole slice exists to avoid: the athlete chose "hard" and is owed either more
    work or the reason there isn't any.
    """
    if intensity == INTENSITY_MEDIUM:
        return
    reason: str | None = None
    if is_recovery_week:
        reason = "recovery-week"
    elif workload_volume == "fixed":
        reason = "fixed-volume"
    elif domain not in INTENSITY_SET_DOMAINS:
        reason = f"no-set-targets:{domain}"

    if reason is None:
        # Phase 2.3 authorship flip: the workload preference edits the STRUCTURE, and the
        # exercise list is re-projected from it. Nothing downstream mutates `exercises`
        # directly any more — structure is the workout, the list is a view of it.
        # Phase 3.1: the workload change goes through the difficulty CONTRACT rather than
        # editing sets inline. LEGACY_TRANSFORM reproduces today's rule exactly (±1 set), so
        # this is behaviour-neutral; 3.2 swaps in real per-family policies, and none of them
        # goes live until its effect under both dose engines has been measured.
        before = structure_from_exercises(rx.exercises, rx.structure)
        after = LEGACY_TRANSFORM.apply(before, intensity)
        moved = sum(
            1
            for old_block, new_block in zip(before, after, strict=True)
            if old_block != new_block
        )
        if moved:
            rx.structure = after
            rx.exercises = project_exercises(after)
        reason = None if moved else "sets-at-floor"

    if rx.why is None:
        return
    rx.why.constraints_applied.append(
        f"block:intensity={intensity}" if reason is None else f"block:intensity={intensity}(no-op:{reason})"
    )


def _finalize(
    candidate: SessionCandidate,
    state: UnifiedStateVector,
    goal: TrainingGoal,
    recent_sessions: list[dict[str, Any]] | None,
) -> WorkoutPrescription:
    rx = WorkoutPrescription(
        type=candidate.type,
        focus=candidate.focus,
        rationale=candidate.rationale,
        duration_min=candidate.duration_min,
    )
    return finalize_prescription(
        rx, state, goal, candidate.branch_id,
        recent_sessions=recent_sessions,
        session_candidate=candidate,
    )


# Each list may name only exercises whose catalog `equipment_required` is covered by the key
# it sits under; "bodyweight" is what an athlete with no matching equipment gets, so its
# exercises require nothing. Guarded by tests/test_fallback_exercise_equipment.py.
_EQUIPMENT_EXERCISE_MAP: dict[str, list[tuple[str, str, str]]] = {
    "barbell": [
        ("Back Squat", "4", "4-6"),
        ("Romanian Deadlift", "3", "5-8"),
        ("Bench Press", "4", "4-6"),
    ],
    "dumbbells": [
        ("Reverse Lunge", "4", "8-10/side"),
        ("DB RDL", "3", "8-10"),
        ("DB Floor Press", "3", "8-12"),
    ],
    "pullup_bar": [
        ("Pull-up", "4", "4-8"),
        ("Hanging Knee Raise", "3", "10-15"),
    ],
    "bodyweight": [
        ("Air Squat", "4", "12-15"),
        ("Push-up", "4", "8-15"),
        ("Lunges", "3", "8-12/side"),
    ],
}


# ---------------------------------------------------------------------------
# Block session preferences (Phase 3a — goal-anchored program)
#
# A training block can carry `accessory_emphasis` / `accessory_focus` /
# `target_session_minutes` (MesocycleBlock, see app.models.mesocycle). The
# prescriber appends accessory slots after the winning template's primary
# `exercise_slots` and nudges `rx.duration_min` toward the target — see the
# end of recommend_next_session for the full contract.
# ---------------------------------------------------------------------------

# Tag → accessory catalog. Movement-pattern focus tags an athlete can request
# via `accessory_focus`, plus the WEAK_POINT_TAGS values that also work as
# catalog keys directly ("posterior_chain", "single_leg").
_ACCESSORY_BY_TAG: dict[str, list[tuple[str, str, str]]] = {
    "posterior_chain": [
        ("Romanian Deadlift", "3", "5-8"),
        ("Back Extension", "3", "12-15"),
    ],
    "push": [
        ("Dumbbell Shoulder Press", "3", "8-10"),
        ("Dips", "3", "8-12"),
    ],
    "pull": [
        ("Chest-Supported Row", "3", "10-12"),
        ("Face Pull", "3", "15-20"),
    ],
    "core": [
        ("Hanging Leg Raise", "3", "10-15"),
        ("Plank", "3", "45-60s"),
    ],
    "single_leg": [
        ("Bulgarian Split Squat", "3", "8-10/side"),
        ("Walking Lunge", "3", "10-12/side"),
    ],
}

# Generic accessories used to fill out an emphasis's accessory count when the
# focus / weak-point tags don't yield enough matches.
_GENERIC_ACCESSORIES: list[tuple[str, str, str]] = [
    ("Face Pull", "3", "15-20"),
    ("Plank", "3", "45-60s"),
    ("Walking Lunge", "3", "10-12/side"),
    ("Dumbbell Shoulder Press", "3", "8-10"),
]

# Max accessory slots appended per `accessory_emphasis` value. Missing/None
# emphasis is treated as "balanced" by the caller.
_ACCESSORY_COUNT_BY_EMPHASIS: dict[str, int] = {
    "minimal": 0,
    "balanced": 2,
    "high": 4,
}


def _select_accessories(
    count: int,
    focus_tags: list[str] | None,
    weak_point_tags: list[str] | None,
    existing_names: set[str],
    is_available: Callable[[str], bool] | None = None,
    skipped_out: list[str] | None = None,
) -> list[tuple[str, str, str]]:
    """Pick up to `count` accessory slots, preferring `focus_tags`, then
    falling back to `weak_point_tags`, then generic accessories. Skips names
    already present among `existing_names` (the template's own slots) to
    avoid duplicate entries.

    An accessory the athlete cannot do with their equipment (``is_available`` returns False) is
    not prescribed: it is recorded in ``skipped_out``, so the explanation can say so, and the next
    option takes its place. ``is_available=None`` means availability cannot be checked."""
    if count <= 0:
        return []
    tags = [t for t in (focus_tags or []) if t in _ACCESSORY_BY_TAG]
    if not tags:
        tags = [t for t in (weak_point_tags or []) if t in _ACCESSORY_BY_TAG]

    seen = set(existing_names)
    picks: list[tuple[str, str, str]] = []

    def consider(item: tuple[str, str, str]) -> None:
        name = item[0]
        if name in seen:
            return
        seen.add(name)
        if is_available is not None and not is_available(name):
            if skipped_out is not None:
                skipped_out.append(name)
            return
        picks.append(item)

    for tag in tags:
        for item in _ACCESSORY_BY_TAG[tag]:
            if len(picks) >= count:
                break
            consider(item)
        if len(picks) >= count:
            break

    if len(picks) < count:
        for item in _GENERIC_ACCESSORIES:
            if len(picks) >= count:
                break
            consider(item)

    return picks[:count]


def _accessory_availability(
    available_equipment: Sequence[str] | None,
    catalog: list[CatalogExercise] | None,
) -> Callable[[str], bool] | None:
    """The availability check appended accessories must pass — the same one primary slots pass.

    ``None`` when nothing can be checked: equipment never set (no filter, exactly as for slots),
    or no catalog to read requirements from (a pure-logic caller). With equipment set, an
    accessory the catalog does not know has unknown requirements and cannot be promised, so it
    counts as unavailable.
    """
    equipment = frozenset(e.strip().lower() for e in (available_equipment or []) if e and e.strip())
    if not equipment or catalog is None:
        return None
    by_name = {ex.name: ex for ex in catalog}

    def available(name: str) -> bool:
        exercise = by_name.get(name)
        return exercise is not None and equipment_available(exercise, equipment)

    return available


#: Equipment tags that mean "no external equipment". A list holding only these is an athlete who
#: chose bodyweight-only training — a configuration, and not the same thing as an empty list.
_BODYWEIGHT_ONLY_TAGS: frozenset[str] = frozenset({"bodyweight", "none"})

_EquipmentState = Literal["unconfigured", "bodyweight_only", "configured"]


def _equipment_state(available_equipment: Sequence[str] | None) -> _EquipmentState:
    """What the athlete's equipment list says: nothing set, bodyweight only, or equipment.

    The three are stored distinctly on ``AthleteProfile.equipment`` — ``[]``, ``["bodyweight"]``,
    and a list of tags — and select differently: an empty list does not filter at all
    (``exercise_slot.equipment_available``), while ``["bodyweight"]`` filters to movements
    that need nothing.
    """
    tags = {e.strip().lower() for e in (available_equipment or []) if e and e.strip()}
    if not tags:
        return "unconfigured"
    if tags <= _BODYWEIGHT_ONLY_TAGS:
        return "bodyweight_only"
    return "configured"


def _equipment_map_exercises(
    available_equipment: Sequence[str] | None,
) -> tuple[list[ExercisePrescription], bool]:
    """The equipment-map list, and whether it is the bodyweight list because no key matched."""
    equipment = {e.lower() for e in (available_equipment or [])}
    picks: list[tuple[str, str, str]] = []

    for key in ("barbell", "dumbbells", "kettlebell", "machine", "cable", "pullup_bar"):
        if key in equipment and key in _EQUIPMENT_EXERCISE_MAP:
            picks.extend(_EQUIPMENT_EXERCISE_MAP[key])

    used_bodyweight_list = not picks
    if used_bodyweight_list:
        picks.extend(_EQUIPMENT_EXERCISE_MAP["bodyweight"])

    exercises = [
        ExercisePrescription(name=name, sets=int(sets), reps=reps, load_note="Autoregulate by RPE")
        for name, sets, reps in picks[:4]
    ]
    return exercises, used_bodyweight_list


def _exercise_list_for_equipment(available_equipment: list[str] | None) -> list[ExercisePrescription]:
    return _equipment_map_exercises(available_equipment)[0]


@dataclass(frozen=True)
class _ExerciseSelection:
    """The exercises chosen for a session, and the equipment facts true of how they were chosen."""

    exercises: list[ExercisePrescription]
    equipment_codes: list[str]
    #: How many slot choices the equipment preference changed, measured against the same pool
    #: with no preference. ``None`` when no preference was applied (none set, or the slot-less
    #: equipment-map path, which does not rank).
    preference_changes: int | None = None
    #: The slot each exercise was chosen for, aligned with ``exercises`` — how an endurance
    #: slot's work shape reaches the structure. None on the equipment-map path (no slots).
    slots: tuple[ExerciseSlot, ...] | None = None


def _map_selection(available_equipment: Sequence[str] | None) -> _ExerciseSelection:
    exercises, used_bodyweight_list = _equipment_map_exercises(available_equipment)
    state = _equipment_state(available_equipment)
    codes: list[str]
    if state == "bodyweight_only":
        codes = [EQUIPMENT_BODYWEIGHT_ONLY]
    elif used_bodyweight_list:
        # The bodyweight list really was used. Say so — and, separately, that equipment was
        # never set, because those are two different facts.
        codes = [EQUIPMENT_UNCONFIGURED] if state == "unconfigured" else []
        codes.append(EQUIPMENT_FALLBACK_BODYWEIGHT)
    else:
        codes = [EQUIPMENT_FILTERED]
    return _ExerciseSelection(exercises, codes)


_CATALOG_EQUIPMENT_CODE: dict[_EquipmentState, str] = {
    "unconfigured": EQUIPMENT_UNCONFIGURED,
    "bodyweight_only": EQUIPMENT_BODYWEIGHT_ONLY,
    "configured": EQUIPMENT_FILTERED,
}


def _select_exercises(
    exercise_slots: list[ExerciseSlot],
    available_equipment: list[str] | None,
    catalog: list[CatalogExercise] | None = None,
    active_weak_points: list[str] | None = None,
    equipment_preference: Sequence[str] | None = None,
) -> _ExerciseSelection:
    """Resolve the winning template's slots against the exercise catalog (ADR-0016).

    Each slot states what the movement must be; the catalog decides which one it is, filtered
    by the equipment the athlete actually has and biased toward their flagged weak points.
    Competition lifts pin by benchmark code and are never substituted.

    ``catalog is None`` keeps the pre-catalog behaviour so this stays callable as pure logic
    (and so a caller without a database still gets a session). A slot that nothing satisfies
    costs that movement rather than the whole session — the reason is carried in `load_note`
    so an unfillable requirement is visible instead of silently shortening the workout.

    The equipment codes describe the path that actually ran: the bodyweight fallback is
    reported only when the bodyweight list was used, never merely because nothing was set.
    """
    if not exercise_slots or catalog is None:
        return _map_selection(available_equipment)

    # An empty list means "never configured", which is NOT "owns nothing" — see
    # exercise_slot.equipment_available. Only a populated list filters.
    equipment = (
        frozenset(e.strip().lower() for e in available_equipment if e and e.strip())
        if available_equipment
        else None
    )
    preferred = preferred_load_types(equipment_preference)
    resolutions = resolve_slots(
        exercise_slots,
        catalog,
        available_equipment=equipment,
        weak_point_tags=frozenset(active_weak_points or ()),
        preferred_load_types=preferred,
    )

    out: list[ExercisePrescription] = []
    chosen_slots: list[ExerciseSlot] = []
    for res in resolutions:
        if res.chosen is None:
            continue
        chosen_slots.append(res.slot)
        try:
            sets = int(res.slot.sets)
        except ValueError:
            sets = 1
        out.append(
            ExercisePrescription(
                name=res.chosen.name,
                sets=sets,
                reps=res.slot.reps,
                load_note=res.slot.load_note or "Autoregulate by RPE; scale to available equipment",
            )
        )
    if not out:
        return _map_selection(available_equipment)
    changes = (
        sum(1 for res in resolutions if res.chosen is not None and res.preference_changed)
        if preferred
        else None
    )
    return _ExerciseSelection(
        out,
        [_CATALOG_EQUIPMENT_CODE[_equipment_state(available_equipment)]],
        changes,
        tuple(chosen_slots),
    )


def _exercise_list_for_candidate(
    exercise_slots: list[ExerciseSlot],
    available_equipment: list[str] | None,
    catalog: list[CatalogExercise] | None = None,
    active_weak_points: list[str] | None = None,
) -> list[ExercisePrescription]:
    """The exercises of :func:`_select_exercises`, for callers that need only the list."""
    return _select_exercises(exercise_slots, available_equipment, catalog, active_weak_points).exercises


#: How many resolved exercises the displayed session title names before "+N more".
_FOCUS_NAMED_EXERCISES = 3


def _structure_for_selection(
    selection: _ExerciseSelection,
    circuit: CircuitSpec | None = None,
    template_slots: Sequence[ExerciseSlot] = (),
) -> WorkoutStructure:
    """The selected exercises as blocks: an endurance slot's work shape, else a strength block.

    Phase 5.3: this is where a running session becomes an interval or continuous block. The
    exercise list stays exactly what it was — it is the block's compatibility projection.

    Phase 6.1: a template's ``circuit`` turns its station slots into ONE circuit block, named
    after the catalog picks. It is built only when every station resolved, in order; a circuit
    missing a station would be a different session, so the resolved exercises stay strength
    blocks instead (see :func:`_circuit_realized`).
    """
    blocks = structure_from_exercises(selection.exercises)
    for i, slot in enumerate(selection.slots or ()):
        if slot.endurance is not None:
            shaped = endurance_block_for(slot.endurance, selection.exercises[i])
            if shaped is not None:
                blocks[i] = shaped
    span = _circuit_span(selection, circuit, template_slots)
    if circuit is not None and span is not None:
        start, stop = span
        stations = [
            circuit_station_for(shape, ex)
            for shape, ex in zip(circuit.stations, selection.exercises[start:stop], strict=True)
        ]
        blocks[start:stop] = [
            CircuitBlock(label=circuit.label, stations=stations, scheme=circuit.scheme)
        ]
    return blocks


def _circuit_span(
    selection: _ExerciseSelection,
    circuit: CircuitSpec | None,
    template_slots: Sequence[ExerciseSlot],
) -> tuple[int, int] | None:
    """Where the circuit's stations sit in the selection, or None if it cannot be built."""
    if circuit is None or selection.slots is None:
        return None
    wanted = list(template_slots[circuit.first_slot : circuit.first_slot + len(circuit.stations)])
    if len(wanted) != len(circuit.stations):
        return None
    chosen = list(selection.slots)
    for start in range(len(chosen) - len(wanted) + 1):
        if all(a is b for a, b in zip(chosen[start : start + len(wanted)], wanted, strict=True)):
            return start, start + len(wanted)
    return None


def _circuit_realized(
    selection: _ExerciseSelection,
    circuit: CircuitSpec | None,
    template_slots: Sequence[ExerciseSlot],
) -> bool:
    """False only for a template circuit whose stations did not all resolve."""
    return circuit is None or _circuit_span(selection, circuit, template_slots) is not None


def _focus_from_exercises(exercises: list[ExercisePrescription]) -> str:
    """The session title, built from the primary exercises actually prescribed.

    Template titles name exercises ("Leg Press 4×12 + Hack Squat 3×15 …") while slots resolve
    by pattern, so a template title can name movements the session does not contain. The
    template's focus still does its earlier job in scoring and constraint encoding; what the
    athlete reads is rebuilt from the resolved list.
    """
    names = [e.name for e in exercises]
    head = " + ".join(names[:_FOCUS_NAMED_EXERCISES])
    extra = len(names) - _FOCUS_NAMED_EXERCISES
    return f"{head} + {extra} more" if extra > 0 else head


def _plan_outcome_code(
    planned: SlotBinding | None,
    rx: WorkoutPrescription,
    *,
    replaced_reason: str,
) -> str | None:
    """Whether today's planned session is the one prescribed — judged by the outcome.

    The plan can be set aside by a safety override, a hard constraint, a readiness redirect or
    the session validator, and the athlete is owed which of those happened. Read from the
    finalized prescription's branch rather than the intent before finalize, so the statement
    stays true when a later stage swapped the session. ``None`` when the slot bound nothing:
    silence, not a claim that the plan was followed.
    """
    if planned is None:
        return None
    branch = rx.why.prescription_branch if rx.why is not None else None
    if branch is not None and branch in planned.branch_ids:
        return f"plan:session_followed={branch}"
    return f"plan:session_replaced={planned.slug}({replaced_reason})"


def _infeasible_prescription(
    application: ConstraintApplication,
    state: UnifiedStateVector,
    goal: TrainingGoal,
    recent_sessions: list[dict[str, Any]] | None,
) -> WorkoutPrescription:
    """Every candidate was barred by a hard constraint.

    Deliberately NOT the equipment fallback and NOT the general-template pool: both would
    prescribe work the constraint forbade, which is the failure ADR-0064:67 names
    ("never bypass a safety constraint to fill the calendar"). The athlete gets a
    conservative session that no exclusion can object to, and — the part that matters —
    the reasons are stated rather than the refusal being silent.
    """
    reasons = application.reason_codes()
    rx = WorkoutPrescription(
        type="Recovery",
        focus="Easy movement + mobility",
        rationale=(
            "Every candidate session was ruled out by an active constraint "
            f"({', '.join(reasons[:4])}). Prescribing low-risk movement rather than "
            "work a constraint forbids."
        ),
        duration_min=30,
    )
    out = finalize_prescription(rx, state, goal, "constraint_infeasible", recent_sessions)
    if out.why is not None:
        out.why.constraints_applied.append("planning:infeasible")
        out.why.constraints_applied.extend(f"constraint:{r}" for r in reasons)
    return out


def recommend_next_session(
    state: UnifiedStateVector,
    goal: TrainingGoal = TRAINING_GOAL_DEFAULT,
    recent_sessions: list[dict[str, Any]] | None = None,
    kpi_summary: dict[str, float] | None = None,
    active_weak_points: list[str] | None = None,
    available_equipment: list[str] | None = None,
    block_context: dict[str, Any] | None = None,
    candidate_log_out: list[SessionCandidate] | None = None,
    prescription_arm: str = "adaptive",
    readiness_override: float | None = None,
    catalog: list[CatalogExercise] | None = None,
    constraints: Sequence[ResolvedPlanningConstraint] | None = None,
    equipment_preference: Sequence[str] | None = None,
) -> WorkoutPrescription:
    """Candidate-based controller — see :func:`_recommend_next_session`.

    Every return path passes through here, so every prescription carries
    ``why.constraint_details``: the athlete-facing labels for the final ``constraints_applied``.
    """
    rx = _recommend_next_session(
        state,
        goal=goal,
        recent_sessions=recent_sessions,
        kpi_summary=kpi_summary,
        active_weak_points=active_weak_points,
        available_equipment=available_equipment,
        block_context=block_context,
        candidate_log_out=candidate_log_out,
        prescription_arm=prescription_arm,
        readiness_override=readiness_override,
        catalog=catalog,
        constraints=constraints,
        equipment_preference=equipment_preference,
    )
    if rx.why is not None:
        rx.why.constraint_details = describe_constraints(
            rx.why.constraints_applied,
            hard_violations=rx.why.validation.hard_violations if rx.why.validation else (),
        )
    return rx


def _recommend_next_session(
    state: UnifiedStateVector,
    goal: TrainingGoal = TRAINING_GOAL_DEFAULT,
    recent_sessions: list[dict[str, Any]] | None = None,
    kpi_summary: dict[str, float] | None = None,
    active_weak_points: list[str] | None = None,
    available_equipment: list[str] | None = None,
    block_context: dict[str, Any] | None = None,
    candidate_log_out: list[SessionCandidate] | None = None,
    prescription_arm: str = "adaptive",
    readiness_override: float | None = None,
    catalog: list[CatalogExercise] | None = None,
    constraints: Sequence[ResolvedPlanningConstraint] | None = None,
    equipment_preference: Sequence[str] | None = None,
) -> WorkoutPrescription:
    """
    Candidate-based controller.

    Builds a pool of session candidates for the given goal and state, scores
    them, and returns the best valid candidate. Hard safety overrides always
    take priority.

    `kpi_summary` holds latest derived dashboard metrics (codes → values).
    These are soft signals: state vectors are the primary controller.

    `active_weak_points` biases candidate scoring toward sessions that address
    flagged limitations. When `block_context` names today's planned session and that slot
    binds candidate templates (`app.logic.planned_session_slots`), the pool is narrowed to
    them — unless a readiness redirect is competing — so the week the athlete was shown is the
    session they get. Either way the outcome is reported, as `plan:session_followed` or
    `plan:session_replaced`.

    `block_context["objective_taper"]` (bool) and `["objective_domain"]`
    (str | None) carry the athlete's nearest/top active Objective signals
    (Phase 4a — see app.services.objective_service.active_objective_signals).
    Absent/None on both behaves exactly as if no objective existed.
    """
    kpi = kpi_summary or {}
    weak_points = active_weak_points or []
    block = block_context or {}
    # Today's planned slot, resolved to the templates that can satisfy it — `None` when the
    # slot binds nothing. Resolved here so every return path below can report the outcome.
    # The slot's OWN canonical domain when the planner recorded one (a multi-style block's
    # running day is a running day), falling back to the block goal for every session planned
    # before that was recorded. `modality` is not a substitute — it is lossy (see the a045
    # migration).
    session_domain = str(block.get("session_domain") or "") or None
    planned = binding_for(session_domain or _candidate_domain(str(goal)), block.get("session_category"))

    # --- 1. Hard safety overrides (always override scoring) ---
    safety = _safety_candidates(state)
    if safety:
        if candidate_log_out is not None:
            candidate_log_out.clear()
        rx = _finalize(safety[0], state, goal, recent_sessions)
        if rx.why is not None:
            # The override replaced whatever the goal would have prescribed. That is an applied
            # adjustment the athlete is owed in words, not only as a branch id.
            rx.why.constraints_applied.append(f"safety:override={safety[0].branch_id}")
            if (code := _plan_outcome_code(planned, rx, replaced_reason="safety")) is not None:
                rx.why.constraints_applied.append(code)
        return rx

    # --- Deload need (shadow/Level 1: explanation only) ---
    deload_need = compute_deload_need(state)

    # --- 2. Build candidate pool: goal-specific + readiness redirects ---
    # Readiness redirects stay modeled-only: acute wellness has no honest per-axis mapping,
    # so it enters via the score channel above, not here (ADR-0052). Computed first: a
    # competing redirect stops a planned category from owning the day's pool.
    redirects = _readiness_redirect(state, goal, kpi)
    goal_candidates = _generate_candidates(
        state, goal, kpi, recent_sessions, readiness_override, domain_override=session_domain,
        session_category=block.get("session_category"), category_owns_day=not redirects,
    )

    all_candidates = redirects + goal_candidates   # redirects evaluated first but scored alongside

    # --- 2b. Typed constraints (ADR-0064 seam) ---
    # This is the only point where pool MEMBERSHIP changes. It sits here because
    # `all_candidates` is still a fully-typed list[SessionCandidate] and nothing has yet
    # collapsed a candidate into a score — every adjustment below is additive bias, which
    # can lower a candidate but never exclude it.
    #
    # P11 never reads PlanningOverride (ADR-0064:95). Constraints arrive already resolved,
    # which is what lets P12 add user overrides by producing more of them rather than by
    # changing anything here.
    constraint_application = apply_constraints(all_candidates, constraints or ())
    if constraint_application.survivors:
        all_candidates = constraint_application.survivors
    elif constraint_application.infeasible:
        # Every candidate was barred. Do NOT fall through to the general-template
        # fallback below: that would quietly prescribe the very work a hard constraint
        # forbade (ADR-0064:67 — "never bypass a safety constraint to fill the calendar").
        # A conservative recovery session, clearly labelled with the reasons, is the only
        # honest output, and the excluded pool is still logged for telemetry.
        infeasible = _infeasible_prescription(
            constraint_application, state, goal, recent_sessions
        )
        if infeasible.why is not None and (
            code := _plan_outcome_code(planned, infeasible, replaced_reason="constraints")
        ):
            infeasible.why.constraints_applied.append(code)
        return infeasible

    # --- 2c. The session the plan says today is ---
    # When today's slot binds templates and one is still in the pool, prescribe from those: a
    # plan the engine ignores is a plan the athlete cannot follow. Deliberately skipped while a
    # readiness redirect is competing — redirects exist to pull work *down* on a bad day, and
    # the plan must not talk over them. Nothing here can add a candidate the pool lacked.
    if planned is not None and not redirects:
        planned_candidates = [c for c in all_candidates if c.branch_id in planned.branch_ids]
        if planned_candidates:
            all_candidates = planned_candidates

    # --- 3. Score and sort ---
    recent_skips = int(block.get("recent_skips", 0) or 0)
    recent_modifications = int(block.get("recent_modifications", 0) or 0)
    adherence_friction = recent_skips + MODIFICATION_FRICTION_WEIGHT * recent_modifications
    objective_domain_raw = block.get("objective_domain")
    objective_domain = canonical_domain(str(objective_domain_raw)) if objective_domain_raw else None

    def _score_with_context(c: SessionCandidate) -> float:
        base = _score_candidate(c)
        # The planned session is handled by pool membership above, not by a bias here: the
        # retired +0.15 compared a slot's category against a template's type — strings from two
        # vocabularies that are never equal, so it never once fired.
        # Objective domain-emphasis (Phase 4a): boost candidates whose domain
        # matches the top active objective's domain.
        if objective_domain and c.domain and c.domain == objective_domain:
            base += OBJECTIVE_DOMAIN_BOOST
        # Repeated skips or modifications → bias toward lighter/variety/recovery work.
        if adherence_friction >= RECENT_SKIPS_BIAS_THRESHOLD and any(
            k in c.type.lower() for k in _ADHERENCE_FRIENDLY_TYPE_KEYWORDS
        ):
            base += min(0.3, 0.1 * adherence_friction)
        # DeloadNeed bias: boost recovery/maintenance/technique if tier == "bias"
        if deload_need.tier == "bias" and any(
            k in c.type.lower() for k in ("recovery", "maintenance", "technique", "deload")
        ):
            base += 0.10
        return base

    scored = sorted(all_candidates, key=_score_with_context, reverse=True)

    if not scored:
        # Fallback — should not happen unless generator returns empty
        r = readiness_override if readiness_override is not None else _readiness(state)
        fallback_templates = get_templates("general", kpi, state=state)
        scored = [score_template(t, state, kpi, readiness=r) for t in fallback_templates]

    # --- Experiment arm dispatch ---
    if prescription_arm == "static_with_safety_caps":
        # Static arm: use first template candidate that passes safety.
        # No adaptive score optimization, no block bias, no habit/novelty scoring.
        # Hard safety overrides still apply (applied above, via early return).
        static_candidates = [c for c in goal_candidates if not c.is_safety_override]
        chosen = static_candidates[0] if static_candidates else (goal_candidates[0] if goal_candidates else None)
        if chosen:
            rx = _finalize(chosen, state, goal, recent_sessions)
            if rx.why:
                rx.why.constraints_applied.append("static_with_safety_caps:arm")
                if (code := _plan_outcome_code(planned, rx, replaced_reason="arm")) is not None:
                    rx.why.constraints_applied.append(code)
            if candidate_log_out is not None:
                candidate_log_out.clear()
                candidate_log_out.extend(static_candidates)
            return rx

    # Capture all scored candidates for offline policy research (Task 8).
    # This must happen after the fallback so callers always see the full pool.
    # Default None means no-op — selection is unchanged.
    if candidate_log_out is not None:
        candidate_log_out.clear()
        candidate_log_out.extend(scored)

    # --- 4. Return best candidate (finalize adds explainability + hard-constraint override) ---
    rx = _finalize(scored[0], state, goal, recent_sessions)

    # Did the athlete get the session their plan showed? The reason, when they did not, names
    # what took precedence: the validator swapped a planned winner, a readiness redirect
    # outranked it, or no template that satisfies the slot was eligible today.
    if planned is not None and rx.why is not None:
        if scored[0].branch_id in planned.branch_ids:
            replaced_reason = "validation"
        elif redirects:
            replaced_reason = "readiness"
        else:
            replaced_reason = "unavailable"
        if (code := _plan_outcome_code(planned, rx, replaced_reason=replaced_reason)) is not None:
            rx.why.constraints_applied.append(code)

    # Level 1: surface deload assessment as explanation only (never blocks)
    if rx.why and deload_need.tier != "none":
        rx.why.constraints_applied.append(
            f"deload_need:{deload_need.tier}(shadow)={deload_need.score:.2f}"
        )

    # Annotate weak-point context in the explanation if present
    if weak_points and rx.why:
        rx.why.constraints_applied.extend(
            [f"weak_point:{tag}" for tag in weak_points]
        )
    # Objective domain-emphasis (Phase 4a): annotate when the winning candidate
    # actually reflects the emphasized domain (the boost above may not always
    # change the winner, e.g. a safety override or an already-dominant redirect).
    if objective_domain and scored[0].domain == objective_domain and rx.why:
        rx.why.constraints_applied.append(f"objective:domain_emphasis={objective_domain}")
    # ADR-0029: apply the week's periodization envelope (volume modifier + RPE target).
    # week_number shapes the prescription within an envelope; state pulls down, not up.
    week_n = int(block.get("week_number") or 0)
    weeks_total = int(block.get("duration_weeks") or 0)
    intensity = normalize_intensity(block.get("intensity"))
    if week_n and weeks_total:
        env = periodization_envelope(
            weeks_total, week_n, int(block.get("deload_every_n_weeks") or 4), intensity=intensity
        )
        vol = env.volume_modifier
        phase = env.phase
        if block.get("is_deload"):
            # Honor the block's configured deload factor on flagged weeks.
            factor = block.get("deload_volume_factor")
            vol = DEFAULT_DELOAD_VOLUME_FACTOR if factor is None else float(factor)
            phase = "deload"
        vol = max(0.1, min(1.2, vol))
        if rx.duration_min > 0:
            rx.duration_min = max(1, round(rx.duration_min * vol))
        if rx.why:
            rx.why.constraints_applied.append(f"block:phase={phase}(×{vol:.2f})")
            rx.why.constraints_applied.append(
                f"block:rpe_target={env.rpe_low:.1f}-{env.rpe_high:.1f}"
            )
    elif block.get("is_deload"):
        # No periodization context — fall back to plain deload scaling.
        factor = block.get("deload_volume_factor")
        factor = DEFAULT_DELOAD_VOLUME_FACTOR if factor is None else float(factor)
        factor = max(0.1, min(1.0, factor))
        if rx.duration_min > 0:
            rx.duration_min = max(1, round(rx.duration_min * factor))
        if rx.why:
            rx.why.constraints_applied.append(f"block:deload(×{factor:.2f})")
    if block.get("is_benchmark") and rx.why:
        rx.why.constraints_applied.append("block:benchmark")
    if rx.why is not None:
        # State what the constraint layer actually did, including a soft hit that did
        # NOT remove anything — an applied-but-not-binding constraint is information the
        # athlete is owed, and reporting only exclusions would make soft constraints
        # invisible and therefore indistinguishable from unimplemented.
        for reason in constraint_application.reason_codes():
            rx.why.constraints_applied.append(f"constraint:{reason}")
        for hit in dict.fromkeys(h.reason for h in constraint_application.soft_hits):
            rx.why.constraints_applied.append(f"constraint_soft:{hit}")
    if adherence_friction >= RECENT_SKIPS_BIAS_THRESHOLD and rx.why:
        # Report the components, not the combined score: the athlete is owed the
        # evidence ("you skipped 3") rather than an opaque friction number. Each
        # component is emitted only when it actually contributed.
        if recent_skips:
            rx.why.constraints_applied.append(f"adherence:recent_skips={recent_skips}")
        if recent_modifications:
            rx.why.constraints_applied.append(
                f"adherence:recent_modifications={recent_modifications}"
            )

    # Goal-specific exercise payload — prefer the winning template's
    # exercise_slots; equipment map (with bodyweight fallback) only applies
    # when the template doesn't specify slots.
    selection = _select_exercises(
        scored[0].exercise_slots,
        available_equipment,
        catalog,
        active_weak_points,
        equipment_preference=equipment_preference,
    )
    rx.exercises = selection.exercises
    rx.structure = _structure_for_selection(
        selection, scored[0].circuit, scored[0].exercise_slots
    )
    if rx.why is not None and not _circuit_realized(
        selection, scored[0].circuit, scored[0].exercise_slots
    ):
        rx.why.constraints_applied.append(STRUCTURE_CIRCUIT_UNREALIZED)
    # A hard validator failure replaced the session with a recovery override; its title says so
    # and must not be rebuilt from the template's exercises.
    overridden = (
        rx.why is not None
        and rx.why.validation is not None
        and bool(rx.why.validation.hard_violations)
    )
    if rx.exercises and not overridden:
        rx.focus = _focus_from_exercises(rx.exercises)
    if rx.why:
        rx.why.constraints_applied.extend(selection.equipment_codes)
        if selection.preference_changes is not None:
            chosen = {p.strip().lower() for p in equipment_preference or ()}
            values = ",".join(p for p in ("barbell", "dumbbell", "machine") if p in chosen)
            # The measured effect, not the intent: how many picks differ from no preference.
            rx.why.constraints_applied.append(
                f"equipment:preference={values}(changed={selection.preference_changes})"
            )

    # --- 5. Block session preferences (Phase 3a): accessory append + target
    # duration override. `template_duration_min` is the winning template's own
    # (pre-periodization) duration, used both as the "short session" reference
    # point and as an upper anchor for the target-duration clamp.
    template_duration_min = scored[0].duration_min
    raw_emphasis = block.get("accessory_emphasis")
    raw_focus = block.get("accessory_focus")
    raw_target = block.get("target_session_minutes")
    # Accessory append and target-duration override are independent athlete
    # prefs: setting only a session length must NOT inject accessories, and vice
    # versa. Gate the accessory branch on accessory prefs alone (emphasis or
    # focus); the duration override below is gated only on `raw_target`. A
    # block_context with none of the three keys (every block created before
    # Phase 3a) is therefore unchanged: no accessories, no duration override.
    has_accessory_prefs = raw_emphasis is not None or bool(raw_focus)
    if has_accessory_prefs:
        # Missing/None emphasis defaults to "balanced" (design decision) once
        # the athlete has expressed *some* accessory preference.
        emphasis = raw_emphasis or "balanced"
        accessory_count = _ACCESSORY_COUNT_BY_EMPHASIS.get(emphasis, 2)
        # A short target session shouldn't pile on accessories, even under
        # "high" emphasis.
        if (
            raw_target is not None
            and template_duration_min > 0
            and int(raw_target) < template_duration_min
        ):
            accessory_count = min(accessory_count, SHORT_TARGET_ACCESSORY_CAP)
        if accessory_count > 0:
            existing_names = {e.name for e in rx.exercises}
            skipped_accessories: list[str] = []
            accessories = _select_accessories(
                accessory_count,
                raw_focus,
                weak_points,
                existing_names,
                is_available=_accessory_availability(available_equipment, catalog),
                skipped_out=skipped_accessories,
            )
            if skipped_accessories and rx.why:
                rx.why.constraints_applied.append(
                    f"equipment:accessories_skipped={len(skipped_accessories)}"
                )
            if accessories:
                rx.exercises = rx.exercises + [
                    ExercisePrescription(
                        name=name,
                        sets=int(sets),
                        reps=reps,
                        load_note="Accessory — autoregulate by RPE",
                    )
                    for name, sets, reps in accessories
                ]
                if rx.why:
                    rx.why.constraints_applied.append(
                        f"block:accessories={emphasis}(+{len(accessories)})"
                    )

    if raw_target is not None:
        # Apply periodization scaling first (done above), then the explicit
        # block target wins over the modifier for the final duration.
        clamped = max(
            TARGET_DURATION_MIN_MINUTES,
            min(TARGET_DURATION_MAX_MINUTES, int(raw_target)),
        )
        if template_duration_min > 0:
            clamped = min(
                clamped, round(template_duration_min * TARGET_DURATION_TEMPLATE_CAP_FACTOR)
            )
        rx.duration_min = clamped
        if rx.why:
            rx.why.constraints_applied.append(f"block:target_duration={clamped}")

    # Objective taper (Phase 4a): applied last so it scales down whatever
    # duration periodization/block-target preferences settled on, rather than
    # being overridden by them.
    if block.get("objective_taper"):
        if rx.duration_min > 0:
            rx.duration_min = max(1, round(rx.duration_min * OBJECTIVE_TAPER_FACTOR))
        if rx.why:
            rx.why.constraints_applied.append(f"objective:taper(×{OBJECTIVE_TAPER_FACTOR:.2f})")

    # Workload preference (E): applied LAST, once this session's exercises and accessories are
    # attached — before that there are no sets to move. Deliberately NOT applied through the
    # envelope's volume modifier, which scales `duration_min` alone and would make a session
    # merely look longer without asking for more work.
    _apply_intensity_sets(
        rx,
        intensity,
        session_domain or _candidate_domain(str(goal)),
        is_recovery_week=_is_recovery_week(block, week_n, weeks_total),
        workload_volume=scored[0].workload_volume,
    )

    if rx.why:
        rx.why.constraints_applied = list(dict.fromkeys(rx.why.constraints_applied))

    # Phase 2.1: attach the typed structure LAST. Exercise selection, accessory appends and
    # the workload set adjustment all mutate `exercises` after finalize_prescription ran, so
    # deriving it any earlier captured an empty session.
    return rx.with_structure()
