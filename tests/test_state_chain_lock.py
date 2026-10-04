"""F2 — one per-athlete lock serializes every writer of the athlete_states chain.

Every state writer reads the latest state, evolves it, and appends. Two writers reading the
same predecessor both append from it and one update is lost. The protocol
(``app/services/state_chain_lock.py``): every writer takes ``lock_athlete_chain`` before it
reads the predecessor, in the transaction that inserts the row.

* The architecture test finds every function that appends or stages a state row and checks it
  locks before any predecessor read.
* The race tests are deterministic, not timing-lucky: a HOLDER session takes the lock and
  keeps it while the contending writer starts. The contender must still be blocked after a
  pause, and once the holder's own write commits the contender must build on it. Without the
  lock the contender would not block, and these tests fail.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.athlete_state import AthleteState
from app.models.user import User
from app.schemas.workouts import WorkoutLog
from app.services import state_service
from app.services.state_chain_lock import lock_athlete_chain

APP = Path(__file__).resolve().parents[1] / "app"

#: Calls that read the predecessor state (or decide from it). The lock must come first.
_PREDECESSOR_READS = (
    "get_latest_state",
    "load_current_state",
    "_current_or_staged_baseline",
    "has_state",
    "_state_row_count",
    "stage_baseline_state",
)


# ── architecture: every chain writer locks before reading its predecessor ─────────────


def _is_chain_writer(fn: ast.AST) -> bool:
    """Appends or stages a state row: adds an AthleteState / a built baseline, or stages one."""
    src_calls = [
        c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", "")
        for c in ast.walk(fn)
        if isinstance(c, ast.Call)
    ]
    # Only a SESSION add stages a row (`db.add(...)`, `self.session.add(...)`), never `set.add`.
    adds = any(
        isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute)
        and c.func.attr == "add"
        and ast.unparse(c.func.value).split(".")[-1] in ("db", "session")
        for c in ast.walk(fn)
    )
    builds = "AthleteState" in src_calls or "_build_baseline_vector" in src_calls
    return (adds and builds) or "stage_baseline_state" in src_calls


def _first_line(fn: ast.AST, names: tuple[str, ...]) -> int | None:
    lines = [
        c.lineno
        for c in ast.walk(fn)
        if isinstance(c, ast.Call)
        and (c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", "")) in names
    ]
    return min(lines) if lines else None


def _chain_writers() -> dict[str, ast.AST]:
    found: dict[str, ast.AST] = {}
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_chain_writer(node):
                found[f"{path.relative_to(APP.parent).as_posix()}:{node.name}"] = node
    return found


def test_the_writer_scan_finds_the_known_writers():
    """Guards the guard: if the scan stops matching, every check below passes vacuously."""
    names = {k.rsplit(":", 1)[1] for k in _chain_writers()}
    assert {
        "process_new_workout",
        "stage_baseline_state",
        "initialize_athlete_state",
        "stage_observation",
        "_current_or_staged_baseline",
        "onboard_athlete",
        "repair_with_db",
    } <= names


@pytest.mark.parametrize("name", sorted(_chain_writers()))
def test_every_chain_writer_locks_before_reading_its_predecessor(name):
    fn = _chain_writers()[name]
    lock_line = _first_line(fn, ("lock_athlete_chain",))
    assert lock_line is not None, f"{name} appends/stages a state row without the chain lock"
    read_line = _first_line(fn, _PREDECESSOR_READS)
    if read_line is not None and fn.name != "stage_baseline_state":
        assert lock_line < read_line, f"{name} reads its predecessor before taking the lock"


# ── deterministic races on two real connections ───────────────────────────────────────


def _log(at: datetime, minutes: float = 40.0) -> WorkoutLog:
    return WorkoutLog(timestamp=at, modality="Running", duration_minutes=minutes, session_rpe=7.0)


async def _user(factory: async_sessionmaker[AsyncSession], email: str) -> int:
    async with factory() as db:
        user = User(email=email, hashed_password="h", is_active=True)
        db.add(user)
        await db.commit()
        return user.id


async def _state_rows(factory: async_sessionmaker[AsyncSession], uid: int) -> int:
    async with factory() as db:
        return int((await db.execute(
            select(func.count()).select_from(AthleteState).where(AthleteState.user_id == uid)
        )).scalar_one())


async def _head(factory: async_sessionmaker[AsyncSession], uid: int) -> dict:
    async with factory() as db:
        state = await state_service.load_current_state(db, uid)
        assert state is not None
        return state.model_dump(mode="json", exclude={"timestamp"})


async def _contend(factory, uid: int, contender, holder_write) -> None:
    """Holder takes the lock; the contender must block until the holder's write commits."""
    async with factory() as holder:
        await lock_athlete_chain(holder, uid)
        task = asyncio.create_task(contender())
        await asyncio.sleep(0.5)
        assert not task.done(), "contender did not wait for the state-chain lock"
        await holder_write(holder)  # takes the lock again (re-entrant) and commits → release
    await asyncio.wait_for(task, timeout=10)


@pytest.fixture
def factory(async_db: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_db.bind, expire_on_commit=False, autoflush=False)


T0 = (datetime.now(UTC) - timedelta(hours=6)).replace(microsecond=0)


@pytest.mark.asyncio
async def test_workout_vs_workout_builds_on_each_other(factory):
    uid = await _user(factory, "f2-ww@test.com")
    ref = await _user(factory, "f2-ww-ref@test.com")
    a, b = _log(T0), _log(T0 + timedelta(hours=1), minutes=60.0)

    async def contender():
        async with factory() as db:
            await state_service.process_new_workout(db, uid, b)

    async def holder_write(db):
        await state_service.process_new_workout(db, uid, a)

    await _contend(factory, uid, contender, holder_write)

    # Sequential reference: A then B for an identical athlete.
    async with factory() as db:
        await state_service.process_new_workout(db, ref, a)
    async with factory() as db:
        await state_service.process_new_workout(db, ref, b)
    assert await _state_rows(factory, uid) == 3  # baseline + A + B, nothing lost
    assert await _head(factory, uid) == await _head(factory, ref)


@pytest.mark.asyncio
async def test_init_vs_workout_creates_exactly_one_baseline(factory):
    uid = await _user(factory, "f2-iw@test.com")

    async def contender():
        async with factory() as db:
            await state_service.initialize_athlete_state(db, uid)

    async def holder_write(db):
        await state_service.process_new_workout(db, uid, _log(T0))

    await _contend(factory, uid, contender, holder_write)
    # The workout staged the baseline + its own row; initialization found state and added none.
    assert await _state_rows(factory, uid) == 2


@pytest.mark.asyncio
async def test_onboarding_vs_workout_creates_exactly_one_baseline(factory):
    from app.api.v1.onboard import onboard_athlete
    from app.models.user import AthleteProfile
    from app.schemas.onboarding import OnboardRequest

    uid = await _user(factory, "f2-ow@test.com")
    async with factory() as db:
        db.add(AthleteProfile(user_id=uid))
        await db.commit()

    async def contender():
        async with factory() as db:
            user = await db.get(User, uid)
            await onboard_athlete(OnboardRequest(experience_level="intermediate"), db, user)

    async def holder_write(db):
        await state_service.process_new_workout(db, uid, _log(T0))

    await _contend(factory, uid, contender, holder_write)
    assert await _state_rows(factory, uid) == 2


@pytest.mark.asyncio
async def test_benchmark_vs_workout_reads_the_workout_as_its_predecessor(factory, monkeypatch):
    """The observation's staging (predecessor read included) waits for the workout. Its read is
    pinned by recording what stage_observation saw as the current state."""
    from app.services import benchmark_service

    uid = await _user(factory, "f2-bw@test.com")
    seen: list[int] = []
    real = state_service.load_current_state

    async def recording(db, user_id):
        seen.append(await _state_rows(factory, user_id))
        return await real(db, user_id)

    monkeypatch.setattr(benchmark_service.state_service, "load_current_state", recording)

    async def contender():
        async with factory() as db:
            await benchmark_service._current_or_staged_baseline(db, uid)
            await db.commit()

    async def holder_write(db):
        await state_service.process_new_workout(db, uid, _log(T0))

    await _contend(factory, uid, contender, holder_write)
    # When the contender first read its predecessor, the workout's rows were already durable.
    assert seen and seen[0] == 2
    assert await _state_rows(factory, uid) == 2  # no second baseline staged
