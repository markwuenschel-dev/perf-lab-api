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
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.logic.tail_replay import row_columns
from app.models.athlete_state import AthleteState
from app.models.benchmark_observation import BenchmarkObservation
from app.models.state_correction import StateCorrection
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.scripts.fold_late_events import _print
from app.services import late_event_repair_service as repair
from app.services.late_event_repair_service import (
    AthleteRepair,
    RepairReport,
    repair_all,
    users_with_pending_events,
)
from app.services.late_event_service import fold_late_events
from app.services.state_chain_lock import STATE_CHAIN_LOCK_NAMESPACE
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
    assert (a.user_id, a.considered, a.folded, a.refused) == (uid, 3, 3, {})  # what --apply would do…
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
    assert (a.considered, a.folded, a.refused) == (3, 3, {})
    assert await _head_cols(async_db, uid) == oracle
    assert (await _snapshot(async_db))["receipts"] == 3  # one correction per event
    assert await users_with_pending_events(async_db) == []


async def test_a_second_run_finds_nothing_left_to_fold(async_db, factory):
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
    assert (a.not_capturable, a.considered, a.folded) == (1, 1, 0)
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
        AthleteRepair(user_id=7, considered=3, folded=2, refused={"window_exceeded": 1}, not_capturable=4, ambiguous_tie=0, gone=1),
    ]))
    out = capsys.readouterr().out
    assert "user 7: considered 3, folded 2, refused [window_exceeded=1], not capturable 4, ambiguous tie 0, gone 1" in out
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


# ----- discovery is a hint: eligibility is decided under the lock, from fresh data ---------------- #

async def _live_write(factory, uid: int, ev: Ev) -> None:
    """A live writer, on its own connection, with the fold-on-arrival flag on."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "APPLY_LATE_EVENTS", True)
        async with factory() as other:
            await arrive_event(other, uid, ev)


async def test_a_tied_event_a_writer_folds_after_discovery_is_not_overtaken(async_db, factory, monkeypatch):
    """Review repro: discovery saw a lone W(8); before its turn a live writer folded a later W(8).
    Folding the older one now would place it after the newer, against arrival order."""
    await seed_definitions(async_db)
    uid, refs = await _athlete_with_pending(async_db, "race-disc", [W(5), W(20)], [W(8, dominant_movement_pattern="squat")])
    newer = W(8, dominant_movement_pattern="hinge")
    oracle = await oracle_head(async_db, "race-disc-oracle@test.com", [W(5), newer, W(20)])  # the applied events
    real_discover = repair._discover

    async def discover_then_a_writer_arrives(db, user_id):
        found = await real_discover(db, user_id)
        await _live_write(factory, user_id, newer)
        return found

    monkeypatch.setattr(repair, "_discover", discover_then_a_writer_arrives)

    report = await repair_all(factory, apply=True)

    a = report.athletes[0]
    assert (a.considered, a.folded, a.ambiguous_tie) == (1, 0, 1)
    assert await is_record_only(async_db, refs[0])  # left as it was
    assert await _head_cols(async_db, uid) == oracle


async def test_a_tied_event_a_writer_folds_between_two_repairs_is_not_overtaken(async_db, factory, monkeypatch):
    """The second gap: the first fold commits, a writer gets in before the second candidate's
    turn. Re-checking only at discovery would miss it."""
    await seed_definitions(async_db)
    uid, refs = await _athlete_with_pending(
        async_db, "race-commit", [W(5), W(20)], [W(8, dominant_movement_pattern="squat"), W(12)]
    )
    newer = W(12, sleep_quality=3.0)
    oracle = await oracle_head(
        async_db, "race-commit-oracle@test.com",
        [W(5), W(8, dominant_movement_pattern="squat"), newer, W(20)],  # the second candidate stays out
    )
    real_lock = repair.lock_athlete_chain
    calls = {"n": 0}

    async def lock_after_a_writer_got_in(db, user_id):
        calls["n"] += 1
        if calls["n"] == 2:  # the first candidate has been folded and committed
            await _live_write(factory, user_id, newer)
        await real_lock(db, user_id)

    monkeypatch.setattr(repair, "lock_athlete_chain", lock_after_a_writer_got_in)

    report = await repair_all(factory, apply=True)

    a = report.athletes[0]
    assert (a.considered, a.folded, a.ambiguous_tie) == (2, 1, 1)
    assert [await is_record_only(async_db, ref) for ref in refs] == [False, True]
    assert await _head_cols(async_db, uid) == oracle


async def test_a_candidate_a_writer_already_folded_is_counted_as_gone(async_db, factory, monkeypatch):
    await seed_definitions(async_db)
    uid, refs = await _athlete_with_pending(async_db, "gone", [W(5), W(20)], [W(8)])
    real_discover = repair._discover
    held: list[object] = []  # the identity map only weakly references rows

    async def discover_then_it_is_folded(db, user_id):
        found = await real_discover(db, user_id)
        held.append(await db.get(WorkoutLogORM, refs[0].event_id))  # held strongly: the session has the row as it was
        async with factory() as other:
            outcome = await fold_late_events(other, user_id, [refs[0]])
            await other.commit()
            assert outcome.applied
        return found

    monkeypatch.setattr(repair, "_discover", discover_then_it_is_folded)

    report = await repair_all(factory, apply=True)

    a = report.athletes[0]
    assert (a.considered, a.folded, a.gone) == (1, 0, 1)


async def _lock_is_held(factory, uid: int) -> bool:
    async with factory() as probe:
        got = (await probe.execute(
            text("SELECT pg_try_advisory_xact_lock(:ns, :uid)"),
            {"ns": STATE_CHAIN_LOCK_NAMESPACE, "uid": uid},
        )).scalar_one()
        await probe.rollback()
    return not got


@pytest.mark.parametrize("apply", [False, True])
async def test_the_lock_is_held_between_a_dry_runs_events_and_released_between_an_applys(
    async_db, factory, monkeypatch, apply
):
    """What the runbook says: a dry run holds the athlete's lock from its first event until its
    rollback; an apply run lets go after each fold."""
    await seed_definitions(async_db)
    uid, _ = await _athlete_with_pending(async_db, f"lock-{apply}", [W(5), W(20)], [W(8), W(12)])
    real_lock = repair.lock_athlete_chain
    held_before_each: list[bool] = []

    async def observing_lock(db, user_id):
        held_before_each.append(await _lock_is_held(factory, user_id))
        await real_lock(db, user_id)

    monkeypatch.setattr(repair, "lock_athlete_chain", observing_lock)

    await repair_all(factory, apply=apply)

    assert held_before_each == [False, not apply]  # the second candidate: held iff a dry run
    assert not await _lock_is_held(factory, uid)  # and released at the end either way
