"""P9: per-IP limits on login and registration, and login's single bcrypt check (ADR-0076).

Deterministic throughout: spies count the credential lookup and bcrypt, a controlled clock
drives the window, and nothing compares elapsed times.
"""

import importlib
import logging
import subprocess
import sys
import tomllib
import types
from collections.abc import AsyncIterator
from pathlib import Path

import bcrypt
import httpx
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Request
from limits import parse
from limits.strategies import FixedWindowRateLimiter, MovingWindowRateLimiter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import app.core.rate_limit as rate_limit
from app.core.auth import BCRYPT_ROUNDS, DUMMY_PASSWORD_HASH
from app.core.config import Settings
from app.core.db import get_db
from app.main import _check_production_proxy_trust, proxy_trust_problem
from app.models.user import User
from app.repositories.user_repository import UserRepository

pytestmark = pytest.mark.asyncio

REPO = Path(__file__).resolve().parents[1]
# The dummy hash's throwaway secret (app/core/auth.py). Not a credential.
DUMMY_SECRET = "perflab-dummy-login-secret-not-a-credential"
PASSWORD = "securepass1"


# ---- helpers ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="function")
async def client_from(async_db: AsyncSession) -> AsyncIterator:
    """A factory of clients that connect to the real app from a chosen address."""
    from app.main import app

    async def _override_get_db():
        yield async_db

    app.dependency_overrides[get_db] = _override_get_db
    opened: list[httpx.AsyncClient] = []

    def make(ip: str) -> httpx.AsyncClient:
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(ip, 40000)), base_url="http://test"
        )
        opened.append(c)
        return c

    yield make
    for c in opened:
        await c.aclose()
    app.dependency_overrides.pop(get_db, None)


def _login(client: httpx.AsyncClient, email: str, password: str):
    return client.post(
        "/auth/token",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


class Spies:
    def __init__(self) -> None:
        self.lookups = 0
        self.checkpw = 0
        self.hashpw = 0


@pytest.fixture
def spies(monkeypatch: pytest.MonkeyPatch) -> Spies:
    """Count credential lookups and bcrypt work; bcrypt itself is replaced with a fast fake
    that never matches (these tests are about how often it runs, not what it returns)."""
    s = Spies()
    original_lookup = UserRepository.get_by_email

    async def counting_lookup(self, email):
        s.lookups += 1
        return await original_lookup(self, email)

    def fake_checkpw(password: bytes, hashed: bytes) -> bool:
        s.checkpw += 1
        return False

    def fake_hash_password(plain: str) -> str:
        s.hashpw += 1
        return DUMMY_PASSWORD_HASH

    monkeypatch.setattr(UserRepository, "get_by_email", counting_lookup)
    monkeypatch.setattr(bcrypt, "checkpw", fake_checkpw)
    monkeypatch.setattr("app.api.v1.auth.hash_password", fake_hash_password)
    return s


class FakeClock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Drive `limits`' in-memory storage from a controlled clock."""
    import limits.storage.memory as memory

    c = FakeClock()
    monkeypatch.setattr(memory, "time", types.SimpleNamespace(time=c.time))
    return c


# ---- the dummy hash ----------------------------------------------------------------------


async def test_the_dummy_hash_is_valid_at_the_policy_cost_and_matches_only_its_secret():
    assert DUMMY_PASSWORD_HASH.startswith(f"$2b${BCRYPT_ROUNDS:02d}$")
    assert bcrypt.checkpw(DUMMY_SECRET.encode(), DUMMY_PASSWORD_HASH.encode())
    assert not bcrypt.checkpw(PASSWORD.encode(), DUMMY_PASSWORD_HASH.encode())


# ---- limits on the real routes -----------------------------------------------------------


async def test_the_11th_login_from_one_address_is_refused_before_lookup_and_bcrypt(
    client_from, spies: Spies
):
    c = client_from("198.51.100.10")
    for _ in range(10):
        r = await _login(c, "nobody@example.com", PASSWORD)
        assert r.status_code == 401
    assert (spies.lookups, spies.checkpw) == (10, 10)

    r = await _login(c, "nobody@example.com", PASSWORD)
    assert r.status_code == 429
    assert r.json() == {"detail": "Too many requests"}
    assert int(r.headers["Retry-After"]) >= 1
    assert (spies.lookups, spies.checkpw) == (10, 10)  # the refused request did neither


async def test_the_6th_registration_from_one_address_is_refused_before_lookup_and_hashing(
    client_from, spies: Spies
):
    c = client_from("198.51.100.11")
    for i in range(5):
        r = await c.post("/auth/register", json={"email": f"r{i}@example.com", "password": PASSWORD})
        assert r.status_code == 201, r.text
    assert (spies.lookups, spies.hashpw) == (5, 5)

    r = await c.post("/auth/register", json={"email": "r5@example.com", "password": PASSWORD})
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert (spies.lookups, spies.hashpw) == (5, 5)


async def test_two_addresses_have_independent_login_buckets(client_from, spies: Spies):
    a, b = client_from("198.51.100.20"), client_from("198.51.100.21")
    for _ in range(10):
        assert (await _login(a, "nobody@example.com", PASSWORD)).status_code == 401
    assert (await _login(a, "nobody@example.com", PASSWORD)).status_code == 429
    assert (await _login(b, "nobody@example.com", PASSWORD)).status_code == 401


async def test_login_and_registration_buckets_are_separate(client_from, spies: Spies):
    c = client_from("198.51.100.30")
    for _ in range(10):
        await _login(c, "nobody@example.com", PASSWORD)
    assert (await _login(c, "nobody@example.com", PASSWORD)).status_code == 429
    r = await c.post("/auth/register", json={"email": "sep@example.com", "password": PASSWORD})
    assert r.status_code == 201


# ---- the limiter itself -----------------------------------------------------------------


async def test_two_workers_keep_separate_counters():
    rule = parse("2/minute")
    worker_1, worker_2 = rate_limit.AuthRateLimiter(), rate_limit.AuthRateLimiter()
    assert worker_1.hit(rule, "s", "203.0.113.1") and worker_1.hit(rule, "s", "203.0.113.1")
    assert not worker_1.hit(rule, "s", "203.0.113.1")
    assert worker_2.hit(rule, "s", "203.0.113.1")  # its own, untouched counter


async def test_the_moving_window_refuses_the_boundary_burst_a_fixed_window_allows(clock: FakeClock):
    """1 attempt at t=0 and 9 at t=59 fill the minute. At t=60.5 a fixed window has
    restarted and admits 10 more (19 in 1.5 s); the moving window still holds the 9 from
    t=59 and admits 1."""
    rule = rate_limit.LOGIN_RATE

    def burst(limiter: rate_limit.AuthRateLimiter) -> int:
        clock.now = 1_000_000.0
        assert limiter.hit(rule, "auth.token", "203.0.113.9")
        clock.now += 59
        for _ in range(9):
            assert limiter.hit(rule, "auth.token", "203.0.113.9")
        clock.now += 1.5
        return sum(limiter.hit(rule, "auth.token", "203.0.113.9") for _ in range(10))

    assert burst(rate_limit.AuthRateLimiter(strategy=FixedWindowRateLimiter)) == 10
    configured = rate_limit.AuthRateLimiter()  # the strategy the routes use
    assert isinstance(configured.strategy, MovingWindowRateLimiter)
    assert burst(configured) == 1


# ---- the client address comes from uvicorn, not from headers ------------------------------


def _probe_app(rule) -> FastAPI:
    probe = FastAPI()

    @probe.get("/probe", dependencies=[Depends(rate_limit.rate_limited("test.probe", rule))])
    async def _probe(request: Request) -> dict[str, str]:
        return {"client": request.client.host if request.client else ""}

    return probe


async def test_a_forwarded_client_counts_only_through_the_trusted_proxy():
    rule = parse("2/minute")
    asgi = ProxyHeadersMiddleware(_probe_app(rule), trusted_hosts="10.9.9.9")

    async def get(peer: str, forwarded: str) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=asgi, client=(peer, 5000)), base_url="http://test"
        ) as c:
            return await c.get("/probe", headers={"X-Forwarded-For": forwarded})

    # Through the trusted proxy: each forwarded client has its own bucket.
    first = await get("10.9.9.9", "203.0.113.5")
    assert first.json() == {"client": "203.0.113.5"}
    assert (await get("10.9.9.9", "203.0.113.5")).status_code == 200
    assert (await get("10.9.9.9", "203.0.113.5")).status_code == 429
    assert (await get("10.9.9.9", "203.0.113.6")).status_code == 200

    # From an untrusted peer, a forged header changes nothing: one bucket, the peer's own.
    spoof = await get("10.9.9.8", "198.51.100.1")
    assert spoof.json() == {"client": "10.9.9.8"}
    assert (await get("10.9.9.8", "198.51.100.2")).status_code == 200
    assert (await get("10.9.9.8", "198.51.100.3")).status_code == 429


# ---- a broken counter fails open over the whole response path ----------------------------


async def test_a_counter_failure_leaves_login_and_registration_working_and_logs_error(
    client_from, async_db: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog
):
    def broken(*args, **kwargs):
        raise RuntimeError("injected counter failure")

    monkeypatch.setattr(rate_limit.auth_limiter.strategy, "hit", broken)
    c = client_from("198.51.100.40")
    caplog.set_level(logging.ERROR, logger="perflab")

    r = await c.post("/auth/register", json={"email": "open@example.com", "password": PASSWORD})
    assert r.status_code == 201, r.text
    count = (
        await async_db.execute(select(func.count()).select_from(User).where(User.email == "open@example.com"))
    ).scalar_one()
    assert count == 1  # committed exactly once

    ok = await _login(c, "open@example.com", PASSWORD)
    assert ok.status_code == 200 and ok.json()["access_token"]
    bad = await _login(c, "open@example.com", "wrongpass1")
    assert bad.status_code == 401

    errors = [rec for rec in caplog.records if rec.levelno == logging.ERROR]
    assert errors and all("Rate limit counter failed" in rec.getMessage() for rec in errors)


# ---- effective configuration cannot be changed from outside ------------------------------


async def test_ratelimit_environment_and_a_dotenv_cannot_change_or_disable_the_limits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    (tmp_path / ".env").write_text(
        "RATELIMIT_ENABLED=false\nRATELIMIT_STRATEGY=fixed-window\nRATELIMIT_DEFAULT=1000/second\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for key, value in {
        "RATELIMIT_ENABLED": "false",
        "RATELIMIT_ENABLE": "false",
        "RATELIMIT_STRATEGY": "fixed-window",
        "RATELIMIT_STORAGE_URI": "redis://nowhere:6379",
        "RATELIMIT_DEFAULT": "1000/second",
        "RATELIMIT_APPLICATION": "1000/second",
    }.items():
        monkeypatch.setenv(key, value)

    reloaded = importlib.reload(rate_limit)
    try:
        assert str(reloaded.LOGIN_RATE) == str(parse("10/minute"))
        assert str(reloaded.REGISTER_RATE) == str(parse("5/hour"))
        assert isinstance(reloaded.auth_limiter.strategy, MovingWindowRateLimiter)
        probe = _probe_app(parse("1/minute"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=probe, client=("203.0.113.70", 1)), base_url="http://test"
        ) as c:
            assert (await c.get("/probe")).status_code == 200
            assert (await c.get("/probe")).status_code == 429
    finally:
        monkeypatch.undo()
        importlib.reload(rate_limit)


# ---- production refuses to start without a single trusted proxy address -----------------


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "*", "172.18.0.0/16", "172.30.0.2/32", "172.30.0.2,127.0.0.1", "caddy", "0.0.0.0", "::", "300.1.1.1"],
)
async def test_production_refuses_an_unusable_proxy_trust_setting(value):
    env = {} if value is None else {"FORWARDED_ALLOW_IPS": value}
    for name in ("production", "prod", " Prod "):
        with pytest.raises(RuntimeError, match="FORWARDED_ALLOW_IPS"):
            _check_production_proxy_trust(Settings(ENVIRONMENT=name), env)


async def test_production_accepts_exactly_one_proxy_address_and_development_never_fails():
    _check_production_proxy_trust(Settings(ENVIRONMENT="production"), {"FORWARDED_ALLOW_IPS": "172.30.0.2"})
    _check_production_proxy_trust(Settings(ENVIRONMENT="production"), {"FORWARDED_ALLOW_IPS": "fd00::2"})
    assert proxy_trust_problem("172.30.0.2") is None
    _check_production_proxy_trust(Settings(ENVIRONMENT="development"), {})
    _check_production_proxy_trust(Settings(ENVIRONMENT="development"), {"FORWARDED_ALLOW_IPS": "*"})


# ---- base dependencies are enough --------------------------------------------------------


async def test_the_limiter_needs_only_base_dependencies():
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    assert any(dep.startswith("limits") for dep in pyproject["project"]["dependencies"])
    out = subprocess.run(
        [sys.executable, "-c", "import sys, app.main; print('slowapi' in sys.modules)"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip().splitlines()[-1] == "False"


# ---- login always verifies once (fork 2 = Y) ---------------------------------------------


async def test_unknown_email_and_wrong_password_each_cost_one_check_and_answer_alike(
    client_from, spies: Spies, async_db: AsyncSession
):
    c = client_from("198.51.100.50")
    async_db.add(User(email="known@example.com", hashed_password=DUMMY_PASSWORD_HASH))
    await async_db.commit()

    unknown = await _login(c, "missing@example.com", PASSWORD)
    assert (spies.lookups, spies.checkpw) == (1, 1)
    wrong = await _login(c, "known@example.com", PASSWORD)
    assert (spies.lookups, spies.checkpw) == (2, 2)

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json() == {"detail": "Incorrect email or password"}
    assert unknown.headers["WWW-Authenticate"] == wrong.headers["WWW-Authenticate"] == "Bearer"


async def test_the_dummy_secret_never_logs_anyone_in(client_from):
    c = client_from("198.51.100.51")
    r = await _login(c, "missing@example.com", DUMMY_SECRET)
    assert r.status_code == 401
    assert "access_token" not in r.json()


async def test_success_and_inactive_accounts_behave_as_before(client_from, async_db: AsyncSession):
    c = client_from("198.51.100.52")
    assert (await c.post("/auth/register", json={"email": "ok@example.com", "password": PASSWORD})).status_code == 201
    ok = await _login(c, "ok@example.com", PASSWORD)
    assert ok.status_code == 200 and ok.json()["token_type"] == "bearer"

    user = (await async_db.execute(select(User).where(User.email == "ok@example.com"))).scalar_one()
    user.is_active = False
    await async_db.commit()
    assert (await _login(c, "ok@example.com", PASSWORD)).status_code == 403

    dup = await c.post("/auth/register", json={"email": "ok@example.com", "password": PASSWORD})
    assert dup.status_code == 409 and dup.json() == {"detail": "Email already registered"}
