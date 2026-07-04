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

from typing import Any

from app.logic.candidate_library import get_templates, score_template
from app.logic.constraint_engine.candidate import (
    SessionCandidate,
)
from app.logic.constraint_engine.candidate import (
    overall_readiness as _readiness,
)
from app.logic.constraint_engine.candidate import (
    score_candidate as _score_candidate,
)
from app.logic.deload_need import compute_deload_need
from app.logic.domain_vocab import GOAL_TO_DOMAIN, canonical_domain
from app.logic.planning import periodization_envelope
from app.logic.prescription_finalize import finalize_prescription
from app.schemas.prescription import ExercisePrescription, WorkoutPrescription
from app.schemas.state import UnifiedStateVector
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT, TrainingGoal

# Note: SessionCandidate, scoring, and readiness helpers now live in
# app.logic.constraint_engine.candidate for better separation of concerns.
# Static content + scoring dispatch lives in app.logic.candidate_library.

# Default volume multiplier applied to a deload session's prescribed duration
# when the active block does not carry its own `deload_volume_factor`. Mirrors
# the MesocycleBlock.deload_volume_factor column default (app.models.mesocycle).
DEFAULT_DELOAD_VOLUME_FACTOR = 0.6

# When an athlete has skipped this many recent planned sessions, bias the
# prescription toward lighter/variety/recovery work to rebuild adherence.
RECENT_SKIPS_BIAS_THRESHOLD = 2
_ADHERENCE_FRIENDLY_TYPE_KEYWORDS = ("variety", "recovery", "maintenance", "skill")

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
) -> list[SessionCandidate]:
    """Build the goal-specific candidate pool via the CandidateTemplate library.

    Dispatches on canonical domain, not the raw goal, so BlockGoal-derived
    intent (e.g. Hyrox/CrossFit → "mixed") reaches a real candidate pool.
    Running and gymnastics use the original goal for sub-distinctions.
    """
    domain = _candidate_domain(goal)
    r = _readiness(state)
    templates = get_templates(domain, kpi, goal=str(goal), state=state)
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
    return finalize_prescription(rx, state, goal, candidate.branch_id, recent_sessions=recent_sessions)


_EQUIPMENT_EXERCISE_MAP: dict[str, list[tuple[str, str, str]]] = {
    "barbell": [
        ("Back Squat", "4", "4-6"),
        ("Romanian Deadlift", "3", "5-8"),
        ("Bench Press", "4", "4-6"),
    ],
    "dumbbells": [
        ("DB Goblet Squat", "4", "8-10"),
        ("DB RDL", "3", "8-10"),
        ("DB Floor Press", "3", "8-12"),
    ],
    "pullup_bar": [
        ("Pull-Up", "4", "4-8"),
        ("Hanging Knee Raise", "3", "10-15"),
    ],
    "bodyweight": [
        ("Tempo Squat", "4", "8-12"),
        ("Push-Up", "4", "8-15"),
        ("Split Squat", "3", "8-12/side"),
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
        ("DB Shoulder Press", "3", "8-10"),
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
    ("DB Shoulder Press", "3", "8-10"),
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
) -> list[tuple[str, str, str]]:
    """Pick up to `count` accessory slots, preferring `focus_tags`, then
    falling back to `weak_point_tags`, then generic accessories. Skips names
    already present among `existing_names` (the template's own slots) to
    avoid duplicate entries."""
    if count <= 0:
        return []
    tags = [t for t in (focus_tags or []) if t in _ACCESSORY_BY_TAG]
    if not tags:
        tags = [t for t in (weak_point_tags or []) if t in _ACCESSORY_BY_TAG]

    seen = set(existing_names)
    picks: list[tuple[str, str, str]] = []
    for tag in tags:
        for item in _ACCESSORY_BY_TAG[tag]:
            if len(picks) >= count:
                break
            if item[0] not in seen:
                picks.append(item)
                seen.add(item[0])
        if len(picks) >= count:
            break

    if len(picks) < count:
        for item in _GENERIC_ACCESSORIES:
            if len(picks) >= count:
                break
            if item[0] not in seen:
                picks.append(item)
                seen.add(item[0])

    return picks[:count]


def _exercise_list_for_equipment(available_equipment: list[str] | None) -> list[ExercisePrescription]:
    equipment = {e.lower() for e in (available_equipment or [])}
    picks: list[tuple[str, str, str]] = []

    for key in ("barbell", "dumbbells", "kettlebell", "machine", "cable", "pullup_bar"):
        if key in equipment and key in _EQUIPMENT_EXERCISE_MAP:
            picks.extend(_EQUIPMENT_EXERCISE_MAP[key])

    if not picks:
        picks.extend(_EQUIPMENT_EXERCISE_MAP["bodyweight"])

    return [
        ExercisePrescription(name=name, sets=int(sets), reps=reps, load_note="Autoregulate by RPE")
        for name, sets, reps in picks[:4]
    ]


def _exercise_list_for_candidate(
    exercise_slots: list[tuple[str, str, str]],
    available_equipment: list[str] | None,
) -> list[ExercisePrescription]:
    """Prefer the winning candidate's goal-specific exercise_slots; fall back
    to the equipment map only when a template doesn't specify slots (empty
    list). This is the fix for the bug where a Powerlifting athlete with no
    equipment configured got the bodyweight default instead of SBD work.
    """
    if exercise_slots:
        return [
            ExercisePrescription(
                name=name,
                sets=int(sets),
                reps=reps,
                load_note="Autoregulate by RPE; scale to available equipment",
            )
            for name, sets, reps in exercise_slots
        ]
    return _exercise_list_for_equipment(available_equipment)


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
) -> WorkoutPrescription:
    """
    Candidate-based controller.

    Builds a pool of session candidates for the given goal and state, scores
    them, and returns the best valid candidate. Hard safety overrides always
    take priority.

    `kpi_summary` holds latest derived dashboard metrics (codes → values).
    These are soft signals: state vectors are the primary controller.

    `active_weak_points` biases candidate scoring toward sessions that address
    flagged limitations. `block_context` applies a +0.15 bias to candidates
    whose type matches the planned session category.
    """
    kpi = kpi_summary or {}
    weak_points = active_weak_points or []
    block = block_context or {}

    # --- 1. Hard safety overrides (always override scoring) ---
    safety = _safety_candidates(state)
    if safety:
        if candidate_log_out is not None:
            candidate_log_out.clear()
        return _finalize(safety[0], state, goal, recent_sessions)

    # --- Deload need (shadow/Level 1: explanation only) ---
    deload_need = compute_deload_need(state)

    # --- 2. Build candidate pool: goal-specific + readiness redirects ---
    goal_candidates = _generate_candidates(state, goal, kpi, recent_sessions)
    redirects = _readiness_redirect(state, goal, kpi)

    all_candidates = redirects + goal_candidates   # redirects evaluated first but scored alongside

    # --- 3. Score and sort ---
    recent_skips = int(block.get("recent_skips", 0) or 0)

    def _score_with_context(c: SessionCandidate) -> float:
        base = _score_candidate(c)
        # Boost candidates whose type matches the planned session category
        if block.get("session_category") and c.type == block["session_category"]:
            base += 0.15
        # Repeated recent skips → bias toward lighter/variety/recovery work.
        if recent_skips >= RECENT_SKIPS_BIAS_THRESHOLD and any(
            k in c.type.lower() for k in _ADHERENCE_FRIENDLY_TYPE_KEYWORDS
        ):
            base += min(0.3, 0.1 * recent_skips)
        # DeloadNeed bias: boost recovery/maintenance/technique if tier == "bias"
        if deload_need.tier == "bias" and any(
            k in c.type.lower() for k in ("recovery", "maintenance", "technique", "deload")
        ):
            base += 0.10
        return base

    scored = sorted(all_candidates, key=_score_with_context, reverse=True)

    if not scored:
        # Fallback — should not happen unless generator returns empty
        r = _readiness(state)
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
    # ADR-0029: apply the week's periodization envelope (volume modifier + RPE target).
    # week_number shapes the prescription within an envelope; state pulls down, not up.
    week_n = int(block.get("week_number") or 0)
    weeks_total = int(block.get("duration_weeks") or 0)
    if week_n and weeks_total:
        env = periodization_envelope(
            weeks_total, week_n, int(block.get("deload_every_n_weeks") or 4)
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
    if recent_skips >= RECENT_SKIPS_BIAS_THRESHOLD and rx.why:
        rx.why.constraints_applied.append(f"adherence:recent_skips={recent_skips}")

    # Goal-specific exercise payload — prefer the winning template's
    # exercise_slots; equipment map (with bodyweight fallback) only applies
    # when the template doesn't specify slots.
    rx.exercises = _exercise_list_for_candidate(scored[0].exercise_slots, available_equipment)
    if rx.why:
        if available_equipment:
            rx.why.constraints_applied.append("equipment:filtered")
        else:
            rx.why.constraints_applied.append("equipment:fallback_bodyweight")

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
            accessories = _select_accessories(accessory_count, raw_focus, weak_points, existing_names)
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

    if rx.why:
        rx.why.constraints_applied = list(dict.fromkeys(rx.why.constraints_applied))

    return rx
