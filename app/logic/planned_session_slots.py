"""What a planned day in the block means to the prescriber.

The weekly plan and the candidate library grew two disjoint vocabularies: a block slot is a
free-text ``category`` ("High Volume Upper", ``planning_service._DEFAULT_TEMPLATES``), while a
candidate template is identified by ``branch_id`` ("hyp_upper_split",
``candidate_library``). The only link was an exact match between a slot's category and a
template's ``type`` — strings that are never equal — so the plan could not influence which
session was prescribed, and the athlete was shown a week it was never going to follow.

This module is that link, and nothing else: per canonical domain, which templates can satisfy
a planned slot. It holds no scoring, no eligibility and no state.

Three deliberate limits:

* **Bindings are a product judgement, not derived.** Each entry below says which template an
  athlete would recognise as the planned session. They are marked where the choice is open.
* **Gaps are allowed and silent.** A slot with no binding (e.g. a running "Active Recovery",
  for which the running pool has no template) leaves selection exactly as it was. A wrong
  guess would be worse than no binding.
* **A binding is not a promise.** It narrows the pool when one of its templates is eligible;
  readiness redirects, safety overrides, hard constraints and the session validator all still
  come first. The prescriber reports which of those happened
  (``plan:session_followed`` / ``plan:session_replaced``).

``tests/test_planned_session_slots.py`` pins every branch id here to a real template in that
domain's library, so a rename cannot silently turn a binding into a gap.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlotBinding:
    """The templates that can satisfy one planned slot.

    ``slug`` is a stable, lowercase identity for the slot itself — safe to put in an
    explanation code, unlike the athlete-facing category text.
    """

    slug: str
    branch_ids: tuple[str, ...]


#: domain → planned ``category`` → the templates that satisfy it.
#:
#: Categories are written exactly as ``planning_service`` writes them onto a PlannedSession
#: (``_DEFAULT_TEMPLATES`` for goal blocks, ``_DOMAIN_SLOT`` for modality-mix blocks).
_BINDINGS: dict[str, dict[str, SlotBinding]] = {
    "strength": {
        "Max Strength": SlotBinding("strength_max", ("strength_max",)),
        "Strength — Volume": SlotBinding("strength_volume", ("strength_volume",)),
        # [assumed] "Accessory Focus" reads as the variety/assistance day rather than the
        # skill-acquisition day; both remain eligible, this only orders the pool.
        "Accessory Focus": SlotBinding("strength_accessory", ("strength_variety",)),
    },
    "hypertrophy": {
        "High Volume Upper": SlotBinding("hypertrophy_upper", ("hyp_upper_split",)),
        "High Volume Lower": SlotBinding("hypertrophy_lower", ("hyp_high_vol",)),
        "Accessory / Isolation": SlotBinding("hypertrophy_accessory", ("hyp_maintenance",)),
        "High Volume": SlotBinding("hypertrophy_high_volume", ("hyp_high_vol",)),
    },
    "running": {
        "Aerobic Base": SlotBinding("running_base", ("run_z2_base", "run_z2_base_threshold")),
        "Threshold Work": SlotBinding("running_threshold", ("run_threshold", "run_threshold_ff")),
        # "Active Recovery" is deliberately unbound: the running pool has no recovery template.
    },
    "power": {
        "Power Development": SlotBinding("power_development", ("power_main",)),
        "Neural Priming": SlotBinding("power_neural_priming", ("power_neural_prime",)),
        # "Strength Potentiation" is unbound: no power template carries that intent.
    },
    "powerlifting": {
        "SBD Strength": SlotBinding("powerlifting_sbd", ("pl_sbd_main", "pl_sbd_main_volume")),
        "Accessory Focus": SlotBinding("powerlifting_accessory", ("pl_accessory",)),
    },
    "weightlifting": {
        "Weightlifting Technique": SlotBinding(
            "weightlifting_technique", ("wl_technique_snatch", "wl_technique_cj")
        ),
    },
    "mixed": {
        "MetCon": SlotBinding("mixed_metcon", ("metcon_mixed_modal",)),
        "Metabolic Conditioning": SlotBinding("mixed_metcon", ("metcon_mixed_modal",)),
        "Mixed Modal": SlotBinding("mixed_modal", ("metcon_mixed_modal",)),
        "Engine Work": SlotBinding("mixed_engine", ("metcon_engine",)),
        "Strength Endurance": SlotBinding("mixed_strength_endurance", ("mixed_strength_endurance",)),
        # "Strength + Skill", "Running + Functional" and "Hyrox Simulation" are unbound: no
        # mixed template is specifically either of those yet.
    },
    "calisthenics": {
        "Skill & Straight-Arm Strength": SlotBinding("calisthenics_skill", ("cal_skill",)),
        "Bodyweight Strength": SlotBinding("calisthenics_strength", ("cal_strength",)),
        "Gymnastics Conditioning": SlotBinding("calisthenics_conditioning", ("cal_conditioning",)),
    },
    "gymnastics": {
        "Gymnastics Skill": SlotBinding("gymnastics_skill", ("gym_skill",)),
    },
    "grip": {
        "Grip & Support": SlotBinding("grip_support", ("grip_main",)),
    },
    # A Recomp block resolves to this domain too (``canonical_domain("Recomp") == "general"``),
    # so its three slots are bound here rather than under a "recomp" key that never resolves.
    "general": {
        "Full-Body GPP": SlotBinding("general_gpp", ("gpp_balanced",)),
        "Active Recovery": SlotBinding("general_recovery", ("gpp_mobility",)),
        # [assumed] "Aerobic + Strength" can be read either way, so both stay bindable.
        "Aerobic + Strength": SlotBinding(
            "general_aerobic_strength", ("gpp_conditioning", "gpp_strength_foundation")
        ),
        "Strength Preservation": SlotBinding(
            "general_strength_preservation", ("gpp_strength_foundation",)
        ),
        "Metabolic Conditioning": SlotBinding("general_conditioning", ("gpp_conditioning",)),
    },
    # A modality-mix block can name this domain; it has no library of its own and draws the
    # general templates (``GOAL_TEMPLATE_LIBRARY.get(domain, GENERAL_TEMPLATES)``).
    "conditioning": {
        "Metabolic Conditioning": SlotBinding("conditioning_metcon", ("gpp_conditioning",)),
    },
}


def binding_for(domain: str, category: str | None) -> SlotBinding | None:
    """The templates that satisfy today's planned slot, or ``None`` when nothing binds it.

    ``None`` means "no opinion" — an unknown domain, a session with no category, a benchmark
    session (whose category is overwritten), or a slot this module deliberately leaves
    unbound. Callers must treat it as "select as before", never as "nothing matched".
    """
    if not category:
        return None
    return _BINDINGS.get(domain, {}).get(category)
