"""Athlete-facing words for the codes in ``PrescriptionExplanation.constraints_applied`` (S-A).

The prescriber records what shaped a session as compact engine codes —
``block:phase=accumulation(×1.15)``, ``equipment:unconfigured``, a validator message — and the
Planning tab used to print them verbatim. This module is the one place those codes become
sentences, so every client reads the same words and none of them parses engine strings.

Three rules keep the labels honest:

* **Every code an emitter can produce has a reviewed label.** Codes are matched by explicit
  patterns for each emitter family (``tests/test_constraint_labels.py`` scans the emitters and
  fails when a new family appears without one). A code nothing here recognises gets
  :data:`UNKNOWN_LABEL` — never a guess assembled by replacing its punctuation.
* **Say what the engine did, not more.** A session-length factor is described as a
  session-length factor (the envelope scales ``duration_min``); a soft plan preference is said to
  have removed nothing; a template rule that was merely *checked* is not presented as applied.
* **Hide bookkeeping, never an applied safety adjustment.** An experiment arm, a shadow-only
  assessment and a template's rule list are ``athlete_visible=False``. A safety override or a
  hard validator failure — both of which replaced the session — is always visible.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from app.schemas.prescription import AppliedConstraint, AppliedConstraintGroup

#: The label for a code no pattern here recognises. Deliberately says nothing about what it did.
UNKNOWN_LABEL = "Another planning rule was applied."

# ── Equipment codes (emitted by app.logic.prescriber) ───────────────────────────────
EQUIPMENT_UNCONFIGURED = "equipment:unconfigured"
EQUIPMENT_FILTERED = "equipment:filtered"
EQUIPMENT_BODYWEIGHT_ONLY = "equipment:bodyweight_only"
EQUIPMENT_FALLBACK_BODYWEIGHT = "equipment:fallback_bodyweight"
EQUIPMENT_ACCESSORIES_SKIPPED_PREFIX = "equipment:accessories_skipped="
EQUIPMENT_PREFERENCE_PREFIX = "equipment:preference="

#: Today's planned session was prescribed, or something took precedence over it.
PLAN_FOLLOWED_PREFIX = "plan:session_followed="
PLAN_REPLACED_PREFIX = "plan:session_replaced="

SAFETY_OVERRIDE_PREFIX = "safety:override="

_EXACT: dict[str, tuple[str, AppliedConstraintGroup, bool]] = {
    "planning:infeasible": (
        "Every session option was ruled out by your plan, so this is an easy recovery session.",
        "plan_rule",
        True,
    ),
    "block:benchmark": ("Benchmark session in your plan.", "block", True),
    "static_with_safety_caps:arm": ("Fixed-template experiment arm.", "internal", False),
    EQUIPMENT_UNCONFIGURED: (
        "Equipment not set, so any exercise may appear. Set it in Settings.",
        "equipment",
        True,
    ),
    EQUIPMENT_FILTERED: ("Exercises limited to the equipment you listed.", "equipment", True),
    EQUIPMENT_BODYWEIGHT_ONLY: (
        "Bodyweight exercises only, as set in Settings.",
        "equipment",
        True,
    ),
    EQUIPMENT_FALLBACK_BODYWEIGHT: (
        "Bodyweight exercises used: this session type has no equipment-specific options for you.",
        "equipment",
        True,
    ),
}

_PHASES = {
    "accumulation": "Accumulation",
    "intensification": "Intensification",
    "peak": "Peak",
    "taper": "Taper",
    "deload": "Deload",
}

_EMPHASES = {"minimal": "minimal", "balanced": "balanced", "high": "high"}

_DOMAINS = {
    "powerlifting": "powerlifting",
    "weightlifting": "weightlifting",
    "strength": "strength",
    "hypertrophy": "hypertrophy",
    "power": "power",
    "running": "running",
    "gymnastics": "gymnastics",
    "calisthenics": "calisthenics",
    "grip": "grip",
    "mixed": "mixed-modal",
    "general": "general fitness",
}

#: ``app.models.weak_point.WEAK_POINT_TAGS``, in words.
_WEAK_POINTS = {
    "hip_hinge": "Hip hinge",
    "squat_pattern": "Squat pattern",
    "push_horizontal": "Horizontal pushing",
    "push_vertical": "Vertical pushing",
    "pull_horizontal": "Horizontal pulling",
    "pull_vertical": "Vertical pulling",
    "carry": "Carries",
    "rotation": "Rotation",
    "core_stability": "Core stability",
    "single_leg": "Single-leg strength",
    "grip": "Grip",
    "posterior_chain": "Posterior chain",
    "anterior_chain": "Anterior chain",
    "overhead_stability": "Overhead stability",
    "hip_mobility": "Hip mobility",
    "ankle_mobility": "Ankle mobility",
    "thoracic_mobility": "Thoracic mobility",
    "aerobic_base": "Aerobic base",
    "lactate_threshold": "Lactate threshold",
    "anaerobic_capacity": "Anaerobic capacity",
    "work_capacity": "Work capacity",
    "running_economy": "Running economy",
    "barbell_technique": "Barbell technique",
    "gymnastics_skill": "Gymnastics skill",
    "olympic_lifting": "Olympic lifting",
    "sled_tolerance": "Sled tolerance",
    "row_technique": "Rowing technique",
    "bike_efficiency": "Bike efficiency",
}

#: Safety override branches (``prescriber._safety_candidates``). Each replaced the session.
_SAFETY_BRANCHES = {
    "safety_regional_tissue": (
        "Safety override: lumbar or knee tissue stress is high, so this is a low-impact "
        "recovery session."
    ),
    "safety_structural_damage": (
        "Safety override: structural or tendon fatigue is high, so this is light movement only."
    ),
    "safety_tendon_structural": (
        "Safety override: tendon or structural fatigue is high, so this is a tissue deload."
    ),
    "safety_systemic_metabolic": (
        "Safety override: systemic fatigue is very high, so this is a rest day."
    ),
}

#: Rule ids a program template declares (``app/data/program_templates.json``). The finalizer lists
#: them whether or not they fired, so they describe what was checked — never shown as applied.
_TEMPLATE_RULE_IDS = {
    "grip_max_frequency": "Checked: maximal grip work frequency.",
    "gymnastics_wrist_tissue": "Checked: wrist tissue stress for gymnastics skill work.",
    "metcon_fatigue_stack": "Checked: stacked conditioning fatigue.",
    "olympic_metabolic_before_technical": "Checked: metabolic work before technical lifting.",
    "olympic_rep_cap": "Checked: rep cap for Olympic lifts.",
    "pl_deadlift_cns": "Checked: central fatigue from heavy deadlifts.",
    "running_zone2_majority": "Checked: easy running making up most volume.",
    "skill_gate": "Checked: skill prerequisites.",
    "sprint_neural_freshness": "Checked: neural freshness for sprinting.",
    "tendon_gradual": "Checked: gradual tendon loading.",
}

#: Validator failure messages (``app/logic/constraint_engine/constraints_impl.py``), in words.
#: Whether one replaced the session depends on whether the template ran it as a hard rule, which
#: :func:`describe_constraints` learns from the prescription's own validation summary.
_VALIDATOR_MESSAGES = {
    "systemic fatigue critical — rest required": "Systemic fatigue is critical, so rest is required.",
    "gymnastics_wrist_tissue: wrist stress too high for skill work": (
        "Wrist stress is too high for gymnastics skill work."
    ),
    "olympic_metabolic_before_technical: elevated systemic fatigue with met-heavy draft": (
        "Systemic fatigue is elevated for metabolically heavy work before technical lifting."
    ),
    "running_zone2_majority: prefer easy volume when fatigue present": (
        "With fatigue present, easy running volume is preferred."
    ),
    "sprint_neural_freshness: CNS elevated — shorten sprint exposure": (
        "CNS fatigue is elevated, so sprint exposure should stay short."
    ),
    "grip_max_frequency: reduce max crush frequency when grip fatigue high": (
        "Grip fatigue is high, so maximal grip work should be reduced."
    ),
    "metcon_fatigue_stack: systemic load high — bias recovery or low density": (
        "Systemic load is high, so recovery or lower-density work is favoured."
    ),
    "pl_deadlift_cns: rotate CNS-heavy lifts when central fatigue high": (
        "Central fatigue is high, so CNS-heavy lifts should rotate."
    ),
    "Competition lift reps per set should stay ≤5": "Competition-lift sets should stay at 5 reps or fewer.",
    "Dense metcon too close to technical classical work": (
        "A dense conditioning session is too close to technical Olympic lifting."
    ),
    "CNS fatigue too high for heavy technical singles": "CNS fatigue is too high for heavy technical singles.",
    "High-intensity running exposures capped for the week": (
        "This week's high-intensity running sessions are capped."
    ),
    "Avoid back-to-back threshold/VO2 quality days": "Avoid back-to-back threshold or VO2 quality days.",
    "Long-run duration ramp too aggressive vs recent history": (
        "The long-run duration increase is too steep for your recent history."
    ),
    "Aerobic base low — bias threshold before VO2": "Aerobic base is low, so threshold work comes before VO2 work.",
    "Deadlift heavy exposures limited for the week": "This week's heavy deadlift sessions are limited.",
    "Readiness low for max/competition intensity": "Readiness is too low for maximal or competition intensity.",
    "Reduce assistance volume when main-lift fatigue is high": (
        "Main-lift fatigue is high, so assistance volume should be reduced."
    ),
    "Peripheral fatigue high — limit volume": "Muscular fatigue is high, so volume should be limited.",
    "Lumbar stress high — limit deadlift volume": "Lumbar stress is high, so deadlift volume should be limited.",
    "Tendon load stacked — insert recovery": "Tendon load is stacking up, so recovery should be inserted.",
    "Build strict pulling strength before kipping volume": (
        "Build strict pulling strength before kipping volume."
    ),
}

_HARD_REPLACED_SUFFIX = " This session was replaced with easy movement."

_NUMBER = r"\d+(?:\.\d+)?"
_BLOCK_PHASE = re.compile(rf"^block:phase=(?P<phase>[a-z_]+)\(×(?P<factor>{_NUMBER})\)$")
_BLOCK_RPE = re.compile(rf"^block:rpe_target=(?P<low>{_NUMBER})-(?P<high>{_NUMBER})$")
_BLOCK_DELOAD = re.compile(rf"^block:deload\(×(?P<factor>{_NUMBER})\)$")
_BLOCK_ACCESSORIES = re.compile(r"^block:accessories=(?P<emphasis>[a-z_]+)\(\+(?P<count>\d+)\)$")
_BLOCK_TARGET_DURATION = re.compile(r"^block:target_duration=(?P<minutes>\d+)$")
_OBJECTIVE_TAPER = re.compile(rf"^objective:taper\(×(?P<factor>{_NUMBER})\)$")
_OBJECTIVE_DOMAIN = re.compile(r"^objective:domain_emphasis=(?P<domain>[a-z_]+)$")
_WEAK_POINT = re.compile(r"^weak_point:(?P<tag>[a-z_]+)$")
_ADHERENCE = re.compile(r"^adherence:recent_(?P<what>skips|modifications)=(?P<count>\d+)$")
_DELOAD_NEED = re.compile(rf"^deload_need:(?P<tier>[a-z]+)\(shadow\)=(?P<score>{_NUMBER})$")
_PLAN_CONSTRAINT = re.compile(
    r"^(?P<family>constraint|constraint_soft):(?P<kind>[a-z_]+)(?:=(?P<target>[^:]*))?:(?P<reason>.+)$"
)
_SAFETY_OVERRIDE = re.compile(r"^safety:override=(?P<branch>[a-z_]+)$")
_PLAN_FOLLOWED = re.compile(r"^plan:session_followed=(?P<branch>[a-z_]+)$")
_PLAN_REPLACED = re.compile(r"^plan:session_replaced=(?P<slug>[a-z_]+)\((?P<reason>[a-z_]+)\)$")
_ACCESSORIES_SKIPPED = re.compile(r"^equipment:accessories_skipped=(?P<count>\d+)$")
_PREFERENCE = re.compile(r"^equipment:preference=(?P<values>[a-z,]+)\(changed=(?P<count>\d+)\)$")
_TISSUE_MESSAGE = re.compile(
    r"^tissue stress elevated \(lumbar (?P<lumbar>-?\d+), knee (?P<knee>-?\d+)\)$"
)

#: The emitter families this module labels, by the stable head of their code. The test suite
#: compares this against the heads the prescriber actually emits.
CODE_FAMILIES: tuple[str, ...] = (
    "planning:infeasible",
    "constraint:",
    "constraint_soft:",
    "static_with_safety_caps:arm",
    "deload_need:",
    "weak_point:",
    "objective:domain_emphasis=",
    "objective:taper(",
    "block:phase=",
    "block:rpe_target=",
    "block:deload(",
    "block:benchmark",
    "block:accessories=",
    "block:target_duration=",
    "adherence:recent_skips=",
    "adherence:recent_modifications=",
    "safety:override=",
    PLAN_FOLLOWED_PREFIX,
    PLAN_REPLACED_PREFIX,
    EQUIPMENT_UNCONFIGURED,
    EQUIPMENT_FILTERED,
    EQUIPMENT_BODYWEIGHT_ONLY,
    EQUIPMENT_FALLBACK_BODYWEIGHT,
    EQUIPMENT_ACCESSORIES_SKIPPED_PREFIX,
    EQUIPMENT_PREFERENCE_PREFIX,
)

#: ``equipment_preference`` values in words, for the preference entry.
PREFERENCE_WORDS = {"barbell": "barbells", "dumbbell": "dumbbells", "machine": "machines"}


def _num(text: str) -> str:
    """``8.0`` reads as ``8``; ``6.5`` stays ``6.5``."""
    value = float(text)
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def _join(words: Sequence[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return f"{', '.join(words[:-1])} and {words[-1]}"


_Labelled = tuple[str, AppliedConstraintGroup, bool]


def _block(code: str) -> _Labelled | None:
    if m := _BLOCK_PHASE.match(code):
        phase = _PHASES.get(m["phase"])
        if phase is None:
            return None
        return f"{phase} phase: session length ×{_num(m['factor'])}.", "block", True
    if m := _BLOCK_RPE.match(code):
        return f"Target effort: RPE {_num(m['low'])}–{_num(m['high'])}.", "block", True
    if m := _BLOCK_DELOAD.match(code):
        return f"Deload week: session length ×{_num(m['factor'])}.", "block", True
    if m := _BLOCK_ACCESSORIES.match(code):
        emphasis = _EMPHASES.get(m["emphasis"])
        if emphasis is None:
            return None
        count = int(m["count"])
        return (
            f"{count} accessory {_plural(count, 'exercise')} added ({emphasis} emphasis).",
            "block",
            True,
        )
    if m := _BLOCK_TARGET_DURATION.match(code):
        return f"Session length set to {m['minutes']} min by your block.", "block", True
    return None


def _objective(code: str) -> _Labelled | None:
    if m := _OBJECTIVE_TAPER.match(code):
        return (
            f"Tapering toward your objective's date: session length ×{_num(m['factor'])}.",
            "objective",
            True,
        )
    if m := _OBJECTIVE_DOMAIN.match(code):
        domain = _DOMAINS.get(m["domain"])
        if domain is None:
            return None
        return f"Favoured {domain} work for your top objective.", "objective", True
    return None


def _athlete_history(code: str) -> _Labelled | None:
    if m := _WEAK_POINT.match(code):
        tag = _WEAK_POINTS.get(m["tag"])
        if tag is None:
            return None
        return f"Weak point considered: {tag}.", "weak_point", True
    if m := _ADHERENCE.match(code):
        count = int(m["count"])
        verb = "skipped" if m["what"] == "skips" else "modified"
        return (
            f"Easier options favoured: {count} recent {_plural(count, 'session')} {verb}.",
            "adherence",
            True,
        )
    if m := _DELOAD_NEED.match(code):
        if m["tier"] == "bias":
            # The only tier the scorer acts on (a small bonus for lighter session types).
            return (
                "Lighter session types were given a small preference: accumulated fatigue "
                "suggests a deload.",
                "state",
                True,
            )
        if m["tier"] in ("watch", "force"):
            return f"Deload assessment ({m['tier']}), explanation only.", "internal", False
        return None
    return None


def _plan_rule(code: str) -> _Labelled | None:
    m = _PLAN_CONSTRAINT.match(code)
    if m is None:
        return None
    target = (m["target"] or "").strip()
    soft = m["family"] == "constraint_soft"
    kind = m["kind"]
    if kind in ("exclude_modality", "exclude_session_type", "exclude_domain"):
        noun = "work" if kind == "exclude_domain" else "sessions"
        subject = f"{target} {noun}" if target else f"some {noun}"
        if soft:
            return (
                f"Your plan's preference against {subject} was considered; it removed no option.",
                "plan_rule",
                True,
            )
        return f"Your plan rules out {subject}.", "plan_rule", True
    if kind == "max_duration_min":
        if soft:
            return (
                "Your plan's preferred session length was considered; it removed no option.",
                "plan_rule",
                True,
            )
        return "Your plan caps session length.", "plan_rule", True
    return None


def _equipment(code: str) -> _Labelled | None:
    if m := _ACCESSORIES_SKIPPED.match(code):
        count = int(m["count"])
        # `count` is every accessory option passed over while filling the requested slots, not
        # exercises removed from the session — the wording must not claim more than that.
        return (
            f"Skipped {count} accessory {_plural(count, 'option')} that "
            f"{_plural(count, 'needs', 'need')} equipment you haven't listed.",
            "equipment",
            True,
        )
    if m := _PREFERENCE.match(code):
        values = [v for v in m["values"].split(",") if v]
        words = [PREFERENCE_WORDS.get(v) for v in values]
        if not words or any(w is None for w in words):
            return None
        listed = _join([w for w in words if w is not None])
        changed = int(m["count"])
        if changed == 0:
            return f"Preference: {listed}. No exercise choice changed.", "equipment", True
        return (
            f"Preference: {listed}. Changed {changed} exercise {_plural(changed, 'choice')}.",
            "equipment",
            True,
        )
    return None


#: Why the planned session was not the session prescribed. Each reason names something that
#: takes precedence over the plan — never a claim that the plan was wrong.
_PLAN_REPLACED_REASONS: dict[str, str] = {
    "safety": "a safety override took precedence",
    "constraints": "your plan's constraints ruled out every option",
    "readiness": "today's readiness called for different work",
    "validation": "it did not pass this session's safety checks",
    "unavailable": "it isn't available for you today",
    "arm": "an experiment arm selected the session",
}

#: Planned slots in the athlete's words, keyed by the slug the prescriber emits.
_PLAN_SLOTS: dict[str, str] = {
    "strength_max": "max strength day",
    "strength_volume": "strength volume day",
    "strength_accessory": "accessory day",
    "hypertrophy_upper": "upper-body day",
    "hypertrophy_lower": "lower-body day",
    "hypertrophy_accessory": "accessory and isolation day",
    "hypertrophy_high_volume": "high-volume day",
    "running_base": "aerobic base run",
    "running_threshold": "threshold run",
    "power_development": "power day",
    "power_neural_priming": "neural priming day",
    "powerlifting_sbd": "squat, bench and deadlift day",
    "powerlifting_accessory": "accessory day",
    "weightlifting_technique": "technique day",
    "mixed_metcon": "conditioning day",
    "mixed_modal": "mixed-modal day",
    "mixed_engine": "engine day",
    "mixed_strength_endurance": "strength-endurance day",
    "calisthenics_skill": "skill day",
    "calisthenics_strength": "bodyweight strength day",
    "calisthenics_conditioning": "conditioning day",
    "gymnastics_skill": "skill day",
    "grip_support": "grip day",
    "general_gpp": "full-body day",
    "general_recovery": "active recovery day",
    "general_aerobic_strength": "aerobic and strength day",
    "general_strength_preservation": "strength preservation day",
    "general_conditioning": "conditioning day",
    "conditioning_metcon": "conditioning day",
}


def _plan_session(code: str) -> _Labelled | None:
    if _PLAN_FOLLOWED.match(code):
        return "This is the session your plan plans for today.", "plan_rule", True
    if m := _PLAN_REPLACED.match(code):
        slot = _PLAN_SLOTS.get(m["slug"])
        reason = _PLAN_REPLACED_REASONS.get(m["reason"])
        if slot is None or reason is None:
            return None
        return (
            f"Your plan's {slot} was not prescribed: {reason}.",
            "plan_rule",
            True,
        )
    return None


def _safety(code: str) -> _Labelled | None:
    m = _SAFETY_OVERRIDE.match(code)
    if m is None:
        return None
    label = _SAFETY_BRANCHES.get(m["branch"], "Safety override: this session was replaced.")
    return label, "safety", True


_PATTERN_LABELLERS: tuple[Callable[[str], _Labelled | None], ...] = (
    _block,
    _objective,
    _athlete_history,
    _plan_rule,
    _equipment,
    _safety,
    _plan_session,
)


def _validator_label(message: str) -> str | None:
    if (label := _VALIDATOR_MESSAGES.get(message)) is not None:
        return label
    if m := _TISSUE_MESSAGE.match(message):
        return f"Tissue stress is elevated (lumbar {m['lumbar']}, knee {m['knee']})."
    return None


def describe_constraint(
    code: str,
    *,
    hard_violations: frozenset[str] = frozenset(),
) -> AppliedConstraint:
    """The athlete-facing entry for one code."""
    if (exact := _EXACT.get(code)) is not None:
        label, group, visible = exact
        return AppliedConstraint(code=code, label=label, group=group, athlete_visible=visible)
    for labeller in _PATTERN_LABELLERS:
        if (found := labeller(code)) is not None:
            label, group, visible = found
            return AppliedConstraint(code=code, label=label, group=group, athlete_visible=visible)
    if (rule := _TEMPLATE_RULE_IDS.get(code)) is not None:
        return AppliedConstraint(code=code, label=rule, group="internal", athlete_visible=False)
    if (message := _validator_label(code)) is not None:
        if code in hard_violations:
            return AppliedConstraint(
                code=code, label=message + _HARD_REPLACED_SUFFIX, group="safety", athlete_visible=True
            )
        return AppliedConstraint(code=code, label=message, group="advisory", athlete_visible=True)
    return AppliedConstraint(code=code, label=UNKNOWN_LABEL, group="other", athlete_visible=True)


def describe_constraints(
    codes: Sequence[str],
    *,
    hard_violations: Sequence[str] = (),
) -> list[AppliedConstraint]:
    """One entry per code, in order. ``hard_violations`` is the prescription's own validation
    summary, which is what distinguishes a validator failure that replaced the session from one
    that was only noted."""
    hard = frozenset(hard_violations)
    return [describe_constraint(code, hard_violations=hard) for code in codes]
