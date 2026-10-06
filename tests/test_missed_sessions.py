"""P2 — missed sessions and feedback supersession.

Pinned here:

* the ``as_of`` boundary: as of Oct 5, Oct 3 is missed and Oct 4 is still within grace;
* the flag: nothing is written while ``RECONCILE_MISSED_SESSIONS`` is off;
* the re-check under the row lock, on two real connections: a late log or a move that
  committed first wins;
* the outcome change and the feedback supersession commit together or not at all;
* the athlete overrules a miss: a late log (explicit or same-day), feedback, a reschedule;
* readers: only active feedback is joined (no fan-out), and missed is counted apart;
* the rollback script.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.db import get_db
from app.main import app
from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.models.telemetry import SessionFeedback
from app.models.user import User
from app.schemas.planning import PlannedSessionUpdateRequest, WeekReviewSession
from app.schemas.session_feedback import SessionFeedbackIn
from app.schemas.workouts import WorkoutLog
from app.scripts.revert_missed_sessions import revert_with_db
from app.services import planning_service, session_feedback_service, state_service
from app.services.missed_session_service import reconcile_missed
from app.services.planned_session_protocol import lock_planned_session
from app.services.state_chain_lock import lock_athlete_chain
from app.services.week_review_service import _counts, _sessions_in_window

P, C, S, M = (
    SessionStatus.PENDING,
    SessionStatus.COMPLETED,
    SessionStatus.SKIPPED,
    SessionStatus.MISSED,
)
OCT_5 = date(2026, 10, 5)


@pytest.fixture
def factory(async_db: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_db.bind, expire_on_commit=False, autoflush=False)


@pytest.fixture
def reconcile_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RECONCILE_MISSED_SESSIONS", True)


async def _athlete(factory, email: str) -> tuple[int, int]:
    async with factory() as db:
        user = User(email=email, hashed_password="h", is_active=True)
        db.add(user)
        await db.commit()
        block = MesocycleBlock(
            user_id=user.id, goal=BlockGoal.STRENGTH, duration_weeks=4, sessions_per_week=3,
            start_date=date.today() - timedelta(days=14), deload_every_n_weeks=4,
            status=BlockStatus.ACTIVE,
        )
        db.add(block)
        await db.commit()
        return user.id, block.id


async def _add_session(factory, uid: int, bid: int, day: date, status: SessionStatus = P) -> int:
    async with factory() as db:
        row = PlannedSession(
            block_id=bid, user_id=uid, scheduled_date=day, week_number=1,
            day_of_week=day.isoweekday(), category="Heavy Lower", modality="Strength",
            domain="strength", status=status,
        )
        db.add(row)
        await db.commit()
        return row.id


async def _add_feedback(factory, sid: int, status: str, describes: str) -> int:
    """A feedback row written directly — the shape a pre-F3 reopen could leave behind."""
    async with factory() as db:
        fb = SessionFeedback(planned_session_id=sid, status=status, describes_status=describes)
        db.add(fb)
        await db.commit()
        return fb.id


async def _status(factory, sid: int) -> SessionStatus:
    async with factory() as db:
        row = await db.get(PlannedSession, sid)
        assert row is not None
        return SessionStatus(row.status)


async def _feedback_rows(factory, sid: int) -> list[tuple[str, bool]]:
    async with factory() as db:
        rows = (await db.execute(
            select(SessionFeedback).where(SessionFeedback.planned_session_id == sid)
            .order_by(SessionFeedback.id)
        )).scalars().all()
    return [(r.status, r.superseded_at is not None) for r in rows]


def _log_on(day: date, planned_session_id: int | None = None) -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime.combine(day, time(9, 0), tzinfo=UTC), modality="Running",
        duration_minutes=30.0, session_rpe=6.0, planned_session_id=planned_session_id,
    )


# ── the as_of boundary and the flag ───────────────────────────────────────────────────


async def test_as_of_oct_5_marks_oct_3_missed_and_leaves_oct_4_in_grace(factory, reconcile_on):
    uid, bid = await _athlete(factory, "p2-boundary@test.com")
    days = {d: await _add_session(factory, uid, bid, date(2026, 10, d)) for d in (2, 3, 4, 5)}
    skipped = await _add_session(factory, uid, bid, date(2026, 10, 1), S)

    async with factory() as db:
        changed = await reconcile_missed(db, uid, OCT_5)

    assert changed == 2
    assert [await _status(factory, days[d]) for d in (2, 3, 4, 5)] == [M, M, P, P]
    assert await _status(factory, skipped) == S  # the athlete's own declaration is untouched

    # One explicit as_of per reconciliation: a day later, Oct 4 has left its grace.
    async with factory() as db:
        assert await reconcile_missed(db, uid, OCT_5 + timedelta(days=1)) == 1
    assert await _status(factory, days[4]) == M


async def test_nothing_is_written_while_the_flag_is_off(factory):
    assert settings.RECONCILE_MISSED_SESSIONS is False  # the shipped default
    uid, bid = await _athlete(factory, "p2-flag-off@test.com")
    sid = await _add_session(factory, uid, bid, OCT_5 - timedelta(days=10))
    async with factory() as db:
        assert await reconcile_missed(db, uid, OCT_5) == 0
    assert await _status(factory, sid) == P


async def test_reconciliation_is_scoped_to_one_athlete(factory, reconcile_on):
    uid, bid = await _athlete(factory, "p2-scope-a@test.com")
    other_uid, other_bid = await _athlete(factory, "p2-scope-b@test.com")
    mine = await _add_session(factory, uid, bid, OCT_5 - timedelta(days=3))
    theirs = await _add_session(factory, other_uid, other_bid, OCT_5 - timedelta(days=3))
    async with factory() as db:
        assert await reconcile_missed(db, uid, OCT_5) == 1
    assert (await _status(factory, mine), await _status(factory, theirs)) == (M, P)


# ── the re-check under the row lock (two real connections) ────────────────────────────


async def _contend(holder_body, factory, *, uid: int, sid: int, chain: bool) -> int:
    """A holder keeps the row lock (after the chain lock, as production orders them) while
    reconciliation starts; reconciliation must wait, then decide on the row the holder left."""
    async def contender() -> int:
        async with factory() as db:
            return await reconcile_missed(db, uid, date.today())

    async with factory() as holder:
        if chain:
            await lock_athlete_chain(holder, uid)
        assert await lock_planned_session(holder, sid, uid) is not None
        task = asyncio.create_task(contender())
        await asyncio.sleep(0.5)
        assert not task.done(), "reconciliation did not wait for the planned-session row lock"
        await holder_body(holder)
    return await asyncio.wait_for(task, timeout=10)


async def test_a_late_log_that_commits_first_wins(factory, reconcile_on):
    uid, bid = await _athlete(factory, "p2-race-log@test.com")
    past = date.today() - timedelta(days=3)
    sid = await _add_session(factory, uid, bid, past)

    async def late_log(db):
        await state_service.process_new_workout(db, uid, _log_on(date.today(), sid))

    assert await _contend(late_log, factory, uid=uid, sid=sid, chain=True) == 0
    assert await _status(factory, sid) == C


async def test_a_move_that_commits_first_wins(factory, reconcile_on):
    """The date is re-checked under the lock, not only the status: the session is still
    PENDING after the move, but no longer in the past."""
    uid, bid = await _athlete(factory, "p2-race-move@test.com")
    sid = await _add_session(factory, uid, bid, date.today() - timedelta(days=3))

    async def move_to_today(db):
        await planning_service.update_session(
            db, uid, sid, PlannedSessionUpdateRequest(scheduled_date=date.today())
        )

    assert await _contend(move_to_today, factory, uid=uid, sid=sid, chain=False) == 0
    async with factory() as db:
        row = await db.get(PlannedSession, sid)
        assert row is not None
        assert (row.status, row.scheduled_date) == (P, date.today())


# ── the outcome and its feedback change in one transaction ────────────────────────────


async def test_reconciliation_supersedes_feedback_in_the_same_commit(factory, reconcile_on):
    uid, bid = await _athlete(factory, "p2-same-tx@test.com")
    sid = await _add_session(factory, uid, bid, OCT_5 - timedelta(days=3))
    await _add_feedback(factory, sid, "skipped", "skipped")

    async with factory() as db:
        assert await reconcile_missed(db, uid, OCT_5) == 1
    assert await _status(factory, sid) == M
    assert await _feedback_rows(factory, sid) == [("skipped", True)]


async def test_a_failed_commit_leaves_both_the_outcome_and_the_feedback(factory, reconcile_on):
    uid, bid = await _athlete(factory, "p2-rollback@test.com")
    sid = await _add_session(factory, uid, bid, OCT_5 - timedelta(days=3))
    await _add_feedback(factory, sid, "skipped", "skipped")

    async with factory() as db:
        async def failing_commit() -> None:
            await db.flush()  # both writes reach the database before the failure
            raise RuntimeError("commit failed")

        db.commit = failing_commit  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="commit failed"):
            await reconcile_missed(db, uid, OCT_5)
        # Non-vacuous: inside the failed transaction both changes were really written...
        row = (await db.execute(
            select(PlannedSession.status).where(PlannedSession.id == sid)
        )).scalar_one()
        superseded = (await db.execute(
            select(SessionFeedback.superseded_at).where(SessionFeedback.planned_session_id == sid)
        )).scalar_one()
        assert (row, superseded is not None) == (M, True)
        await db.rollback()
    # ...and neither survived it.
    assert await _status(factory, sid) == P
    assert await _feedback_rows(factory, sid) == [("skipped", False)]


# ── the athlete overrules a miss ──────────────────────────────────────────────────────


async def _missed_with_feedback(factory, email: str) -> tuple[int, int, date]:
    uid, bid = await _athlete(factory, email)
    day = date.today() - timedelta(days=3)
    sid = await _add_session(factory, uid, bid, day)
    async with factory() as db:
        assert await reconcile_missed(db, uid, date.today()) == 1
    async with factory() as db:
        await session_feedback_service.create_feedback(
            db, uid, SessionFeedbackIn(planned_session_id=sid, status="skipped", skip_reason="ill")
        )
    return uid, sid, day


async def test_feedback_on_a_miss_says_skipped_or_unknown_never_completed(factory, reconcile_on):
    uid, sid, _ = await _missed_with_feedback(factory, "p2-fb@test.com")
    async with factory() as db:
        fb = (await db.execute(
            select(SessionFeedback).where(SessionFeedback.planned_session_id == sid)
        )).scalar_one()
        assert (fb.status, fb.describes_status) == ("skipped", "missed")

    uid2, bid2 = await _athlete(factory, "p2-fb-completed@test.com")
    sid2 = await _add_session(factory, uid2, bid2, date.today() - timedelta(days=3))
    async with factory() as db:
        await reconcile_missed(db, uid2, date.today())
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await session_feedback_service.create_feedback(
                db, uid2, SessionFeedbackIn(planned_session_id=sid2, status="completed")
            )
    assert exc.value.status_code == 409


async def test_a_late_explicit_log_completes_a_miss_and_supersedes_its_feedback(factory, reconcile_on):
    uid, sid, _ = await _missed_with_feedback(factory, "p2-late-explicit@test.com")
    async with factory() as db:
        await state_service.process_new_workout(db, uid, _log_on(date.today(), sid))
    assert await _status(factory, sid) == C
    assert await _feedback_rows(factory, sid) == [("skipped", True)]
    async with factory() as db:
        await session_feedback_service.create_feedback(
            db, uid, SessionFeedbackIn(planned_session_id=sid, status="completed")
        )
    assert await _feedback_rows(factory, sid) == [("skipped", True), ("completed", False)]


async def test_a_log_dated_on_a_missed_day_completes_it_by_the_same_day_match(factory, reconcile_on):
    uid, sid, day = await _missed_with_feedback(factory, "p2-late-implicit@test.com")
    async with factory() as db:
        await state_service.process_new_workout(db, uid, _log_on(day))
    assert await _status(factory, sid) == C
    assert await _feedback_rows(factory, sid) == [("skipped", True)]


async def test_rescheduling_a_miss_reopens_it_and_supersedes_its_feedback(factory, reconcile_on):
    uid, sid, _ = await _missed_with_feedback(factory, "p2-resched@test.com")
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:  # back to pending only on today or later
            await planning_service.update_session(db, uid, sid, PlannedSessionUpdateRequest(status=P))
    assert exc.value.status_code == 409
    async with factory() as db:
        await planning_service.update_session(
            db, uid, sid, PlannedSessionUpdateRequest(status=P, scheduled_date=date.today())
        )
    assert await _status(factory, sid) == P
    assert await _feedback_rows(factory, sid) == [("skipped", True)]


async def test_a_session_cannot_be_patched_to_missed(factory):
    uid, bid = await _athlete(factory, "p2-patch-missed@test.com")
    sid = await _add_session(factory, uid, bid, date.today() - timedelta(days=3))
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await planning_service.update_session(db, uid, sid, PlannedSessionUpdateRequest(status=M))
    assert exc.value.status_code == 409 and "reconciliation" in str(exc.value.detail)
    assert await _status(factory, sid) == P


# ── readers ───────────────────────────────────────────────────────────────────────────


async def test_superseded_feedback_is_invisible_to_every_reader(factory):
    """A completed session with a superseded "modified" report and an active plain one: the
    joins must take one row, and only the active one."""
    uid, bid = await _athlete(factory, "p2-readers@test.com")
    day = date.today() - timedelta(days=1)
    sid = await _add_session(factory, uid, bid, day, C)
    old = await _add_feedback(factory, sid, "modified", "skipped")
    async with factory() as db:
        fb = await db.get(SessionFeedback, old)
        assert fb is not None
        fb.superseded_at = datetime.now(UTC).replace(tzinfo=None)
        await db.commit()
    await _add_feedback(factory, sid, "completed", "completed")

    async with factory() as db:
        signals = await planning_service.block_adherence_signals(db, uid, bid)
        rows = await _sessions_in_window(db, uid, bid, day, day)
        listed = await session_feedback_service.list_feedback(db, uid)
    assert signals == {"recent_skips": 0, "recent_modifications": 0}
    assert [r.feedback.status if r.feedback else None for r in rows] == ["completed"]
    assert [f.status for f in listed] == ["completed"]


def _review_session(status: SessionStatus, day: date) -> WeekReviewSession:
    return WeekReviewSession(
        planned_session_id=1, scheduled_date=day, original_scheduled_date=None, week_number=1,
        category="Heavy Lower", modality="Strength", status=status, is_deload=False,
        is_benchmark=False, workout_log_id=None, felt_rpe=None, prescribed_rpe=None,
        feedback_status=None, followed_as_prescribed=None, modified=False, modified_volume=None,
        modified_intensity=None, modified_exercises=None, modification_reason=None,
    )


def test_week_review_counts_missed_apart_from_skipped_and_pending():
    today = OCT_5
    counts = _counts(
        [
            _review_session(M, today - timedelta(days=3)),
            _review_session(S, today - timedelta(days=2)),
            _review_session(P, today - timedelta(days=1)),
            _review_session(C, today),
        ],
        today,
    )
    assert (counts.missed, counts.skipped, counts.pending, counts.completed) == (1, 1, 1, 1)
    assert (counts.due, counts.adherence_pct) == (4, 25.0)


async def _client(db: AsyncSession, user: User) -> AsyncIterator[AsyncClient]:
    async def _override_db():
        yield db

    async def _override_user() -> User:
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("flag", [False, True])
async def test_the_session_list_reads_missed_only_with_the_flag_on(
    async_db, factory, monkeypatch, flag
):
    monkeypatch.setattr(settings, "RECONCILE_MISSED_SESSIONS", flag)
    uid, bid = await _athlete(factory, f"p2-route-{flag}@test.com")
    past = await _add_session(factory, uid, bid, date.today() - timedelta(days=3))
    yesterday = await _add_session(factory, uid, bid, date.today() - timedelta(days=1))
    user = await async_db.get(User, uid)
    assert user is not None
    async for client in _client(async_db, user):
        resp = await client.get("/v1/planning/sessions")
    assert resp.status_code == 200
    by_id = {s["id"]: s["status"] for s in resp.json()}
    assert by_id == {past: "missed" if flag else "pending", yesterday: "pending"}


@pytest.mark.parametrize("builder", ["deload_risk", "experiment", "scoring_weight"])
async def test_offline_feature_builders_join_only_active_feedback(factory, builder):
    """The offline datasets join session_feedback by planned session; with superseded
    history that join would fan a decision out into one row per feedback row."""
    import importlib

    from sqlalchemy import text

    module = importlib.import_module(f"app.analysis.feature_builders.{builder}_features")
    uid, bid = await _athlete(factory, f"p2-builder-{builder}@test.com")
    sid = await _add_session(factory, uid, bid, date.today() - timedelta(days=1), C)
    old = await _add_feedback(factory, sid, "skipped", "skipped")
    async with factory() as db:
        await db.execute(
            text("UPDATE session_feedback SET superseded_at = now() WHERE id = :i"), {"i": old}
        )
        decision_id = (await db.execute(
            text(
                "INSERT INTO prescription_decisions (athlete_id, planned_session_id, goal, created_at) "
                "VALUES (:u, :s, 'Strength', now()) RETURNING id"
            ),
            {"u": uid, "s": sid},
        )).scalar_one()
        await db.execute(
            text(
                "INSERT INTO candidate_decision_logs (prescription_decision_id, candidate_type, branch_id) "
                "VALUES (:d, 'session', 'b1')"
            ),
            {"d": decision_id},
        )
        await db.execute(
            text(
                "INSERT INTO experiment_assignments (user_id, experiment_name, arm, assigned_at) "
                "VALUES (:u, 'exp', 'a', now() - interval '1 day')"
            ),
            {"u": uid},
        )
        await db.commit()
    await _add_feedback(factory, sid, "completed", "completed")

    async with factory() as db:
        rows = await module.build_dataset(db)
    mine = [r for r in rows if r.get("status") is not None]
    assert [r["status"] for r in mine] == ["completed"]


# ── the rollback script ───────────────────────────────────────────────────────────────


async def test_revert_dry_runs_refuses_while_on_then_reverts(factory, monkeypatch):
    monkeypatch.setattr(settings, "RECONCILE_MISSED_SESSIONS", True)
    uid, sid, _ = await _missed_with_feedback(factory, "p2-revert@test.com")

    async with factory() as db:
        dry = await revert_with_db(db, apply=False)
    assert (dry.sessions >= 1, dry.applied) == (True, False)
    assert await _status(factory, sid) == M

    async with factory() as db:
        refused = await revert_with_db(db, apply=True)
    assert refused.refused is not None and refused.exit_code == 1
    assert await _status(factory, sid) == M

    monkeypatch.setattr(settings, "RECONCILE_MISSED_SESSIONS", False)
    async with factory() as db:
        done = await revert_with_db(db, apply=True)
    assert done.applied and done.feedback_superseded >= 1
    assert await _status(factory, sid) == P
    assert await _feedback_rows(factory, sid) == [("skipped", True)]
