"""`/v1/planning/today` declares a typed prescription, not an untyped object.

The payload was already a serialized ``WorkoutPrescription``: the route used to fill
this field with ``rx.to_prescribed_content()``, which is exactly ``self.model_dump()``
(``WorkoutPrescription.to_prescribed_content``, app/schemas/prescription.py). What was
missing was the *declaration* — ``TodaySessionResponse.prescription`` was
``dict[str, Any] | None``, so the published contract said "some object" and the web
client hand-rolled a matching type that could never fail when the contract moved.
The route now hands over ``rx`` itself (app/api/v1/planning.py, ``get_today``); the
bytes on the wire are unchanged, which is what these tests hold down.

Three guarantees are pinned here:

1. The published OpenAPI contract references the ``WorkoutPrescription`` component
   for that field (the red→green driver — the declaration is what this slice moves).
2. A request with no session today still yields ``prescription: null`` over HTTP.
3. A request with a real session yields a payload that validates against
   ``WorkoutPrescription``.

(2) and (3) are the integration proof: once the field is declared as a model,
FastAPI validates the *outgoing* payload against it, so a mismatch between the
declaration and what the prescriber actually emits would surface as a 500
``ResponseValidationError`` rather than a silently wrong contract. They are the
reason regeneration alone is not proof.

NOTE: (2) and (3) are async DB integration tests. They run against the
session-scoped test schema whenever a database is available, and skip only when
none is (a hard failure under REQUIRE_DB in CI).
"""

from datetime import date, timedelta
from typing import Any

from httpx import ASGITransport, AsyncClient

from app.core.auth import get_current_user
from app.core.db import get_db
from app.main import app
from app.models.mesocycle import BlockGoal, MesocycleBlock, PlannedSession
from app.models.user import AthleteProfile, User
from app.schemas.prescription import WorkoutPrescription
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT
from app.services.state_service import initialize_athlete_state

# `asyncio_mode = "auto"` (pyproject.toml:155) runs the async tests below; the one
# sync test in this module stays sync deliberately and needs no marker.

PRESCRIPTION_REF = "#/components/schemas/WorkoutPrescription"


def _today_prescription_schema() -> dict[str, Any]:
    """The `prescription` property as the published OpenAPI document declares it."""
    schema = app.openapi()
    return schema["components"]["schemas"]["TodaySessionResponse"]["properties"]["prescription"]


def test_openapi_declares_prescription_as_workout_prescription() -> None:
    """The contract must name the model, not a bare object.

    Pydantic renders ``WorkoutPrescription | None`` as ``anyOf: [$ref, {"type": "null"}]``;
    the old ``dict[str, Any] | None`` rendered as ``{"type": "object"} | null`` with no
    ``$ref`` anywhere. So "a $ref to WorkoutPrescription appears" is the exact
    discriminator between the two declarations.
    """
    prop = _today_prescription_schema()
    refs = [branch.get("$ref") for branch in prop.get("anyOf", []) if isinstance(branch, dict)]
    assert PRESCRIPTION_REF in refs, prop
    # And null is still an allowed state — the field stays optional.
    assert {"type": "null"} in prop.get("anyOf", []), prop


def test_openapi_declares_each_exercise_load_explanation() -> None:
    """N1: every prescribed exercise publishes a typed, nullable explanation of its weight."""
    schema = app.openapi()
    prop = schema["components"]["schemas"]["ExercisePrescription"]["properties"]["load_explanation"]
    refs = [branch.get("$ref") for branch in prop.get("anyOf", []) if isinstance(branch, dict)]
    assert "#/components/schemas/LoadExplanation" in refs, prop
    assert {"type": "null"} in prop.get("anyOf", []), prop


async def _mk_user(db, email: str) -> User:
    u = User(email=email, hashed_password="h", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _get_today(async_db, user: User) -> dict[str, Any]:
    async def _override_db():
        yield async_db

    async def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/v1/planning/today", params={"goal": TRAINING_GOAL_DEFAULT}
            )
            assert resp.status_code == 200, resp.text
            return resp.json()
    finally:
        app.dependency_overrides.clear()


async def test_today_returns_null_prescription_when_no_session_today(async_db):
    """State 1 of 2: no session scheduled today -> `prescription: null` over HTTP."""
    user = await _mk_user(async_db, "today-contract-null@test.com")
    async_db.add(AthleteProfile(user_id=user.id, equipment=["barbell"]))
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)

    # A block whose only session is scheduled for tomorrow: the athlete has a plan,
    # but nothing lands on today, which is the branch that returns both fields null.
    block = MesocycleBlock(
        user_id=user.id,
        goal=BlockGoal.STRENGTH,
        duration_weeks=4,
        sessions_per_week=3,
        start_date=date.today(),
        deload_every_n_weeks=4,
    )
    async_db.add(block)
    await async_db.commit()
    await async_db.refresh(block)

    tomorrow = date.today() + timedelta(days=1)
    async_db.add(
        PlannedSession(
            block_id=block.id,
            user_id=user.id,
            scheduled_date=tomorrow,
            week_number=1,
            day_of_week=tomorrow.isoweekday(),
            category="Heavy Lower",
            modality="Strength",
        )
    )
    await async_db.commit()

    payload = await _get_today(async_db, user)
    assert payload["session"] is None, payload
    assert payload["prescription"] is None, payload


async def test_today_prescription_validates_as_workout_prescription(async_db):
    """State 2 of 2: a real session yields a payload the declared model accepts.

    ``model_validate`` here is not decoration: after the retype FastAPI validates the
    response against ``WorkoutPrescription`` on the way out, so this asserts the same
    contract the framework now enforces — and re-asserts it on the parsed payload so a
    field that silently vanished from the wire would fail here too.
    """
    user = await _mk_user(async_db, "today-contract-populated@test.com")
    async_db.add(AthleteProfile(user_id=user.id, equipment=["barbell"]))
    await async_db.commit()
    await initialize_athlete_state(async_db, user.id)

    block = MesocycleBlock(
        user_id=user.id,
        goal=BlockGoal.STRENGTH,
        duration_weeks=8,
        sessions_per_week=3,
        start_date=date.today(),
        deload_every_n_weeks=4,
    )
    async_db.add(block)
    await async_db.commit()
    await async_db.refresh(block)

    async_db.add(
        PlannedSession(
            block_id=block.id,
            user_id=user.id,
            scheduled_date=date.today(),
            week_number=2,
            day_of_week=date.today().isoweekday(),
            category="Heavy Lower",
            modality="Strength",
        )
    )
    await async_db.commit()

    payload = await _get_today(async_db, user)
    assert payload["session"] is not None, payload
    raw = payload["prescription"]
    assert raw is not None, payload

    rx = WorkoutPrescription.model_validate(raw)
    # Required fields survived the round trip with real content, not just defaults.
    assert rx.type, raw
    assert rx.focus, raw
    assert rx.rationale, raw
    assert rx.duration_min > 0, raw
    assert rx.exercises, raw
    # The nested explanation is typed too, not a passthrough dict.
    assert rx.why is None or rx.why.goal_alignment is not None
    # N1: the weight explanation is on the wire for every exercise — null when there is
    # nothing to explain, never an absent key the client has to guess about.
    assert all("load_explanation" in ex for ex in raw["exercises"]), raw
