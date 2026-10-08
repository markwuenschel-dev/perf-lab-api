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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.engine.transition_identity import current_identity
from app.logic.replay_inputs import MappingSnapshot
from app.logic.state_transitions import (
    BenchmarkOperatorInput,
    floor_if_raised,
    run_benchmark_operator,
)
from app.logic.tail_replay import (
    CorrectionMark,
    ReplayEvent,
    ReplayUnsupported,
    diff_columns,
    is_stale,
    order_events,
    replay,
    state_columns,
)
from app.models.athlete_state import AthleteState
from app.models.benchmark_observation import BenchmarkObservation
from app.models.engine_transition_identity import EngineTransitionIdentity
from app.models.state_correction import StateCorrection, StateCorrectionEvent
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.schemas.workouts import StressDose
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

def _ev(kind: str, eid: int, ts: datetime, row_key: int, ordinal: int = 0, **ri: Any) -> ReplayEvent:
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


def test_two_events_at_one_position_cannot_be_ordered():
    with pytest.raises(ReplayUnsupported) as exc:
        order_events([_ev("workout", 1, T, 10), _ev("benchmark", 2, T, 10)])
    assert exc.value.code == "ambiguous_tie"
    # The same timestamp is fine once the arrival order (the row key) separates them.
    assert len(order_events([_ev("workout", 1, T, 10), _ev("benchmark", 2, T, 11)])) == 2


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
        "initializer_tail": _ev("benchmark", 1, hour, 5, **{**bench, "mappings": [], "effect": "initialize_prior"}),
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


def test_the_live_floor_ratchet_writes_no_state_when_it_raises_nothing():
    """``floor_if_raised`` is the live writer's non-regressing handler (initial priors, floors).
    The replay refuses those, but the live path still depends on it."""
    base = _base_state()
    mapping = MappingSnapshot(
        id=1, target_vector="capacity", target_key="aerobic", mapping_type="residual",
        coefficient=1.0, intercept=0.0, min_value=None, max_value=None, config=None,
    )

    def op(score01: float) -> BenchmarkOperatorInput:
        return BenchmarkOperatorInput(
            observed_at=T + timedelta(hours=1), raw_value=50.0, normalized_value=score01 * 100,
            score01=score01, better_direction="higher", observation_weight_used=1.0,
            mappings=[mapping],
        )

    low = floor_if_raised(base, run_benchmark_operator(base, op(0.0)))  # far below the seeded capacity
    assert low is None
    high = floor_if_raised(base, run_benchmark_operator(base, op(1.0)))  # far above: raises the floor
    assert high is not None and high.capacity_x.aerobic > base.capacity_x.aerobic


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

from replay_support import (  # noqa: E402
    BASE,
    B,
    Ev,
    W,
    arrive_all,
    arrive_event,
    head_row,
    is_record_only,
    make_user,
    oracle_head,
    seed_definitions,
    seeded_athlete,
)


async def _assert_plan_equals_chronological(db, tag: str, arrivals: list[Ev], expect_late: int) -> TailReplayPlan:
    oracle = await oracle_head(db, f"{tag}-oracle@test.com", arrivals)
    uid, late, _ = await arrive_all(db, f"{tag}-subject@test.com", arrivals)
    assert len(late) == expect_late
    plan = await plan_tail_replay(db, uid, late)
    assert diff_columns(state_columns(plan.corrected_head), oracle) == []
    assert plan.algorithm_version == ALGORITHM_VERSION and plan.transition_identity == current_identity().digest
    return plan


async def test_a_late_workout_between_two_others(async_db):
    await seed_definitions(async_db)
    plan = await _assert_plan_equals_chronological(
        async_db, "w-mid", [W(5, dominant_movement_pattern="squat"), W(20), W(10, dominant_movement_pattern="hinge", sleep_quality=4.0, life_stress_inverse=3.0)], 1,
    )
    # The checkpoint is the 5 h workout's row; the 20 h workout is the one stored row after it.
    assert plan.rows_proven == 1 and len(plan.members) == 1 and len(plan.new_events) == 1


async def test_a_late_workout_just_after_the_baseline(async_db):
    await seed_definitions(async_db)
    plan = await _assert_plan_equals_chronological(async_db, "w-first", [W(10), W(2)], 1)
    ck = await async_db.get(AthleteState, plan.checkpoint_row_id)
    assert ck is not None and ck.event_kind == "baseline"  # nothing but the seeded baseline precedes it


async def test_a_late_benchmark_among_workouts(async_db):
    await seed_definitions(async_db)
    plan = await _assert_plan_equals_chronological(
        async_db, "b-late", [W(4), W(12), W(20), B(8, raw=70.0)], 1,
    )
    assert [e.kind for e in plan.new_events] == ["benchmark"] and len(plan.members) == 2


async def test_a_tail_that_already_holds_a_benchmark_is_replayed_exactly(async_db):
    await seed_definitions(async_db)
    plan = await _assert_plan_equals_chronological(
        async_db, "b-tail", [W(4), B(10, raw=60.0), W(14), W(22), W(8, dominant_movement_pattern="hinge")], 1,
    )
    assert "benchmark" in {e.kind for e in plan.members}  # the replayed tail includes a stored benchmark


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
async def test_a_batch_of_three_late_events_in_every_arrival_order(async_db, order):
    await seed_definitions(async_db)
    late_events = [W(6, dominant_movement_pattern="squat"), B(9, raw=65.0), W(13, sleep_quality=3.0)]
    arrivals = [W(24), *[late_events[i] for i in order]]
    tag = "batch" + "".join(map(str, order))
    plan = await _assert_plan_equals_chronological(async_db, tag, arrivals, 3)
    assert len(plan.new_events) == 3


async def test_a_late_event_sharing_an_existing_events_timestamp_goes_after_it(async_db):
    await seed_definitions(async_db)
    # First W(5) arrives on time; the second W(5) arrives after the head and is late. Arrival order
    # decides the tie, so the oracle receives them in that order too.
    plan = await _assert_plan_equals_chronological(
        async_db, "tie", [W(5, dominant_movement_pattern="squat"), W(15), W(5, dominant_movement_pattern="hinge")], 1,
    )
    # The row at the late event's own timestamp is the checkpoint: it arrived first, so it precedes.
    ck = await async_db.get(AthleteState, plan.checkpoint_row_id)
    assert ck is not None and ck.timestamp == BASE + timedelta(hours=5) and ck.event_kind == "workout"


async def test_the_plan_writes_nothing(async_db):
    await seed_definitions(async_db)
    uid, late, _ = await arrive_all(async_db, "ro@test.com", [W(5), W(20), W(10)])
    counts = lambda: [  # noqa: E731
        select(func.count()).select_from(m).where(m.user_id == uid)
        for m in (AthleteState, WorkoutLogORM, BenchmarkObservation, StateCorrection)
    ]
    before = [(await async_db.execute(q)).scalar_one() for q in counts()]

    await plan_tail_replay(async_db, uid, late)

    assert [(await async_db.execute(q)).scalar_one() for q in counts()] == before
    assert not async_db.new and not async_db.dirty and not async_db.deleted


# ----- a second correction, after an on-time event (a hand-written P3b-3 stand-in) -------------- #

async def _apply_for_test(
    db, uid: int, plan: TailReplayPlan, *, receipt: dict[str, Any] | None = None,
    head_predecessor: int | None = None, events: list[NewEventRef] | None = None,
) -> None:
    """What P3b-3 will do atomically: a receipt, its events, and a correction head at the old
    head's timestamp. Hand-written here so the second correction has real lineage to read.
    The keyword arguments write a deliberately wrong receipt, to prove it is refused."""
    c = StateCorrection(**{
        "user_id": uid, "algorithm_version": plan.algorithm_version,
        "transition_identity": plan.transition_identity,
        "checkpoint_state_id": plan.checkpoint_row_id,
        "head_before_state_id": plan.head_before_row_id, "affected_from": plan.affected_from,
        **(receipt or {}),
    })
    db.add(c)
    await db.flush()
    members = events if events is not None else [
        NewEventRef(e.kind, e.event_id) for e in plan.new_events
    ]
    for ordinal, e in enumerate(members):
        db.add(StateCorrectionEvent(
            correction_id=c.id, ordinal=ordinal,
            workout_log_id=e.event_id if e.kind == "workout" else None,
            observation_id=e.event_id if e.kind == "benchmark" else None,
        ))
    from app.engine.state_bridge import athlete_state_kwargs_from_unified
    db.add(AthleteState(
        user_id=uid, event_kind="correction", source_correction_id=c.id,
        predecessor_state_id=(
            head_predecessor if head_predecessor is not None else plan.head_before_row_id
        ),
        transition_identity=plan.transition_identity,
        **athlete_state_kwargs_from_unified(plan.corrected_head),
    ))
    await db.commit()


@pytest.mark.parametrize("second_late_hours", [3, 7, 11, 16])
async def test_a_second_correction_includes_the_first_exactly_once(async_db, second_late_hours):
    await seed_definitions(async_db)
    # chronological truth: 4, 7(first late), 11, 16, 18 (on time, after the first correction), + the second late event.
    first_late = W(7, dominant_movement_pattern="hinge")
    second_late = W(second_late_hours, sleep_quality=4.0, life_stress_inverse=6.0)
    truth = [W(4), first_late, W(11), W(16), W(18), second_late]
    oracle = await oracle_head(async_db, f"two-oracle-{second_late_hours}@test.com", truth)

    uid = await seeded_athlete(async_db, f"two-subject-{second_late_hours}@test.com")
    for ev in (W(4), W(11), W(16)):
        await arrive_event(async_db, uid, ev)
    r1 = await arrive_event(async_db, uid, first_late)
    assert await is_record_only(async_db, r1)

    plan1 = await plan_tail_replay(async_db, uid, [r1])
    await _apply_for_test(async_db, uid, plan1)
    await arrive_event(async_db, uid, W(18))  # an on-time event on top of the correction head
    r2 = await arrive_event(async_db, uid, second_late)
    assert await is_record_only(async_db, r2)

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
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "rcpt@test.com")
    for ev in (W(4), W(11), W(16)):
        await arrive_event(async_db, uid, ev)
    r1 = await arrive_event(async_db, uid, W(7))
    plan1 = await plan_tail_replay(async_db, uid, [r1])
    await _apply_for_test(async_db, uid, plan1)
    r2 = await arrive_event(async_db, uid, W(9))  # late again, against the correction head

    plan2 = await plan_tail_replay(async_db, uid, [r2])

    # Members are workouts and observations only; the receipt contributes the events it introduced.
    assert {e.kind for e in plan2.members} <= {"workout", "benchmark"}
    assert not any(e.event_id == 1 and e.kind == "correction" for e in plan2.members)  # type: ignore[comparison-overlap]


async def test_an_event_already_introduced_cannot_be_introduced_again(async_db):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "again@test.com")
    for ev in (W(4), W(16)):
        await arrive_event(async_db, uid, ev)
    r1 = await arrive_event(async_db, uid, W(7))
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
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "legacy@test.com", captured=False)
    for ev in (W(10), W(20)):
        await arrive_event(async_db, uid, ev)
    late = await arrive_event(async_db, uid, W(5))
    assert await _code(async_db, uid, [late]) == "untrusted_checkpoint"


async def test_no_state_at_or_before_the_earliest_event(async_db):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "early@test.com")
    for ev in (W(10), W(20)):
        await arrive_event(async_db, uid, ev)
    late = await arrive_event(async_db, uid, W(-60))  # before the seeded baselines
    assert await _code(async_db, uid, [late]) == "no_checkpoint"


async def test_a_repair_row_in_the_tail_is_refused(async_db):
    await seed_definitions(async_db)
    uid, late, _ = await arrive_all(async_db, "repair@test.com", [W(5), W(20), W(10)])
    head = await head_row(async_db, uid)
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
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "decline@test.com")
    await arrive_event(async_db, uid, W(5))
    await benchmark_service.create_observation(
        async_db, uid, BenchmarkObservationCreate(
            benchmark_code="pl_e1rm_squat", raw_value=150.0, source="benchmark_test",
            observed_at=BASE + timedelta(hours=12),
        ),
    )
    await arrive_event(async_db, uid, W(20))
    late = await arrive_event(async_db, uid, W(3))
    assert await _code(async_db, uid, [late]) == "decline_policy_engaged"


async def test_a_late_benchmark_on_the_strength_axis_is_refused(async_db):
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "decline-late@test.com")
    await arrive_event(async_db, uid, W(20))
    await benchmark_service.create_observation(
        async_db, uid, BenchmarkObservationCreate(
            benchmark_code="pl_e1rm_squat", raw_value=150.0, source="benchmark_test",
            observed_at=BASE + timedelta(hours=5),
        ),
    )
    oid = (await async_db.execute(select(BenchmarkObservation.id).where(BenchmarkObservation.user_id == uid))).scalar_one()
    assert await _code(async_db, uid, [NewEventRef("benchmark", oid)]) == "decline_policy_engaged"


async def test_rows_from_other_code_are_refused(async_db):
    await seed_definitions(async_db)
    uid, late, _ = await arrive_all(async_db, "ident@test.com", [W(5), W(20), W(10)])
    async_db.add(EngineTransitionIdentity(digest="f" * 64, components={}))
    await async_db.flush()
    head = await head_row(async_db, uid)
    head.transition_identity = "f" * 64
    await async_db.commit()
    assert await _code(async_db, uid, late) == "identity_mismatch"


async def test_an_inconsistent_registry_is_refused(async_db):
    await seed_definitions(async_db)
    uid, late, _ = await arrive_all(async_db, "registry@test.com", [W(5), W(20), W(10)])
    reg = await async_db.get(EngineTransitionIdentity, current_identity().digest)
    assert reg is not None
    reg.components = {**reg.components, "parameters": "0" * 64}
    await async_db.commit()
    assert await _code(async_db, uid, late) == "registry_inconsistent"


async def test_a_stored_row_the_capture_cannot_reproduce_fails_the_proof(async_db):
    await seed_definitions(async_db)
    uid, late, _ = await arrive_all(async_db, "proof@test.com", [W(5), W(20), W(10)])
    head = await head_row(async_db, uid)
    head.f_nm_central = head.f_nm_central + 0.01  # something changed the row outside the capture
    await async_db.commit()
    with pytest.raises(ReplayUnsupported) as exc:
        await plan_tail_replay(async_db, uid, late)
    assert exc.value.code == "reconstruction_mismatch" and "f_nm_central" in exc.value.detail


async def test_a_broken_predecessor_link_is_refused(async_db):
    await seed_definitions(async_db)
    uid, late, _ = await arrive_all(async_db, "lineage@test.com", [W(5), W(20), W(10)])
    head = await head_row(async_db, uid)
    head.predecessor_state_id = None
    await async_db.commit()
    assert await _code(async_db, uid, late) == "lineage"


async def test_another_athletes_event_is_refused(async_db):
    await seed_definitions(async_db)
    uid, late, _ = await arrive_all(async_db, "own-a@test.com", [W(5), W(20), W(10)])
    other, other_late, _ = await arrive_all(async_db, "own-b@test.com", [W(5), W(20), W(10)])
    assert await _code(async_db, uid, other_late) == "ownership"
    assert other != uid and late


async def test_an_event_that_is_not_before_the_head_is_not_late(async_db):
    await seed_definitions(async_db)
    uid, _, refs = await arrive_all(async_db, "notlate@test.com", [W(5), W(20)])
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
    await seed_definitions(async_db)
    uid, _, refs = await arrive_all(async_db, "applied@test.com", [W(5), W(20)])
    assert await _code(async_db, uid, [refs[0]]) == "not_record_only"


async def test_a_late_event_without_capture_is_refused(async_db):
    await seed_definitions(async_db)
    uid, _, _ = await arrive_all(async_db, "nocap@test.com", [W(5), W(20)])
    row = WorkoutLogORM(
        user_id=uid, session_timestamp=BASE + timedelta(hours=2), modality="Running",
        duration_minutes=30.0, session_rpe=6.0, state_disposition="record_only",
        state_disposition_reason="event_before_current_state", dose_snapshot={},
    )
    async_db.add(row)
    await async_db.commit()
    assert await _code(async_db, uid, [NewEventRef("workout", row.id)]) == "no_capture"


async def test_the_caller_can_bound_the_window_and_the_tail(async_db):
    await seed_definitions(async_db)
    uid, late, _ = await arrive_all(async_db, "bounds@test.com", [W(5), W(30), W(10)])
    assert await _code(async_db, uid, late, max_gap=timedelta(hours=10)) == "window_exceeded"
    assert await _code(async_db, uid, late, max_events=1) == "tail_too_long"
    await plan_tail_replay(async_db, uid, late, max_gap=timedelta(hours=40), max_events=2)


async def test_no_new_events_is_an_error(async_db):
    uid = (await make_user(async_db, "none@test.com")).id
    assert await _code(async_db, uid, []) == "no_new_events"


# ----- review round: initial priors, row-less events, receipt integrity ------------------------- #

async def _onramp(db, uid: int, hours: float, raw: float) -> None:
    await benchmark_service.create_observation(
        db, uid, BenchmarkObservationCreate(
            benchmark_code="aero_test", raw_value=raw, source="benchmark_test",
            observed_at=BASE + timedelta(hours=hours), collection_mode="onboarding_onramp",
        ),
    )


async def test_an_initial_prior_in_the_tail_is_refused_not_reapplied(async_db):
    """Review repro. The live writer applies an initial prior only while the athlete has no
    state. A workout slipped in just before it would make chronological logging skip the prior;
    replaying it anyway produced a head that disagreed (aerobic 580.8 vs 300.1)."""
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "init-high@test.com", seed=False)
    await _onramp(async_db, uid, 10, raw=100.0)  # stages the baseline 1 s before itself, then applies
    await arrive_event(async_db, uid, W(20))
    late = await arrive_event(async_db, uid, W(10 - 0.5 / 3600))  # inside the 1 s between baseline and prior
    assert await is_record_only(async_db, late)

    assert await _code(async_db, uid, [late]) == "initializer_tail"


async def test_an_applied_benchmark_that_wrote_no_row_is_refused(async_db):
    """Review repro (second defect): an applied benchmark with no state row has no place in the
    order. It was accepted once and then broke the next correction; it is refused up front.
    Built directly: the live writers only produce one as an initial prior, which the next
    workout re-anchors out of any replayable window."""
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "rowless@test.com")
    await arrive_event(async_db, uid, W(4))
    donor_ref = await arrive_event(async_db, uid, B(8, raw=60.0))
    await arrive_event(async_db, uid, W(20))
    donor = await async_db.get(BenchmarkObservation, donor_ref.event_id)
    assert donor is not None and donor.replay_input is not None
    at = BASE + timedelta(hours=12)
    async_db.add(BenchmarkObservation(
        user_id=uid, benchmark_definition_id=donor.benchmark_definition_id, observed_at=at,
        raw_value=60.0, validity_status="valid", source="benchmark_test",
        state_disposition="applied",
        replay_input={**donor.replay_input, "observed_at": at.isoformat(), "state_row_written": False},
    ))
    await async_db.commit()
    late = await arrive_event(async_db, uid, W(6))
    assert await is_record_only(async_db, late)

    assert await _code(async_db, uid, [late]) == "event_without_row"


async def _second_late_after(
    db, tag: str, *, after_correction: bool = False, **apply_kw: Any
) -> tuple[int, NewEventRef]:
    """An athlete with one (possibly malformed) applied correction and a second late event.
    ``after_correction`` puts that event after the correction head's time, so the correction
    head is the checkpoint rather than part of the tail."""
    await seed_definitions(db)
    uid = await seeded_athlete(db, f"{tag}@test.com")
    for ev in (W(4), W(16)):
        await arrive_event(db, uid, ev)
    r1 = await arrive_event(db, uid, W(7))
    await _apply_for_test(db, uid, await plan_tail_replay(db, uid, [r1]), **apply_kw)
    if after_correction:
        await arrive_event(db, uid, W(24))
        return uid, await arrive_event(db, uid, W(20))
    return uid, await arrive_event(db, uid, W(9))


async def test_a_receipt_whose_checkpoint_belongs_to_another_athlete_is_refused(async_db):
    other = await seeded_athlete(async_db, "rc-other@test.com")
    foreign = await head_row(async_db, other)
    uid, r2 = await _second_late_after(async_db, "rc-ck", receipt={"checkpoint_state_id": foreign.id})
    assert await _code(async_db, uid, [r2]) == "ownership"


async def test_a_receipt_whose_old_head_belongs_to_another_athlete_is_refused(async_db):
    other = await seeded_athlete(async_db, "rc-other2@test.com")
    foreign = await head_row(async_db, other)
    uid, r2 = await _second_late_after(async_db, "rc-before", receipt={"head_before_state_id": foreign.id})
    assert await _code(async_db, uid, [r2]) == "ownership"


async def test_a_receipt_that_disagrees_with_its_correction_head_is_refused(async_db):
    # The head claims a predecessor other than the old head the receipt names.
    await seed_definitions(async_db)
    probe = await seeded_athlete(async_db, "rc-pred-probe@test.com")
    wrong = (await head_row(async_db, probe)).id
    uid, r2 = await _second_late_after(async_db, "rc-pred", head_predecessor=wrong)
    assert await _code(async_db, uid, [r2]) == "lineage"


async def test_a_receipt_with_the_wrong_start_time_is_refused(async_db):
    uid, r2 = await _second_late_after(
        async_db, "rc-start", receipt={"affected_from": BASE + timedelta(hours=6)}
    )
    assert await _code(async_db, uid, [r2]) == "lineage"


async def test_a_receipt_listing_another_athletes_event_is_refused(async_db):
    await seed_definitions(async_db)
    other_uid, other_late, _ = await arrive_all(async_db, "rc-ev-other@test.com", [W(5), W(20), W(8)])
    uid, r2 = await _second_late_after(async_db, "rc-event", events=other_late)
    assert await _code(async_db, uid, [r2]) == "ownership"


async def test_a_receipts_membership_cannot_be_deleted(async_db):
    """Review repro: deleting a member left the receipt and its correction head standing and
    released the event's uniqueness protection. Neither the member nor the receipt can go."""
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "rc-delete@test.com")
    for ev in (W(4), W(16)):
        await arrive_event(async_db, uid, ev)
    r1 = await arrive_event(async_db, uid, W(7))
    await _apply_for_test(async_db, uid, await plan_tail_replay(async_db, uid, [r1]))

    for table in ("state_correction_events", "state_corrections"):
        with pytest.raises(DBAPIError, match="receipts are append-only"):
            await async_db.execute(text(f"DELETE FROM {table}"))
        await async_db.rollback()
    with pytest.raises(DBAPIError, match="receipts are append-only"):
        await async_db.execute(text("UPDATE state_correction_events SET ordinal = 7"))
    await async_db.rollback()


async def test_a_receipt_is_validated_even_when_its_head_is_only_the_checkpoint(async_db):
    """The predecessor chain only covers rows in the tail. A correction head that is the
    checkpoint is outside it, so its receipt has to be checked on its own."""
    await seed_definitions(async_db)
    probe = await seeded_athlete(async_db, "rc-ck-probe@test.com")
    wrong = (await head_row(async_db, probe)).id
    uid, r2 = await _second_late_after(
        async_db, "rc-ck-head", after_correction=True, head_predecessor=wrong
    )
    assert await _code(async_db, uid, [r2]) == "lineage"
