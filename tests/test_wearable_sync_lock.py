"""One wearable sync per connection, its budgets, and what survives a failure (Wave 4, L1 + C0).

Contention comes in two shapes, and both are tested. Two callers in one process queue on the
per-id process lock. Two processes (two API workers, or the CLI beside a worker) contend on the
Postgres advisory lock; a sync run on its own thread with its own event loop is that second
process here, so the test exercises the real ``pg_advisory_lock`` wait.

The provider is ``FakeOura``. Its hooks run inside the provider call (holding it, relinking
meanwhile, or looking at the database), and use thread-safe gates, so a hook can hold a sync
running on another loop.
"""
import asyncio
import logging
import re
import threading
import time
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from conftest import TEST_DATABASE_URL
from cryptography.fernet import Fernet
from oauth_helpers import athlete
from sqlalchemy import event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.util import await_only

from app.core import crypto
from app.core import db as core_db
from app.core.config import settings
from app.core.db import get_db
from app.integrations.base import NormalizedWellness, TokenBundle
from app.integrations.oura import OuraApiError
from app.models.wearable_connection import WearableConnection
from app.scripts import sync_wearables
from app.services import readiness_service, wearable_service, wearable_sync_lock

WSY1 = wearable_sync_lock.SYNC_LOCK_NAMESPACE
DAYS = [date(2026, 10, 10), date(2026, 10, 11), date(2026, 10, 12)]
LOCK_LOGGER = "perflab.wearable.sync_lock"


class Gate:
    """Holds a provider call until released. Thread-safe, so it can hold another loop's sync."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    async def __call__(self) -> None:
        self.entered.set()
        await asyncio.to_thread(self.release.wait, 30)

    async def reached(self, timeout: float = 10) -> None:
        assert await asyncio.to_thread(self.entered.wait, timeout), "the sync never reached its hold"


class FakeOura:
    def __init__(self) -> None:
        self.refreshes = 0
        self.fetches: list[str] = []
        self.refresh_hooks: list = []
        self.fetch_hooks: list = []
        self.fail_fetch: Exception | None = None

    async def refresh_tokens(self, refresh_token: str) -> TokenBundle:
        self.refreshes += 1
        if self.refresh_hooks:
            await self.refresh_hooks.pop(0)()
        return TokenBundle(access_token="acc-refreshed", refresh_token="ref-refreshed",
                           expires_at=datetime.now(UTC) + timedelta(hours=1))

    async def fetch_daily_wellness(self, access_token, start, end):
        self.fetches.append(access_token)
        if self.fetch_hooks:
            await self.fetch_hooks.pop(0)()
        if self.fail_fetch is not None:
            raise self.fail_fetch
        return [NormalizedWellness(day=d, hrv_ms=60.0, raw={}) for d in DAYS]


@pytest.fixture
def fake(monkeypatch) -> FakeOura:
    monkeypatch.setattr(settings, "APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    crypto._fernet_for.cache_clear()
    adapter = FakeOura()
    monkeypatch.setattr(wearable_service, "_adapter", lambda provider="oura": adapter)
    yield adapter
    crypto._fernet_for.cache_clear()


@pytest.fixture
async def sessions(async_db):  # async_db: an emptied, migrated database
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    await engine.dispose()


@pytest.fixture
async def concurrent_client(sessions):
    """The real app, with a fresh session per request, so two requests can run at once."""
    from app.main import app

    async def _get_db():
        async with sessions() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


# --- helpers -------------------------------------------------------------------------------


async def _user(sessions, email: str) -> int:
    async with sessions() as db:
        uid = (await db.execute(text(
            "INSERT INTO users (email, hashed_password, is_active, created_at) "
            "VALUES (:e, 'x', true, now()) RETURNING id"), {"e": email})).scalar_one()
        await db.commit()
    return uid


async def _link(sessions, uid: int, *, access: str = "acc-1", refresh: str | None = "ref-1",
                expired: bool = False, auth_type: str = "oauth") -> tuple[int, int]:
    expires = (datetime(2020, 1, 1) if expired
               else datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=2))
    if auth_type == "pat":
        expires, refresh = None, None
    async with sessions() as db:
        cid, gen = await wearable_service.upsert_connection(
            db, uid, provider="oura", auth_type=auth_type, access_enc=crypto.encrypt(access),
            refresh_enc=crypto.encrypt(refresh) if refresh else None, expires_at=expires, scope=None)
        await db.commit()
    return cid, gen


async def _row(sessions, *, cid: int | None = None, uid: int | None = None) -> dict | None:
    async with sessions() as db:
        where, val = ("id = :v", cid) if cid is not None else ("user_id = :v", uid)
        row = (await db.execute(text(f"SELECT * FROM wearable_connections WHERE {where}"),
                                {"v": val})).mappings().one_or_none()
        return dict(row) if row else None


async def _wellness_days(sessions, uid: int) -> list[date]:
    async with sessions() as db:
        return list((await db.execute(text(
            "SELECT date FROM wellness_samples WHERE user_id = :u AND source = 'oura' ORDER BY date"),
            {"u": uid})).scalars())


async def _sync(sessions, cid: int, **kw):
    async with sessions() as db:
        return await wearable_service.sync_connection(db, cid, **kw)


def _sync_in_another_process(cid: int, **kw):
    """Run one sync on its own thread and event loop: its own process state, its own
    connections, so it meets other syncs only at the Postgres advisory lock."""
    async def run():
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False,
                                   autoflush=False)
            return await _sync(factory, cid, **kw)
        finally:
            await engine.dispose()
    return asyncio.run(run())


async def _scalar(sql: str, **params):
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as c:
            value = (await c.execute(text(sql), params)).scalar_one()
            await c.commit()
            return value
    finally:
        await engine.dispose()


# pg_locks spans the cluster, and each xdist worker has its own database (conftest), where
# connection ids repeat. Advisory locks are per database, so count only this one's.
_THIS_DATABASE = "database = (SELECT oid FROM pg_database WHERE datname = current_database())"


async def _advisory(cid: int, *, granted: bool) -> int:
    return await _scalar(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND classid = :ns "
        "AND objid = :id AND objsubid = 2 AND granted = :g AND " + _THIS_DATABASE,
        ns=WSY1, id=cid, g=granted)


async def _all_sync_locks_held() -> int:
    return await _scalar(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND classid = :ns "
        "AND objsubid = 2 AND granted AND " + _THIS_DATABASE, ns=WSY1)


async def _lock_is_free(cid: int) -> bool:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as c:
            got = (await c.execute(text("SELECT pg_try_advisory_lock(:ns, :id)"),
                                   {"ns": WSY1, "id": cid})).scalar_one()
            if got:
                await c.execute(text("SELECT pg_advisory_unlock(:ns, :id)"), {"ns": WSY1, "id": cid})
            await c.commit()
            return got
    finally:
        await engine.dispose()


async def _until(predicate, *tasks, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not await predicate():
        for t in tasks:
            if t.done():
                raise AssertionError(f"a sync finished before the expected state: {t.result()!r}")
        if time.monotonic() > deadline:
            raise AssertionError("the expected state never arrived")
        await asyncio.sleep(0.03)


def _process_state_is_clean() -> bool:
    state = wearable_sync_lock._state()
    return (state.capacity._value == wearable_sync_lock.LOCK_CONNECTIONS_PER_PROCESS
            and not state.keyed)


async def _settle() -> None:
    """Let release's done-callbacks (per-id lock, key bookkeeping) run."""
    for _ in range(3):
        await asyncio.sleep(0)


# --- the race the lock exists for ------------------------------------------------------------


async def test_two_processes_with_expired_credentials_refresh_once(fake, sessions):
    uid = await _user(sessions, "w4-race@test.com")
    cid, _ = await _link(sessions, uid, expired=True)
    gate = Gate()
    fake.refresh_hooks = [gate]

    first = asyncio.create_task(_sync(sessions, cid))
    await gate.reached()
    second = asyncio.ensure_future(asyncio.to_thread(_sync_in_another_process, cid))
    await _until(lambda: _waiting(cid), first, second)  # waiting at Postgres, not in-process
    gate.release.set()
    a = await asyncio.wait_for(first, 30)
    b = await asyncio.wait_for(second, 30)

    assert fake.refreshes == 1
    assert fake.fetches == ["acc-refreshed", "acc-refreshed"]  # the second used the first's pair
    assert (a.rows_written, b.rows_written) == (3, 3)
    row = await _row(sessions, cid=cid)
    assert crypto.decrypt(row["access_token_enc"]) == "acc-refreshed"
    assert await _lock_is_free(cid)


async def _waiting(cid: int) -> bool:
    return await _advisory(cid, granted=False) == 1


async def test_app_load_and_check_in_syncs_through_the_route(fake, sessions, concurrent_client, async_db):
    """The live interleaving: two POST /sync at once. The second waits, then re-pulls, so the
    wellness read the check-in makes after its sync returns sees the first sync's rows. (The form
    itself shows manual controls and can save while either sync is pending.)"""
    headers, uid = await athlete(concurrent_client, async_db, "w4-route@test.com")
    await _link(sessions, uid, auth_type="pat")
    gate = Gate()
    fake.fetch_hooks = [gate]

    load = asyncio.create_task(concurrent_client.post("/v1/integrations/oura/sync", headers=headers))
    await gate.reached()
    checkin = asyncio.create_task(concurrent_client.post("/v1/integrations/oura/sync", headers=headers))
    await asyncio.sleep(0.2)
    assert not checkin.done()  # queued behind the app-load sync, not failed
    gate.release.set()
    first = await asyncio.wait_for(load, 30)
    second = await asyncio.wait_for(checkin, 30)
    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert len(fake.fetches) == 2

    listed = await concurrent_client.get("/v1/wellness?limit=8", headers=headers)
    assert sorted(r["date"] for r in listed.json() if r["source"] == "oura") == [d.isoformat() for d in DAYS]
    await _settle()
    assert _process_state_is_clean()


# --- acquisition budget ---------------------------------------------------------------------


async def test_a_sync_queued_too_long_in_process_is_409(fake, sessions, concurrent_client, async_db,
                                                       monkeypatch):
    monkeypatch.setattr(wearable_sync_lock, "ACQUIRE_BUDGET_S", 0.5)
    headers, uid = await athlete(concurrent_client, async_db, "w4-busy@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")
    gate = Gate()
    fake.fetch_hooks = [gate]

    holder = asyncio.create_task(_sync(sessions, cid))
    await gate.reached()
    r = await concurrent_client.post("/v1/integrations/oura/sync", headers=headers)
    assert r.status_code == 409 and "still running" in r.json()["detail"]
    gate.release.set()
    assert (await asyncio.wait_for(holder, 30)).rows_written == 3
    await _settle()
    assert await _lock_is_free(cid) and _process_state_is_clean()


class _WithoutAcquireDeadline:
    """``wearable_sync_lock``'s view of asyncio, minus the acquire backstop (``timeout_at``,
    used only around acquisition). Everything else is the real module."""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    @staticmethod
    def timeout_at(when):
        return asyncio.timeout(None)


def _record_lock_timeouts():
    """Every ``SET LOCAL lock_timeout`` actually sent, in ms, from any engine on any thread."""
    sent: list[int] = []

    def before(_conn, _cursor, statement, *_args):
        m = re.match(r"SET LOCAL lock_timeout = '(\d+)ms'", statement)
        if m:
            sent.append(int(m.group(1)))

    event.listen(Engine, "before_cursor_execute", before)
    return sent, lambda: event.remove(Engine, "before_cursor_execute", before)


async def test_lock_timeout_ends_a_cross_process_wait_server_side(fake, sessions, monkeypatch):
    """Proves the [assumed] claim the plan rested on: lock_timeout, derived from the acquisition
    budget, bounds an advisory-lock wait on the server.

    Only the server may end the wait here. With the asyncio backstop in place, which of the two
    fires first is decided by the database host's clock, not by this code: Postgres times
    lock_timeout on its wall clock, and the local Docker VM's steps back ~2.4 s roughly every
    25-28 s, delaying a lock_timeout pending across a step by up to as much (measured: 7 of 300
    waits ended 2.0-2.45 s late, each at a step). So the backstop is removed and, instead of racing
    it, the test reads the lock_timeout the waiter actually sent and requires it to fit inside the
    budget (strictly shorter, so the server can fire before the deadline). The backstop itself is
    test_the_acquire_deadline_ends_an_advisory_wait_the_server_does_not."""
    budget_ms = 800
    monkeypatch.setattr(wearable_sync_lock, "ACQUIRE_BUDGET_S", budget_ms / 1000)
    monkeypatch.setattr(wearable_sync_lock, "asyncio", _WithoutAcquireDeadline())
    uid = await _user(sessions, "w4-locktimeout@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")
    gate = Gate()
    fake.fetch_hooks = [gate]

    sent, stop_recording = _record_lock_timeouts()
    try:
        holder = asyncio.create_task(_sync(sessions, cid))
        await gate.reached()
        sent.clear()  # only the waiter's acquisition, not the holder's
        waiter = asyncio.ensure_future(asyncio.to_thread(_sync_in_another_process, cid))
        ended, _ = await asyncio.wait([waiter], timeout=30)
        gate.release.set()  # either way: a waiter still blocked gets the lock once the holder ends
        await asyncio.wait_for(holder, 30)
        await asyncio.wait([waiter], timeout=30)  # a failing run leaves no sync running behind it
    finally:
        stop_recording()
    assert ended, "nothing ended the advisory-lock wait server side: lock_timeout did not bound it"
    assert len(sent) == 1, sent
    # Short of the budget, so the server can fire before the deadline would (not read from the
    # module's margin constant, which a regression could change along with it).
    assert 0 < sent[0] < budget_ms, (
        f"lock_timeout {sent[0]}ms does not fit inside the {budget_ms}ms acquisition budget")
    with pytest.raises(wearable_sync_lock.SyncBusy) as busy:
        waiter.result()
    cause = busy.value.__cause__
    assert isinstance(cause, DBAPIError) and wearable_sync_lock._is_lock_timeout(cause)
    assert await _lock_is_free(cid)


async def test_the_acquire_deadline_ends_an_advisory_wait_the_server_does_not(
        fake, sessions, monkeypatch):
    """The backstop: when lock_timeout would not end the wait (here: set 20 s past the budget),
    the asyncio deadline cancels the in-flight pg_advisory_lock, the caller gets SyncBusy caused
    by TimeoutError, and the cancelled acquisition leaves no lock behind (its connection is
    invalidated). Deterministic: the server cannot fire within the 0.8 s budget."""
    monkeypatch.setattr(wearable_sync_lock, "ACQUIRE_BUDGET_S", 0.8)
    monkeypatch.setattr(wearable_sync_lock, "_SERVER_FIRST_MS", -20_000)
    uid = await _user(sessions, "w4-backstop@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")
    gate = Gate()
    fake.fetch_hooks = [gate]

    sent, stop_recording = _record_lock_timeouts()
    try:
        holder = asyncio.create_task(_sync(sessions, cid))
        await gate.reached()
        sent.clear()  # only the waiter's acquisition, not the holder's
        started = time.monotonic()
        with pytest.raises(wearable_sync_lock.SyncBusy) as busy:
            await asyncio.wait_for(asyncio.to_thread(_sync_in_another_process, cid), 30)
        elapsed = time.monotonic() - started
        # While the holder still holds the lock: the cancelled pg_advisory_lock left no waiter
        # on the server (one that stayed would be granted the lock the moment the holder ends).
        orphaned_waiters = await _advisory(cid, granted=False)
    finally:
        gate.release.set()
        stop_recording()
    await asyncio.wait_for(holder, 30)
    assert sent, "the waiter never reached the advisory-lock query, so no in-flight wait was cancelled"
    assert isinstance(busy.value.__cause__, TimeoutError), repr(busy.value.__cause__)
    assert elapsed < 10, elapsed  # failure bound: the deadline ended it, not the 20.6 s lock_timeout
    assert orphaned_waiters == 0
    assert await _lock_is_free(cid)


# --- release, on every way out --------------------------------------------------------------


@pytest.mark.parametrize("ending", ["success", "provider_error", "cancelled", "work_deadline"])
async def test_the_lock_is_released_however_the_sync_ends(fake, sessions, monkeypatch, ending):
    uid = await _user(sessions, f"w4-release-{ending}@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")
    gate = Gate()
    if ending == "provider_error":
        fake.fail_fetch = OuraApiError("upstream 500")
    if ending in ("cancelled", "work_deadline"):
        fake.fetch_hooks = [gate]
    if ending == "work_deadline":
        monkeypatch.setattr(wearable_service, "SYNC_WORK_BUDGET_S", 0.3)

    task = asyncio.create_task(_sync(sessions, cid))
    if ending == "cancelled":
        await gate.reached()
        assert await _advisory(cid, granted=True) == 1
        task.cancel()
    outcome = await asyncio.gather(task, return_exceptions=True)
    gate.release.set()

    expected = {"success": wearable_service.SyncOutcome, "provider_error": OuraApiError,
                "cancelled": asyncio.CancelledError, "work_deadline": wearable_service.SyncTimedOut}
    assert isinstance(outcome[0], expected[ending]), outcome
    await _settle()
    assert await _lock_is_free(cid)
    assert _process_state_is_clean()


async def test_an_unconfirmed_unlock_invalidates_the_connection_and_frees_the_lock(
        fake, sessions, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger=LOCK_LOGGER)
    uid = await _user(sessions, "w4-invalidate@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")

    async def unlock_the_wrong_key():
        # Once the lock is held: the unlock then names a key this session never took, so
        # pg_advisory_unlock returns false. (Not a zero cleanup budget, which would also cut
        # the work session's rollback short and quarantine it.)
        monkeypatch.setattr(wearable_sync_lock, "SYNC_LOCK_NAMESPACE", WSY1 + 1)

    fake.fetch_hooks = [unlock_the_wrong_key]
    assert (await _sync(sessions, cid)).rows_written == 3
    assert any("release not confirmed" in r.getMessage() for r in caplog.records)
    assert any("released by invalidation" in r.getMessage() for r in caplog.records)
    assert not any("quarantined" in r.getMessage() for r in caplog.records)  # cleanup was normal
    assert await _lock_is_free(cid)


async def test_acquiring_restores_lock_timeout_and_leaves_no_open_transaction(async_db):
    default = await _scalar("SHOW lock_timeout")
    async with wearable_sync_lock.hold_sync_lock(make_url(TEST_DATABASE_URL), 4242) as lock:
        assert not lock.conn.in_transaction()
        shown = (await lock.conn.execute(text("SHOW lock_timeout"))).scalar_one()
        await lock.conn.commit()
        assert shown == default
        assert not await _lock_is_free(4242)
    assert await _lock_is_free(4242)


# --- no transaction is open while the provider is called ------------------------------------


@pytest.mark.parametrize("kind", ["pat", "oauth_fresh", "oauth_refresh"])
async def test_no_transaction_is_open_during_provider_calls(fake, sessions, kind):
    uid = await _user(sessions, f"w4-notxn-{kind}@test.com")
    if kind == "pat":
        cid, _ = await _link(sessions, uid, auth_type="pat")
    else:
        cid, _ = await _link(sessions, uid, expired=(kind == "oauth_refresh"))
    seen: list[tuple[str, int]] = []

    def probe(where: str):
        async def check():
            seen.append((where, await _scalar(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()")))
        return check

    fake.refresh_hooks = [probe("refresh")]
    fake.fetch_hooks = [probe("fetch")]
    # The caller arrives the way the route does: having read in an open transaction.
    async with sessions() as db:
        conn = await wearable_service.get_connection(db, uid, "oura")
        assert db.in_transaction()
        await wearable_service.sync_connection(db, conn.id)

    expected = ["refresh", "fetch"] if kind == "oauth_refresh" else ["fetch"]
    assert [w for w, _ in seen] == expected
    assert all(n == 0 for _, n in seen), seen


async def test_a_caller_transaction_that_wrote_is_refused_not_committed(fake, sessions):
    uid = await _user(sessions, "w4-step0@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")
    async with sessions() as db:
        await db.execute(text("UPDATE wearable_connections SET scope = 'raw-write' WHERE id = :i"),
                         {"i": cid})
        with pytest.raises(RuntimeError, match="has not written"):
            await wearable_service.sync_connection(db, cid)
        await db.rollback()
    async with sessions() as db:
        conn = await db.get(WearableConnection, cid)
        conn.scope = "orm-change"
        with pytest.raises(RuntimeError, match="no pending changes"):
            await wearable_service.sync_connection(db, cid)
        await db.rollback()
    assert (await _row(sessions, cid=cid))["scope"] is None
    assert fake.fetches == []


# --- capacity -------------------------------------------------------------------------------


async def test_distinct_connections_beyond_capacity_wait_without_a_connection(fake, sessions):
    cids = []
    for i in range(3):
        uid = await _user(sessions, f"w4-cap-{i}@test.com")
        cids.append((await _link(sessions, uid, auth_type="pat"))[0])
    gates = [Gate(), Gate(), Gate()]
    fake.fetch_hooks = list(gates)

    tasks = [asyncio.create_task(_sync(sessions, c)) for c in cids]
    await gates[0].reached()
    await gates[1].reached()
    await asyncio.sleep(0.2)
    assert await _all_sync_locks_held() == 2  # LOCK_CONNECTIONS_PER_PROCESS
    assert not gates[2].entered.is_set()
    assert sum(await asyncio.gather(*(_advisory(c, granted=False) for c in cids))) == 0
    gates[0].release.set()
    await gates[2].reached()
    gates[1].release.set()
    gates[2].release.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), 30)
    assert [r.rows_written for r in results] == [3, 3, 3]
    await _settle()
    assert _process_state_is_clean()


async def test_waiting_for_capacity_counts_against_the_acquisition_budget(fake, sessions, monkeypatch):
    monkeypatch.setattr(wearable_sync_lock, "ACQUIRE_BUDGET_S", 0.5)
    cids = []
    for i in range(3):
        uid = await _user(sessions, f"w4-capbudget-{i}@test.com")
        cids.append((await _link(sessions, uid, auth_type="pat"))[0])
    gates = [Gate(), Gate()]
    fake.fetch_hooks = list(gates)
    holders = [asyncio.create_task(_sync(sessions, c)) for c in cids[:2]]
    await gates[0].reached()
    await gates[1].reached()
    with pytest.raises(wearable_sync_lock.SyncBusy):
        await _sync(sessions, cids[2])
    for g in gates:
        g.release.set()
    await asyncio.wait_for(asyncio.gather(*holders), 30)
    await _settle()
    assert _process_state_is_clean()


async def test_a_small_work_pool_still_makes_progress_under_contention(fake, sessions):
    uid = await _user(sessions, "w4-smallpool@test.com")
    cid, _ = await _link(sessions, uid, expired=True)

    async def slow():
        await asyncio.sleep(0.05)

    fake.fetch_hooks = [slow, slow, slow, slow]
    small = create_async_engine(TEST_DATABASE_URL, pool_size=2, max_overflow=0, pool_timeout=20)
    try:
        factory = sessionmaker(small, class_=AsyncSession, expire_on_commit=False, autoflush=False)
        inproc = [asyncio.create_task(_sync(factory, cid)) for _ in range(4)]
        other = asyncio.ensure_future(asyncio.to_thread(_sync_in_another_process, cid))
        results = await asyncio.wait_for(asyncio.gather(*inproc, other), 60)
    finally:
        await small.dispose()
    assert [r.rows_written for r in results] == [3] * 5
    assert fake.refreshes == 1
    assert await _lock_is_free(cid)


# --- refresh persistence is protected -------------------------------------------------------


@pytest.mark.parametrize("interruption", ["work_deadline", "cancelled"])
async def test_new_credentials_are_saved_even_if_the_sync_is_interrupted_before_their_commit(
        fake, sessions, monkeypatch, caplog, interruption):
    caplog.set_level(logging.DEBUG, logger=LOCK_LOGGER)
    uid = await _user(sessions, f"w4-protect-{interruption}@test.com")
    cid, _ = await _link(sessions, uid, expired=True)
    real = wearable_service._save_refreshed_credentials
    saved_at: list[float] = []
    started = asyncio.Event()

    async def slow_save(conn, snap, tokens):
        started.set()
        await asyncio.sleep(0.4)
        ok = await real(conn, snap, tokens)
        saved_at.append(time.time())
        return ok

    monkeypatch.setattr(wearable_service, "_save_refreshed_credentials", slow_save)
    if interruption == "work_deadline":
        monkeypatch.setattr(wearable_service, "SYNC_WORK_BUDGET_S", 0.2)
    task = asyncio.create_task(_sync(sessions, cid))
    if interruption == "cancelled":
        await asyncio.wait_for(started.wait(), 10)
        task.cancel()
    outcome = (await asyncio.gather(task, return_exceptions=True))[0]

    assert isinstance(outcome, wearable_service.SyncTimedOut if interruption == "work_deadline"
                      else asyncio.CancelledError), outcome
    row = await _row(sessions, cid=cid)
    assert crypto.decrypt(row["access_token_enc"]) == "acc-refreshed"  # Oura's pair was kept
    assert row["last_sync_at"] is None and fake.fetches == []
    released = [r.created for r in caplog.records if r.getMessage() == f"sync lock {cid} released"]
    assert saved_at and released and saved_at[0] <= released[0]  # saved, then unlocked
    assert await _lock_is_free(cid)


async def test_a_protected_save_that_cannot_finish_is_aborted_before_the_unlock(
        fake, sessions, monkeypatch, caplog):
    monkeypatch.setattr(wearable_sync_lock, "CLEANUP_BUDGET_S", 0.3)
    monkeypatch.setattr(wearable_service, "SYNC_WORK_BUDGET_S", 0.2)
    caplog.set_level(logging.DEBUG, logger=LOCK_LOGGER)
    uid = await _user(sessions, "w4-abort@test.com")
    cid, _ = await _link(sessions, uid, expired=True)
    real = wearable_service._save_refreshed_credentials

    async def stuck_save(conn, snap, tokens):
        await asyncio.sleep(2)
        return await real(conn, snap, tokens)

    monkeypatch.setattr(wearable_service, "_save_refreshed_credentials", stuck_save)
    with pytest.raises(wearable_service.SyncTimedOut):
        await _sync(sessions, cid)
    assert await _lock_is_free(cid)
    assert any("outcome is unknown" in r.getMessage() for r in caplog.records)
    await asyncio.sleep(2.2)  # past when the stuck save would have written
    assert crypto.decrypt((await _row(sessions, cid=cid))["access_token_enc"]) == "acc-1"


# --- one frozen (id, generation) per attempt ------------------------------------------------


async def _relink(sessions, uid: int, access: str) -> None:
    await _link(sessions, uid, access=access, refresh=f"ref-{access}")


async def test_a_relink_between_the_snapshot_and_the_refresh_keeps_the_new_credentials(fake, sessions):
    uid = await _user(sessions, "w4-relink-refresh@test.com")
    cid, _ = await _link(sessions, uid, expired=True)

    async def relink():
        await _relink(sessions, uid, "acc-second")

    fake.refresh_hooks = [relink]
    with pytest.raises(wearable_service.ConnectionReplaced):
        await _sync(sessions, cid)
    row = await _row(sessions, cid=cid)
    assert row["generation"] == 2 and crypto.decrypt(row["access_token_enc"]) == "acc-second"
    assert row["last_sync_at"] is None and fake.fetches == []


async def test_a_relink_during_the_fetch_keeps_rows_but_not_the_watermark(fake, sessions):
    uid = await _user(sessions, "w4-relink-fetch@test.com")
    cid, _ = await _link(sessions, uid)

    async def relink():
        await _relink(sessions, uid, "acc-second")

    fake.fetch_hooks = [relink]
    with pytest.raises(wearable_service.ConnectionReplaced, match="3 wellness rows were kept"):
        await _sync(sessions, cid)
    row = await _row(sessions, cid=cid)
    assert row["generation"] == 2 and crypto.decrypt(row["access_token_enc"]) == "acc-second"
    assert row["last_sync_at"] is None  # the old attempt did not advance the new link's watermark
    assert await _wellness_days(sessions, uid) == DAYS  # partial writes stay: the athlete's data


# --- disconnect and reconnect while a sync waits --------------------------------------------


async def _holder_and_waiter(fake, sessions, cid: int):
    gate = Gate()
    fake.fetch_hooks = [gate]
    holder = asyncio.create_task(_sync(sessions, cid))
    await gate.reached()
    waiter = asyncio.ensure_future(asyncio.to_thread(_sync_in_another_process, cid))
    await _until(lambda: _waiting(cid), holder, waiter)
    return gate, holder, waiter


async def test_a_disconnect_while_a_sync_waits_ends_it_without_provider_calls(fake, sessions):
    uid = await _user(sessions, "w4-q-disconnect@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")
    gate, holder, waiter = await _holder_and_waiter(fake, sessions, cid)
    async with sessions() as db:
        assert await wearable_service.disconnect(db, uid, "oura")
    gate.release.set()
    held, waited = await asyncio.wait_for(asyncio.gather(holder, waiter, return_exceptions=True), 30)
    assert isinstance(held, wearable_service.ConnectionReplaced)
    assert isinstance(waited, wearable_service.ConnectionReplaced) and "removed before" in str(waited)
    assert fake.fetches == ["acc-1"]  # only the holder's
    assert await _wellness_days(sessions, uid) == DAYS  # the holder's rows stay
    assert await _lock_is_free(cid)


async def test_a_recreated_connection_is_never_synced_under_the_old_ones_lock(fake, sessions):
    uid = await _user(sessions, "w4-q-recreate@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")
    gate, holder, waiter = await _holder_and_waiter(fake, sessions, cid)
    async with sessions() as db:
        await wearable_service.disconnect(db, uid, "oura")
    new_cid, new_gen = await _link(sessions, uid, access="acc-new", auth_type="pat")
    assert new_cid != cid and new_gen == 1
    gate.release.set()
    held, waited = await asyncio.wait_for(asyncio.gather(holder, waiter, return_exceptions=True), 30)
    assert isinstance(held, wearable_service.ConnectionReplaced)
    assert isinstance(waited, wearable_service.ConnectionReplaced)
    assert "acc-new" not in fake.fetches
    assert (await _row(sessions, cid=new_cid))["last_sync_at"] is None


async def test_an_in_place_relink_while_a_sync_waits_is_synced_with_the_new_credentials(fake, sessions):
    uid = await _user(sessions, "w4-q-relink@test.com")
    cid, _ = await _link(sessions, uid)
    gate, holder, waiter = await _holder_and_waiter(fake, sessions, cid)
    await _relink(sessions, uid, "acc-2")
    gate.release.set()
    held, waited = await asyncio.wait_for(asyncio.gather(holder, waiter, return_exceptions=True), 30)
    assert isinstance(held, wearable_service.ConnectionReplaced)  # its generation was replaced
    assert isinstance(waited, wearable_service.SyncOutcome), waited
    assert fake.fetches == ["acc-1", "acc-2"]
    row = await _row(sessions, cid=cid)
    assert row["generation"] == 2 and row["last_sync_at"] is not None


# --- the batch and the CLI ------------------------------------------------------------------


async def _two_connections_one_failing(sessions, monkeypatch):
    uid_a = await _user(sessions, "w4-batch-a@test.com")
    await _link(sessions, uid_a, auth_type="pat")
    uid_b = await _user(sessions, "w4-batch-b@test.com")
    await _link(sessions, uid_b, auth_type="pat")
    real = readiness_service.upsert_wellness_sample

    async def fails_on_a_second_day(db, user_id, payload, **kwargs):
        if user_id == uid_a and payload.date == DAYS[1]:
            await db.execute(text("SELECT * FROM w4_no_such_table"))  # a real database error
        return await real(db, user_id, payload, **kwargs)

    monkeypatch.setattr(readiness_service, "upsert_wellness_sample", fails_on_a_second_day)
    return uid_a, uid_b


async def test_a_database_failure_on_one_connection_does_not_stop_the_next(fake, sessions, monkeypatch):
    uid_a, uid_b = await _two_connections_one_failing(sessions, monkeypatch)
    async with sessions() as db:
        reports = await wearable_service.sync_all(db)

    a, b = reports
    assert (a.key, a.ok, a.rows_confirmed, a.in_doubt_day) == (f"user{uid_a}:oura", False, 1, DAYS[1])
    assert a.error.startswith("ProgrammingError")
    assert (b.key, b.ok, b.rows_confirmed) == (f"user{uid_b}:oura", True, 3)
    assert await _wellness_days(sessions, uid_a) == DAYS[:1]  # the confirmed row is really there
    assert await _wellness_days(sessions, uid_b) == DAYS
    assert (await _row(sessions, uid=uid_a))["last_sync_at"] is None
    assert (await _row(sessions, uid=uid_b))["last_sync_at"] is not None


async def test_the_cli_exits_nonzero_and_reports_partial_success(fake, sessions, monkeypatch, capsys):
    await _two_connections_one_failing(sessions, monkeypatch)
    monkeypatch.setattr(core_db, "AsyncSessionLocal", sessions)
    assert await sync_wearables._run(days=7, user_id=None) == 1
    out = capsys.readouterr().out
    assert "FAILED after 1 saved rows" in out and "may or may not have been saved" in out
    assert "4 rows saved across 2 connections, 1 failed" in out


async def test_the_cli_exits_zero_when_every_connection_syncs(fake, sessions, monkeypatch, capsys):
    uid = await _user(sessions, "w4-cli-ok@test.com")
    await _link(sessions, uid, auth_type="pat")
    monkeypatch.setattr(core_db, "AsyncSessionLocal", sessions)
    assert await sync_wearables._run(days=7, user_id=None) == 0
    assert "3 rows saved across 1 connections, 0 failed" in capsys.readouterr().out


def test_main_returns_the_run_status(monkeypatch):
    async def failed(**_kw):
        return 1

    monkeypatch.setattr(sync_wearables, "_run", failed)
    assert sync_wearables.main(["--days", "3"]) == 1


# --- review round 1: bounded cleanup, the commit boundary, one reporting contract ------------


async def test_a_stalled_rollback_is_bounded_and_the_lock_and_slot_are_still_released(
        fake, sessions, monkeypatch, caplog):
    """After the work deadline, a rollback that never returns must not hold the lock, its slot,
    or the response: it is cancelled and the work session's connection invalidated."""
    monkeypatch.setattr(wearable_sync_lock, "CLEANUP_BUDGET_S", 0.3)
    monkeypatch.setattr(wearable_service, "SYNC_WORK_BUDGET_S", 0.3)
    caplog.set_level(logging.INFO)
    uid = await _user(sessions, "w4-stalled-rollback@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")
    gate = Gate()
    fake.fetch_hooks = [gate]
    invalidated: list[bool] = []
    try:
        async with sessions() as db:
            real_invalidate = db.invalidate

            async def stalled_rollback():
                await asyncio.sleep(60)

            async def counted_invalidate():
                invalidated.append(True)
                await real_invalidate()

            db.rollback = stalled_rollback
            db.invalidate = counted_invalidate
            started = time.monotonic()
            with pytest.raises(wearable_service.SyncTimedOut):
                await asyncio.wait_for(wearable_service.sync_connection(db, cid), 10)
            elapsed = time.monotonic() - started
    finally:
        gate.release.set()
    # work budget + rollback budget + its cancel wait + the invalidation, with slack
    assert elapsed < 0.3 + 3 * 0.3 + 1.5, elapsed
    assert invalidated == [True]
    assert any("rollback did not finish" in r.getMessage() for r in caplog.records)
    await _settle()
    assert await _lock_is_free(cid) and _process_state_is_clean()


async def test_a_day_whose_commit_returned_is_confirmed_even_if_its_read_back_fails(
        fake, sessions, monkeypatch):
    uid = await _user(sessions, "w4-ack@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")
    real_upsert = readiness_service.upsert_wellness_sample
    real_select = readiness_service.select
    armed = {"on": False}

    def select_failing_when_armed(*args, **kwargs):
        if armed["on"]:
            armed["on"] = False
            raise RuntimeError("read-back failed")
        return real_select(*args, **kwargs)

    async def upsert(db, user_id, payload, **kwargs):
        # The helper's only select() is its read-back, which runs after its COMMIT.
        armed["on"] = payload.date == DAYS[1]
        try:
            return await real_upsert(db, user_id, payload, **kwargs)
        finally:
            armed["on"] = False

    monkeypatch.setattr(readiness_service, "select", select_failing_when_armed)
    monkeypatch.setattr(readiness_service, "upsert_wellness_sample", upsert)
    async with sessions() as db:
        report = await wearable_service.sync_one(db, cid, "k")
    assert (report.ok, report.rows_confirmed, report.in_doubt_day) == (False, 2, None)
    assert "read-back failed" in report.error
    assert await _wellness_days(sessions, uid) == DAYS[:2]


async def test_the_cli_reports_partial_success_for_one_user_too(fake, sessions, monkeypatch, capsys):
    uid_a, _ = await _two_connections_one_failing(sessions, monkeypatch)
    monkeypatch.setattr(core_db, "AsyncSessionLocal", sessions)
    assert await sync_wearables._run(days=7, user_id=uid_a) == 1
    out = capsys.readouterr().out
    assert f"user{uid_a}:oura: FAILED after 1 saved rows" in out
    assert "may or may not have been saved" in out
    assert "1 rows saved across 1 connections, 1 failed" in out
    assert await sync_wearables._run(days=7, user_id=999_999) == 1
    assert "no connection" in capsys.readouterr().out


# --- review round 2: a rollback that outlives its cancellation, repeated cancellation --------


async def test_a_rollback_still_running_after_its_cancel_quarantines_the_session(
        fake, sessions, monkeypatch):
    """A rollback that keeps unwinding after it is cancelled still owns the session: it is not
    invalidated underneath it, and the batch never reuses it, while the lock and slot are freed."""
    monkeypatch.setattr(wearable_sync_lock, "CLEANUP_BUDGET_S", 0.2)
    monkeypatch.setattr(wearable_service, "SYNC_WORK_BUDGET_S", 0.3)
    uid_a = await _user(sessions, "w4-quarantine-a@test.com")
    cid_a, _ = await _link(sessions, uid_a, auth_type="pat")
    uid_b = await _user(sessions, "w4-quarantine-b@test.com")
    await _link(sessions, uid_b, auth_type="pat")
    gate, unwound = Gate(), asyncio.Event()
    fake.fetch_hooks = [gate]
    invalidated: list[bool] = []
    try:
        async with sessions() as db:
            async def rollback_that_outlives_its_cancel():
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    await unwound.wait()  # a driver operation still unwinding
                    raise

            async def counted_invalidate():
                invalidated.append(True)

            db.rollback = rollback_that_outlives_its_cancel
            db.invalidate = counted_invalidate
            reports = await asyncio.wait_for(wearable_service.sync_all(db), 10)
            assert core_db.quarantine_reason(db)
    finally:
        gate.release.set()
        unwound.set()

    a, b = reports
    assert not a.ok and a.error.startswith("SyncTimedOut")
    assert not b.ok and b.error.startswith("SessionQuarantined") and b.rows_confirmed == 0
    assert invalidated == []                       # never raced the rollback that owns the session
    assert len(fake.fetches) == 1                  # the second connection was not attempted on it
    assert await _wellness_days(sessions, uid_b) == []
    await _settle()
    assert await _lock_is_free(cid_a) and _process_state_is_clean()


async def test_repeated_cancellation_waits_for_cleanup_before_the_unlock_and_slot_release(
        fake, sessions):
    uid = await _user(sessions, "w4-recancel@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")
    gate, in_rollback = Gate(), asyncio.Event()
    fake.fetch_hooks = [gate]
    seen: dict[str, object] = {}
    try:
        async with sessions() as db:
            real_rollback = db.rollback

            async def slow_rollback():
                in_rollback.set()
                await asyncio.sleep(0.5)
                await real_rollback()
                state = wearable_sync_lock._state()
                seen["lock_held"] = await _advisory(cid, granted=True) == 1
                seen["capacity_available"] = state.capacity._value
                seen["rollback_finished"] = True

            db.rollback = slow_rollback
            task = asyncio.create_task(wearable_service.sync_connection(db, cid))
            await gate.reached()
            task.cancel()                          # 1: ends the attempt, cleanup starts
            await asyncio.wait_for(in_rollback.wait(), 5)
            task.cancel()                          # 2 and 3: while cleanup runs
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.wait({task}, timeout=10)
            assert task.cancelled()
            # Cleanup ended before this caller got control back, under the lock and its slot.
            assert seen == {"lock_held": True,
                            "capacity_available": wearable_sync_lock.LOCK_CONNECTIONS_PER_PROCESS - 1,
                            "rollback_finished": True}
            # And release had finished by then too: no settling needed.
            assert _process_state_is_clean()
    finally:
        gate.release.set()
    assert await _lock_is_free(cid)


# --- review round 3: the session's owner, while a real rollback still holds it ----------------


class HeldRollback:
    """Holds a real SQLAlchemy rollback in progress (inside its ``after_rollback`` event, so the
    session's transaction is mid-state-change) until released, resisting cancellation, as a
    stuck driver operation would. Armed once; only the next rollback is held."""

    def __init__(self) -> None:
        self.armed = False
        self.release = asyncio.Event()
        self.held = asyncio.Event()
        self.ended = asyncio.Event()
        self.session: AsyncSession | None = None
        self.closes_after_end: list[bool] = []  # one entry per close() of the held session

    def listener(self, _session) -> None:
        if self.armed:
            self.armed = False
            await_only(self._hold())

    async def _hold(self) -> None:
        self.held.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            await self.release.wait()
            raise
        finally:
            self.ended.set()


@pytest.fixture
def held_rollback(monkeypatch):
    monkeypatch.setattr(wearable_sync_lock, "CLEANUP_BUDGET_S", 0.2)
    monkeypatch.setattr(wearable_service, "SYNC_WORK_BUDGET_S", 0.3)
    held = HeldRollback()
    real_close = AsyncSession.close

    async def recorded_close(self):
        if self is held.session:
            held.closes_after_end.append(held.ended.is_set())
        return await real_close(self)

    monkeypatch.setattr(AsyncSession, "close", recorded_close)
    event.listen(Session, "after_rollback", held.listener)
    yield held
    held.release.set()
    event.remove(Session, "after_rollback", held.listener)


def _first_day_hangs_for(uid: int, held: HeldRollback, monkeypatch) -> None:
    """``uid``'s first day opens a transaction on the work session, then hangs past the deadline."""
    real = readiness_service.upsert_wellness_sample

    async def hangs(db, user_id, payload, **kwargs):
        if user_id == uid:
            await db.execute(text("SELECT 1"))
            held.session, held.armed = db, True
            await asyncio.sleep(60)
        return await real(db, user_id, payload, **kwargs)

    monkeypatch.setattr(readiness_service, "upsert_wellness_sample", hangs)


async def _deferred_close_done(held: HeldRollback) -> None:
    held.release.set()
    await asyncio.wait_for(held.ended.wait(), 5)
    await _until(lambda: asyncio.sleep(0, result=not core_db._held), timeout=5)
    # The owner closed it exactly once, and only after the rollback holding it had ended.
    assert held.closes_after_end == [True]


@pytest.fixture
async def owner_client(sessions, monkeypatch):
    """The real app with the real ``get_db`` (no override), its factory pointed at the test DB."""
    from app.main import app

    monkeypatch.setattr(core_db, "AsyncSessionLocal", sessions)
    app.dependency_overrides.pop(get_db, None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_the_route_still_answers_504_when_its_session_is_quarantined(
        fake, sessions, held_rollback, owner_client, async_db, monkeypatch, caplog):
    headers, uid = await athlete(owner_client, async_db, "w4-owner-route@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")
    _first_day_hangs_for(uid, held_rollback, monkeypatch)

    r = await asyncio.wait_for(owner_client.post("/v1/integrations/oura/sync", headers=headers), 10)
    assert r.status_code == 504, r.text
    assert "took too long" in r.json()["detail"]
    assert held_rollback.held.is_set() and not held_rollback.ended.is_set()  # still holding it
    assert core_db._held                                                     # close deferred

    await _deferred_close_done(held_rollback)
    assert not any("closing a quarantined session failed" in r.getMessage() for r in caplog.records)
    await _settle()
    assert await _lock_is_free(cid) and _process_state_is_clean()


async def test_the_cli_prints_its_reports_when_its_session_is_quarantined(
        fake, sessions, held_rollback, monkeypatch, capsys, caplog):
    uid_a = await _user(sessions, "w4-owner-cli-a@test.com")
    await _link(sessions, uid_a, auth_type="pat")
    uid_b = await _user(sessions, "w4-owner-cli-b@test.com")
    await _link(sessions, uid_b, auth_type="pat")
    _first_day_hangs_for(uid_a, held_rollback, monkeypatch)
    monkeypatch.setattr(core_db, "AsyncSessionLocal", sessions)

    assert await asyncio.wait_for(sync_wearables._run(days=7, user_id=None), 10) == 1
    out = capsys.readouterr().out
    assert f"user{uid_a}:oura: FAILED after 0 saved rows (SyncTimedOut" in out
    assert f"user{uid_b}:oura: FAILED after 0 saved rows (SessionQuarantined" in out
    assert "done: 0 rows saved across 2 connections, 2 failed" in out
    assert not held_rollback.ended.is_set()

    await _deferred_close_done(held_rollback)
    assert not any("closing a quarantined session failed" in r.getMessage() for r in caplog.records)
    assert await _wellness_days(sessions, uid_b) == []


async def test_an_ordinary_session_is_closed_by_its_owner_at_once(sessions):
    async with core_db.session_scope(sessions) as db:
        await db.execute(text("SELECT 1"))
        assert db.in_transaction()
    assert not db.in_transaction() and not core_db._held
