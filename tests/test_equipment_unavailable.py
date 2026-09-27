"""Honest equipment outcomes (phase 9.4, ADR-0072).

```
 all required slots resolve                  -> the planned session
 planned session cannot be done with the kit -> an in-domain session, plan:session_replaced=<slug>(equipment)
 nothing in-domain can be done               -> "Equipment Unavailable", zero work,
                                                plan:session_unavailable=<slug>,
                                                equipment:unavailable=<missing tags>
```

Never the general pool, never the bodyweight map, never part of an authored session.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.logic.prescriber import recommend_next_session
from app.scripts import simulate_matrix as sm


@pytest.fixture(scope="module")
def catalog():
    return sm._catalog()


def _prescribe(catalog, goal: str, domain: str, category: str, equipment: list[str] | None):
    level_key, _ = sm.EXPERIENCE["intermediate"]
    return recommend_next_session(
        sm._state(level_key, *sm.FRESHNESS["fresh"]),
        goal=goal,  # type: ignore[arg-type]
        catalog=catalog,
        available_equipment=equipment,
        block_context={
            "block_goal": goal, "session_category": category, "session_domain": domain,
            "week_number": 2, "duration_weeks": 8, "deload_every_n_weeks": 4,
        },
    )


def test_a_weightlifting_day_without_a_barbell_is_unavailable_not_filler(catalog) -> None:
    rx = _prescribe(catalog, "OlympicLifts", "weightlifting", "Weightlifting Technique",
                    ["bodyweight"])
    assert rx.why is not None
    codes = rx.why.constraints_applied
    assert rx.type == "Equipment Unavailable"
    assert rx.exercises == []
    assert rx.duration_min == 0
    assert rx.structure is None
    assert "plan:session_unavailable=weightlifting_technique" in codes
    assert "equipment:unavailable=barbell" in codes
    # Nothing was prescribed in its place, so it is NOT a replacement.
    assert not any(c.startswith("plan:session_replaced=") for c in codes)
    assert "equipment:fallback_bodyweight" not in codes


def test_the_unavailable_outcome_keeps_the_planned_identity(catalog) -> None:
    rx = _prescribe(catalog, "OlympicLifts", "weightlifting", "Weightlifting Technique",
                    ["bodyweight"])
    assert rx.why is not None and rx.why.session_unavailable is not None
    u = rx.why.session_unavailable
    assert u.planned_domain == "weightlifting"
    assert u.planned_category == "Weightlifting Technique"
    assert u.planned_slug == "weightlifting_technique"
    assert u.planned_branch_ids == ["wl_technique_snatch", "wl_technique_cj"]
    assert u.missing_equipment == ["barbell"]


def test_a_hyrox_day_at_home_is_unavailable_never_general_gpp(catalog) -> None:
    """Before 9.4 this day silently became gpp_balanced (the general-pool fallback)."""
    rx = _prescribe(catalog, "MetCon", "mixed", "Hyrox Simulation",
                    ["dumbbells", "kettlebell", "pullup_bar"])
    assert rx.why is not None
    assert rx.why.prescription_branch != "gpp_balanced"
    assert rx.type == "Equipment Unavailable"
    assert "plan:session_unavailable=hyrox_simulation" in rx.why.constraints_applied


def test_an_in_domain_replacement_says_equipment_and_is_not_unavailable(catalog) -> None:
    rx = _prescribe(catalog, "Power", "power", "Power Development",
                    ["dumbbells", "kettlebell", "pullup_bar"])
    assert rx.why is not None
    codes = rx.why.constraints_applied
    assert "plan:session_replaced=power_development(equipment)" in codes
    assert rx.why.session_unavailable is None
    assert rx.exercises, "a replacement is a real session"


def test_unconfigured_equipment_is_permissive_and_never_unavailable(catalog) -> None:
    for goal, domain, category in [
        ("OlympicLifts", "weightlifting", "Weightlifting Technique"),
        ("MetCon", "mixed", "Hyrox Simulation"),
        ("Strength", "strength", "Max Strength"),
    ]:
        for equipment in (None, []):
            rx = _prescribe(catalog, goal, domain, category, equipment)
            assert rx.type != "Equipment Unavailable", (category, equipment)
            assert rx.why is not None and rx.why.session_unavailable is None


def test_an_authored_session_is_never_realized_in_part(catalog) -> None:
    """High Volume Upper at home: its barbell slots cannot resolve, so it is not prescribed
    with only its dumbbell slot (before 9.4 it was)."""
    rx = _prescribe(catalog, "Hypertrophy", "hypertrophy", "High Volume Upper",
                    ["dumbbells", "kettlebell", "pullup_bar"])
    assert rx.why is not None
    assert rx.why.prescription_branch != "hyp_upper_split" or len(rx.exercises) == 3


# ── zero work: an unavailable prescription never becomes work by existing ─────────────


def test_an_unavailable_prescription_carries_no_work_or_forecast(catalog) -> None:
    rx = _prescribe(catalog, "Powerlifting", "powerlifting", "SBD Strength", ["bodyweight"])
    assert rx.type == "Equipment Unavailable"
    assert rx.exercises == [] and rx.duration_min == 0 and rx.structure is None
    assert rx.why is not None and rx.why.expected_outcomes == []


def test_logging_against_an_unavailable_plan_seeds_nothing(catalog) -> None:
    """The only path from a planned session's content to dose is seeding a logged workout's
    exercises from it. Zero exercises seed nothing, so the log carries only what the athlete
    explicitly logs."""
    from datetime import UTC, datetime

    from app.schemas.workouts import WorkoutLog
    from app.services.state_service import _seed_exercises_from_prescription

    rx = _prescribe(catalog, "Powerlifting", "powerlifting", "SBD Strength", ["bodyweight"])
    planned: Any = SimpleNamespace(prescribed_content=rx.to_prescribed_content())
    log = WorkoutLog(timestamp=datetime(2026, 9, 27, tzinfo=UTC), modality="Strength",
                     duration_minutes=40.0, session_rpe=6.0)
    seeded = _seed_exercises_from_prescription(log, planned)
    assert seeded.exercises == []
    assert seeded is log


# ── phase 9.4c: kit variants ─────────────────────────────────────────────────────────

HOME = ["dumbbells", "kettlebell", "pullup_bar"]


@pytest.mark.parametrize(
    ("goal", "domain", "category", "variant"),
    [
        ("Strength", "strength", "Strength — Volume", "strength_volume_home"),
        ("Hypertrophy", "hypertrophy", "High Volume Upper", "hyp_upper_split_home"),
        ("Hypertrophy", "hypertrophy", "High Volume Lower", "hyp_high_vol_home"),
        ("Hypertrophy", "hypertrophy", "Accessory / Isolation", "hyp_maintenance_home"),
        ("Power", "power", "Neural Priming", "power_neural_prime_jumps"),
    ],
)
def test_a_home_kit_gets_the_named_variant_and_says_equipment(
    catalog, goal, domain, category, variant
) -> None:
    rx = _prescribe(catalog, goal, domain, category, HOME)
    assert rx.why is not None
    assert rx.why.prescription_branch == variant
    assert any(c.startswith("plan:session_replaced=") and c.endswith("(equipment)")
               for c in rx.why.constraints_applied)
    assert rx.why.session_unavailable is None
    # Visibly a fallback version: the rationale says so (the title is rebuilt from the
    # resolved exercises, so the authored focus is not where it shows).
    assert rx.rationale.startswith("Home-kit version (ADR-0072)")


@pytest.mark.parametrize("week", [1, 2, 3, 4])
@pytest.mark.parametrize("equipment", [None, "full_gym"])
def test_a_kit_variant_never_reaches_an_athlete_who_can_do_the_primary(
    catalog, week, equipment
) -> None:
    """A variant is a candidate only when its primary cannot be done, so it can never tie
    with, or rotate against, its primary (week rotation, phase 7.2)."""
    from app.logic.kit_support import KIT_SUPPORT  # noqa: F401  (documents the kit contract)
    from app.scripts.kit_matrix import bindings

    kit = (sorted({e for ex in catalog for e in ex.equipment_required if e})
           if equipment == "full_gym" else None)
    level_key, _ = sm.EXPERIENCE["intermediate"]
    for domain, category, _binding in bindings():
        goal = {"strength": "Strength", "hypertrophy": "Hypertrophy", "power": "Power"}.get(domain)
        if goal is None:
            continue
        rx = recommend_next_session(
            sm._state(level_key, *sm.FRESHNESS["fresh"]), goal=goal,  # type: ignore[arg-type]
            catalog=catalog, available_equipment=kit,
            block_context={"block_goal": goal, "session_category": category,
                           "session_domain": domain, "week_number": week,
                           "duration_weeks": 8, "deload_every_n_weeks": 4},
        )
        assert rx.why is not None
        assert not (rx.why.prescription_branch or "").endswith(("_home", "_jumps")), (
            category, week, rx.why.prescription_branch)


@pytest.mark.parametrize("equipment", [None, "full_gym"])
def test_a_kit_variant_is_not_even_a_candidate_while_its_primary_can_be_done(
    catalog, equipment
) -> None:
    """Stronger than "never wins": a variant ties its primary's score, so today a stable sort
    hides it, but any future score term that differs (novelty for a repeated branch, say)
    would hand a full-gym athlete the home version. It must not be in the pool at all."""
    from app.logic.candidate_library import template_by_branch
    from app.scripts.kit_matrix import bindings

    kit = (sorted({e for ex in catalog for e in ex.equipment_required if e})
           if equipment == "full_gym" else None)
    level_key, _ = sm.EXPERIENCE["intermediate"]
    for domain, category, _binding in bindings():
        goal = {"strength": "Strength", "hypertrophy": "Hypertrophy", "power": "Power"}.get(domain)
        if goal is None:
            continue
        pool: list[Any] = []
        recommend_next_session(
            sm._state(level_key, *sm.FRESHNESS["fresh"]), goal=goal,  # type: ignore[arg-type]
            catalog=catalog, available_equipment=kit, candidate_log_out=pool,
            block_context={"block_goal": goal, "session_category": category,
                           "session_domain": domain, "week_number": 2,
                           "duration_weeks": 8, "deload_every_n_weeks": 4},
        )
        variants = [c.branch_id for c in pool
                    if (t := template_by_branch(c.branch_id)) and t.kit_fallback_for]
        assert variants == [], (category, variants)


def test_unilateral_reps_say_per_side() -> None:
    from app.logic.candidate_library import template_by_branch

    t = template_by_branch("hyp_high_vol_home")
    assert t is not None
    reps = {s.exercise: s.reps for s in t.exercise_slots}
    assert reps["Split Squat"] == "12 each side"
    assert reps["Walking Lunge"] == "15 each side"


def test_home_variants_never_assume_a_bench() -> None:
    """The catalog does not model a bench, and a home kit does not guarantee one, so a home
    variant must not pin a movement that needs one (DB Bench Press, rear-foot-elevated
    Bulgarian Split Squat). Their floor / flat equivalents are used instead."""
    from app.logic.candidate_library import GOAL_TEMPLATE_LIBRARY

    needs_bench = {"Dumbbell Bench Press", "Bulgarian Split Squat", "Incline Dumbbell Press"}
    pinned = {s.exercise for pool in GOAL_TEMPLATE_LIBRARY.values() for t in pool
              if t.kit_fallback_for for s in t.exercise_slots}
    assert not pinned & needs_bench


@pytest.mark.parametrize("empty", [None, []])
def test_no_catalog_is_never_reported_as_the_athletes_equipment(empty) -> None:
    """An unloaded or empty catalog is OUR missing data. Telling the athlete their equipment
    cannot do the session would be false, so it keeps the pre-phase-9 behaviour."""
    level_key, _ = sm.EXPERIENCE["intermediate"]
    rx = recommend_next_session(
        sm._state(level_key, *sm.FRESHNESS["fresh"]), goal="Strength",
        catalog=empty, available_equipment=["barbell"],
        block_context={"block_goal": "Strength", "session_category": "Max Strength",
                       "session_domain": "strength", "week_number": 2,
                       "duration_weeks": 8, "deload_every_n_weeks": 4},
    )
    assert rx.type != "Equipment Unavailable"
    assert rx.why is not None and rx.why.session_unavailable is None
