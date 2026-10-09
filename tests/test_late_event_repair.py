"""P3c: fold the late events that were recorded but never applied.

``app.services.late_event_repair_service`` finds record-only events and folds each through
``fold_late_events`` (same proof, same policy, same atomic apply). Pinned here:

* a dry run does every fold and keeps none of it: the database afterwards is exactly as before;
* ``--apply`` folds them oldest first and the head equals chronological logging (the oracle),
  also with a tie between same-kind events (arrival order = id order);
* a second run finds nothing; a refusal is recorded (apply) or not (dry run);
* what cannot be repaired is counted and left alone: events written before capture, events whose
  arrival order against another event cannot be recovered;
* athletes are independent, and ``user_id`` limits the run.
"""

from __future__ import annotations

from typing import Any

import pytest
from replay_support import (
    Ev,
    W,
    arrive_event,
    head_row,
    is_record_only,
    oracle_head,
    seed_definitions,
    seeded_athlete,
)
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.logic.tail_replay import row_columns
from app.models.athlete_state import AthleteState
from app.models.benchmark_observation import BenchmarkObservation
from app.models.state_correction import StateCorrection
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.scripts.fold_late_events import _print
from app.services.late_event_repair_service import (
    AthleteRepair,
    RepairReport,
    repair_all,
    users_with_pending_events,
)
from app.services.tail_replay_service import NewEventRef


@pytest.fixture
def factory(async_db: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_db.bind, expire_on_commit=False, autoflush=False)


def B(hours: float, raw: float = 60.0) -> Ev:
    return Ev("benchmark", hours, raw=raw)


async def _head_cols(db: AsyncSession, uid: int) -> dict[str, Any]:
    db.expire_all()
    return row_columns(await head_row(db, uid))


async def _snapshot(db: AsyncSession) -> dict[str, Any]:
    """Everything a repair could touch."""
    db.expire_all()
    out: dict[str, Any] = {}
    for name, model in (("states", AthleteState), ("receipts", StateCorrection)):
        out[name] = (await db.execute(select(func.count()).select_from(model))).scalar_one()
    out["workouts"] = [
        (r.id, r.state_disposition, r.state_disposition_reason, r.replay_refusal)
        for r in (await db.execute(select(WorkoutLogORM).order_by(WorkoutLogORM.id))).scalars()
    ]
    out["observations"] = [
        (r.id, r.state_disposition, r.state_disposition_reason, r.replay_refusal)
        for r in (await db.execute(select(BenchmarkObservation).order_by(BenchmarkObservation.id))).scalars()
    ]
    return out


async def _athlete_with_pending(db: AsyncSession, tag: str, tail: list[Ev], late: list[Ev], **kw: Any):
    uid = await seeded_athlete(db, f"{tag}@test.com", **kw)
    for ev in tail:
        await arrive_event(db, uid, ev)
    refs = [await arrive_event(db, uid, ev) for ev in late]  # flag off: all record-only
    for ref in refs:
        assert await is_record_only(db, ref)
    return uid, refs


async def test_a_dry_run_does_every_fold_and_keeps_none_of_it(async_db, factory):
    await seed_definitions(async_db)
    uid, _ = await _athlete_with_pending(async_db, "dry", [W(5), W(20)], [W(8), B(12), W(10)])
    before = await _snapshot(async_db)
    head_before = await _head_cols(async_db, uid)

    report = await repair_all(factory, apply=False)

    (a,) = report.athletes
    assert (a.user_id, a.pending, a.folded, a.refused) == (uid, 3, 3, {})  # what --apply would do…
    assert not report.applied
    assert await _snapshot(async_db) == before  # …and nothing kept
    assert await _head_cols(async_db, uid) == head_before


async def test_apply_folds_oldest_first_and_the_head_equals_chronological_logging(async_db, factory):
    await seed_definitions(async_db)
    late = [W(8, dominant_movement_pattern="squat"), B(12), W(10, dominant_movement_pattern="hinge", sleep_quality=4.0)]
    oracle = await oracle_head(async_db, "ap-oracle@test.com", sorted([W(5), W(20), *late], key=lambda e: e.hours))
    uid, _ = await _athlete_with_pending(async_db, "ap", [W(5), W(20)], late)

    report = await repair_all(factory, apply=True)

    (a,) = report.athletes
    assert (a.pending, a.folded, a.refused) == (3, 3, {})
    assert await _head_cols(async_db, uid) == oracle
    assert (await _snapshot(async_db))["receipts"] == 3  # one correction per event
    assert await users_with_pending_events(async_db) == []


async def test_a_second_run_finds_nothing(async_db, factory):
    await seed_definitions(async_db)
    await _athlete_with_pending(async_db, "again", [W(5), W(20)], [W(8)])
    await repair_all(factory, apply=True)
    after = await _snapshot(async_db)

    report = await repair_all(factory, apply=True)

    assert report.athletes == [] and await _snapshot(async_db) == after


async def test_events_at_one_timestamp_of_the_same_kind_fold_in_arrival_order(async_db, factory):
    await seed_definitions(async_db)
    first, second = W(8, dominant_movement_pattern="squat"), W(8, dominant_movement_pattern="hinge")
    oracle = await oracle_head(async_db, "tie-oracle@test.com", [W(5), first, second, W(20)])
    uid, _ = await _athlete_with_pending(async_db, "tie", [W(5), W(20)], [first, second])

    report = await repair_all(factory, apply=True)

    assert report.athletes[0].folded == 2 and report.athletes[0].ambiguous_tie == 0
    assert await _head_cols(async_db, uid) == oracle


async def test_events_of_different_kinds_at_one_timestamp_are_left_alone(async_db, factory):
    await seed_definitions(async_db)
    uid, refs = await _athlete_with_pending(async_db, "xtie", [W(5), W(20)], [W(8), B(8)])
    before = await _head_cols(async_db, uid)

    report = await repair_all(factory, apply=True)

    a = report.athletes[0]
    assert (a.folded, a.ambiguous_tie) == (0, 2)
    assert all([await is_record_only(async_db, ref) for ref in refs])
    assert await _head_cols(async_db, uid) == before


async def test_a_tie_with_a_later_arrival_already_in_the_state_is_left_alone(async_db, factory):
    """The first W(8) arrived before the second; if the second is already in the state, folding
    the first now would place it after the second, against arrival order."""
    await seed_definitions(async_db)
    uid, refs = await _athlete_with_pending(async_db, "later", [W(5), W(20)], [W(8), W(8)])
    await async_db.execute(
        update(WorkoutLogORM).where(WorkoutLogORM.id == refs[1].event_id).values(state_disposition="applied")
    )
    await async_db.commit()

    report = await repair_all(factory, apply=False)

    assert report.athletes[0].ambiguous_tie == 1 and report.athletes[0].folded == 0


async def test_a_refusal_is_recorded_on_apply_and_not_on_a_dry_run(async_db, factory):
    await seed_definitions(async_db)
    uid, refs = await _athlete_with_pending(
        async_db, "refuse", [W(10), W(20)], [W(5)], captured=False  # history from before capture
    )
    before = await _snapshot(async_db)

    dry = await repair_all(factory, apply=False)
    assert dry.athletes[0].refused == {"untrusted_checkpoint": 1}
    assert await _snapshot(async_db) == before  # a dry run records nothing, not even the refusal

    wet = await repair_all(factory, apply=True)

    assert wet.athletes[0].refused == {"untrusted_checkpoint": 1} and wet.athletes[0].folded == 0
    row = await async_db.get(WorkoutLogORM, refs[0].event_id)
    assert row is not None
    await async_db.refresh(row)
    assert (row.state_disposition, row.replay_refusal) == ("record_only", "untrusted_checkpoint")


async def test_events_written_before_capture_are_counted_and_untouched(async_db, factory):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "old@test.com")
    for ev in (W(5), W(20)):
        await arrive_event(async_db, uid, ev)
    async_db.add(WorkoutLogORM(
        user_id=uid, session_timestamp=W(8).ts, modality="Running", duration_minutes=30.0,
        session_rpe=6.0, state_disposition="record_only",
        state_disposition_reason="event_before_current_state", dose_snapshot={},
    ))
    await async_db.commit()
    before = await _snapshot(async_db)

    report = await repair_all(factory, apply=True)

    a = report.athletes[0]
    assert (a.not_capturable, a.pending, a.folded) == (1, 0, 0)
    assert await _snapshot(async_db) == before


async def test_athletes_are_independent_and_user_id_limits_the_run(async_db, factory):
    await seed_definitions(async_db)
    good, _ = await _athlete_with_pending(async_db, "ind-good", [W(5), W(20)], [W(8)])
    bad, bad_refs = await _athlete_with_pending(async_db, "ind-bad", [W(10), W(20)], [W(5)], captured=False)
    only = await repair_all(factory, apply=True, user_id=bad)
    assert [(a.user_id, a.folded) for a in only.athletes] == [(bad, 0)]
    assert await is_record_only(async_db, NewEventRef("workout", (await _pending_id(async_db, good))))

    report = await repair_all(factory, apply=True)

    by_user = {a.user_id: a for a in report.athletes}
    assert by_user[good].folded == 1  # the other athlete's refusal did not matter
    assert bad_refs and by_user[bad].folded == 0


async def _pending_id(db: AsyncSession, uid: int) -> int:
    return (await db.execute(
        select(WorkoutLogORM.id).where(WorkoutLogORM.user_id == uid, WorkoutLogORM.state_disposition == "record_only")
    )).scalar_one()


async def test_only_athletes_with_waiting_events_are_listed(async_db):
    await seed_definitions(async_db)
    waiting, _ = await _athlete_with_pending(async_db, "list-w", [W(5), W(20)], [W(8)])
    quiet = await seeded_athlete(async_db, "list-q@test.com")
    await arrive_event(async_db, quiet, W(5))

    assert await users_with_pending_events(async_db) == [waiting]


def test_the_report_reads_plainly(capsys):
    _print(RepairReport(applied=False, athletes=[
        AthleteRepair(user_id=7, pending=3, folded=2, refused={"window_exceeded": 1}, not_capturable=4, ambiguous_tie=0),
    ]))
    out = capsys.readouterr().out
    assert "user 7: considered 3, folded 2, refused [window_exceeded=1], not capturable 4" in out
    assert "Would fold 2 event(s)" in out and "Nothing was written" in out
    _print(RepairReport(applied=True, athletes=[]))
    assert "Nothing to do" in capsys.readouterr().out


async def test_a_dry_run_on_a_callers_session_leaves_nothing_even_if_the_caller_commits(async_db):
    """The dry run's promise is its own, not the session closing: a caller that reuses the
    session and commits afterwards must not persist the folds."""
    from app.services.late_event_repair_service import repair_athlete

    await seed_definitions(async_db)
    uid, _ = await _athlete_with_pending(async_db, "callers", [W(5), W(20)], [W(8)])
    before = await _snapshot(async_db)

    result = await repair_athlete(async_db, uid, apply=False)
    await async_db.commit()

    assert result.folded == 1
    assert await _snapshot(async_db) == before
