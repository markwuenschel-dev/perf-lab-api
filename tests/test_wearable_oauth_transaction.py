"""The wearable OAuth transaction, end to end through the routes and the database (P6b, ADR-0075).

Most cases go through the real routes (``http_client``). The races use independent sessions on
their own connections, because one shared session cannot run two requests at once. The provider
is ``FakeOuraOAuth``, which records what each exchange was given and can be held mid-exchange.
"""
import asyncio
import hashlib
import logging
import uuid

import httpx
import pytest
from conftest import TEST_DATABASE_URL
from cryptography.fernet import Fernet
from oauth_helpers import (
    CHALLENGE,
    VERIFIER,
    FakeOuraOAuth,
    athlete,
    complete,
    connect,
    pkce,
    start,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.api.v1.integrations_oura import BAD_CHALLENGE, CALLBACK_RETIRED, RELOAD_TO_CONNECT
from app.core import crypto
from app.core.config import settings
from app.integrations.base import ProviderRejected
from app.services import wearable_oauth, wearable_service

pytestmark = pytest.mark.asyncio

CLOCK = "timezone('UTC', clock_timestamp())"


@pytest.fixture
def fake(monkeypatch) -> FakeOuraOAuth:
    monkeypatch.setattr(settings, "APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "WEB_APP_URL", "https://perflab.test")
    crypto._fernet_for.cache_clear()
    adapter = FakeOuraOAuth()
    monkeypatch.setattr(wearable_service, "_adapter", lambda provider="oura": adapter)
    yield adapter
    crypto._fernet_for.cache_clear()


@pytest.fixture
async def sessions():
    """A factory of independent sessions on the test database."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def clock_behind(monkeypatch):
    """Make each statement's clock sample read ``seconds`` behind the database clock (negative:
    ahead), as after a step. No clock is changed."""
    def behind(seconds: float) -> None:
        monkeypatch.setattr(wearable_oauth, "_NOW",
                            f"(SELECT {CLOCK} - make_interval(secs => {float(seconds)}) AS t) c")
    return behind


@pytest.fixture
async def pinned_clock(async_db, monkeypatch) -> str:
    """Freeze the transaction's clock at one instant read now; returns it as a SQL literal, for
    seeding rows at exact offsets from it. A boundary test then cannot be moved by a real step
    between the seed and the check."""
    at = (await async_db.execute(text(f"SELECT {CLOCK}"))).scalar_one()
    await async_db.commit()
    literal = f"TIMESTAMP '{at.isoformat(sep=' ')}'"
    monkeypatch.setattr(wearable_oauth, "_NOW", f"(SELECT {literal} AS t) c")
    return literal


async def _until_held(entered: asyncio.Event, task: asyncio.Task) -> None:
    """Wait for ``task`` to block on its hold. If it finished or stalled instead, say how."""
    waiter = asyncio.ensure_future(entered.wait())
    await asyncio.wait({waiter, task}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
    if waiter.done():
        return
    waiter.cancel()
    if task.done():
        raise AssertionError(f"the worker finished before reaching its hold: {task.result()!r}")
    task.cancel()
    raise AssertionError("the worker neither reached its hold nor finished within 10s")


async def _until_waiting_on_a_lock(db, task: asyncio.Task, timeout: float = 10) -> None:
    """Wait until some backend of this test database is blocked on a lock (the worker)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        waiting = (await db.execute(text(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE datname = current_database() AND wait_event_type = 'Lock'"))).scalar_one()
        await db.commit()
        if waiting:
            return
        if task.done():
            raise AssertionError(f"the worker finished without waiting for the row: {task.result()!r}")
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("the worker never waited for the row lock")
        await asyncio.sleep(0.03)


def _h(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


async def _attempt(db, state: str) -> dict:
    row = (await db.execute(text(
        "SELECT * FROM oauth_authorizations WHERE state_hash = :h"), {"h": _h(state)})).mappings().one()
    await db.commit()
    return dict(row)


async def _connection(db, uid: int) -> dict | None:
    row = (await db.execute(text(
        "SELECT * FROM wearable_connections WHERE user_id = :u"), {"u": uid})).mappings().one_or_none()
    await db.commit()
    return dict(row) if row else None


async def _seed_attempt(db, uid: int, state: str, *, status: str = "pending", age_s: float = 0,
                        ttl_s: float = 600, claimed_age_s: float | None = None,
                        at: str = CLOCK) -> None:
    """An attempt with chosen times, as offsets before ``at`` (inputs are immutable once written,
    so it is inserted so)."""
    await db.execute(text(
        "INSERT INTO oauth_authorizations (user_id, provider, state_hash, code_challenge, "
        "redirect_uri, status, created_at, expires_at, claimed_at) VALUES (:u, 'oura', :h, :c, "
        f"'https://perflab.test/settings/oura-callback', :s, {at} - make_interval(secs => :age), "
        f"{at} - make_interval(secs => :age) + make_interval(secs => :ttl), "
        f"CASE WHEN CAST(:cl AS float8) IS NULL THEN NULL ELSE {at} - make_interval(secs => CAST(:cl AS float8)) END)"
    ), {"u": uid, "h": _h(state), "c": CHALLENGE, "s": status, "age": age_s, "ttl": ttl_s,
        "cl": claimed_age_s})
    await db.commit()


def _detail(resp) -> dict:
    return resp.json()["detail"]


# --- /authorize ------------------------------------------------------------------


async def test_authorize_records_a_pending_attempt_and_stores_only_the_state_hash(
        http_client, async_db, fake):
    headers, uid = await athlete(http_client, async_db, "oauth-start@test.com")
    state = await start(http_client, headers)
    row = await _attempt(async_db, state)
    assert row["status"] == "pending" and row["user_id"] == uid
    assert row["redirect_uri"] == "https://perflab.test/settings/oura-callback"
    assert row["code_challenge"] == CHALLENGE
    assert row["state_hash"] == _h(state) and state not in str(row.values())
    assert 599 <= (row["expires_at"] - row["created_at"]).total_seconds() <= 601


@pytest.mark.parametrize("params, detail", [
    ({}, RELOAD_TO_CONNECT),
    ({"code_challenge_method": "S256"}, RELOAD_TO_CONNECT),
    ({"code_challenge": CHALLENGE}, BAD_CHALLENGE),
    ({"code_challenge": CHALLENGE, "code_challenge_method": "plain"}, BAD_CHALLENGE),
    ({"code_challenge": "short", "code_challenge_method": "S256"}, BAD_CHALLENGE),
])
async def test_authorize_never_starts_an_unbound_attempt(http_client, async_db, fake, params, detail):
    headers, _ = await athlete(http_client, async_db, f"oauth-unbound-{len(params)}-{hash(str(params)) % 997}@test.com")
    r = await http_client.get("/v1/integrations/oura/authorize", headers=headers, params=params)
    assert r.status_code == 400 and r.json()["detail"] == detail
    count = (await async_db.execute(text("SELECT count(*) FROM oauth_authorizations"))).scalar_one()
    assert count == 0


@pytest.mark.parametrize("web_app_url, environment", [
    ("https://perflab.test/?x=1", "development"),
    ("not a url", "development"),
    ("http://perflab.test", "production"),
    # Every spelling settings.is_production accepts (review, 2026-10-14).
    ("http://perflab.test", "prod"),
    ("http://perflab.test", "Production"),
    ("http://perflab.test", " production "),
])
async def test_an_unusable_redirect_uri_starts_nothing(http_client, async_db, fake, monkeypatch,
                                                       web_app_url, environment):
    headers, _ = await athlete(http_client, async_db, f"oauth-uri-{abs(hash(web_app_url)) % 9973}@test.com")
    monkeypatch.setattr(settings, "WEB_APP_URL", web_app_url)
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)
    assert settings.is_production == (environment != "development")  # the cases mean what they say
    r = await http_client.get("/v1/integrations/oura/authorize", headers=headers,
                              params={"code_challenge": CHALLENGE, "code_challenge_method": "S256"})
    assert r.status_code == 503
    assert (await async_db.execute(text("SELECT count(*) FROM oauth_authorizations"))).scalar_one() == 0


# --- /complete: the happy path and duplicates ---------------------------------------


async def test_complete_links_once_and_every_retry_returns_the_same_receipt(http_client, async_db, fake):
    headers, uid = await athlete(http_client, async_db, "oauth-ok@test.com")
    state = await start(http_client, headers)
    first = await complete(http_client, headers, state)
    assert first.status_code == 200, first.text
    receipt = first.json()
    conn = await _connection(async_db, uid)
    assert receipt["status"] == "succeeded" and receipt["reason"] is None
    assert receipt["linked"] == {"connection_id": conn["id"], "generation": 1}
    assert fake.exchanges == [{"code": "code-1", "verifier": VERIFIER,
                               "redirect_uri": "https://perflab.test/settings/oura-callback"}]
    for _ in range(2):
        again = await complete(http_client, headers, state, code="a-different-code")
        assert again.status_code == 200 and again.json() == receipt
    assert len(fake.exchanges) == 1


async def test_the_exchange_uses_the_redirect_uri_stored_with_the_attempt(
        http_client, async_db, fake, monkeypatch):
    headers, _ = await athlete(http_client, async_db, "oauth-uri-pinned@test.com")
    state = await start(http_client, headers)
    monkeypatch.setattr(settings, "WEB_APP_URL", "https://moved.test")  # changed mid-consent
    assert (await complete(http_client, headers, state)).status_code == 200
    assert fake.exchanges[0]["redirect_uri"] == "https://perflab.test/settings/oura-callback"


# --- /complete: checks that consume nothing -----------------------------------------


async def test_a_wrong_verifier_or_unknown_state_changes_nothing(http_client, async_db, fake):
    headers, _ = await athlete(http_client, async_db, "oauth-verifier@test.com")
    state = await start(http_client, headers)
    other_verifier, _ = pkce("not-the-one")
    r = await complete(http_client, headers, state, verifier=other_verifier)
    assert r.status_code == 400 and _detail(r) == {"status": "invalid", "reason": "verifier_mismatch"}
    r = await complete(http_client, headers, "no-such-state")
    assert r.status_code == 400 and _detail(r) == {"status": "invalid", "reason": "invalid_state"}
    assert (await _attempt(async_db, state))["status"] == "pending"
    assert fake.exchanges == []
    assert (await complete(http_client, headers, state)).status_code == 200


async def test_an_attackers_state_in_the_victims_session_is_403(http_client, async_db, fake):
    attacker, _ = await athlete(http_client, async_db, "oauth-attacker@test.com")
    victim, victim_id = await athlete(http_client, async_db, "oauth-victim@test.com")
    state = await start(http_client, attacker)
    r = await complete(http_client, victim, state)
    assert r.status_code == 403 and _detail(r) == {"status": "forbidden", "reason": "not_your_attempt"}
    assert (await _attempt(async_db, state))["status"] == "pending"
    assert fake.exchanges == [] and await _connection(async_db, victim_id) is None


# --- /complete: expiry and stale claims, decided by the database clock -----------------


async def test_an_expired_attempt_fails_durably_without_an_exchange(http_client, async_db, fake):
    headers, uid = await athlete(http_client, async_db, "oauth-expired@test.com")
    await _seed_attempt(async_db, uid, "old-state", age_s=720)  # expired 120 s ago
    r = await complete(http_client, headers, "old-state")
    assert r.status_code == 409 and _detail(r) == {"status": "failed", "reason": "expired"}
    row = await _attempt(async_db, "old-state")
    assert (row["status"], row["error"], row["claimed_at"]) == ("failed", "expired", None)
    assert fake.exchanges == []
    # Durable: the next call reads the outcome, it does not re-decide it.
    assert _detail(await complete(http_client, headers, "old-state"))["reason"] == "expired"


@pytest.mark.parametrize("claimed_age_s, expected", [
    (119.999, {"status": "in_progress", "reason": None}),
    (120, {"status": "failed", "reason": "claim_stale"}),  # at least two minutes
    (121, {"status": "failed", "reason": "claim_stale"}),
])
async def test_a_claim_is_stale_after_two_minutes(http_client, async_db, fake, pinned_clock,
                                                  claimed_age_s, expected):
    headers, uid = await athlete(http_client, async_db, f"oauth-stale-{claimed_age_s}@test.com")
    await _seed_attempt(async_db, uid, "claimed-state", status="claimed", age_s=200,
                        claimed_age_s=claimed_age_s, at=pinned_clock)
    r = await complete(http_client, headers, "claimed-state")
    assert r.status_code == 409 and _detail(r) == expected
    row = await _attempt(async_db, "claimed-state")
    assert row["status"] == ("failed" if expected["status"] == "failed" else "claimed")
    assert fake.exchanges == []


# --- failures ---------------------------------------------------------------------


@pytest.mark.parametrize("exc, reason", [
    (ProviderRejected("token request refused (400): invalid_grant"), "exchange_rejected"),
    (httpx.ConnectError("connection refused"), "exchange_failed"),
    (KeyError("access_token"), "exchange_failed"),
])
async def test_a_failed_reconnect_leaves_the_existing_connection_as_it_was(
        http_client, async_db, fake, exc, reason):
    headers, uid = await athlete(http_client, async_db, f"oauth-fail-{reason}-{type(exc).__name__}@test.com")
    await connect(http_client, headers, code="good")
    before = await _connection(async_db, uid)

    fake.fail_with = exc
    state = await start(http_client, headers)
    r = await complete(http_client, headers, state, code="bad")
    assert r.status_code == 409 and _detail(r) == {"status": "failed", "reason": reason}
    assert await _connection(async_db, uid) == before
    row = await _attempt(async_db, state)
    assert (row["status"], row["error"], row["receipt_generation"]) == ("failed", reason, None)


async def test_a_failed_exchange_logs_no_secret(http_client, async_db, fake, caplog):
    headers, _ = await athlete(http_client, async_db, "oauth-log@test.com")
    fake.fail_with = RuntimeError("provider echoed SENTINEL-CODE and SENTINEL-VERIFIER")
    state = await start(http_client, headers)
    with caplog.at_level(logging.DEBUG):
        await complete(http_client, headers, state, code="SENTINEL-CODE")
    assert "SENTINEL" not in caplog.text and state not in caplog.text


# --- generations and receipts ----------------------------------------------------


async def test_each_link_advances_the_generation_and_each_receipt_keeps_its_own(
        http_client, async_db, fake):
    headers, uid = await athlete(http_client, async_db, "oauth-gen@test.com")
    s1 = await start(http_client, headers)
    r1 = (await complete(http_client, headers, s1, code="one")).json()
    s2 = await start(http_client, headers)
    r2 = (await complete(http_client, headers, s2, code="two")).json()
    conn = await _connection(async_db, uid)
    assert r1["linked"] == {"connection_id": conn["id"], "generation": 1}
    assert r2["linked"] == {"connection_id": conn["id"], "generation": 2}
    assert conn["generation"] == 2 and crypto.decrypt(conn["access_token_enc"]) == "acc-two"
    # Retrying the first attempt still answers with the first attempt's generation.
    assert (await complete(http_client, headers, s1)).json() == r1

    # A PAT is a link too.
    pat = await http_client.post("/v1/integrations/oura/connect/pat", headers=headers,
                                 json={"token": "personal-token"})
    assert pat.status_code == 200, pat.text
    assert (await _connection(async_db, uid))["generation"] == 3


async def test_disconnect_then_recreate_starts_again_at_1_and_receipts_survive(
        http_client, async_db, fake):
    headers, uid = await athlete(http_client, async_db, "oauth-recreate@test.com")
    state = await start(http_client, headers)
    receipt = (await complete(http_client, headers, state)).json()
    old_id = receipt["linked"]["connection_id"]

    assert (await http_client.delete("/v1/integrations/oura/connection", headers=headers)).status_code == 204
    row = await _attempt(async_db, state)
    assert (row["connection_id"], row["receipt_connection_id"], row["receipt_generation"]) == (None, old_id, 1)
    assert (await complete(http_client, headers, state)).json() == receipt  # history, unchanged

    new = await connect(http_client, headers, code="again")
    assert new["linked"]["connection_id"] != old_id and new["linked"]["generation"] == 1
    status = await http_client.get("/v1/integrations/oura/connection", headers=headers)
    assert status.json()["connected"] is True


# --- /cancel ---------------------------------------------------------------------


async def test_cancel_fails_only_a_pending_attempt(http_client, async_db, fake):
    headers, uid = await athlete(http_client, async_db, "oauth-cancel@test.com")
    state = await start(http_client, headers)
    body = {"state": state, "code_verifier": VERIFIER}
    for _ in range(2):  # idempotent
        r = await http_client.post("/v1/integrations/oura/cancel", headers=headers, json=body)
        assert r.status_code == 200
        assert (r.json()["status"], r.json()["reason"]) == ("failed", "cancelled")
    assert _detail(await complete(http_client, headers, state))["reason"] == "cancelled"
    assert fake.exchanges == []

    # Cancelling a finished success reports the success; it never says "cancelled".
    done = await start(http_client, headers)
    receipt = (await complete(http_client, headers, done)).json()
    r = await http_client.post("/v1/integrations/oura/cancel", headers=headers,
                               json={"state": done, "code_verifier": VERIFIER})
    assert r.status_code == 200 and r.json() == receipt

    # A claimed attempt is reported in progress and left as it is.
    await _seed_attempt(async_db, uid, "in-flight", status="claimed", age_s=30, claimed_age_s=10)
    r = await http_client.post("/v1/integrations/oura/cancel", headers=headers,
                               json={"state": "in-flight", "code_verifier": VERIFIER})
    assert r.json()["status"] == "in_progress"
    assert (await _attempt(async_db, "in-flight"))["status"] == "claimed"


async def test_cancel_checks_owner_and_verifier(http_client, async_db, fake):
    owner, _ = await athlete(http_client, async_db, "oauth-cancel-owner@test.com")
    other, _ = await athlete(http_client, async_db, "oauth-cancel-other@test.com")
    state = await start(http_client, owner)
    r = await http_client.post("/v1/integrations/oura/cancel", headers=other,
                               json={"state": state, "code_verifier": VERIFIER})
    assert r.status_code == 403
    wrong, _ = pkce("wrong")
    r = await http_client.post("/v1/integrations/oura/cancel", headers=owner,
                               json={"state": state, "code_verifier": wrong})
    assert r.status_code == 400 and _detail(r)["reason"] == "verifier_mismatch"
    assert (await _attempt(async_db, state))["status"] == "pending"


# --- the retired callback (crossing 1) -----------------------------------------------


async def test_the_old_callback_never_exchanges(http_client, async_db, fake, caplog):
    with caplog.at_level(logging.DEBUG):
        r = await http_client.get("/v1/integrations/oura/callback",
                                  params={"code": "SENTINEL-CODE", "state": "SENTINEL-STATE"})
        assert r.status_code == 303
        assert r.headers["location"] == "https://perflab.test/settings?oura=error&reason=interrupted"
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            r = await http_client.request(method, "/v1/integrations/oura/callback?code=SENTINEL-CODE")
            assert r.status_code == 410 and r.json()["detail"] == CALLBACK_RETIRED
    assert fake.exchanges == []
    # The test client logs its own request URLs (logger "httpx"); only the server side counts
    # here. Uvicorn's access log is covered through a real server in test_access_log_redaction.
    server_side = [r.getMessage() for r in caplog.records if not r.name.startswith("httpx")]
    assert not any("SENTINEL" in m for m in server_side)


# --- commit failures ---------------------------------------------------------------


async def test_a_finalize_that_never_commits_reports_no_outcome(http_client, async_db, fake, monkeypatch,
                                                                 clock_behind):
    headers, uid = await athlete(http_client, async_db, "oauth-nocommit@test.com")
    state = await start(http_client, headers)
    real_upsert = wearable_service.upsert_connection

    async def upsert_then_break_commit(db, *a, **kw):
        out = await real_upsert(db, *a, **kw)

        async def failing_commit():
            raise RuntimeError("connection lost before COMMIT")

        monkeypatch.setattr(db, "commit", failing_commit)
        return out

    monkeypatch.setattr(wearable_service, "upsert_connection", upsert_then_break_commit)
    r = await complete(http_client, headers, state)
    monkeypatch.undo()
    monkeypatch.setattr(wearable_service, "_adapter", lambda provider="oura": fake)
    monkeypatch.setattr(settings, "WEB_APP_URL", "https://perflab.test")
    assert r.status_code == 503 and _detail(r) == {"status": "unknown", "reason": "outcome_unknown"}
    assert (await _attempt(async_db, state))["status"] == "claimed"
    assert await _connection(async_db, uid) is None
    # Nothing landed: once the claim is stale it reads as failed, and nothing is exchanged again.
    clock_behind(-300)  # five minutes on: well past the two-minute stale threshold
    r = await complete(http_client, headers, state)
    assert _detail(r) == {"status": "failed", "reason": "claim_stale"}
    assert len(fake.exchanges) == 1


async def test_a_commit_that_lands_then_raises_is_recovered_by_retry(http_client, async_db, fake, monkeypatch):
    headers, uid = await athlete(http_client, async_db, "oauth-landed@test.com")
    state = await start(http_client, headers)
    real_upsert = wearable_service.upsert_connection

    async def upsert_then_raise_after_commit(db, *a, **kw):
        out = await real_upsert(db, *a, **kw)
        real_commit = db.commit

        async def landed_then_raised():
            await real_commit()
            raise RuntimeError("reply lost after COMMIT")

        monkeypatch.setattr(db, "commit", landed_then_raised)
        return out

    monkeypatch.setattr(wearable_service, "upsert_connection", upsert_then_raise_after_commit)
    r = await complete(http_client, headers, state)
    monkeypatch.undo()
    monkeypatch.setattr(wearable_service, "_adapter", lambda provider="oura": fake)
    monkeypatch.setattr(settings, "WEB_APP_URL", "https://perflab.test")
    assert r.status_code == 503
    retry = await complete(http_client, headers, state)
    assert retry.status_code == 200
    conn = await _connection(async_db, uid)
    assert retry.json()["linked"] == {"connection_id": conn["id"], "generation": 1}
    assert (await complete(http_client, headers, state)).json() == retry.json()
    assert len(fake.exchanges) == 1


async def test_a_failure_before_the_commit_reports_no_outcome(http_client, async_db, fake, monkeypatch,
                                                              clock_behind):
    """The connection write itself fails: still inside the boundary, so 503, not a generic 500."""
    headers, uid = await athlete(http_client, async_db, "oauth-precommit@test.com")
    state = await start(http_client, headers)

    async def upsert_fails(db, *a, **kw):
        raise RuntimeError("deadlock detected")

    monkeypatch.setattr(wearable_service, "upsert_connection", upsert_fails)
    r = await complete(http_client, headers, state)
    assert r.status_code == 503 and _detail(r) == {"status": "unknown", "reason": "outcome_unknown"}
    assert (await _attempt(async_db, state))["status"] == "claimed"
    assert await _connection(async_db, uid) is None
    clock_behind(-300)  # five minutes on: well past the two-minute stale threshold
    assert _detail(await complete(http_client, headers, state)) == {"status": "failed", "reason": "claim_stale"}
    assert len(fake.exchanges) == 1


@pytest.mark.parametrize("lands", [False, True], ids=["fails_before_landing", "lands_then_raises"])
async def test_saving_an_exchange_failure_is_inside_the_boundary(
        http_client, async_db, fake, monkeypatch, clock_behind, lands):
    """Oura refused the code, then saving that outcome failed at the commit. The code is spent:
    503, and a retry reads whatever landed without exchanging again. (Review, 2026-10-14.)"""
    headers, uid = await athlete(http_client, async_db, f"oauth-failsave-{lands}@test.com")
    state = await start(http_client, headers)
    fake.fail_with = ProviderRejected("token request refused (400): invalid_grant")
    real_finalize = wearable_oauth._finalize_failure

    async def commit_breaks(db, attempt_id, reason):
        real_commit = db.commit

        async def broken():
            if lands:
                await real_commit()
            raise RuntimeError("connection lost at COMMIT")

        db.commit = broken
        try:
            await real_finalize(db, attempt_id, reason)
        finally:
            db.commit = real_commit

    monkeypatch.setattr(wearable_oauth, "_finalize_failure", commit_breaks)
    r = await complete(http_client, headers, state)
    assert r.status_code == 503 and _detail(r) == {"status": "unknown", "reason": "outcome_unknown"}
    monkeypatch.setattr(wearable_oauth, "_finalize_failure", real_finalize)
    row = await _attempt(async_db, state)
    if lands:
        assert (row["status"], row["error"]) == ("failed", "exchange_rejected")
        assert _detail(await complete(http_client, headers, state)) == {
            "status": "failed", "reason": "exchange_rejected"}
    else:
        assert row["status"] == "claimed"
        assert _detail(await complete(http_client, headers, state)) == {
            "status": "in_progress", "reason": None}
        clock_behind(-300)  # five minutes on: well past the two-minute stale threshold
        assert _detail(await complete(http_client, headers, state)) == {
            "status": "failed", "reason": "claim_stale"}
    assert len(fake.exchanges) == 1
    assert await _connection(async_db, uid) is None


# --- a database clock that steps backward (ADR-0075: recorded times are clamped) -------


def _clamps(caplog, attempt_id: int) -> list[float]:
    """The clamp sizes (seconds) logged for one attempt."""
    return [rec.args[1] for rec in caplog.records
            if rec.name == "perflab.wearable.oauth" and "clamped" in rec.msg
            and isinstance(rec.args, tuple) and rec.args[0] == attempt_id]


async def test_an_attempt_lives_exactly_its_ttl_from_one_clock_sample(
        http_client, async_db, fake, monkeypatch):
    # A clock that moves between any two readings, as a step between them would. One sample
    # still gives exactly ATTEMPT_TTL; two readings would not.
    jumpy = f"({CLOCK} + random() * interval '1 hour')"
    monkeypatch.setattr(wearable_oauth, "_CLOCK", jumpy)
    monkeypatch.setattr(wearable_oauth, "_NOW", f"(SELECT {jumpy} AS t) c")
    headers, _ = await athlete(http_client, async_db, "oauth-clock-ttl@test.com")
    row = await _attempt(async_db, await start(http_client, headers))
    assert row["expires_at"] - row["created_at"] == wearable_oauth.ATTEMPT_TTL


@pytest.mark.parametrize("fails", [False, True], ids=["success", "exchange_failure"])
async def test_a_step_back_after_the_claim_clamps_the_outcome_to_the_claim(
        http_client, async_db, fake, clock_behind, monkeypatch, caplog, fails):
    """Claimed after creation, then the clock steps back below the claim (still above creation):
    the outcome is recorded at the claim, never before it."""
    caplog.set_level(logging.WARNING, logger="perflab.wearable.oauth")
    headers, uid = await athlete(http_client, async_db, f"oauth-clock-after-claim-{fails}@test.com")
    state = await start(http_client, headers)
    real_exchange = fake.exchange_code

    async def step_back_while_at_oura(code, **kw):
        clock_behind(0)  # 20 s back from the reading the claim used
        return await real_exchange(code, **kw)

    monkeypatch.setattr(fake, "exchange_code", step_back_while_at_oura)
    if fails:
        fake.fail_with = ProviderRejected("token request refused (400): invalid_grant")
    clock_behind(-20)  # the claim reads 20 s ahead
    r = await complete(http_client, headers, state)
    assert r.status_code == (409 if fails else 200)
    row = await _attempt(async_db, state)
    assert row["claimed_at"] > row["created_at"]
    assert row["outcome_at"] == row["claimed_at"]
    sizes = _clamps(caplog, row["id"])
    assert len(sizes) == 1 and 10 < sizes[0] < 30, sizes  # only the outcome needed clamping
    assert (await _connection(async_db, uid) is None) == fails


async def test_a_clock_behind_the_attempt_clamps_the_claim_and_the_success(
        http_client, async_db, fake, clock_behind, caplog):
    caplog.set_level(logging.WARNING, logger="perflab.wearable.oauth")
    headers, uid = await athlete(http_client, async_db, "oauth-clock-success@test.com")
    state = await start(http_client, headers)
    clock_behind(30)
    r = await complete(http_client, headers, state)
    assert r.status_code == 200
    row = await _attempt(async_db, state)
    assert row["status"] == "succeeded"
    assert row["claimed_at"] == row["created_at"] and row["outcome_at"] == row["claimed_at"]
    conn = await _connection(async_db, uid)  # the credential and the receipt landed together
    assert r.json()["linked"] == {"connection_id": conn["id"], "generation": 1}
    assert (row["receipt_connection_id"], row["receipt_generation"], row["connection_id"]) == (
        conn["id"], 1, conn["id"])
    sizes = _clamps(caplog, row["id"])
    assert len(sizes) == 2 and all(20 < s < 40 for s in sizes), sizes  # claim, then outcome
    assert (await complete(http_client, headers, state)).json() == r.json()
    assert len(fake.exchanges) == 1


async def test_a_clock_behind_the_attempt_clamps_an_exchange_failure(
        http_client, async_db, fake, clock_behind, caplog):
    caplog.set_level(logging.WARNING, logger="perflab.wearable.oauth")
    headers, uid = await athlete(http_client, async_db, "oauth-clock-fail@test.com")
    state = await start(http_client, headers)
    clock_behind(30)
    fake.fail_with = ProviderRejected("token request refused (400): invalid_grant")
    r = await complete(http_client, headers, state)
    assert r.status_code == 409 and _detail(r) == {"status": "failed", "reason": "exchange_rejected"}
    row = await _attempt(async_db, state)
    assert row["outcome_at"] == row["claimed_at"] == row["created_at"]
    assert await _connection(async_db, uid) is None
    assert len(_clamps(caplog, row["id"])) == 2
    assert _detail(await complete(http_client, headers, state))["reason"] == "exchange_rejected"
    assert len(fake.exchanges) == 1


async def test_a_clock_behind_the_attempt_clamps_a_cancellation(
        http_client, async_db, fake, clock_behind, caplog):
    caplog.set_level(logging.WARNING, logger="perflab.wearable.oauth")
    headers, _ = await athlete(http_client, async_db, "oauth-clock-cancel@test.com")
    state = await start(http_client, headers)
    clock_behind(30)
    r = await http_client.post("/v1/integrations/oura/cancel", headers=headers,
                               json={"state": state, "code_verifier": VERIFIER})
    assert r.status_code == 200 and (r.json()["status"], r.json()["reason"]) == ("failed", "cancelled")
    row = await _attempt(async_db, state)
    assert row["claimed_at"] is None and row["outcome_at"] == row["created_at"]
    assert len(_clamps(caplog, row["id"])) == 1
    assert fake.exchanges == []


async def test_expiry_and_staleness_read_the_raw_clock(http_client, async_db, fake, clock_behind, caplog):
    """Their guards use the same sample as the recorded time, so a guard that passes is never
    behind the row and needs no clamp. A step larger than the margin extends them instead."""
    caplog.set_level(logging.WARNING, logger="perflab.wearable.oauth")
    headers, uid = await athlete(http_client, async_db, "oauth-clock-raw@test.com")
    # Margins of 120 s: far larger than the real steps this suite may meet, which are seconds.
    await _seed_attempt(async_db, uid, "expired", age_s=720)  # expired 120 s ago
    await _seed_attempt(async_db, uid, "stale", status="claimed", age_s=300, claimed_age_s=240)
    clock_behind(5)
    assert _detail(await complete(http_client, headers, "expired")) == {"status": "failed", "reason": "expired"}
    assert _detail(await complete(http_client, headers, "stale")) == {"status": "failed", "reason": "claim_stale"}
    for state in ("expired", "stale"):
        row = await _attempt(async_db, state)
        assert row["outcome_at"] >= (row["claimed_at"] or row["created_at"]) >= row["created_at"]
        assert _clamps(caplog, row["id"]) == []

    await _seed_attempt(async_db, uid, "expired-2", age_s=720)
    await _seed_attempt(async_db, uid, "stale-2", status="claimed", age_s=300, claimed_age_s=240)
    clock_behind(300)  # documented: the lifetime and the stale threshold stretch with the step
    assert (await complete(http_client, headers, "expired-2")).status_code == 200
    assert _detail(await complete(http_client, headers, "stale-2"))["status"] == "in_progress"


async def test_an_unclamped_receipt_write_is_inside_the_boundary(
        http_client, async_db, fake, clock_behind, monkeypatch):
    """Without the clamp the receipt breaks the time-order CHECK. That failure must be the agreed
    503 with nothing written, the existing connection untouched, and no second exchange."""
    headers, uid = await athlete(http_client, async_db, "oauth-clock-check@test.com")
    await connect(http_client, headers, code="existing")
    before = await _connection(async_db, uid)
    state = await start(http_client, headers)
    clock_behind(30)
    monkeypatch.setattr(wearable_oauth, "_OUTCOME_AT", "c.t")
    r = await complete(http_client, headers, state)
    assert r.status_code == 503 and _detail(r) == {"status": "unknown", "reason": "outcome_unknown"}
    assert await _connection(async_db, uid) == before
    assert (await _attempt(async_db, state))["status"] == "claimed"
    clock_behind(-300)  # five minutes on: well past the two-minute stale threshold
    assert _detail(await complete(http_client, headers, state)) == {"status": "failed", "reason": "claim_stale"}
    assert len(fake.exchanges) == 2  # "existing", then this attempt's one exchange


# --- races, on independent sessions -------------------------------------------------


async def test_concurrent_duplicates_exchange_once(http_client, async_db, fake, sessions):
    headers, uid = await athlete(http_client, async_db, "oauth-race@test.com")
    state = await start(http_client, headers)
    fake.hold = asyncio.Event()

    async def call():
        async with sessions() as db:
            return await wearable_oauth.complete(db, fake, uid, state=state, code="c",
                                                 code_verifier=VERIFIER)

    first = asyncio.create_task(call())
    await _until_held(fake.entered, first)
    second = await asyncio.wait_for(call(), 30)  # the claim is taken: no second exchange
    assert second.status == "in_progress"
    fake.hold.set()
    done = await asyncio.wait_for(first, 30)
    assert done.status == "succeeded" and done.generation == 1
    assert len(fake.exchanges) == 1


async def test_a_stale_failure_fences_the_worker_that_was_still_exchanging(
        http_client, async_db, fake, sessions, clock_behind):
    headers, uid = await athlete(http_client, async_db, "oauth-fence@test.com")
    await connect(http_client, headers, code="existing")
    before = await _connection(async_db, uid)
    state = await start(http_client, headers)
    fake.hold = asyncio.Event()

    async def worker():
        async with sessions() as db:
            return await wearable_oauth.complete(db, fake, uid, state=state, code="slow",
                                                 code_verifier=VERIFIER)

    task = asyncio.create_task(worker())
    await _until_held(fake.entered, task)
    clock_behind(-300)  # five minutes on: well past the two-minute stale threshold
    async with sessions() as db:
        dup = await asyncio.wait_for(wearable_oauth.complete(
            db, fake, uid, state=state, code="slow", code_verifier=VERIFIER), 30)
    assert (dup.status, dup.reason) == ("failed", "claim_stale")
    fake.hold.set()
    late = await asyncio.wait_for(task, 30)
    assert (late.status, late.reason) == ("failed", "claim_stale")  # the duplicate's answer stands
    assert await _connection(async_db, uid) == before  # nothing written by the fenced worker
    assert (await _attempt(async_db, state))["status"] == "failed"


async def test_a_claim_judges_expiry_after_waiting_for_the_row(http_client, async_db, fake, sessions,
                                                             monkeypatch):
    """A claim queued behind another transaction's lock reads the clock once it holds the row, so
    time that passed during the wait counts. (Review probe, 2026-10-14.)"""
    headers, uid = await athlete(http_client, async_db, "oauth-lock-wait@test.com")
    seq = "oauth_test_clock_" + uuid.uuid4().hex
    await async_db.execute(text(f"CREATE SEQUENCE {seq}"))
    await async_db.commit()
    # A logical clock: each read is one second after the last, and setval jumps it.
    monkeypatch.setattr(wearable_oauth, "_NOW", (
        f"(SELECT TIMESTAMP '2030-01-01 00:00:00' + nextval('{seq}') * interval '1 second' AS t) c"))
    task = None
    try:
        state = await start(http_client, headers)  # created at +1 s, so it expires at +601 s
        async with sessions() as holder:
            await holder.execute(text(
                "SELECT id FROM oauth_authorizations WHERE state_hash = :h FOR UPDATE"), {"h": _h(state)})

            async def worker():
                async with sessions() as db:
                    return await wearable_oauth.complete(db, fake, uid, state=state, code="queued",
                                                         code_verifier=VERIFIER)

            task = asyncio.create_task(worker())
            await _until_waiting_on_a_lock(async_db, task)
            await async_db.execute(text(f"SELECT setval('{seq}', 700)"))  # past expiry, mid-wait
            await async_db.commit()
            await holder.rollback()
            out = await asyncio.wait_for(task, 30)
        assert (out.status, out.reason) == ("failed", "expired")
        assert fake.exchanges == []
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await async_db.rollback()
        await async_db.execute(text(f"DROP SEQUENCE IF EXISTS {seq}"))
        await async_db.commit()


async def test_a_lost_claim_reclassifies_without_exchanging(http_client, async_db, fake, sessions, monkeypatch):
    """Someone else claims between our read and our claim: we re-read fresh and back off."""
    headers, uid = await athlete(http_client, async_db, "oauth-lostclaim@test.com")
    state = await start(http_client, headers)
    real_claim = wearable_oauth._claim

    async def claimed_elsewhere_first(db, attempt_id):
        async with sessions() as other:
            assert await real_claim(other, attempt_id)
        return await real_claim(db, attempt_id)

    monkeypatch.setattr(wearable_oauth, "_claim", claimed_elsewhere_first)
    r = await complete(http_client, headers, state)
    assert r.status_code == 409 and _detail(r)["status"] == "in_progress"
    assert fake.exchanges == []


async def test_a_refresh_that_raced_a_reconnect_does_not_overwrite_it(http_client, async_db, fake, sessions):
    headers, uid = await athlete(http_client, async_db, "oauth-refresh-race@test.com")
    await connect(http_client, headers, code="first")
    await async_db.execute(text(
        "UPDATE wearable_connections SET expires_at = '2020-01-01' WHERE user_id = :u"), {"u": uid})
    await async_db.commit()
    fake.refresh_hold = asyncio.Event()

    async def sync():
        async with sessions() as db:
            conn = await wearable_service.get_connection(db, uid, "oura")
            return await wearable_service.sync_connection(db, conn.id)

    task = asyncio.create_task(sync())
    await _until_held(fake.refresh_entered, task)
    async with sessions() as db:  # the reconnect commits while the refresh is out at Oura
        s = await wearable_oauth.start(db, fake, uid, CHALLENGE)
        from urllib.parse import parse_qs, urlsplit
        state = parse_qs(urlsplit(s).query)["state"][0]
        relinked = await wearable_oauth.complete(db, fake, uid, state=state, code="second",
                                                 code_verifier=VERIFIER)
    assert relinked.generation == 2
    fake.refresh_hold.set()
    with pytest.raises(wearable_service.ConnectionReplaced):
        await asyncio.wait_for(task, 30)
    conn = await _connection(async_db, uid)
    assert conn["generation"] == 2 and crypto.decrypt(conn["access_token_enc"]) == "acc-second"


async def test_connection_reads_are_fresh_within_one_session(http_client, async_db, fake, sessions):
    headers, uid = await athlete(http_client, async_db, "oauth-fresh@test.com")
    await connect(http_client, headers, code="one")
    first = await wearable_service.get_connection(async_db, uid, "oura")
    assert first.generation == 1
    async with sessions() as other:
        await other.execute(text(
            "UPDATE wearable_connections SET generation = 5 WHERE user_id = :u"), {"u": uid})
        await other.commit()
    again = await wearable_service.get_connection(async_db, uid, "oura")
    assert again.generation == 5
