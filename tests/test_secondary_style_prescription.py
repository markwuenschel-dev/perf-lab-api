"""A secondary style's day is prescribed as that style — not relabelled.

`modality_mix` has always produced a multi-domain weekly template, but the prescriber built
every candidate pool from the BLOCK GOAL, so a Strength block's running day was a running
LABEL on a strength session. These tests drive `recommend_next_session` the way
prescription_service does, with the planned slot's own domain in the block context.

Every test here fails on main before the `session_domain` wiring, which is the point: the
old behaviour is indistinguishable from "the mix works" unless you look at what was prescribed.
"""
from __future__ import annotations

import pytest
from test_prescriber_candidates import _state

from app.logic.prescriber import recommend_next_session
from app.services.planning_service import _DOMAIN_SLOT, _template_from_modality_mix

STRENGTH_GOAL = "Strength"


def _slot_context(domain: str, *, block_goal: str = STRENGTH_GOAL) -> dict:
    """The block context prescription_service builds for a planned slot of this domain."""
    category, _modality = _DOMAIN_SLOT[domain]
    return {"block_goal": block_goal, "session_category": category, "session_domain": domain}


def test_the_running_day_of_a_strength_block_is_a_running_session() -> None:
    running_day = recommend_next_session(
        _state(), goal=STRENGTH_GOAL, block_context=_slot_context("running")
    )
    strength_day = recommend_next_session(
        _state(), goal=STRENGTH_GOAL, block_context=_slot_context("strength")
    )

    assert running_day.type != strength_day.type
    assert any(
        c.startswith("plan:session_followed=") for c in (running_day.why.constraints_applied or [])
    ), running_day.why.constraints_applied


@pytest.mark.parametrize(
    "pair",
    [("strength", "powerlifting"), ("power", "weightlifting"), ("calisthenics", "gymnastics")],
)
def test_lossy_pairs_prescribe_differently_although_they_share_a_modality_label(pair) -> None:
    """The reason a session carries its own domain instead of reusing `modality`."""
    first, second = pair
    assert _DOMAIN_SLOT[first][1] == _DOMAIN_SLOT[second][1], "precondition: the label is shared"

    first_rx = recommend_next_session(
        _state(), goal=STRENGTH_GOAL, block_context=_slot_context(first)
    )
    second_rx = recommend_next_session(
        _state(), goal=STRENGTH_GOAL, block_context=_slot_context(second)
    )

    assert (first_rx.type, first_rx.focus) != (second_rx.type, second_rx.focus)


def test_a_session_with_no_recorded_domain_behaves_exactly_as_before() -> None:
    """Sessions planned before the domain column exists must not change what they prescribe."""
    with_domain_absent = recommend_next_session(
        _state(),
        goal=STRENGTH_GOAL,
        block_context={"block_goal": STRENGTH_GOAL, "session_category": "Max Strength"},
    )
    with_domain_null = recommend_next_session(
        _state(),
        goal=STRENGTH_GOAL,
        block_context={
            "block_goal": STRENGTH_GOAL,
            "session_category": "Max Strength",
            "session_domain": None,
        },
    )

    assert with_domain_absent.type == with_domain_null.type
    assert with_domain_absent.focus == with_domain_null.focus


def test_the_mix_and_the_prescriber_agree_on_the_domain_vocabulary() -> None:
    """Whatever the template generator can emit, the prescriber must be able to act on."""
    slots = _template_from_modality_mix(dict.fromkeys(_DOMAIN_SLOT, 1.0), len(_DOMAIN_SLOT))

    assert slots is not None
    for slot in slots:
        assert slot.domain is not None
        rx = recommend_next_session(
            _state(),
            goal=STRENGTH_GOAL,
            block_context={
                "block_goal": STRENGTH_GOAL,
                "session_category": slot.category,
                "session_domain": slot.domain,
            },
        )
        assert rx.type, f"{slot.domain} produced no prescription"


# --- the route stores what the athlete chose -------------------------------------------

async def _register(client, email: str) -> dict[str, str]:
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


@pytest.mark.asyncio
async def test_a_created_block_keeps_its_mix_domains_and_workload(http_client):
    hdr = await _register(http_client, "mix_block@test.com")

    created = await http_client.post(
        "/v1/planning/blocks",
        json={
            "goal": "Strength",
            "start_date": "2026-09-21",
            "duration_weeks": 4,
            "sessions_per_week": 3,
            "modality_mix": {"strength": 0.67, "running": 0.33},
            "intensity": "hard",
        },
        headers=hdr,
    )
    assert created.status_code in (200, 201), created.text
    block = created.json()
    assert block["intensity"] == "hard"
    assert sorted(s["domain"] for s in block["weekly_template"]) == [
        "running",
        "strength",
        "strength",
    ]

    sessions = await http_client.get(
        "/v1/planning/sessions", params={"block_id": block["id"]}, headers=hdr
    )
    assert sessions.status_code == 200, sessions.text
    week_one = [s for s in sessions.json() if s["week_number"] == 1]
    assert sorted(s["domain"] for s in week_one) == ["running", "strength", "strength"]
