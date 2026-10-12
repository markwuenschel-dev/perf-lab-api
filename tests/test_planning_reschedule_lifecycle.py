"""Moving a pending session's date must not take it out of the lifecycle.

`PATCH /v1/planning/sessions/{id}` used to auto-assign `SessionStatus.RESCHEDULED`
whenever `scheduled_date` changed and the caller sent no explicit status. Both live
session-resolution paths filter on `PENDING` — `planning_service.get_today_session`
(app/services/planning_service.py:289) and `state_service._match_planned_session`
(app/services/state_service.py:571) — and nothing in `app/` ever writes a session back
to `PENDING`. A moved session was therefore permanently invisible to
`/v1/planning/today`, never auto-linked a logged workout, and could never reach
`COMPLETED`. See ADR-0069.

These tests pin the route-observable behaviour: a date move leaves status alone, an
explicit status still wins, and `original_scheduled_date` provenance is unchanged.
"""

from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import get_current_user
from app.core.db import get_db
from app.main import app
from app.models.user import AthleteProfile, User
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT
from app.services.state_service import initialize_athlete_state

pytestmark = pytest.mark.asyncio


async def _mk_user(db, email: str) -> User:
    u = User(email=email, hashed_password="h", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    profile = AthleteProfile(user_id=u.id, equipment=["dumbbells"])
    db.add(profile)
    await db.commit()
    await initialize_athlete_state(db, u.id)
    return u


def _override(db, user) -> None:
    async def _override_db():
        yield db

    async def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user


async def _create_future_block(client: AsyncClient, start: date) -> None:
    """A block whose sessions all fall on/after ``start``.

    Sessions are laid out at ``start_date + (week - 1) * 7 + (slot - 1)``
    (``create_block_with_sessions``, ADR-0074), so a future ``start`` guarantees no
    session exists on today — which is what makes the later move-to-today observable.
    """
    resp = await client.post(
        "/v1/planning/blocks",
        json={
            "goal": "Strength",
            "start_date": start.isoformat(),
            "duration_weeks": 2,
            "sessions_per_week": 3,
        },
    )
    assert resp.status_code == 200, resp.text


async def _first_session(client: AsyncClient, start: date) -> dict:
    resp = await client.get(
        "/v1/planning/sessions",
        params={
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=21)).isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    sessions = resp.json()
    assert sessions, "block creation should have produced planned sessions"
    return sessions[0]


async def test_moving_a_session_to_today_keeps_it_discoverable_and_pending(async_db):
    """REQ-1 + REQ-2: a bare date move to today stays visible to /planning/today.

    ``get_today_session`` filters ``scheduled_date == today AND status == PENDING``
    (app/services/planning_service.py:287-289), so moving the session *to today* is
    the only way to observe the defect through the route.
    """
    user = await _mk_user(async_db, "reschedule-today@test.com")
    _override(async_db, user)

    today = date.today()
    start = today + timedelta(days=7)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await _create_future_block(client, start)

            # Precondition: nothing is scheduled for today yet.
            pre = await client.get(
                "/v1/planning/today", params={"goal": TRAINING_GOAL_DEFAULT}
            )
            assert pre.status_code == 200, pre.text
            assert pre.json()["session"] is None

            session = await _first_session(client, start)
            assert session["status"] == "pending"

            patch = await client.patch(
                f"/v1/planning/sessions/{session['id']}",
                json={"scheduled_date": today.isoformat()},
            )
            assert patch.status_code == 200, patch.text
            moved = patch.json()
            assert moved["scheduled_date"] == today.isoformat()

            # REQ-2: still lifecycle-completable — this is exactly the predicate
            # state_service._match_planned_session uses to auto-link a workout log.
            assert moved["status"] == "pending", (
                "a bare date move must not change lifecycle status; "
                f"got {moved['status']!r}"
            )

            # REQ-1: the moved session is discoverable again.
            today_resp = await client.get(
                "/v1/planning/today", params={"goal": TRAINING_GOAL_DEFAULT}
            )
            assert today_resp.status_code == 200, today_resp.text
            payload = today_resp.json()
            assert payload["session"] is not None, (
                "session moved to today must be resolvable by /v1/planning/today"
            )
            assert payload["session"]["id"] == session["id"]
    finally:
        app.dependency_overrides.clear()


async def test_explicit_status_still_wins_when_the_date_also_moves(async_db):
    """REQ-3: only the *automatic* write was removed; explicit status still applies."""
    user = await _mk_user(async_db, "reschedule-explicit@test.com")
    _override(async_db, user)

    today = date.today()
    start = today + timedelta(days=7)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await _create_future_block(client, start)
            session = await _first_session(client, start)

            patch = await client.patch(
                f"/v1/planning/sessions/{session['id']}",
                json={
                    "scheduled_date": (start + timedelta(days=1)).isoformat(),
                    "status": "skipped",
                },
            )
            assert patch.status_code == 200, patch.text
            body = patch.json()
            assert body["status"] == "skipped"
            assert body["scheduled_date"] == (start + timedelta(days=1)).isoformat()

            # An explicit "rescheduled" is still honoured — the enum value is retained
            # as compatibility surface (app/models/mesocycle.py:48, ADR-0069).
            patch2 = await client.patch(
                f"/v1/planning/sessions/{session['id']}",
                json={"status": "rescheduled"},
            )
            assert patch2.status_code == 200, patch2.text
            assert patch2.json()["status"] == "rescheduled"
    finally:
        app.dependency_overrides.clear()


async def test_original_scheduled_date_provenance_is_unchanged(async_db):
    """REQ-4: first move records the old date, later moves preserve it, no-op records nothing."""
    user = await _mk_user(async_db, "reschedule-provenance@test.com")
    _override(async_db, user)

    today = date.today()
    start = today + timedelta(days=7)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            await _create_future_block(client, start)
            session = await _first_session(client, start)
            original = session["scheduled_date"]
            assert session["original_scheduled_date"] is None

            # Moving to the same date records nothing.
            noop = await client.patch(
                f"/v1/planning/sessions/{session['id']}",
                json={"scheduled_date": original},
            )
            assert noop.status_code == 200, noop.text
            assert noop.json()["original_scheduled_date"] is None
            assert noop.json()["scheduled_date"] == original

            # First genuine move records the original plan date.
            first_target = (date.fromisoformat(original) + timedelta(days=1)).isoformat()
            first = await client.patch(
                f"/v1/planning/sessions/{session['id']}",
                json={"scheduled_date": first_target},
            )
            assert first.status_code == 200, first.text
            assert first.json()["original_scheduled_date"] == original
            assert first.json()["scheduled_date"] == first_target

            # Second move preserves the first recorded value.
            second_target = (date.fromisoformat(original) + timedelta(days=2)).isoformat()
            second = await client.patch(
                f"/v1/planning/sessions/{session['id']}",
                json={"scheduled_date": second_target},
            )
            assert second.status_code == 200, second.text
            assert second.json()["original_scheduled_date"] == original
            assert second.json()["scheduled_date"] == second_target
    finally:
        app.dependency_overrides.clear()
