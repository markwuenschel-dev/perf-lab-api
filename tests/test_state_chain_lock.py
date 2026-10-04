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
import textwrap
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
#
# What the guard proves, per function that writes the chain (discovered below):
#   1. it AWAITS lock_athlete_chain (an un-awaited call acquires nothing);
#   2. the awaited lock comes, lexically, before its first predecessor read AND its first
#      write; and
#   3. no commit()/rollback() appears between the lock and either of them (that would end
#      the transaction, and with it the lock, before the read or the write).
# It is a static, lexical check of each function body — calls made through other functions
# are covered by those functions' own entries, not traced here.


def _call_name(c: ast.Call) -> str:
    return c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", "")


def _session_adds(fn: ast.AST) -> list[int]:
    """Lines of `<session>.add(...)` — `db.add`, `self.session.add` — never `set.add`."""
    return [
        c.lineno
        for c in ast.walk(fn)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Attribute)
        and c.func.attr == "add"
        and ast.unparse(c.func.value).split(".")[-1] in ("db", "session")
    ]


def _core_state_writes(fn: ast.AST) -> list[int]:
    """Lines of Core writes to the table: insert/pg_insert/update(AthleteState), or raw SQL
    naming athlete_states in an INSERT/UPDATE."""
    lines = [
        c.lineno
        for c in ast.walk(fn)
        if isinstance(c, ast.Call)
        and _call_name(c) in ("insert", "pg_insert", "update")
        and any(ast.unparse(a).split(".")[-1] == "AthleteState" for a in c.args)
    ]
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql = node.value.lower()
            if "athlete_states" in sql and ("insert" in sql or "update" in sql):
                lines.append(node.lineno)
    return lines


def _write_lines(fn: ast.AST) -> list[int]:
    calls = {_call_name(c) for c in ast.walk(fn) if isinstance(c, ast.Call)}
    builds = "AthleteState" in calls or "_build_baseline_vector" in calls
    lines = _core_state_writes(fn)
    if builds:
        lines += _session_adds(fn)
    lines += [c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call) and _call_name(c) == "stage_baseline_state"]
    return lines


def _lines_of(fn: ast.AST, names: tuple[str, ...]) -> list[int]:
    return [c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call) and _call_name(c) in names]


def _violations(fn: ast.AST) -> list[str]:
    """Every way `fn` breaks the lock protocol (empty for a non-writer or a correct writer)."""
    writes = _write_lines(fn)
    if not writes:
        return []
    awaited = [
        n.value.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Await)
        and isinstance(n.value, ast.Call)
        and _call_name(n.value) == "lock_athlete_chain"
    ]
    if not awaited:
        bare = _lines_of(fn, ("lock_athlete_chain",))
        return ["lock_athlete_chain is called but never awaited" if bare else "no chain lock"]
    lock = min(awaited)
    out: list[str] = []
    ends = _lines_of(fn, ("commit", "rollback"))
    for label, targets in (
        ("predecessor read", _lines_of(fn, _PREDECESSOR_READS)),
        ("write", writes),
    ):
        if fn.name == "stage_baseline_state" and label == "predecessor read":
            continue  # it IS the staged baseline; its only "read" is its own name
        if not targets:
            continue
        first = min(targets)
        if first < lock:
            out.append(f"{label} at line {first} precedes the lock at line {lock}")
        elif any(lock < e < first for e in ends):
            out.append(f"commit/rollback between the lock (line {lock}) and the first {label} (line {first})")
    return out


def _chain_writers() -> dict[str, ast.AST]:
    found: dict[str, ast.AST] = {}
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _write_lines(node):
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
def test_every_chain_writer_follows_the_lock_protocol(name):
    assert _violations(_chain_writers()[name]) == []


def _fn(src: str) -> ast.AST:
    node = ast.parse(src).body[0]
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    return node


_CASES = {
    "correct": (
        """
        async def w(db, uid):
            await lock_athlete_chain(db, uid)
            s = await repo.get_latest_state(uid)
            db.add(AthleteState(user_id=uid))
        """,
        None,
    ),
    "unawaited": (  # an un-awaited lock acquires nothing
        """
        async def w(db, uid):
            lock_athlete_chain(db, uid)
            s = await repo.get_latest_state(uid)
            db.add(AthleteState(user_id=uid))
        """,
        "never awaited",
    ),
    "commit-between": (  # a commit ends the transaction, and the lock, before the read
        """
        async def w(db, uid):
            await lock_athlete_chain(db, uid)
            await db.commit()
            s = await repo.get_latest_state(uid)
            db.add(AthleteState(user_id=uid))
        """,
        "commit/rollback between",
    ),
    "read-first": (
        """
        async def w(db, uid):
            s = await repo.get_latest_state(uid)
            await lock_athlete_chain(db, uid)
            db.add(AthleteState(user_id=uid))
        """,
        "precedes the lock",
    ),
    "core-insert": (  # a Core insert with no lock is still a writer
        """
        async def w(db, uid):
            await db.execute(insert(AthleteState).values(user_id=uid))
        """,
        "no chain lock",
    ),
    "raw-sql": (  # raw SQL with no lock is still a writer
        """
        async def w(db, uid):
            await db.execute(text("INSERT INTO athlete_states (user_id) VALUES (1)"))
        """,
        "no chain lock",
    ),
}


@pytest.mark.parametrize("case", list(_CASES))
def test_the_guard_catches_what_it_claims_to(case):
    src, expected = _CASES[case]
    found = _violations(_fn(textwrap.dedent(src)))
    if expected is None:
        assert found == []
    else:
        assert any(expected in v for v in found), found



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


async def _seed_squat_benchmark(factory: async_sessionmaker[AsyncSession]) -> None:
    """A measured benchmark mapped to max_strength: a `benchmark_test` observation of it is a
    bidirectional update that appends a capacity state row (test_capacity_corruption_hotfix)."""
    from app.models.benchmark_definition import BenchmarkDefinition
    from app.models.observation_mapping import ObservationMapping

    async with factory() as db:
        definition = BenchmarkDefinition(
            code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting",
            metric_type="load", unit="kg", better_direction="higher",
            observation_weight=1.0, standardization_rules={"floor": 40.0, "cap": 250.0},
        )
        db.add(definition)
        await db.flush()
        db.add(ObservationMapping(
            benchmark_definition_id=definition.id, target_vector="capacity",
            target_key="max_strength", mapping_type="residual", coefficient=1.0, intercept=0.0,
        ))
        await db.commit()


@pytest.mark.asyncio
async def test_a_state_changing_benchmark_vs_a_workout_equals_the_sequential_result(factory):
    """Review gap: the full benchmark transition — staging, the state row it appends, the
    commit — under contention, not just the baseline helper."""
    from app.schemas.benchmarks import BenchmarkObservationCreate
    from app.services import benchmark_service

    await _seed_squat_benchmark(factory)
    uid = await _user(factory, "f2-bench@test.com")
    ref = await _user(factory, "f2-bench-ref@test.com")
    workout = _log(T0)
    body = BenchmarkObservationCreate(
        benchmark_code="pl_e1rm_squat", raw_value=150.0, source="benchmark_test",
        observed_at=(T0 + timedelta(hours=2)).replace(tzinfo=None),
    )

    async def contender():
        async with factory() as db:
            await benchmark_service.create_observation(db, uid, body)

    async def holder_write(db):
        await state_service.process_new_workout(db, uid, workout)

    await _contend(factory, uid, contender, holder_write)

    async with factory() as db:
        await state_service.process_new_workout(db, ref, workout)
    async with factory() as db:
        await benchmark_service.create_observation(db, ref, body)
    # baseline + workout + the observation's capacity row, built on the workout's state.
    assert await _state_rows(factory, uid) == await _state_rows(factory, ref) == 3
    assert await _head(factory, uid) == await _head(factory, ref)


async def _corrupted_athlete(factory: async_sessionmaker[AsyncSession], email: str) -> int:
    """An athlete whose latest max_strength is below an earlier watermark — what the repair
    script corrects."""
    from app.engine.state_bridge import athlete_state_kwargs_from_unified

    uid = await _user(factory, email)
    async with factory() as db:
        base = await state_service.initialize_athlete_state(db, uid)
    lowered = base.model_copy(deep=True)
    lowered.capacity_x.max_strength = base.capacity_x.max_strength - 10.0
    # Now, not the future: the repair stamps its correction at now, and it must sort after.
    lowered.timestamp = datetime.now(UTC).replace(tzinfo=None)
    async with factory() as db:
        db.add(AthleteState(user_id=uid, **athlete_state_kwargs_from_unified(lowered)))
        await db.commit()
    return uid


@pytest.mark.asyncio
async def test_concurrent_repairs_in_opposite_orders_do_not_deadlock(factory, monkeypatch):
    """Review repro: --apply holds every athlete's chain lock until one final commit, and the
    athletes came from an unordered SELECT DISTINCT. Two repairs enumerating them in opposite
    orders each took one lock and waited for the other's: SQLSTATE 40P01. The barrier below
    makes each repair take its first lock before either proceeds, so an unsorted acquisition
    order deadlocks deterministically."""
    from app.scripts import repair_capacity_corruption as rc

    a = await _corrupted_athlete(factory, "f2-repair-a@test.com")
    b = await _corrupted_athlete(factory, "f2-repair-b@test.com")
    orders = iter([[a, b], [b, a]])

    async def opposite_orders(_db):
        return next(orders)

    real_lock = rc.lock_athlete_chain
    first_locked: set[int] = set()
    both = asyncio.Event()
    acquired: dict[int, list[int]] = {}  # per repair session: athlete ids in acquisition order

    async def barrier_lock(db, uid):
        await real_lock(db, uid)
        acquired.setdefault(id(db), []).append(uid)
        if id(db) not in first_locked:
            first_locked.add(id(db))
            if len(first_locked) == 2:
                both.set()
            try:
                await asyncio.wait_for(both.wait(), timeout=1.0)
            except TimeoutError:
                pass  # the other repair is (correctly) blocked behind this one's first lock

    monkeypatch.setattr(rc, "_affected_user_ids", opposite_orders)
    monkeypatch.setattr(rc, "lock_athlete_chain", barrier_lock)

    async def run():
        async with factory() as db:
            return await rc.repair_with_db(db, apply=True)

    results = await asyncio.wait_for(asyncio.gather(run(), run(), return_exceptions=True), 30)
    assert not [r for r in results if isinstance(r, BaseException)], results
    # The direct proof, independent of the barrier's scheduling: each repair acquired its
    # locks in ascending athlete-id order, whatever order the athletes were enumerated in.
    assert len(acquired) == 2
    assert all(order == sorted(order) == sorted({a, b}) for order in acquired.values()), acquired
    # Each athlete corrected exactly once across the two repairs.
    assert sum(r.corrected for r in results if not isinstance(r, BaseException)) == 2
