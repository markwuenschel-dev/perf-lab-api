"""P3b-3: a late event is folded into the state atomically, behind ``APPLY_LATE_EVENTS``.

Pinned here, through the real writers (``process_new_workout`` / ``create_observation``):

* the flag is off by default and, off, nothing changes (the event stays record-only);
* on, a late workout or benchmark is folded in: the head equals what the same events produce
  logged in true chronological order (the oracle), the receipt, the introduced event, and the
  correction head are written together with the log, and the disposition says ``applied``;
* every way the fold can refuse leaves the event record-only with the code that said why
  (``replay_refusal``) and writes no receipt or head: the 48 h window, the tail size, rows
  written before capture, a strength-axis benchmark, a batch beyond the declared size;
* **atomicity**: a failure after the receipt has been written rolls all of it back, keeps the
  workout, and a retry succeeds;
* sequences: a second late event after a correction and an on-time event; a timestamp tie;
  a batch folded together in every arrival order; two athletes' writers racing on two real
  connections;
* a fold repeats none of the live writers' side effects (evidence, decline candidates, weak
  points, shadow telemetry);
* **real safety decisions**: the production hard constraints, built from the corrected head by
  the production context builder, decide exactly as they do on the chronological head, and a
  late heavy session flips a decision the uncorrected head would have passed.
"""

from __future__ import annotations

import asyncio
import itertools
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from replay_support import (
    BASE,
    B,
    Ev,
    W,
    arrive_event,
    head_row,
    is_record_only,
    oracle_head,
    seed_definitions,
    seeded_athlete,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.engine.state_bridge import unified_from_athlete_row
from app.logic.constraint_engine.constraints_impl import (
    CONSTRAINT_REGISTRY,
    UNIVERSAL_HARD_CONSTRAINTS,
)
from app.logic.constraint_engine.context_builder import build_constraint_context
from app.logic.tail_replay import row_columns
from app.models.athlete_state import AthleteState
from app.models.benchmark_observation import BenchmarkObservation
from app.models.dose_model_shadow import DoseModelShadowLog
from app.models.dose_routing_shadow import DoseRoutingShadowLog
from app.models.ekf_shadow import EkfShadowLog
from app.models.state_correction import StateCorrection, StateCorrectionEvent
from app.models.strength_decline_candidate import StrengthDeclineCandidate
from app.models.weak_point import WeakPoint
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.schemas.workouts import WorkoutLog
from app.services import benchmark_service, late_event_service, state_service
from app.services.late_event_service import (
    MAX_BATCH,
    MAX_REPLAY_EVENTS,
    MAX_REPLAY_GAP,
    fold_late_events,
)
from app.services.tail_replay_service import NewEventRef


@pytest.fixture
def flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "APPLY_LATE_EVENTS", True)


async def _log(db: AsyncSession, uid: int, ev: Ev):
    """Log a workout and return the writer's response."""
    return await state_service.process_new_workout(
        db, uid, WorkoutLog(timestamp=ev.ts.replace(tzinfo=UTC), **(ev.extra or {})),
        received_at=datetime.now(UTC),
    )


async def _count(db: AsyncSession, model: Any, uid: int | None = None) -> int:
    q = select(func.count()).select_from(model)
    if uid is not None and hasattr(model, "user_id"):
        q = q.where(model.user_id == uid)
    return (await db.execute(q)).scalar_one()


async def _workout(db: AsyncSession, wid: int) -> WorkoutLogORM:
    row = await db.get(WorkoutLogORM, wid)
    assert row is not None
    await db.refresh(row)
    return row


async def _head_cols(db: AsyncSession, uid: int) -> dict[str, Any]:
    return row_columns(await head_row(db, uid))


async def _all(db: AsyncSession, uid: int, arrivals: list[Ev], tag: str):
    """(subject head columns, oracle head columns) for the same events."""
    oracle = await oracle_head(db, f"{tag}-oracle@test.com", arrivals)
    uid2 = await seeded_athlete(db, f"{tag}-subject@test.com")
    for ev in arrivals:
        await arrive_event(db, uid2, ev)
    return uid2, await _head_cols(db, uid2), oracle


def test_the_flag_is_off_by_default():
    assert settings.APPLY_LATE_EVENTS is False


def test_the_declared_policy_is_the_one_the_tests_cover():
    """The sizes below are what the oracle tests exercise (up to 8 events, batches of 4, a 48 h
    window). Raising a limit without extending that coverage is the thing this pins."""
    assert (MAX_REPLAY_GAP, MAX_REPLAY_EVENTS, MAX_BATCH) == (timedelta(hours=48), 8, 4)


async def test_off_by_default_a_late_workout_stays_record_only_and_writes_no_receipt(async_db):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "off@test.com")
    for ev in (W(5), W(20)):
        await arrive_event(async_db, uid, ev)
    before = await _head_cols(async_db, uid)

    resp = await _log(async_db, uid, W(10))

    assert resp.state_disposition == "record_only" and resp.state_disposition_reason == "event_before_current_state"
    assert await _count(async_db, StateCorrection, uid) == 0
    assert await _head_cols(async_db, uid) == before
    assert (await _workout(async_db, resp.workout_log_id)).replay_refusal is None  # never attempted


async def test_a_late_workout_is_folded_in_and_the_head_matches_chronological_logging(async_db, flag):
    await seed_definitions(async_db)
    arrivals = [W(5, dominant_movement_pattern="squat"), W(20), W(10, dominant_movement_pattern="hinge", sleep_quality=4.0)]
    oracle = await oracle_head(async_db, "fold-oracle@test.com", arrivals)
    uid = await seeded_athlete(async_db, "fold@test.com")
    for ev in arrivals[:2]:
        await arrive_event(async_db, uid, ev)
    old_head = await head_row(async_db, uid)
    old_head_id, old_head_ts = old_head.id, old_head.timestamp
    rows_before = await _count(async_db, AthleteState, uid)

    resp = await _log(async_db, uid, arrivals[2])

    assert (resp.state_disposition, resp.state_disposition_reason) == ("applied", None)
    head = await head_row(async_db, uid)
    assert row_columns(head) == oracle
    assert (head.event_kind, head.predecessor_state_id, head.timestamp) == ("correction", old_head_id, old_head_ts)
    assert await _count(async_db, AthleteState, uid) == rows_before + 1  # one new row; nothing deleted
    receipt = (await async_db.execute(select(StateCorrection).where(StateCorrection.user_id == uid))).scalar_one()
    assert head.source_correction_id == receipt.id and receipt.head_before_state_id == old_head_id
    member = (await async_db.execute(select(StateCorrectionEvent))).scalar_one()
    assert member.workout_log_id == resp.workout_log_id
    w = await _workout(async_db, resp.workout_log_id)
    assert (w.state_disposition, w.state_disposition_reason, w.replay_refusal) == ("applied", None, None)
    # The response reports the corrected state, not the stale head.
    assert resp.model_dump()["capacity_x"] == unified_from_athlete_row(head).capacity_x.model_dump()


async def test_a_late_benchmark_is_folded_in_the_same_way(async_db, flag):
    await seed_definitions(async_db)
    arrivals = [W(4), W(12), W(20), B(8, raw=70.0)]
    oracle = await oracle_head(async_db, "fold-b-oracle@test.com", arrivals)
    uid = await seeded_athlete(async_db, "fold-b@test.com")
    for ev in arrivals:
        await arrive_event(async_db, uid, ev)

    assert await _head_cols(async_db, uid) == oracle
    obs = (await async_db.execute(select(BenchmarkObservation).where(BenchmarkObservation.user_id == uid))).scalar_one()
    await async_db.refresh(obs)
    assert (obs.state_disposition, obs.state_disposition_reason, obs.replay_refusal) == ("applied", None, None)
    assert await _count(async_db, StateCorrection, uid) == 1


@pytest.mark.parametrize("second_late", [3, 7, 11, 16])
async def test_a_second_late_event_after_a_correction_and_an_on_time_event(async_db, flag, second_late):
    await seed_definitions(async_db)
    arrivals = [W(4), W(16), W(7, dominant_movement_pattern="hinge"), W(24), W(second_late, sleep_quality=4.0)]
    oracle = await oracle_head(async_db, f"seq-oracle-{second_late}@test.com", arrivals)
    uid = await seeded_athlete(async_db, f"seq-{second_late}@test.com")
    for ev in arrivals:
        await arrive_event(async_db, uid, ev)

    assert await _head_cols(async_db, uid) == oracle
    assert await _count(async_db, StateCorrection, uid) == 2


async def test_a_late_event_sharing_an_existing_timestamp_is_folded_after_it(async_db, flag):
    await seed_definitions(async_db)
    arrivals = [W(5, dominant_movement_pattern="squat"), W(15), W(5, dominant_movement_pattern="hinge")]
    oracle = await oracle_head(async_db, "tie-oracle@test.com", arrivals)
    uid = await seeded_athlete(async_db, "tie@test.com")
    for ev in arrivals:
        await arrive_event(async_db, uid, ev)

    assert await _head_cols(async_db, uid) == oracle


# ----- batches folded together, every arrival order -------------------------------------------- #

@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
async def test_a_batch_folded_together_in_every_arrival_order(async_db, monkeypatch, order):
    await seed_definitions(async_db)
    late = [W(6, dominant_movement_pattern="squat"), B(9, raw=65.0), W(13, sleep_quality=3.0)]
    tag = "batch" + "".join(map(str, order))
    oracle = await oracle_head(async_db, f"{tag}-oracle@test.com", [W(24), *late])
    uid = await seeded_athlete(async_db, f"{tag}@test.com")
    await arrive_event(async_db, uid, W(24))
    refs = [await arrive_event(async_db, uid, late[i]) for i in order]  # flag off: all record-only

    outcome = await fold_late_events(async_db, uid, refs)
    await async_db.commit()

    assert outcome.applied, outcome.refusal
    assert await _head_cols(async_db, uid) == oracle
    members = (await async_db.execute(select(StateCorrectionEvent).order_by(StateCorrectionEvent.ordinal))).scalars().all()
    assert [m.ordinal for m in members] == [0, 1, 2]  # recorded in arrival order


async def test_a_batch_with_a_tie_inside_it_keeps_its_arrival_order(async_db):
    await seed_definitions(async_db)
    first, second = W(8, dominant_movement_pattern="squat"), W(8, dominant_movement_pattern="hinge")
    oracle = await oracle_head(async_db, "bt-oracle@test.com", [W(20), first, second])
    uid = await seeded_athlete(async_db, "bt@test.com")
    await arrive_event(async_db, uid, W(20))
    refs = [await arrive_event(async_db, uid, first), await arrive_event(async_db, uid, second)]

    outcome = await fold_late_events(async_db, uid, refs)
    await async_db.commit()

    assert outcome.applied and await _head_cols(async_db, uid) == oracle


# ----- refusals leave the event record-only, with the reason, and write nothing ----------------- #

async def _assert_kept_record_only(db, uid: int, wid: int, code: str, head_before: dict[str, Any]) -> None:
    w = await _workout(db, wid)
    assert (w.state_disposition, w.state_disposition_reason, w.replay_refusal) == (
        "record_only", "event_before_current_state", code,
    )
    assert await _count(db, StateCorrection, uid) == 0
    assert await _count(db, StateCorrectionEvent) == 0
    assert await _head_cols(db, uid) == head_before


async def test_a_late_event_beyond_the_window_stays_record_only(async_db, flag):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "win@test.com")
    for ev in (W(2), W(58)):
        await arrive_event(async_db, uid, ev)
    before = await _head_cols(async_db, uid)

    resp = await _log(async_db, uid, W(5))  # head is 53 h later

    assert resp.state_disposition == "record_only"
    await _assert_kept_record_only(async_db, uid, resp.workout_log_id, "window_exceeded", before)


async def test_a_tail_longer_than_the_declared_size_stays_record_only(async_db, flag):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "long@test.com")
    for h in range(1, 10):  # nine on-time events: one more than the declared tail size (8)
        await arrive_event(async_db, uid, W(h))
    before = await _head_cols(async_db, uid)

    resp = await _log(async_db, uid, W(0.5))

    assert resp.state_disposition == "record_only"
    await _assert_kept_record_only(async_db, uid, resp.workout_log_id, "tail_too_long", before)


async def test_history_written_before_capture_stays_record_only(async_db, flag):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "legacy@test.com", captured=False)
    for ev in (W(10), W(20)):
        await arrive_event(async_db, uid, ev)
    before = await _head_cols(async_db, uid)

    resp = await _log(async_db, uid, W(5))

    assert resp.state_disposition == "record_only"
    await _assert_kept_record_only(async_db, uid, resp.workout_log_id, "untrusted_checkpoint", before)


async def test_a_late_strength_axis_benchmark_stays_record_only(async_db, flag):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "strength@test.com")
    await arrive_event(async_db, uid, W(20))

    await benchmark_service.create_observation(
        async_db, uid, BenchmarkObservationCreate(
            benchmark_code="pl_e1rm_squat", raw_value=150.0, source="benchmark_test",
            observed_at=BASE + timedelta(hours=5),
        ),
    )

    obs = (await async_db.execute(select(BenchmarkObservation).where(BenchmarkObservation.user_id == uid))).scalar_one()
    await async_db.refresh(obs)
    assert (obs.state_disposition, obs.replay_refusal) == ("record_only", "decline_policy_engaged")
    assert await _count(async_db, StateCorrection, uid) == 0


async def test_a_batch_beyond_the_declared_size_is_refused(async_db):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "bigbatch@test.com")
    await arrive_event(async_db, uid, W(30))
    refs = [await arrive_event(async_db, uid, W(h)) for h in range(1, 6)]  # five: one more than 4

    outcome = await fold_late_events(async_db, uid, refs)

    assert (outcome.applied, outcome.refusal) == (False, "batch_too_large")
    assert await _count(async_db, StateCorrection, uid) == 0


# ----- atomicity -------------------------------------------------------------------------------- #

async def test_a_failure_after_the_receipt_is_written_rolls_all_of_it_back(async_db, flag, monkeypatch):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "atomic@test.com")
    for ev in (W(5), W(20)):
        await arrive_event(async_db, uid, ev)
    before = await _head_cols(async_db, uid)
    real_apply = late_event_service.apply_plan

    async def apply_then_fail(db, user_id, plan):
        await real_apply(db, user_id, plan)  # receipt, member, head and disposition are staged…
        raise RuntimeError("injected failure after the writes")

    monkeypatch.setattr(late_event_service, "apply_plan", apply_then_fail)

    resp = await _log(async_db, uid, W(10))

    # …and none of it survived, but the athlete's workout did.
    assert resp.state_disposition == "record_only"
    await _assert_kept_record_only(async_db, uid, resp.workout_log_id, "internal_error", before)

    monkeypatch.undo()
    monkeypatch.setattr(settings, "APPLY_LATE_EVENTS", True)
    outcome = await fold_late_events(async_db, uid, [NewEventRef("workout", resp.workout_log_id)])
    await async_db.commit()
    assert outcome.applied  # a retry works: nothing was left half-done
    w = await _workout(async_db, resp.workout_log_id)
    assert (w.state_disposition, w.replay_refusal) == ("applied", None)


async def test_a_plan_over_a_head_that_moved_is_not_applied(async_db):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "moved@test.com")
    for ev in (W(5), W(20)):
        await arrive_event(async_db, uid, ev)
    ref = await arrive_event(async_db, uid, W(10))
    from app.services.tail_replay_service import plan_tail_replay
    plan = await plan_tail_replay(async_db, uid, [ref])
    await arrive_event(async_db, uid, W(30))  # the head moves after the proof

    from app.logic.tail_replay import ReplayUnsupported
    with pytest.raises(ReplayUnsupported) as exc:
        await late_event_service.apply_plan(async_db, uid, plan)
    assert exc.value.code == "head_moved"


# ----- a fold repeats none of the live writers' side effects ----------------------------------- #

async def test_a_fold_repeats_no_side_effect_of_the_live_writers(async_db):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "sidefx@test.com")
    await arrive_event(async_db, uid, W(5))
    await arrive_event(async_db, uid, W(20))
    late = [await arrive_event(async_db, uid, W(8, dominant_movement_pattern="squat")), await arrive_event(async_db, uid, B(12, raw=70.0))]
    tables = (BenchmarkObservation, StrengthDeclineCandidate, WeakPoint, EkfShadowLog, DoseRoutingShadowLog, DoseModelShadowLog)
    before = [await _count(async_db, t) for t in tables]

    outcome = await fold_late_events(async_db, uid, late)
    await async_db.commit()

    assert outcome.applied
    assert [await _count(async_db, t) for t in tables] == before


# ----- two writers racing on two real connections ---------------------------------------------- #

@pytest.fixture
def factory(async_db: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_db.bind, expire_on_commit=False, autoflush=False)


async def test_two_late_events_racing_on_separate_connections_both_fold_exactly(async_db, factory, monkeypatch):
    monkeypatch.setattr(settings, "APPLY_LATE_EVENTS", True)
    await seed_definitions(async_db)
    oracle = await oracle_head(async_db, "race-oracle@test.com", [W(4), W(20), W(8), W(12, sleep_quality=3.0)])
    uid = await seeded_athlete(async_db, "race@test.com")
    for ev in (W(4), W(20)):
        await arrive_event(async_db, uid, ev)
    await async_db.commit()
    a, b = W(8), W(12, sleep_quality=3.0)

    async def go(ev: Ev):
        async with factory() as db:
            return await _log(db, uid, ev)

    ra, rb = await asyncio.gather(go(a), go(b))

    assert (ra.state_disposition, rb.state_disposition) == ("applied", "applied")
    async_db.expire_all()
    assert await _head_cols(async_db, uid) == oracle
    assert await _count(async_db, StateCorrection, uid) == 2


# ----- the declared policy, exercised: random sequences up to the declared sizes ----------------- #

@pytest.mark.parametrize("seed", range(12))
async def test_random_sequences_within_the_declared_policy_match_chronological_logging(async_db, flag, seed):
    """Up to ``MAX_REPLAY_EVENTS`` events in a random arrival order (so up to that many
    corrections), mixed workouts and benchmarks, some timestamps tied. Every case is inside the
    declared policy, so every late event must fold, and the head must equal the one the same
    events produce logged chronologically (equal times in arrival order)."""
    await seed_definitions(async_db)
    rng = random.Random(seed)
    n = rng.randint(4, MAX_REPLAY_EVENTS)
    hours = [rng.choice([2, 4, 6, 8, 10, 12, 14, 16, 18]) + rng.choice([0, 0, 0.5]) for _ in range(n)]
    events: list[Ev] = []
    for h in hours:
        if rng.random() < 0.25:
            events.append(B(h, raw=rng.choice([45.0, 60.0, 75.0])))
        else:
            events.append(W(h, dominant_movement_pattern=rng.choice(["squat", "hinge", None]),
                            sleep_quality=rng.choice([None, 3.0, 8.0])))
    arrival = list(events)
    rng.shuffle(arrival)
    oracle = await oracle_head(async_db, f"rnd-oracle-{seed}@test.com", sorted(arrival, key=lambda e: e.hours))
    uid = await seeded_athlete(async_db, f"rnd-{seed}@test.com")

    refs = [await arrive_event(async_db, uid, ev) for ev in arrival]

    assert [await is_record_only(async_db, ref) for ref in refs] == [False] * n  # all folded
    assert await _head_cols(async_db, uid) == oracle


async def test_a_refusal_mid_sequence_leaves_a_head_equal_to_the_events_actually_applied(async_db, flag):
    """A refused event must not leave the head wrong for the others. The strength-axis
    benchmark is refused (the decline machine could judge it); the head must equal
    chronological logging of exactly the events that were applied."""
    await seed_definitions(async_db)
    refused = Ev("benchmark", 9, raw=150.0, code="pl_e1rm_squat")
    arrivals = [W(4), W(20), W(8, dominant_movement_pattern="hinge"), refused, W(12, sleep_quality=3.0)]
    uid = await seeded_athlete(async_db, "subset@test.com")

    refs = [await arrive_event(async_db, uid, ev) for ev in arrivals]

    state = [await is_record_only(async_db, ref) for ref in refs]
    assert state == [False, False, False, True, False]  # only the strength benchmark stays out
    applied = [ev for ev, waiting in zip(arrivals, state, strict=True) if not waiting]
    oracle = await oracle_head(async_db, "subset-oracle@test.com", sorted(applied, key=lambda e: e.hours))
    assert await _head_cols(async_db, uid) == oracle


# ----- real safety decisions -------------------------------------------------------------------- #

def _decisions(row: AthleteState) -> dict[str, tuple[bool, str]]:
    ctx = build_constraint_context(unified_from_athlete_row(row), [], "Strength")
    out: dict[str, tuple[bool, str]] = {}
    for code in UNIVERSAL_HARD_CONSTRAINTS:
        result = CONSTRAINT_REGISTRY[code]({}, ctx)
        out[code] = (result.passed, result.severity)
    return out


async def test_the_production_hard_constraints_decide_on_the_corrected_head_as_on_the_chronological_one(async_db, flag):
    """Three heavy sessions an hour apart take systemic fatigue past the hard limit (80); two do
    not. Logging the middle one late must flip the decision on the live head exactly as logging
    it on time does, instead of leaving the stale head to pass a session it should rest."""
    await seed_definitions(async_db)

    def heavy(h: float) -> Ev:
        return W(h, dominant_movement_pattern="hinge", duration_minutes=150.0, session_rpe=10.0,
                 total_volume_load=60000.0)

    arrivals = [W(4), heavy(18), heavy(20), heavy(19)]
    oracle_uid = await seeded_athlete(async_db, "safe-oracle@test.com")
    for ev in sorted(arrivals, key=lambda e: e.hours):
        await arrive_event(async_db, oracle_uid, ev)
    uid = await seeded_athlete(async_db, "safe@test.com")
    for ev in arrivals[:3]:
        await arrive_event(async_db, uid, ev)
    uncorrected = _decisions(await head_row(async_db, uid))

    await arrive_event(async_db, uid, arrivals[3])  # late: folded in

    corrected = _decisions(await head_row(async_db, uid))
    chronological = _decisions(await head_row(async_db, oracle_uid))
    assert corrected == chronological
    assert uncorrected["universal_fatigue_ok"][0] is True  # the stale head would have passed…
    assert corrected["universal_fatigue_ok"][0] is False  # …the corrected one does not


async def test_off_by_default_a_late_benchmark_stays_record_only(async_db):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "off-b@test.com")
    for ev in (W(4), W(20), B(8, raw=70.0)):
        await arrive_event(async_db, uid, ev)

    obs = (await async_db.execute(select(BenchmarkObservation).where(BenchmarkObservation.user_id == uid))).scalar_one()
    await async_db.refresh(obs)
    assert (obs.state_disposition, obs.replay_refusal) == ("record_only", None)  # never attempted
    assert await _count(async_db, StateCorrection, uid) == 0


# ----- ownership: a bad reference never writes to someone else's event -------------------------- #

async def test_a_foreign_reference_is_refused_without_writing_to_the_foreign_event(async_db):
    await seed_definitions(async_db)
    mine = await seeded_athlete(async_db, "own-mine@test.com")
    theirs = await seeded_athlete(async_db, "own-theirs@test.com")
    for uid in (mine, theirs):
        await arrive_event(async_db, uid, W(5))
        await arrive_event(async_db, uid, W(20))
    foreign = await arrive_event(async_db, theirs, W(10))  # theirs, record-only
    own = await arrive_event(async_db, mine, W(10))

    outcome = await fold_late_events(async_db, mine, [own, foreign])

    assert (outcome.applied, outcome.refusal) == (False, "ownership")
    for ref in (foreign, own):
        row = await _workout(async_db, ref.event_id)
        assert (row.state_disposition, row.replay_refusal) == ("record_only", None)
    assert await _count(async_db, StateCorrection) == 0


async def test_an_oversized_batch_with_a_foreign_reference_is_refused_as_ownership_first(async_db):
    await seed_definitions(async_db)
    mine = await seeded_athlete(async_db, "own-big@test.com")
    theirs = await seeded_athlete(async_db, "own-big-t@test.com")
    for uid in (mine, theirs):
        await arrive_event(async_db, uid, W(30))
    foreign = await arrive_event(async_db, theirs, W(3))
    refs = [await arrive_event(async_db, mine, W(h)) for h in range(1, 5)] + [foreign]  # five

    outcome = await fold_late_events(async_db, mine, refs)

    assert outcome.refusal == "ownership"
    assert (await _workout(async_db, foreign.event_id)).replay_refusal is None
    for r in refs:
        assert (await _workout(async_db, r.event_id)).replay_refusal is None


async def test_an_unknown_reference_is_refused(async_db):
    uid = await seeded_athlete(async_db, "own-unknown@test.com")

    outcome = await fold_late_events(async_db, uid, [NewEventRef("workout", 987654)])

    assert (outcome.applied, outcome.refusal) == (False, "ownership")


async def test_a_refusal_is_not_written_onto_an_event_that_is_already_applied(async_db):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "own-applied@test.com")
    on_time = await arrive_event(async_db, uid, W(5))
    await arrive_event(async_db, uid, W(20))

    outcome = await fold_late_events(async_db, uid, [on_time])
    await async_db.flush()  # a pending write would be visible to the read below

    assert outcome.refusal == "not_record_only"
    assert (await _workout(async_db, on_time.event_id)).replay_refusal is None


# ----- the declared limits, at exactly the boundary --------------------------------------------- #

def _boundary_events() -> tuple[list[Ev], list[Ev]]:
    """Four on-time events (the head at 50 h) and four late ones; the earliest late event is
    exactly 48 h before the head, and 4 + 4 = 8 events are replayed."""
    tail = [W(20), W(30, dominant_movement_pattern="squat"), W(40), W(50, sleep_quality=4.0)]
    late = [W(2), B(8, raw=70.0), W(14, dominant_movement_pattern="hinge"), W(35, sleep_quality=3.0)]
    return tail, late


@pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
async def test_a_four_event_batch_over_eight_replayed_events_at_exactly_48_hours_in_every_order(async_db, order):
    await seed_definitions(async_db)
    tail, late = _boundary_events()
    tag = "edge" + "".join(map(str, order))
    oracle = await oracle_head(async_db, f"{tag}-oracle@test.com", sorted([*tail, *late], key=lambda e: e.hours))
    uid = await seeded_athlete(async_db, f"{tag}@test.com")
    for ev in tail:
        await arrive_event(async_db, uid, ev)
    refs = [await arrive_event(async_db, uid, late[i]) for i in order]  # flag off: record-only

    outcome = await fold_late_events(async_db, uid, refs)
    await async_db.commit()

    assert outcome.applied, outcome.refusal
    assert await _head_cols(async_db, uid) == oracle


async def test_one_second_past_the_window_is_refused(async_db):
    await seed_definitions(async_db)
    tail, late = _boundary_events()
    late[0] = W(2 - 1 / 3600)  # 48 h and one second before the head
    uid = await seeded_athlete(async_db, "edge-window@test.com")
    for ev in tail:
        await arrive_event(async_db, uid, ev)
    refs = [await arrive_event(async_db, uid, ev) for ev in late]

    outcome = await fold_late_events(async_db, uid, refs)

    assert (outcome.applied, outcome.refusal) == (False, "window_exceeded")


async def test_nine_replayed_events_are_refused(async_db):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "edge-tail@test.com")
    for h in (20, 25, 30, 35, 40):
        await arrive_event(async_db, uid, W(h))
    refs = [await arrive_event(async_db, uid, W(h)) for h in (10, 12, 14, 16)]  # 5 + 4 = 9

    outcome = await fold_late_events(async_db, uid, refs)

    assert (outcome.applied, outcome.refusal) == (False, "tail_too_long")
