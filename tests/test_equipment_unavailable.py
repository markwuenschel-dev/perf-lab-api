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
