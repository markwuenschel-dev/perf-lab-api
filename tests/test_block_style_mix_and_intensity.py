"""A multi-style block prescribes each day's own style, and workload is a real preference.

Two things this pins, both of which today's main gets wrong:

**D — secondary styles.** `modality_mix` has always built a multi-domain weekly template, but
the prescriber built every session from the BLOCK GOAL, so the extra days were labels only.
The slot's `modality` cannot stand in for its domain: `_DOMAIN_SLOT`
(app/services/planning_service.py:82) maps powerlifting→Strength, weightlifting→Power and
gymnastics→Calisthenics, so canonicalizing the label back cannot recover what was asked for.
The lossy pairs are therefore tested explicitly, not just the easy strength/running case.

**E — workload preference.** Easy/medium/hard moves working sets and the RPE cap INSIDE the
periodization envelope. It never loosens a safety override or a readiness redirect, never
touches a deload or taper week, and says so in the explanation when it does nothing.

The invariant, stated once: A PREFERENCE MOVES TARGETS, A LIMIT MOVES NOTHING. There is
deliberately no test asserting "hard always yields more" — caps and overrides may bind.
"""
import pytest

from app.logic.planned_session_slots import binding_for
from app.logic.planning import (
    INTENSITY_HARD,
    intensity_set_delta,
    normalize_intensity,
    periodization_envelope,
)
from app.services.planning_service import _DOMAIN_SLOT, _template_from_modality_mix

# --- D: the mix records a domain per slot, and it survives the lossy label -------------

def test_mix_template_records_the_domain_each_slot_was_asked_for():
    slots = _template_from_modality_mix({"strength": 0.67, "running": 0.33}, 3)

    assert slots is not None
    assert sorted(s.domain for s in slots) == ["running", "strength", "strength"]


@pytest.mark.parametrize(
    ("pair", "shared_label"),
    [
        (("strength", "powerlifting"), "Strength"),
        (("power", "weightlifting"), "Power"),
        (("calisthenics", "gymnastics"), "Calisthenics"),
    ],
)
def test_lossy_pairs_keep_distinct_domains_behind_one_modality_label(pair, shared_label):
    """The exact failure mode the domain column exists for."""
    first, second = pair
    assert _DOMAIN_SLOT[first][1] == shared_label
    assert _DOMAIN_SLOT[second][1] == shared_label

    slots = _template_from_modality_mix({first: 0.5, second: 0.5}, 2)

    assert slots is not None
    assert {s.modality for s in slots} == {shared_label}, "the label really is shared"
    assert sorted(s.domain for s in slots) == sorted(pair), "the domain still distinguishes them"


def test_every_mix_domain_binds_its_own_slot_category():
    """A domain the mix can name must resolve to templates, or its day silently falls back."""
    unbound = [
        domain
        for domain, (category, _modality) in _DOMAIN_SLOT.items()
        if binding_for(domain, category) is None
    ]

    assert unbound == []


def test_zero_share_styles_are_visible_in_the_allocation():
    """Three styles across two sessions: one gets nothing, and the template shows that."""
    slots = _template_from_modality_mix({"strength": 0.5, "running": 0.3, "conditioning": 0.2}, 2)

    assert slots is not None
    assert len(slots) == 2
    assert "conditioning" not in {s.domain for s in slots}


# --- E: the workload preference moves the envelope, and only where it may ---------------

def test_unset_preference_is_medium_and_changes_nothing():
    plain = periodization_envelope(8, 2, 4)
    unset = periodization_envelope(8, 2, 4, intensity=None)
    medium = periodization_envelope(8, 2, 4, intensity="medium")

    assert normalize_intensity(None) == "medium"
    assert (unset.rpe_low, unset.rpe_high) == (plain.rpe_low, plain.rpe_high)
    assert (medium.rpe_low, medium.rpe_high) == (plain.rpe_low, plain.rpe_high)


@pytest.mark.parametrize("week", [2, 5, 7])
def test_easy_and_hard_move_the_band_in_opposite_directions(week):
    base = periodization_envelope(8, week, 4)
    easy = periodization_envelope(8, week, 4, intensity="easy")
    hard = periodization_envelope(8, week, 4, intensity="hard")

    assert easy.rpe_high < base.rpe_high
    assert hard.rpe_high >= base.rpe_high  # >= because the ceiling may bind
    assert hard.rpe_high <= 9.5


def test_the_peak_week_cap_binds_instead_of_running_away():
    """Peak already targets 9.5; hard must saturate there rather than exceed it."""
    peak = periodization_envelope(8, 7, 0)
    hard_peak = periodization_envelope(8, 7, 0, intensity="hard")

    assert peak.phase == "peak"
    assert peak.rpe_high == 9.5
    assert hard_peak.rpe_high == 9.5


@pytest.mark.parametrize("intensity", ["easy", "hard"])
def test_recovery_weeks_ignore_the_preference(intensity):
    """A deload you can opt out of is not a deload; a taper exists to arrive fresh."""
    deload = periodization_envelope(8, 4, 4)
    deload_pref = periodization_envelope(8, 4, 4, intensity=intensity)
    taper = periodization_envelope(6, 6, 0)
    taper_pref = periodization_envelope(6, 6, 0, intensity=intensity)

    assert deload.phase == "deload" and taper.phase == "taper"
    assert (deload_pref.rpe_low, deload_pref.rpe_high) == (deload.rpe_low, deload.rpe_high)
    assert (taper_pref.rpe_low, taper_pref.rpe_high) == (taper.rpe_low, taper.rpe_high)
    assert deload_pref.volume_modifier == deload.volume_modifier


def test_set_deltas_are_the_documented_ones():
    assert intensity_set_delta("easy") == -1
    assert intensity_set_delta("medium") == 0
    assert intensity_set_delta(INTENSITY_HARD) == 1
    assert intensity_set_delta("nonsense") == 0


# --- E end to end: what the athlete actually receives ----------------------------------

from test_prescriber_candidates import _state  # noqa: E402

from app.logic.prescriber import recommend_next_session  # noqa: E402

STRENGTH_WEEK = {
    "block_goal": "Strength",
    "session_category": "Max Strength",
    "session_domain": "strength",
    "week_number": 2,
    "duration_weeks": 8,
    "deload_every_n_weeks": 4,
}


def _codes(rx) -> list[str]:
    return list(rx.why.constraints_applied) if rx.why is not None else []


def _total_sets(rx) -> int:
    return sum(ex.sets or 0 for ex in rx.exercises)


def _rx(intensity: str | None, **overrides):
    context = dict(STRENGTH_WEEK, **overrides)
    if intensity is not None:
        context["intensity"] = intensity
    return recommend_next_session(_state(), goal="Strength", block_context=context)


def test_a_strength_session_gains_and_loses_working_sets_with_the_preference():
    easy, medium, hard = _rx("easy"), _rx("medium"), _rx("hard")

    assert _total_sets(easy) < _total_sets(medium) < _total_sets(hard)
    assert all(ex.sets >= 1 for ex in easy.exercises if ex.sets is not None)


def test_an_unset_preference_prescribes_exactly_what_medium_does():
    assert _total_sets(_rx(None)) == _total_sets(_rx("medium"))
    assert _rx(None).type == _rx("medium").type


def test_the_explanation_names_the_preference_only_when_it_moved_something():
    hard = _codes(_rx("hard"))
    medium = _codes(_rx("medium"))

    assert "block:intensity=hard" in hard
    assert not any(c.startswith("block:intensity") for c in medium)


def test_a_recovery_week_says_the_preference_did_nothing_there():
    deload = _codes(_rx("hard", week_number=4, is_deload=True))

    assert "block:intensity=hard(no-op:recovery-week)" in deload


def test_an_endurance_day_says_it_has_no_set_targets_to_move():
    """Fork 5: the preference adjusts strength-type sessions only, and admits it elsewhere."""
    running = _codes(_rx("hard", session_category="Aerobic Base", session_domain="running"))

    assert "block:intensity=hard(no-op:no-set-targets:running)" in running


def test_a_safety_override_prescribes_the_same_session_at_every_preference():
    """A preference is not permission: the override wins and is not amplified."""
    hurt = _state(lumbar=85.0, knee=85.0, f_struct_damage=80.0)

    prescriptions = [
        recommend_next_session(hurt, goal="Strength", block_context=dict(STRENGTH_WEEK, intensity=i))
        for i in ("easy", "medium", "hard")
    ]

    assert len({rx.type for rx in prescriptions}) == 1
    assert len({_total_sets(rx) for rx in prescriptions}) == 1
    assert all(any(c.startswith("safety:override=") for c in _codes(rx)) for rx in prescriptions)
