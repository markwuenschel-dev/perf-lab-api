"""The block preview labels each slot with the weekday it falls on (P5b, ADR-0074).

The addition is response-only and additive: every existing field, value and the array order are
what the P5a preview returned, so a browser built before P5b reads the same week; each slot only
gains ``resolved_weekday``. The label never enters a stored template or a scheduling calculation:
a request that carries it in its template is scheduled exactly as one that does not.
"""
from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.auth import get_current_user
from app.core.db import get_db
from app.main import app
from app.models.mesocycle import BlockGoal, MesocycleBlock, PlannedSession
from app.models.user import User
from app.schemas.planning import BlockCreateRequest
from app.services.planning_service import preview_weekly_template

pytestmark = pytest.mark.asyncio

MONDAY = date(2026, 10, 12)
START_WEEKDAYS = [MONDAY + timedelta(days=i) for i in range(7)]
CUSTOM = [
    {"day_of_week": 5, "category": "C", "modality": "Strength"},
    {"day_of_week": 1, "category": "A", "modality": "Strength"},
    {"day_of_week": 3, "category": "B", "modality": "Strength"},
]


@pytest.fixture
async def client(async_db):
    user = User(email="preview-weekday@test.com", hashed_password="h", is_active=True)
    async_db.add(user)
    await async_db.commit()
    await async_db.refresh(user)

    async def _db():
        yield async_db

    async def _user():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _bodies(start: date) -> list[dict]:
    base = {"start_date": start.isoformat(), "duration_weeks": 2}
    return [
        *({**base, "goal": g.value, "sessions_per_week": n} for g in BlockGoal for n in (3, 5)),
        {**base, "goal": "Strength", "sessions_per_week": 3, "weekly_template": CUSTOM},
        {**base, "goal": "Running", "sessions_per_week": 4, "modality_mix": {"sprinting": 0.6, "strength": 0.4}},
    ]


@pytest.mark.parametrize("start", START_WEEKDAYS, ids=lambda d: f"{d:%a}")
async def test_the_preview_adds_a_weekday_and_changes_nothing_else(client, start: date) -> None:
    for body in _bodies(start):
        r = await client.post("/v1/planning/blocks/preview", json=body)
        assert r.status_code == 200, r.text
        slots = r.json()
        # What the P5a preview returned, field for field and in order.
        before = [s.model_dump(mode="json") for s in preview_weekly_template(BlockCreateRequest(**body))]
        assert [{k: v for k, v in s.items() if k != "resolved_weekday"} for s in slots] == before, body
        assert [s["resolved_weekday"] for s in slots] == [
            (start + timedelta(days=s["day_of_week"] - 1)).isoweekday() for s in slots
        ], body


@pytest.mark.parametrize("start", START_WEEKDAYS, ids=lambda d: f"{d:%a}")
async def test_each_previewed_weekday_is_where_creation_puts_the_session(
    client, async_db, start: date
) -> None:
    """The seam: the label the athlete sees is the weekday of the session that gets created."""
    for body in _bodies(start):
        preview = (await client.post("/v1/planning/blocks/preview", json=body)).json()
        created = await client.post("/v1/planning/blocks", json=body)
        assert created.status_code == 200, created.text
        week1 = (await async_db.execute(
            select(PlannedSession)
            .where(PlannedSession.block_id == created.json()["id"], PlannedSession.week_number == 1)
            .order_by(PlannedSession.id)
        )).scalars().all()
        assert [s.scheduled_date.isoweekday() for s in week1] == [p["resolved_weekday"] for p in preview], body
        assert [s.block_day for s in week1] == [p["day_of_week"] for p in preview], body


async def test_a_template_carrying_the_label_is_scheduled_and_stored_without_it(client, async_db) -> None:
    """Echoing a previewed week back as the template must not change anything: the label is not
    part of a slot."""
    thursday = MONDAY + timedelta(days=3)
    body = {"goal": "Strength", "start_date": thursday.isoformat(), "duration_weeks": 2,
            "sessions_per_week": 3, "weekly_template": CUSTOM}
    echoed = {**body, "weekly_template": (await client.post("/v1/planning/blocks/preview", json=body)).json()}
    assert all("resolved_weekday" in s for s in echoed["weekly_template"])

    plain = await client.post("/v1/planning/blocks", json=body)
    labelled = await client.post("/v1/planning/blocks", json=echoed)
    assert plain.status_code == labelled.status_code == 200, labelled.text
    assert labelled.json()["weekly_template"] == plain.json()["weekly_template"] == [
        {**slot, "domain": None} for slot in CUSTOM
    ]

    async def dates(block_id: int) -> list[tuple]:
        rows = (await async_db.execute(
            select(PlannedSession).where(PlannedSession.block_id == block_id).order_by(PlannedSession.id)
        )).scalars().all()
        return [(s.scheduled_date, s.block_day, s.day_of_week, s.category, s.is_benchmark) for s in rows]

    assert await dates(labelled.json()["id"]) == await dates(plain.json()["id"])
    stored = (await async_db.execute(
        select(MesocycleBlock.weekly_template).where(MesocycleBlock.id == labelled.json()["id"])
    )).scalar_one()
    assert all("resolved_weekday" not in s for s in stored)
