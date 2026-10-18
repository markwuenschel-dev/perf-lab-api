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
import threading
import time
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from conftest import TEST_DATABASE_URL
from cryptography.fernet import Fernet
from oauth_helpers import athlete
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core import crypto
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


async def _advisory(cid: int, *, granted: bool) -> int:
    return await _scalar(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND classid = :ns "
        "AND objid = :id AND objsubid = 2 AND granted = :g", ns=WSY1, id=cid, g=granted)


async def _all_sync_locks_held() -> int:
    return await _scalar(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND classid = :ns "
        "AND objsubid = 2 AND granted", ns=WSY1)


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


async def test_lock_timeout_ends_a_cross_process_wait_server_side(fake, sessions, monkeypatch):
    """Proves the [assumed] claim the plan rested on: lock_timeout bounds an advisory-lock wait."""
    monkeypatch.setattr(wearable_sync_lock, "ACQUIRE_BUDGET_S", 0.8)
    uid = await _user(sessions, "w4-locktimeout@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")
    gate = Gate()
    fake.fetch_hooks = [gate]

    holder = asyncio.create_task(_sync(sessions, cid))
    await gate.reached()
    with pytest.raises(wearable_sync_lock.SyncBusy) as busy:
        await asyncio.wait_for(asyncio.to_thread(_sync_in_another_process, cid), 30)
    cause = busy.value.__cause__
    assert isinstance(cause, DBAPIError) and wearable_sync_lock._is_lock_timeout(cause)
    gate.release.set()
    await asyncio.wait_for(holder, 30)
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
    monkeypatch.setattr(wearable_sync_lock, "CLEANUP_BUDGET_S", 0)  # the unlock cannot complete
    caplog.set_level(logging.DEBUG, logger=LOCK_LOGGER)
    uid = await _user(sessions, "w4-invalidate@test.com")
    cid, _ = await _link(sessions, uid, auth_type="pat")

    assert (await _sync(sessions, cid)).rows_written == 3
    assert any("release not confirmed" in r.getMessage() for r in caplog.records)
    assert any("released by invalidation" in r.getMessage() for r in caplog.records)
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

    async def fails_on_a_second_day(db, user_id, payload):
        if user_id == uid_a and payload.date == DAYS[1]:
            await db.execute(text("SELECT * FROM w4_no_such_table"))  # a real database error
        return await real(db, user_id, payload)

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
    monkeypatch.setattr(sync_wearables, "AsyncSessionLocal", sessions)
    assert await sync_wearables._run(days=7, user_id=None) == 1
    out = capsys.readouterr().out
    assert "FAILED after 1 saved rows" in out and "may or may not have been saved" in out
    assert "4 rows saved across 2 connections, 1 failed" in out


async def test_the_cli_exits_zero_when_every_connection_syncs(fake, sessions, monkeypatch, capsys):
    uid = await _user(sessions, "w4-cli-ok@test.com")
    await _link(sessions, uid, auth_type="pat")
    monkeypatch.setattr(sync_wearables, "AsyncSessionLocal", sessions)
    assert await sync_wearables._run(days=7, user_id=None) == 0
    assert "3 rows saved across 1 connections, 0 failed" in capsys.readouterr().out


def test_main_returns_the_run_status(monkeypatch):
    async def failed(**_kw):
        return 1

    monkeypatch.setattr(sync_wearables, "_run", failed)
    assert sync_wearables.main(["--days", "3"]) == 1
