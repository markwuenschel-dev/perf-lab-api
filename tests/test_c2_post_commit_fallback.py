"""INT-15 2D — a committed workout never fails on an unreadable head it did not write.

After ``process_new_workout`` commits, its e1RM extraction re-reads the athlete's head. The
commit released the chain lock, so that head may be a CONCURRENT writer's row. If it cannot be
decoded strictly, the request keeps the valid result it had already materialized for its own
workout. Pinned on every branch that reaches the re-read — ordinary, record-only, and
replay-corrected — together with what must NOT happen: no failed save, no legacy
reconstruction, no reseeding, no extra row. "Unreadable" covers both a row with no payload and
a row whose payload decodes but whose own columns cannot form a state (review P1).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import TEST_DATABASE_URL
from replay_support import W, arrive_event, head_row, seed_definitions, seeded_athlete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.engine import state_bridge, state_loading
from app.engine.state_bridge import default_engine_state_dict
from app.engine.state_loading import unified_from_athlete_row_strict
from app.models.athlete_state import AthleteState
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.schemas.workouts import WorkoutLog, WorkoutSetEntry
from app.services import state_service

pytestmark = pytest.mark.asyncio

FAR_FUTURE = datetime(2099, 1, 1, 12, 0)

# Each way the concurrent head can be unreadable: (engine_state, row-field overrides).
UNREADABLE = [
    pytest.param((None, {}), id="no-payload"),
    pytest.param((default_engine_state_dict(), {"habit_strength": 2.0}), id="habit-above-one"),
    pytest.param((default_engine_state_dict(), {"s_struct_signal": -1.0}), id="negative-signal"),
    pytest.param((default_engine_state_dict(), {"skill_state": "oops"}), id="skill-state-string"),
]


async def _insert_unreadable_head_concurrently(
    user_id: int, payload: Any, fields: dict[str, Any]
) -> None:
    """Another writer's row, committed on its own connection: newest by (timestamp, id), and
    unreadable."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with AsyncSession(engine) as other:
            other.add(AthleteState(
                user_id=user_id, timestamp=FAR_FUTURE, engine_state=payload,
                c_met_aerobic=300.0, c_nm_force=1400.0, c_struct=100.0, b_met_anaerobic=15000.0,
                f_met_systemic=5.0, f_nm_peripheral=5.0, f_nm_central=5.0, f_struct_damage=5.0,
                **{"s_struct_signal": 0.0, "habit_strength": 0.5, "skill_state": {}, **fields},
            ))
            await other.commit()
    finally:
        await engine.dispose()


@pytest.fixture(params=UNREADABLE)
def concurrent_unreadable_head(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Make the post-commit e1RM path run, and land a concurrent unreadable head inside it.

    The set materialization is replaced so the workout carries one e1RM spec without needing a
    lift catalog; the extraction then commits the concurrent row and reports one write, which
    is exactly what sends ``process_new_workout`` to re-read the head.
    """

    async def _sets(db: Any, user_id: int, log: WorkoutLog) -> Any:
        return log.model_copy(update={"sets": []}), [], [{"code": "x", "raw_value": 1.0}], None

    async def _extract(db: Any, user_id: int, *_a: Any, **_k: Any) -> int:
        await _insert_unreadable_head_concurrently(user_id, *request.param)
        return 1

    monkeypatch.setattr(state_service, "_apply_sets_to_log", _sets)
    monkeypatch.setattr(state_service, "_extract_e1rm_observations", _extract)

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("legacy reconstruction reached from a committed workout")

    for mod in (state_bridge, state_loading):
        for name in ("capacity_from_legacy", "fatigue_from_legacy", "tissue_from_legacy"):
            monkeypatch.setattr(mod, name, _boom)
    monkeypatch.setattr(state_service, "reconstruct_legacy_state_for_display", _boom)
    monkeypatch.setattr(state_service, "initialize_athlete_state", _boom)


def _with_a_set(ev: Any) -> WorkoutLog:
    return WorkoutLog(
        timestamp=ev.ts.replace(tzinfo=UTC), **(ev.extra or {}),
        sets=[WorkoutSetEntry(free_text_name="squat", load_kg=100.0, reps=5)],
    )


async def _rows(db: AsyncSession, uid: int) -> int:
    q = select(func.count()).select_from(AthleteState).where(AthleteState.user_id == uid)
    return int((await db.execute(q)).scalar_one())


async def _log_and_check(
    db: AsyncSession, uid: int, ev: Any, caplog: pytest.LogCaptureFixture, expected: AthleteState
) -> Any:
    rows_before = await _rows(db, uid)
    expected_state = unified_from_athlete_row_strict(expected)
    caplog.set_level(logging.ERROR, logger=state_service.logger.name)

    resp = await state_service.process_new_workout(
        db, uid, _with_a_set(ev), received_at=datetime.now(UTC)
    )

    # Not a failed save: the workout is durable and the response is this workout's result.
    assert await db.get(WorkoutLogORM, resp.workout_log_id) is not None
    assert resp.capacity_x == expected_state.capacity_x
    assert resp.fatigue_f == expected_state.fatigue_f
    # Reported loudly, not swallowed.
    assert "post_commit_state_unreadable site=post_e1rm_reread" in caplog.text
    return resp, rows_before


async def test_ordinary_workout_keeps_its_own_new_row(
    async_db: AsyncSession, concurrent_unreadable_head: None, caplog: pytest.LogCaptureFixture
) -> None:
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "pc-ordinary@test.com")
    await arrive_event(async_db, uid, W(5))
    rows_before = await _rows(async_db, uid)
    caplog.set_level(logging.ERROR, logger=state_service.logger.name)

    resp = await state_service.process_new_workout(
        async_db, uid, _with_a_set(W(10)), received_at=datetime.now(UTC)
    )

    own = (await async_db.execute(
        select(AthleteState).where(AthleteState.source_workout_log_id == resp.workout_log_id)
    )).scalar_one()
    assert resp.state_disposition == "applied"
    assert resp.capacity_x == unified_from_athlete_row_strict(own).capacity_x
    assert resp.fatigue_f == unified_from_athlete_row_strict(own).fatigue_f
    assert "post_commit_state_unreadable site=post_e1rm_reread" in caplog.text
    # Its own row and the concurrent one; nothing reseeded.
    assert await _rows(async_db, uid) == rows_before + 2


async def test_record_only_workout_keeps_the_unchanged_head(
    async_db: AsyncSession, concurrent_unreadable_head: None, caplog: pytest.LogCaptureFixture
) -> None:
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "pc-record-only@test.com")
    await arrive_event(async_db, uid, W(20))
    head = await head_row(async_db, uid)

    resp, rows_before = await _log_and_check(async_db, uid, W(10), caplog, head)

    assert (resp.state_disposition, resp.state_disposition_reason) == (
        "record_only", "event_before_current_state",
    )
    assert await _rows(async_db, uid) == rows_before + 1  # only the concurrent row


async def test_replay_corrected_workout_keeps_the_correction_head(
    async_db: AsyncSession,
    concurrent_unreadable_head: None,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "APPLY_LATE_EVENTS", True)
    await seed_definitions(async_db)
    uid = await seeded_athlete(async_db, "pc-replay@test.com")
    for ev in (W(5), W(20)):
        await arrive_event(async_db, uid, ev)
    rows_before = await _rows(async_db, uid)
    caplog.set_level(logging.ERROR, logger=state_service.logger.name)

    resp = await state_service.process_new_workout(
        async_db, uid, _with_a_set(W(10)), received_at=datetime.now(UTC)
    )

    correction = (await async_db.execute(
        select(AthleteState).where(
            AthleteState.user_id == uid, AthleteState.event_kind == "correction"
        )
    )).scalar_one()
    assert resp.state_disposition == "applied"
    assert resp.capacity_x == unified_from_athlete_row_strict(correction).capacity_x
    assert resp.fatigue_f == unified_from_athlete_row_strict(correction).fatigue_f
    assert "post_commit_state_unreadable site=post_e1rm_reread" in caplog.text
    # The correction row and the concurrent one; nothing reseeded.
    assert await _rows(async_db, uid) == rows_before + 2


@pytest.mark.parametrize(
    "fields",
    [{"engine_state": {"version": 99}},
     {"habit_strength": 2.0}, {"s_struct_signal": -1.0}, {"skill_state": "oops"}],
    ids=["future-version", "habit-above-one", "negative-signal", "skill-state-string"],
)
def test_materialize_committed_never_raises_and_never_reconstructs(fields: dict[str, Any]) -> None:
    from types import SimpleNamespace

    fallback = object()
    row = SimpleNamespace(**{
        "id": 1, "timestamp": FAR_FUTURE, "engine_state": default_engine_state_dict(),
        "s_struct_signal": 0.0, "habit_strength": 0.5, "skill_state": {}, **fields,
    })
    assert state_service._materialize_committed(row, fallback=fallback, site="t") is fallback  # type: ignore[arg-type]
