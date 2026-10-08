"""P3b-2: exact tail replay: plan a correction and prove the stored history first. Read-only.

Two kinds of test:

* **Pure** (``app.logic.tail_replay``): ordering, ties, staleness, and what the stepper refuses.
* **Against the live writers.** The independent proof is an oracle: one athlete receives a set
  of events in true chronological order through the real ``process_new_workout`` /
  ``create_observation``; another receives the same events out of order, so the late ones are
  recorded record-only. The plan's corrected head for the second must equal the first athlete's
  head, column for column, with no tolerance. That covers a late workout, a late benchmark,
  a batch in every arrival order, timestamp ties, and a second correction after an on-time event
  (a hand-written correction stands in for the P3b-3 writer).

The refusals are each pinned to the stable code they raise.
"""

from __future__ import annotations

import ast
import itertools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from app.engine.transition_identity import current_identity
from app.logic.tail_replay import (
    CorrectionMark,
    ReplayEvent,
    ReplayUnsupported,
    diff_columns,
    is_stale,
    order_events,
    replay,
    row_columns,
    state_columns,
)
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.engine_transition_identity import EngineTransitionIdentity
from app.models.observation_mapping import ObservationMapping
from app.models.state_correction import StateCorrection, StateCorrectionEvent
from app.models.user import User
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.schemas.workouts import StressDose, WorkoutLog
from app.services import benchmark_service, state_service
from app.services.tail_replay_service import (
    ALGORITHM_VERSION,
    NewEventRef,
    TailReplayPlan,
    plan_tail_replay,
)

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Pure
# --------------------------------------------------------------------------- #

def _ev(kind: str, eid: int, ts: datetime, row_key: int | None, ordinal: int = 0, **ri: Any) -> ReplayEvent:
    return ReplayEvent(
        kind=kind, event_id=eid, timestamp=ts, row_key=row_key, ordinal=ordinal,  # type: ignore[arg-type]
        replay_input={"v": 1, "kind": kind, **ri},
    )


T = datetime(2026, 10, 1, 12, 0, 0)


def test_events_order_by_time_then_arrival_and_new_events_come_last_among_ties():
    a = _ev("workout", 1, T, 10)
    b = _ev("workout", 2, T, 11)
    new = _ev("workout", 3, T, 2**62, 0)
    earlier = _ev("workout", 4, T - timedelta(hours=1), 12)
    assert [e.event_id for e in order_events([new, b, earlier, a])] == [4, 1, 2, 3]


def test_an_introduced_batch_sorts_after_events_that_arrived_before_its_correction():
    plain = _ev("workout", 1, T, 10)
    introduced_a = _ev("workout", 2, T, 40, 0)  # correction head row 40
    introduced_b = _ev("benchmark", 3, T, 40, 1)
    assert [e.event_id for e in order_events([introduced_b, introduced_a, plain])] == [1, 2, 3]


def test_an_event_with_no_row_key_cannot_share_a_timestamp():
    with pytest.raises(ReplayUnsupported) as exc:
        order_events([_ev("workout", 1, T, 10), _ev("benchmark", 2, T, None)])
    assert exc.value.code == "ambiguous_tie"
    # …but it is fine on its own timestamp.
    assert len(order_events([_ev("workout", 1, T, 10), _ev("benchmark", 2, T + timedelta(hours=1), None)])) == 2


def test_the_same_event_twice_is_refused():
    with pytest.raises(ReplayUnsupported) as exc:
        order_events([_ev("workout", 1, T, 10), _ev("workout", 1, T + timedelta(hours=1), 11)])
    assert exc.value.code == "double_membership"


def test_staleness_covers_rows_between_the_first_introduced_event_and_the_correction_head():
    marks = [CorrectionMark(head_row_id=40, affected_from=T)]
    assert is_stale(30, T + timedelta(hours=1), marks)  # computed without the introduced event
    assert not is_stale(30, T, marks)  # at the introduced event's time: it arrived after
    assert not is_stale(30, T - timedelta(hours=1), marks)
    assert not is_stale(40, T + timedelta(hours=9), marks)  # the head itself
    assert not is_stale(41, T + timedelta(hours=9), marks)  # built on the head


def _base_state():
    state, _ = state_service._build_baseline_vector(0)
    state.timestamp = T
    return state


def test_the_stepper_refuses_what_it_cannot_reproduce():
    base = _base_state()
    mapping = {
        "id": 1, "target_vector": "capacity", "target_key": "max_strength", "mapping_type": "residual",
        "coefficient": 1.0, "intercept": 0.0, "min_value": None, "max_value": None, "config": None,
    }
    bench: dict[str, Any] = {
        "observed_at": (T + timedelta(hours=1)).isoformat(), "raw_value": 100.0,
        "normalized_value": 50.0, "score01": 0.5, "better_direction": "higher",
        "observation_weight_used": 1.0, "mappings": [mapping],
        "effect": "bidirectional_update", "decline": None,
    }
    workout: dict[str, Any] = {
        "modality": "Running", "dominant_movement_pattern": None, "sleep_quality": None,
        "life_stress_inverse": None, "dose": StressDose().model_dump(),
    }
    hour = T + timedelta(hours=1)
    cases = {
        "decline_policy_engaged": _ev("benchmark", 1, hour, 5, **bench),
        "decline_outcome": _ev("benchmark", 1, hour, 5, **{**bench, "mappings": [], "decline": {"intercepted": True}}),
        "unsupported_effect": _ev("benchmark", 1, hour, 5, **{**bench, "mappings": [], "effect": "upward_lower_bound"}),
        "timestamp_mismatch": _ev("benchmark", 1, hour + timedelta(hours=1), 5, **{**bench, "mappings": []}),
        "capture_invalid": _ev("workout", 1, hour, 5),  # no dose
        "negative_interval": _ev("workout", 1, T - timedelta(hours=1), 5, **workout),
    }
    for code, event in cases.items():
        with pytest.raises(ReplayUnsupported) as exc:
            replay(base, [event])
        assert exc.value.code == code, (code, exc.value)
    # …and the same workout, in order, steps fine.
    assert replay(base, [_ev("workout", 1, hour, 5, **workout)])[0].wrote_row
    wrong_version = ReplayEvent(
        kind="workout", event_id=1, timestamp=T + timedelta(hours=1), row_key=1, ordinal=0,
        replay_input={"v": 2, "kind": "workout"},
    )
    with pytest.raises(ReplayUnsupported) as exc:
        replay(base, [wrong_version])
    assert exc.value.code == "unknown_capture"


def _initialize_prior_event(score01: float, eid: int = 1) -> ReplayEvent:
    mapping = {
        "id": 1, "target_vector": "capacity", "target_key": "aerobic", "mapping_type": "residual",
        "coefficient": 1.0, "intercept": 0.0, "min_value": None, "max_value": None, "config": None,
    }
    return _ev(
        "benchmark", eid, T + timedelta(hours=1), 5,
        observed_at=(T + timedelta(hours=1)).isoformat(), raw_value=50.0,
        normalized_value=score01 * 100, score01=score01, better_direction="higher",
        observation_weight_used=1.0, mappings=[mapping], effect="initialize_prior", decline=None,
    )


def test_an_initializing_prior_that_raises_nothing_writes_no_state():
    base = _base_state()
    low = replay(base, [_initialize_prior_event(0.0)])[0]  # a reading far below the seeded capacity
    assert low.wrote_row is False and low.state is base
    high = replay(base, [_initialize_prior_event(1.0)])[0]  # far above: raises the floor
    assert high.wrote_row is True
    assert high.state.capacity_x.aerobic > base.capacity_x.aerobic


def test_column_comparison_is_exact():
    cols = state_columns(_base_state())
    assert diff_columns(cols, dict(cols)) == []
    off = dict(cols)
    off["f_nm_central"] = cols["f_nm_central"] + 1e-12
    assert diff_columns(cols, off) == ["f_nm_central"]


def test_the_workout_operator_reads_only_the_fields_a_snapshot_captures():
    """A replay builds the operator's log from the snapshot; if the operator started reading
    another field, the replay would feed it a stand-in. The identity would change then, but
    this fails first and says why."""
    src = (ROOT / "app/logic/state_update_v0.py").read_text(encoding="utf-8")
    read: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "log"
        ):
            read.add(node.attr)
    assert read == {"modality", "dominant_movement_pattern", "sleep_quality", "life_stress_inverse"}


# --------------------------------------------------------------------------- #
# Against the live writers
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


BASE = _now() - timedelta(hours=60)  # events land between BASE and BASE + 40 h: all in the past


@dataclass
class Ev:
    kind: str
    hours: float
    raw: float = 0.0
    extra: dict[str, Any] | None = None

    @property
    def ts(self) -> datetime:
        return BASE + timedelta(hours=self.hours)


def W(hours: float, **extra: Any) -> Ev:
    return Ev("workout", hours, extra={"modality": "Strength", "duration_minutes": 60.0,
                                       "session_rpe": 8.0, **extra})


def B(hours: float, raw: float = 55.0) -> Ev:
    return Ev("benchmark", hours, raw=raw)


async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _definitions(db) -> None:
    aero = BenchmarkDefinition(
        code="aero_test", name="Aerobic test", domain="endurance", metric_type="load", unit="u",
        better_direction="higher", observation_weight=0.8,
        standardization_rules={"floor": 10.0, "cap": 100.0},
    )
    strength = BenchmarkDefinition(
        code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting", metric_type="load",
        unit="kg", better_direction="higher", observation_weight=1.0,
        standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    db.add_all([aero, strength])
    await db.flush()
    db.add_all([
        ObservationMapping(
            benchmark_definition_id=aero.id, target_vector="capacity", target_key="aerobic",
            mapping_type="residual", coefficient=1.0, intercept=0.0,
        ),
        ObservationMapping(
            benchmark_definition_id=aero.id, target_vector="tissue", target_key="lumbar",
            mapping_type="direct", coefficient=0.5, intercept=20.0, config={"scale": 25.0, "amp": 3.0},
        ),
        ObservationMapping(
            benchmark_definition_id=strength.id, target_vector="capacity", target_key="max_strength",
            mapping_type="residual", coefficient=1.0, intercept=0.0,
        ),
    ])
    await db.commit()


async def _athlete(db, email: str, *, captured: bool = True) -> int:
    """An athlete with two baseline rows before BASE (so no first-event re-anchor applies)."""
    uid = (await _user(db, email)).id
    for ts in (BASE - timedelta(days=2), BASE - timedelta(days=1)):
        _, row = state_service._build_baseline_vector(uid)
        row.timestamp = ts
        if not captured:
            row.event_kind = None
        db.add(row)
        await db.commit()
    return uid


async def _arrive(db, uid: int, ev: Ev) -> NewEventRef:
    user = await db.get(User, uid)
    assert user is not None
    if ev.kind == "workout":
        await state_service.process_new_workout(
            db, uid, WorkoutLog(timestamp=ev.ts.replace(tzinfo=UTC), **(ev.extra or {})),
            received_at=datetime.now(UTC),
        )
        wid = (await db.execute(
            select(WorkoutLogORM.id).where(WorkoutLogORM.user_id == uid).order_by(WorkoutLogORM.id.desc()).limit(1)
        )).scalar_one()
        return NewEventRef("workout", wid)
    await benchmark_service.create_observation(
        db, uid, BenchmarkObservationCreate(
            benchmark_code="aero_test", raw_value=ev.raw, source="benchmark_test", observed_at=ev.ts,
        ),
    )
    oid = (await db.execute(
        select(BenchmarkObservation.id).where(BenchmarkObservation.user_id == uid)
        .order_by(BenchmarkObservation.id.desc()).limit(1)
    )).scalar_one()
    return NewEventRef("benchmark", oid)


async def _head(db, uid: int) -> AthleteState:
    row = (await db.execute(
        select(AthleteState).where(AthleteState.user_id == uid)
        .order_by(AthleteState.timestamp.desc(), AthleteState.id.desc()).limit(1)
    )).scalar_one()
    await db.refresh(row)
    return row


async def _record_only(db, ref: NewEventRef) -> bool:
    model = WorkoutLogORM if ref.kind == "workout" else BenchmarkObservation
    row = await db.get(model, ref.event_id)
    assert row is not None
    await db.refresh(row)
    return row.state_disposition == "record_only"


async def _oracle(db, email: str, events: list[Ev]) -> dict[str, Any]:
    """The same events through the live writers, in true chronological order."""
    uid = await _athlete(db, email)
    for ev in sorted(events, key=lambda e: e.hours):  # stable: equal hours keep their given order
        await _arrive(db, uid, ev)
    return row_columns(await _head(db, uid))


async def _subject(db, email: str, arrivals: list[Ev]) -> tuple[int, list[NewEventRef], list[NewEventRef]]:
    """Events arriving in the given order. Returns (user, late refs in arrival order, all refs)."""
    uid = await _athlete(db, email)
    late: list[NewEventRef] = []
    refs: list[NewEventRef] = []
    for ev in arrivals:
        ref = await _arrive(db, uid, ev)
        refs.append(ref)
        if await _record_only(db, ref):
            late.append(ref)
    return uid, late, refs


async def _assert_plan_equals_chronological(db, tag: str, arrivals: list[Ev], expect_late: int) -> TailReplayPlan:
    oracle = await _oracle(db, f"{tag}-oracle@test.com", arrivals)
    uid, late, _ = await _subject(db, f"{tag}-subject@test.com", arrivals)
    assert len(late) == expect_late
    plan = await plan_tail_replay(db, uid, late)
    assert diff_columns(state_columns(plan.corrected_head), oracle) == []
    assert plan.algorithm_version == ALGORITHM_VERSION and plan.transition_identity == current_identity().digest
    return plan


async def test_a_late_workout_between_two_others(async_db):
    await _definitions(async_db)
    plan = await _assert_plan_equals_chronological(
        async_db, "w-mid", [W(5, dominant_movement_pattern="squat"), W(20), W(10, dominant_movement_pattern="hinge", sleep_quality=4.0, life_stress_inverse=3.0)], 1,
    )
    # The checkpoint is the 5 h workout's row; the 20 h workout is the one stored row after it.
    assert plan.rows_proven == 1 and len(plan.members) == 1 and len(plan.new_events) == 1


async def test_a_late_workout_just_after_the_baseline(async_db):
    await _definitions(async_db)
    plan = await _assert_plan_equals_chronological(async_db, "w-first", [W(10), W(2)], 1)
    ck = await async_db.get(AthleteState, plan.checkpoint_row_id)
    assert ck is not None and ck.event_kind == "baseline"  # nothing but the seeded baseline precedes it


async def test_a_late_benchmark_among_workouts(async_db):
    await _definitions(async_db)
    plan = await _assert_plan_equals_chronological(
        async_db, "b-late", [W(4), W(12), W(20), B(8, raw=70.0)], 1,
    )
    assert [e.kind for e in plan.new_events] == ["benchmark"] and len(plan.members) == 2


async def test_a_tail_that_already_holds_a_benchmark_is_replayed_exactly(async_db):
    await _definitions(async_db)
    plan = await _assert_plan_equals_chronological(
        async_db, "b-tail", [W(4), B(10, raw=60.0), W(14), W(22), W(8, dominant_movement_pattern="hinge")], 1,
    )
    assert "benchmark" in {e.kind for e in plan.members}  # the replayed tail includes a stored benchmark


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
async def test_a_batch_of_three_late_events_in_every_arrival_order(async_db, order):
    await _definitions(async_db)
    late_events = [W(6, dominant_movement_pattern="squat"), B(9, raw=65.0), W(13, sleep_quality=3.0)]
    arrivals = [W(24), *[late_events[i] for i in order]]
    tag = "batch" + "".join(map(str, order))
    plan = await _assert_plan_equals_chronological(async_db, tag, arrivals, 3)
    assert len(plan.new_events) == 3


async def test_a_late_event_sharing_an_existing_events_timestamp_goes_after_it(async_db):
    await _definitions(async_db)
    # First W(5) arrives on time; the second W(5) arrives after the head and is late. Arrival order
    # decides the tie, so the oracle receives them in that order too.
    plan = await _assert_plan_equals_chronological(
        async_db, "tie", [W(5, dominant_movement_pattern="squat"), W(15), W(5, dominant_movement_pattern="hinge")], 1,
    )
    # The row at the late event's own timestamp is the checkpoint: it arrived first, so it precedes.
    ck = await async_db.get(AthleteState, plan.checkpoint_row_id)
    assert ck is not None and ck.timestamp == BASE + timedelta(hours=5) and ck.event_kind == "workout"


async def test_the_plan_writes_nothing(async_db):
    await _definitions(async_db)
    uid, late, _ = await _subject(async_db, "ro@test.com", [W(5), W(20), W(10)])
    counts = lambda: [  # noqa: E731
        select(func.count()).select_from(m).where(m.user_id == uid)
        for m in (AthleteState, WorkoutLogORM, BenchmarkObservation, StateCorrection)
    ]
    before = [(await async_db.execute(q)).scalar_one() for q in counts()]

    await plan_tail_replay(async_db, uid, late)

    assert [(await async_db.execute(q)).scalar_one() for q in counts()] == before
    assert not async_db.new and not async_db.dirty and not async_db.deleted


# ----- a second correction, after an on-time event (a hand-written P3b-3 stand-in) -------------- #

async def _apply_for_test(db, uid: int, plan: TailReplayPlan) -> None:
    """What P3b-3 will do atomically: a receipt, its events, and a correction head at the old
    head's timestamp. Hand-written here so the second correction has real lineage to read."""
    c = StateCorrection(
        user_id=uid, algorithm_version=plan.algorithm_version,
        transition_identity=plan.transition_identity, checkpoint_state_id=plan.checkpoint_row_id,
        head_before_state_id=plan.head_before_row_id, affected_from=plan.affected_from,
    )
    db.add(c)
    await db.flush()
    for ordinal, e in enumerate(plan.new_events):
        db.add(StateCorrectionEvent(
            correction_id=c.id, ordinal=ordinal,
            workout_log_id=e.event_id if e.kind == "workout" else None,
            observation_id=e.event_id if e.kind == "benchmark" else None,
        ))
    from app.engine.state_bridge import athlete_state_kwargs_from_unified
    db.add(AthleteState(
        user_id=uid, event_kind="correction", source_correction_id=c.id,
        predecessor_state_id=plan.head_before_row_id, transition_identity=plan.transition_identity,
        **athlete_state_kwargs_from_unified(plan.corrected_head),
    ))
    await db.commit()


@pytest.mark.parametrize("second_late_hours", [3, 7, 11, 16])
async def test_a_second_correction_includes_the_first_exactly_once(async_db, second_late_hours):
    await _definitions(async_db)
    # chronological truth: 4, 7(first late), 11, 16, 18 (on time, after the first correction), + the second late event.
    first_late = W(7, dominant_movement_pattern="hinge")
    second_late = W(second_late_hours, sleep_quality=4.0, life_stress_inverse=6.0)
    truth = [W(4), first_late, W(11), W(16), W(18), second_late]
    oracle = await _oracle(async_db, f"two-oracle-{second_late_hours}@test.com", truth)

    uid = await _athlete(async_db, f"two-subject-{second_late_hours}@test.com")
    for ev in (W(4), W(11), W(16)):
        await _arrive(async_db, uid, ev)
    r1 = await _arrive(async_db, uid, first_late)
    assert await _record_only(async_db, r1)

    plan1 = await plan_tail_replay(async_db, uid, [r1])
    await _apply_for_test(async_db, uid, plan1)
    await _arrive(async_db, uid, W(18))  # an on-time event on top of the correction head
    r2 = await _arrive(async_db, uid, second_late)
    assert await _record_only(async_db, r2)

    plan2 = await plan_tail_replay(async_db, uid, [r2])

    assert diff_columns(state_columns(plan2.corrected_head), oracle) == []
    ids = [(e.kind, e.event_id) for e in plan2.members]
    assert len(ids) == len(set(ids))
    if ("workout", r1.event_id) not in ids:
        # Already inside the checkpoint: the first correction's own head (the new event ties
        # its timestamp and arrived after it).
        ck = await async_db.get(AthleteState, plan2.checkpoint_row_id)
        assert ck is not None and ck.event_kind == "correction"
    assert all(e.kind != "workout" or e.event_id != r2.event_id for e in plan2.members)


async def test_a_receipt_is_never_a_training_event(async_db):
    await _definitions(async_db)
    uid = await _athlete(async_db, "rcpt@test.com")
    for ev in (W(4), W(11), W(16)):
        await _arrive(async_db, uid, ev)
    r1 = await _arrive(async_db, uid, W(7))
    plan1 = await plan_tail_replay(async_db, uid, [r1])
    await _apply_for_test(async_db, uid, plan1)
    r2 = await _arrive(async_db, uid, W(9))  # late again, against the correction head

    plan2 = await plan_tail_replay(async_db, uid, [r2])

    # Members are workouts and observations only; the receipt contributes the events it introduced.
    assert {e.kind for e in plan2.members} <= {"workout", "benchmark"}
    assert not any(e.event_id == 1 and e.kind == "correction" for e in plan2.members)  # type: ignore[comparison-overlap]


async def test_an_event_already_introduced_cannot_be_introduced_again(async_db):
    await _definitions(async_db)
    uid = await _athlete(async_db, "again@test.com")
    for ev in (W(4), W(16)):
        await _arrive(async_db, uid, ev)
    r1 = await _arrive(async_db, uid, W(7))
    await _apply_for_test(async_db, uid, await plan_tail_replay(async_db, uid, [r1]))

    with pytest.raises(ReplayUnsupported) as exc:
        await plan_tail_replay(async_db, uid, [r1])
    assert exc.value.code == "already_introduced"


# ----- refusals ------------------------------------------------------------------------------- #

async def _code(db, uid: int, refs: list[NewEventRef], **kw: Any) -> str:
    """The stable code the plan refuses with (the test fails if it does not refuse)."""
    try:
        await plan_tail_replay(db, uid, refs, **kw)
    except ReplayUnsupported as exc:
        return exc.code
    raise AssertionError("the plan was expected to refuse")


async def test_rows_written_before_capture_are_not_a_trustworthy_checkpoint(async_db):
    await _definitions(async_db)
    uid = await _athlete(async_db, "legacy@test.com", captured=False)
    for ev in (W(10), W(20)):
        await _arrive(async_db, uid, ev)
    late = await _arrive(async_db, uid, W(5))
    assert await _code(async_db, uid, [late]) == "untrusted_checkpoint"


async def test_no_state_at_or_before_the_earliest_event(async_db):
    await _definitions(async_db)
    uid = await _athlete(async_db, "early@test.com")
    for ev in (W(10), W(20)):
        await _arrive(async_db, uid, ev)
    late = await _arrive(async_db, uid, W(-60))  # before the seeded baselines
    assert await _code(async_db, uid, [late]) == "no_checkpoint"


async def test_a_repair_row_in_the_tail_is_refused(async_db):
    await _definitions(async_db)
    uid, late, _ = await _subject(async_db, "repair@test.com", [W(5), W(20), W(10)])
    head = await _head(async_db, uid)
    async_db.add(AthleteState(
        user_id=uid, event_kind="repair", predecessor_state_id=head.id,
        timestamp=head.timestamp + timedelta(minutes=1),
        **{k: getattr(head, k) for k in ("c_met_aerobic", "c_nm_force", "c_struct", "b_met_anaerobic",
                                         "f_met_systemic", "f_nm_peripheral", "f_nm_central",
                                         "f_struct_damage", "s_struct_signal", "habit_strength",
                                         "skill_state", "engine_state")},
    ))
    await async_db.commit()
    assert await _code(async_db, uid, late) == "unclassified_state_write"


async def test_a_benchmark_the_decline_machine_could_judge_is_refused(async_db):
    await _definitions(async_db)
    uid = await _athlete(async_db, "decline@test.com")
    await _arrive(async_db, uid, W(5))
    await benchmark_service.create_observation(
        async_db, uid, BenchmarkObservationCreate(
            benchmark_code="pl_e1rm_squat", raw_value=150.0, source="benchmark_test",
            observed_at=BASE + timedelta(hours=12),
        ),
    )
    await _arrive(async_db, uid, W(20))
    late = await _arrive(async_db, uid, W(3))
    assert await _code(async_db, uid, [late]) == "decline_policy_engaged"


async def test_a_late_benchmark_on_the_strength_axis_is_refused(async_db):
    await _definitions(async_db)
    uid = await _athlete(async_db, "decline-late@test.com")
    await _arrive(async_db, uid, W(20))
    await benchmark_service.create_observation(
        async_db, uid, BenchmarkObservationCreate(
            benchmark_code="pl_e1rm_squat", raw_value=150.0, source="benchmark_test",
            observed_at=BASE + timedelta(hours=5),
        ),
    )
    oid = (await async_db.execute(select(BenchmarkObservation.id).where(BenchmarkObservation.user_id == uid))).scalar_one()
    assert await _code(async_db, uid, [NewEventRef("benchmark", oid)]) == "decline_policy_engaged"


async def test_rows_from_other_code_are_refused(async_db):
    await _definitions(async_db)
    uid, late, _ = await _subject(async_db, "ident@test.com", [W(5), W(20), W(10)])
    async_db.add(EngineTransitionIdentity(digest="f" * 64, components={}))
    await async_db.flush()
    head = await _head(async_db, uid)
    head.transition_identity = "f" * 64
    await async_db.commit()
    assert await _code(async_db, uid, late) == "identity_mismatch"


async def test_an_inconsistent_registry_is_refused(async_db):
    await _definitions(async_db)
    uid, late, _ = await _subject(async_db, "registry@test.com", [W(5), W(20), W(10)])
    reg = await async_db.get(EngineTransitionIdentity, current_identity().digest)
    assert reg is not None
    reg.components = {**reg.components, "parameters": "0" * 64}
    await async_db.commit()
    assert await _code(async_db, uid, late) == "registry_inconsistent"


async def test_a_stored_row_the_capture_cannot_reproduce_fails_the_proof(async_db):
    await _definitions(async_db)
    uid, late, _ = await _subject(async_db, "proof@test.com", [W(5), W(20), W(10)])
    head = await _head(async_db, uid)
    head.f_nm_central = head.f_nm_central + 0.01  # something changed the row outside the capture
    await async_db.commit()
    with pytest.raises(ReplayUnsupported) as exc:
        await plan_tail_replay(async_db, uid, late)
    assert exc.value.code == "reconstruction_mismatch" and "f_nm_central" in exc.value.detail


async def test_a_broken_predecessor_link_is_refused(async_db):
    await _definitions(async_db)
    uid, late, _ = await _subject(async_db, "lineage@test.com", [W(5), W(20), W(10)])
    head = await _head(async_db, uid)
    head.predecessor_state_id = None
    await async_db.commit()
    assert await _code(async_db, uid, late) == "lineage"


async def test_another_athletes_event_is_refused(async_db):
    await _definitions(async_db)
    uid, late, _ = await _subject(async_db, "own-a@test.com", [W(5), W(20), W(10)])
    other, other_late, _ = await _subject(async_db, "own-b@test.com", [W(5), W(20), W(10)])
    assert await _code(async_db, uid, other_late) == "ownership"
    assert other != uid and late


async def test_an_event_that_is_not_before_the_head_is_not_late(async_db):
    await _definitions(async_db)
    uid, _, refs = await _subject(async_db, "notlate@test.com", [W(5), W(20)])
    donor = await async_db.get(WorkoutLogORM, refs[0].event_id)
    assert donor is not None and donor.replay_input is not None
    row = WorkoutLogORM(
        user_id=uid, session_timestamp=BASE + timedelta(hours=21), modality="Strength",
        duration_minutes=30.0, session_rpe=6.0, state_disposition="record_only",
        state_disposition_reason="event_before_current_state", dose_snapshot={},
        replay_input=donor.replay_input,
    )
    async_db.add(row)
    await async_db.commit()
    assert await _code(async_db, uid, [NewEventRef("workout", row.id)]) == "not_late"


async def test_an_applied_event_is_not_a_late_event(async_db):
    await _definitions(async_db)
    uid, _, refs = await _subject(async_db, "applied@test.com", [W(5), W(20)])
    assert await _code(async_db, uid, [refs[0]]) == "not_record_only"


async def test_a_late_event_without_capture_is_refused(async_db):
    await _definitions(async_db)
    uid, _, _ = await _subject(async_db, "nocap@test.com", [W(5), W(20)])
    row = WorkoutLogORM(
        user_id=uid, session_timestamp=BASE + timedelta(hours=2), modality="Running",
        duration_minutes=30.0, session_rpe=6.0, state_disposition="record_only",
        state_disposition_reason="event_before_current_state", dose_snapshot={},
    )
    async_db.add(row)
    await async_db.commit()
    assert await _code(async_db, uid, [NewEventRef("workout", row.id)]) == "no_capture"


async def test_the_caller_can_bound_the_window_and_the_tail(async_db):
    await _definitions(async_db)
    uid, late, _ = await _subject(async_db, "bounds@test.com", [W(5), W(30), W(10)])
    assert await _code(async_db, uid, late, max_gap=timedelta(hours=10)) == "window_exceeded"
    assert await _code(async_db, uid, late, max_events=1) == "tail_too_long"
    await plan_tail_replay(async_db, uid, late, max_gap=timedelta(hours=40), max_events=2)


async def test_no_new_events_is_an_error(async_db):
    uid = (await _user(async_db, "none@test.com")).id
    assert await _code(async_db, uid, []) == "no_new_events"
