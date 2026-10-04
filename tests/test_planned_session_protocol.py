"""F3 — planned-session row protocol: one row lock, one table of allowed transitions.

Every mutation of a PlannedSession locks the row (FOR UPDATE, refreshed), validates against
its current state, and commits in the same transaction
(``app/services/planned_session_protocol.py``). These tests pin:

* the transition table, exhaustively, as a pure function;
* the route-visible 409s it produces;
* deterministic races on two real connections: a HOLDER keeps the row lock while the
  contender starts; the contender must still be blocked, and once the holder commits it must
  decide on the row the holder left. Where a test takes the state-chain lock too, it takes it
  FIRST, the same order production uses (F2 → F3).
"""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from conftest import assert_does_not_raise
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.models.user import User
from app.schemas.planning import PlannedSessionUpdateRequest
from app.schemas.prescription import WorkoutPrescription
from app.schemas.workouts import WorkoutLog
from app.services import planning_service, prescription_service, state_service
from app.services.planned_session_protocol import check_patch, lock_planned_session
from app.services.state_chain_lock import lock_athlete_chain

P, C, S, R = (
    SessionStatus.PENDING,
    SessionStatus.COMPLETED,
    SessionStatus.SKIPPED,
    SessionStatus.RESCHEDULED,
)
TODAY = date(2026, 10, 4)


# ── the transition table ──────────────────────────────────────────────────────────────


def _row(status: SessionStatus, day: date = TODAY) -> PlannedSession:
    return PlannedSession(status=status, scheduled_date=day)


_ALLOWED_STATUS = {
    (P, S), (P, R),
    (S, P), (S, R),
    (R, P), (R, S),
}


@pytest.mark.parametrize("current", [P, C, S, R], ids=lambda s: s.value)
@pytest.mark.parametrize("target", [P, C, S, R], ids=lambda s: s.value)
def test_patch_status_table(current, target):
    """Exhaustive: every (from, to) pair either passes or is a 409, as the table says."""
    allowed = current == target or (current, target) in _ALLOWED_STATUS
    if allowed:
        check_patch(_row(current), new_status=target, new_date=None, today=TODAY)
    else:
        with pytest.raises(HTTPException) as exc:
            check_patch(_row(current), new_status=target, new_date=None, today=TODAY)
        assert exc.value.status_code == 409


def test_a_completed_session_cannot_be_moved():
    with pytest.raises(HTTPException) as exc:
        check_patch(_row(C), new_status=None, new_date=TODAY + timedelta(days=1), today=TODAY)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("source", [S, R], ids=lambda s: s.value)
def test_returning_to_pending_requires_today_or_later(source):
    past = TODAY - timedelta(days=2)
    with pytest.raises(HTTPException):
        check_patch(_row(source, past), new_status=P, new_date=None, today=TODAY)
    # ...unless it moves to today or later in the same change.
    check_patch(_row(source, past), new_status=P, new_date=TODAY, today=TODAY)
    check_patch(_row(source, TODAY), new_status=P, new_date=None, today=TODAY)


def test_moving_a_pending_session_is_unconstrained():
    with assert_does_not_raise():
        check_patch(_row(P), new_status=None, new_date=TODAY - timedelta(days=3), today=TODAY)
        check_patch(_row(S), new_status=None, new_date=TODAY + timedelta(days=3), today=TODAY)


# ── fixtures ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def factory(async_db: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_db.bind, expire_on_commit=False, autoflush=False)


async def _athlete_with_session(
    factory, email: str, *, status: SessionStatus = P, day: date | None = None
) -> tuple[int, int]:
    day = day or date.today()
    async with factory() as db:
        user = User(email=email, hashed_password="h", is_active=True)
        db.add(user)
        await db.commit()
        block = MesocycleBlock(
            user_id=user.id, goal=BlockGoal.STRENGTH, duration_weeks=4, sessions_per_week=3,
            start_date=day, deload_every_n_weeks=4, status=BlockStatus.ACTIVE,
        )
        db.add(block)
        await db.commit()
        session = PlannedSession(
            block_id=block.id, user_id=user.id, scheduled_date=day, week_number=1,
            day_of_week=day.isoweekday(), category="Heavy Lower", modality="Strength",
            domain="strength", status=status,
        )
        db.add(session)
        await db.commit()
        return user.id, session.id


async def _session(factory, sid: int) -> PlannedSession:
    async with factory() as db:
        row = await db.get(PlannedSession, sid)
        assert row is not None
        return row


def _log_today(planned_session_id: int | None = None) -> WorkoutLog:
    at = datetime.combine(date.today(), time(9, 0), tzinfo=UTC)
    return WorkoutLog(
        timestamp=at, modality="Running", duration_minutes=30.0, session_rpe=6.0,
        planned_session_id=planned_session_id,
    )


async def _contend(contender, holder_body, factory, *, uid: int, sid: int, chain: bool) -> object:
    """Holder takes (chain lock, if `chain`, then) the row lock and keeps them; the contender
    must still be blocked; the holder's body runs and commits; the contender then finishes.
    Returns the contender's result or the exception it raised."""
    async with factory() as holder:
        if chain:
            await lock_athlete_chain(holder, uid)
        assert await lock_planned_session(holder, sid, uid) is not None
        task = asyncio.create_task(contender())
        await asyncio.sleep(0.5)
        assert not task.done(), "contender did not wait for the planned-session row lock"
        await holder_body(holder)
    try:
        return await asyncio.wait_for(task, timeout=10)
    except Exception as exc:  # noqa: BLE001 - returned for the test to assert on
        return exc


# ── route-visible 409s ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_rejections_leave_the_row_untouched(factory):
    uid, sid = await _athlete_with_session(factory, "f3-patch@test.com")
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await planning_service.update_session(
                db, uid, sid, PlannedSessionUpdateRequest(status=C)
            )
    assert exc.value.status_code == 409
    assert (await _session(factory, sid)).status == P


@pytest.mark.asyncio
async def test_an_explicit_log_to_a_completed_session_is_409_and_writes_nothing(factory):
    from sqlalchemy import func, select

    from app.models.workout_log import WorkoutLog as WorkoutLogORM

    uid, sid = await _athlete_with_session(factory, "f3-relink@test.com")
    async with factory() as db:
        await state_service.process_new_workout(db, uid, _log_today(sid))
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await state_service.process_new_workout(db, uid, _log_today(sid))
    assert exc.value.status_code == 409
    async with factory() as db:
        logs = (await db.execute(
            select(func.count()).select_from(WorkoutLogORM).where(WorkoutLogORM.user_id == uid)
        )).scalar_one()
    assert logs == 1  # the second workout was refused, not silently re-linked
    row = await _session(factory, sid)
    assert row.status == C


@pytest.mark.asyncio
async def test_a_late_explicit_log_completes_a_skipped_session(factory):
    uid, sid = await _athlete_with_session(factory, "f3-late@test.com", status=S)
    async with factory() as db:
        await state_service.process_new_workout(db, uid, _log_today(sid))
    row = await _session(factory, sid)
    assert row.status == C and row.workout_log_id is not None


# ── deterministic races ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_link_vs_link_the_second_explicit_log_is_refused(factory):
    uid, sid = await _athlete_with_session(factory, "f3-ll@test.com")

    async def contender():
        async with factory() as db:
            await state_service.process_new_workout(db, uid, _log_today(sid))

    async def holder_body(db):  # the first log, inside the holder's locks
        await state_service.process_new_workout(db, uid, _log_today(sid))

    result = await _contend(contender, holder_body, factory, uid=uid, sid=sid, chain=True)
    assert isinstance(result, HTTPException) and result.status_code == 409
    assert (await _session(factory, sid)).status == C


@pytest.mark.asyncio
async def test_link_vs_reschedule_a_moved_session_is_not_linked(factory):
    """The implicit same-day match waits for the PATCH; once the session has moved to
    tomorrow, Postgres re-checks the filter and the workout stays unlinked."""
    uid, sid = await _athlete_with_session(factory, "f3-lr@test.com")

    async def contender():
        async with factory() as db:
            await state_service.process_new_workout(db, uid, _log_today())

    async def holder_body(db):
        await planning_service.update_session(
            db, uid, sid,
            PlannedSessionUpdateRequest(scheduled_date=date.today() + timedelta(days=1)),
        )

    result = await _contend(contender, holder_body, factory, uid=uid, sid=sid, chain=False)
    assert not isinstance(result, Exception), result
    row = await _session(factory, sid)
    assert row.status == P and row.workout_log_id is None
    assert row.scheduled_date == date.today() + timedelta(days=1)


@pytest.mark.asyncio
async def test_issuance_vs_reschedule_a_moved_session_keeps_its_content(factory):
    """A prescription scored for today's row must not land on a row moved meanwhile."""
    uid, sid = await _athlete_with_session(factory, "f3-ir@test.com")
    async with factory() as db:
        target = await db.get(PlannedSession, sid)  # what scoring resolved, pre-move
    rx = WorkoutPrescription(type="Strength", focus="x", rationale="y", duration_min=45)

    async def contender():
        async with factory() as db:
            await prescription_service._persist_prescription(db, target, rx)

    async def holder_body(db):
        await planning_service.update_session(
            db, uid, sid,
            PlannedSessionUpdateRequest(scheduled_date=date.today() + timedelta(days=2)),
        )

    result = await _contend(contender, holder_body, factory, uid=uid, sid=sid, chain=False)
    assert not isinstance(result, Exception), result
    assert (await _session(factory, sid)).prescribed_content is None


@pytest.mark.asyncio
async def test_issuance_persists_when_nothing_changed(factory):
    """The control for the race above: an unchanged PENDING row does get the content."""
    uid, sid = await _athlete_with_session(factory, "f3-issue-ok@test.com")
    async with factory() as db:
        target = await db.get(PlannedSession, sid)
    rx = WorkoutPrescription(type="Strength", focus="x", rationale="y", duration_min=45)
    async with factory() as db:
        await prescription_service._persist_prescription(db, target, rx)
    content = (await _session(factory, sid)).prescribed_content
    assert content is not None and content["type"] == "Strength"


@pytest.mark.asyncio
async def test_feedback_vs_unskip_feedback_sees_the_current_status(factory):
    """Feedback validated against SKIPPED while a PATCH returns the session to PENDING
    would describe an outcome the session no longer has. Locked, it sees PENDING: 409."""
    from app.schemas.session_feedback import SessionFeedbackIn
    from app.services import session_feedback_service

    uid, sid = await _athlete_with_session(
        factory, "f3-fb@test.com", status=S, day=date.today() + timedelta(days=1)
    )

    async def contender():
        async with factory() as db:
            await session_feedback_service.create_feedback(
                db, uid, SessionFeedbackIn(planned_session_id=sid, status="skipped")
            )

    async def holder_body(db):
        await planning_service.update_session(db, uid, sid, PlannedSessionUpdateRequest(status=P))

    result = await _contend(contender, holder_body, factory, uid=uid, sid=sid, chain=False)
    assert isinstance(result, HTTPException) and result.status_code == 409
    assert (await _session(factory, sid)).status == P


# ── architecture: every planned-session field write goes through the locked row ───────
#
# What this guards, precisely: a function in app/ that assigns a PlannedSession lifecycle
# field (status, scheduled_date, prescribed_content, workout_log_id, completed_at,
# original_scheduled_date) on a variable named like a session (`*session*`, `locked`), or
# issues a bulk update(PlannedSession), must obtain the row through the protocol's locking
# loaders. Name-based by necessity (Python has no static types at assignment sites); the
# discovered set is pinned below so the scan cannot silently stop matching.


_APP = Path(__file__).resolve().parents[1] / "app"
_FIELDS = {
    "status", "scheduled_date", "prescribed_content", "workout_log_id",
    "completed_at", "original_scheduled_date",
}
_LOCKING_LOADERS = {"lock_planned_session", "_match_planned_session"}


def _session_writers() -> dict[str, ast.AST]:
    found: dict[str, ast.AST] = {}
    for path in _APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            writes = any(
                isinstance(n, ast.Assign)
                and any(
                    isinstance(t, ast.Attribute)
                    and t.attr in _FIELDS
                    and isinstance(t.value, ast.Name)
                    and ("session" in t.value.id or t.value.id == "locked")
                    for t in n.targets
                )
                for n in ast.walk(fn)
            ) or any(
                isinstance(c, ast.Call)
                and getattr(c.func, "id", getattr(c.func, "attr", "")) == "update"
                and any(ast.unparse(a).split(".")[-1] == "PlannedSession" for a in c.args)
                for c in ast.walk(fn)
            )
            if writes:
                found[f"{path.relative_to(_APP.parent).as_posix()}:{fn.name}"] = fn
    return found


def test_the_session_writer_scan_finds_the_known_writers():
    assert {k.rsplit(":", 1)[1] for k in _session_writers()} == {
        "update_session", "_persist_prescription", "process_new_workout",
    }


@pytest.mark.parametrize("name", sorted(_session_writers()))
def test_every_session_writer_loads_the_row_locked(name):
    fn = _session_writers()[name]
    calls = {
        getattr(c.func, "id", getattr(c.func, "attr", ""))
        for c in ast.walk(fn)
        if isinstance(c, ast.Call)
    }
    assert calls & _LOCKING_LOADERS, f"{name} writes a planned session it did not lock"


# ── review follow-ups ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["move", "skip", "complete"])
async def test_today_never_publishes_a_prescription_it_could_not_persist(factory, change):
    """Review repro: scoring raced a move / skip / completion; persistence correctly declined
    the write, but /planning/today still returned HTTP 200 with the prescription. It now
    refuses to publish it (409), stores nothing, and records no decision for it."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import func, select

    from app.core.auth import get_current_user
    from app.core.db import get_db
    from app.main import app
    from app.models.telemetry import PrescriptionDecision
    from app.models.user import AthleteProfile

    uid, sid = await _athlete_with_session(factory, f"f3-today-{change}@test.com")
    async with factory() as db:
        db.add(AthleteProfile(user_id=uid, equipment=["barbell"]))
        await db.commit()
        await state_service.initialize_athlete_state(db, uid)
        user = await db.get(User, uid)

    async def _db():
        async with factory() as db:
            yield db

    async def _user():
        return user

    async def contender():
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = _user
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                return await c.get("/v1/planning/today", params={"goal": "Strength"})
        finally:
            app.dependency_overrides.clear()

    async def holder_body(db):
        if change == "move":
            await planning_service.update_session(
                db, uid, sid,
                PlannedSessionUpdateRequest(scheduled_date=date.today() + timedelta(days=1)),
            )
        elif change == "skip":
            await planning_service.update_session(db, uid, sid, PlannedSessionUpdateRequest(status=S))
        else:
            await state_service.process_new_workout(db, uid, _log_today(sid))

    resp = await _contend(contender, holder_body, factory, uid=uid, sid=sid, chain=False)
    assert not isinstance(resp, Exception), resp
    assert resp.status_code == 409, resp.text
    assert (await _session(factory, sid)).prescribed_content is None
    async with factory() as db:
        decisions = (await db.execute(
            select(func.count()).select_from(PrescriptionDecision)
            .where(PrescriptionDecision.athlete_id == uid)
        )).scalar_one()
    assert decisions == 0


async def _skipped_with_feedback(factory, email: str) -> tuple[int, int]:
    from app.schemas.session_feedback import SessionFeedbackIn
    from app.services import session_feedback_service

    uid, sid = await _athlete_with_session(factory, email, status=S)
    async with factory() as db:
        await session_feedback_service.create_feedback(
            db, uid, SessionFeedbackIn(planned_session_id=sid, status="skipped")
        )
    return uid, sid


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [P, R], ids=lambda s: s.value)
async def test_a_status_change_cannot_strand_existing_feedback(factory, target):
    """Review repro: "skipped" feedback, then the session reopened or rescheduled — the
    feedback kept describing an outcome the session no longer had."""
    uid, sid = await _skipped_with_feedback(factory, f"f3-fb-{target.value}@test.com")
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await planning_service.update_session(
                db, uid, sid, PlannedSessionUpdateRequest(status=target)
            )
    assert exc.value.status_code == 409 and "feedback" in str(exc.value.detail)
    assert (await _session(factory, sid)).status == S


@pytest.mark.asyncio
async def test_a_late_log_cannot_strand_skipped_feedback(factory):
    uid, sid = await _skipped_with_feedback(factory, "f3-fb-late@test.com")
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await state_service.process_new_workout(db, uid, _log_today(sid))
    assert exc.value.status_code == 409 and "feedback" in str(exc.value.detail)
    assert (await _session(factory, sid)).status == S


@pytest.mark.asyncio
async def test_moving_a_session_with_feedback_keeps_its_outcome(factory):
    """A date move does not change the status the feedback describes: allowed."""
    uid, sid = await _skipped_with_feedback(factory, "f3-fb-move@test.com")
    new_day = date.today() + timedelta(days=3)
    async with factory() as db:
        await planning_service.update_session(
            db, uid, sid, PlannedSessionUpdateRequest(scheduled_date=new_day)
        )
    row = await _session(factory, sid)
    assert row.status == S and row.scheduled_date == new_day
