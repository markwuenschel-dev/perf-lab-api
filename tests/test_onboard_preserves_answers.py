"""What POST /v1/onboard does with fields the athlete did and did not answer (live DB).

Two defects this pins, both found by using the app:

1. **The web screen collected days-per-week, session length and equipment and never sent
   them**, so `can_prescribe` stayed false right after onboarding — the hard gate wants
   equipment (app/logic/onboarding_state.py:72). The client half is fixed in
   OnboardingScreen.tsx; here we prove the route honours a complete payload and names exactly
   what is missing from an incomplete one.
2. **An omitted field was written as the request-schema default**, so re-submitting onboarding
   wiped a stored date of birth, bodyweight and 5K, and relabelled the athlete "intermediate"
   while the AthleteProfile column defaults to "beginner".

The invariant, stated once: AN OMITTED FIELD MEANS "NOT ANSWERED", NEVER "CLEAR IT", and the
value written to the profile is the same one the baseline seed reads.
"""
from datetime import date

import pytest
from httpx import AsyncClient

from app.models.user import AthleteProfile

pytestmark = pytest.mark.asyncio

DOB = "1990-04-17"


async def _register_and_token(client: AsyncClient, email: str) -> dict[str, str]:
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


def _complete_payload(**overrides) -> dict:
    """Exactly what the fixed onboarding screen sends for the hard gate."""
    body = {
        "goal": "Strength",
        "date_of_birth": DOB,
        "equipment": ["barbell", "dumbbell"],
        "available_days_per_week": 4,
        "session_duration_minutes": 75,
    }
    body.update(overrides)
    return body


async def test_complete_submission_satisfies_the_prescribe_gate(http_client: AsyncClient):
    headers = await _register_and_token(http_client, "onb_complete@test.com")

    onboard = await http_client.post("/v1/onboard", json=_complete_payload(), headers=headers)
    assert onboard.status_code == 200, onboard.text

    state = await http_client.get("/v1/onboarding/state", headers=headers)
    assert state.status_code == 200, state.text
    assert state.json()["can_prescribe"] is True
    assert state.json()["missing_basics"] == []

    profile = (await http_client.get("/v1/profile", headers=headers)).json()
    assert profile["available_days_per_week"] == 4
    assert profile["session_duration_minutes"] == 75
    assert profile["equipment"] == ["barbell", "dumbbell"]


@pytest.mark.parametrize(
    ("omit", "expected_missing"),
    [
        ("equipment", "equipment"),
        ("date_of_birth", "date_of_birth"),
        ("goal", "primary_goal"),
    ],
)
async def test_each_basic_is_required_on_its_own(
    http_client: AsyncClient, omit: str, expected_missing: str
):
    """Equipment alone does not open the gate — every basic is load-bearing."""
    headers = await _register_and_token(http_client, f"onb_missing_{omit}@test.com")
    payload = _complete_payload()
    payload.pop(omit)

    onboard = await http_client.post("/v1/onboard", json=payload, headers=headers)
    assert onboard.status_code == 200, onboard.text

    state = (await http_client.get("/v1/onboarding/state", headers=headers)).json()
    if omit == "goal":
        # goal falls back to the documented default, so the gate stays open.
        assert state["can_prescribe"] is True
        assert state["missing_basics"] == []
    else:
        assert state["can_prescribe"] is False
        assert state["missing_basics"] == [expected_missing]


async def test_resubmission_does_not_erase_earlier_answers(http_client: AsyncClient):
    """The defect: a second submission used to write schema defaults over stored answers."""
    headers = await _register_and_token(http_client, "onb_resubmit@test.com")

    first = await http_client.post(
        "/v1/onboard",
        json=_complete_payload(
            bodyweight_kg=82.5,
            run_5k_seconds=1380.0,
            experience_level="advanced",
            experience_years=7.0,
            height_cm=180.0,
        ),
        headers=headers,
    )
    assert first.status_code == 200, first.text

    # A later pass that only re-states the goal — every other field omitted.
    second = await http_client.post("/v1/onboard", json={"goal": "Hypertrophy"}, headers=headers)
    assert second.status_code == 200, second.text

    profile = (await http_client.get("/v1/profile", headers=headers)).json()
    assert profile["primary_goal"] == "Hypertrophy"
    assert profile["date_of_birth"] == DOB
    assert profile["bodyweight_kg"] == 82.5
    assert profile["run_5k_seconds"] == 1380.0
    assert profile["experience_level"] == "advanced"
    assert profile["experience_years"] == 7.0
    assert profile["height_cm"] == 180.0
    assert profile["equipment"] == ["barbell", "dumbbell"]
    assert profile["available_days_per_week"] == 4
    assert profile["session_duration_minutes"] == 75


async def test_unanswered_experience_level_agrees_with_the_column_default(
    http_client: AsyncClient, async_db
):
    """No experience level submitted → the model's own default, not a second opinion.

    The request schema used to default this to "intermediate" while AthleteProfile defaults to
    "beginner", so an athlete who never answered was silently seeded as intermediate.
    """
    headers = await _register_and_token(http_client, "onb_exp_default@test.com")

    onboard = await http_client.post("/v1/onboard", json=_complete_payload(), headers=headers)
    assert onboard.status_code == 200, onboard.text

    column_default = AthleteProfile.__table__.c.experience_level.default.arg
    profile = (await http_client.get("/v1/profile", headers=headers)).json()
    assert profile["experience_level"] == column_default


async def test_explicit_empty_equipment_is_an_answer(http_client: AsyncClient):
    """[] means "not set" (nothing is filtered) and is distinguishable from not answering."""
    headers = await _register_and_token(http_client, "onb_equip_empty@test.com")

    first = await http_client.post("/v1/onboard", json=_complete_payload(), headers=headers)
    assert first.status_code == 200, first.text

    cleared = await http_client.post("/v1/onboard", json={"equipment": []}, headers=headers)
    assert cleared.status_code == 200, cleared.text

    profile = (await http_client.get("/v1/profile", headers=headers)).json()
    assert profile["equipment"] == []
    state = (await http_client.get("/v1/onboarding/state", headers=headers)).json()
    assert state["can_prescribe"] is False
    assert "equipment" in state["missing_basics"]


async def test_stored_context_fields_round_trip(http_client: AsyncClient):
    """Height, overhead, pull-ups and 1.5 mi are saved — and nothing claims they shape a plan."""
    headers = await _register_and_token(http_client, "onb_context@test.com")

    onboard = await http_client.post(
        "/v1/onboard",
        json=_complete_payload(
            height_cm=175.0,
            overhead_1rm_kg=60.0,
            pullup_max_reps=14,
            run_1p5mi_seconds=540.0,
        ),
        headers=headers,
    )
    assert onboard.status_code == 200, onboard.text

    profile = (await http_client.get("/v1/profile", headers=headers)).json()
    assert profile["height_cm"] == 175.0
    assert profile["overhead_1rm_kg"] == 60.0
    assert profile["pullup_max_reps"] == 14
    assert profile["run_1p5mi_seconds"] == 540.0


async def test_dob_in_the_future_is_still_refused(http_client: AsyncClient):
    """Making the field optional must not weaken its validation."""
    headers = await _register_and_token(http_client, "onb_future_dob@test.com")
    future = date(date.today().year + 1, 1, 1).isoformat()

    resp = await http_client.post(
        "/v1/onboard", json=_complete_payload(date_of_birth=future), headers=headers
    )

    assert resp.status_code == 422, resp.text
