"""A timezone-aware datetime never reaches a naive timestamp column.

Stored timestamps are naive UTC (``utc_naive``). asyncpg refuses to bind an aware value to a
``timestamp without time zone`` column, and the failure surfaces as an unhandled 500, not a
400. Production hit this on 2026-09-28: saving a 1RM in Settings created a benchmark weak
point with ``detected_at = datetime.now(UTC)``. Each test drives a real route to a real row,
with the aware value arriving the way a client or a provider actually sends it.
"""
from datetime import UTC, date, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core import crypto
from app.core.config import settings
from app.integrations.base import NormalizedWellness, TokenBundle
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.observation_mapping import ObservationMapping
from app.models.user import User
from app.models.weak_point import WeakPoint, WeakPointSource
from app.models.wearable_connection import WearableConnection
from app.models.wellness import WellnessSample
from app.services import wearable_service

pytestmark = pytest.mark.asyncio

_CODE = "pl_e1rm_squat"


async def _athlete(client, db, email: str) -> tuple[dict[str, str], int]:
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    user_id = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, user_id


async def _squat_as_seeded(db) -> None:
    """The production squat definition: WITH state_targets and its capacity mapping, which is
    exactly what the older strength-evidence tests leave out (so they never reach the
    weak-point feedback)."""
    db.add(Exercise(
        name="Back Squat", modality="Strength", movement_pattern="squat",
        load_type="barbell", is_benchmark=True, e1rm_benchmark_code=_CODE,
    ))
    d = BenchmarkDefinition(
        code=_CODE, name="Squat e1RM", domain="powerlifting", metric_type="load", unit="kg",
        better_direction="higher", observation_weight=1.0, is_primary_anchor=True,
        state_targets=["max_strength"], standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    db.add(d)
    await db.flush()
    db.add(ObservationMapping(
        benchmark_definition_id=d.id, target_vector="capacity", target_key="max_strength",
        mapping_type="residual", coefficient=1.0, intercept=0.0,
    ))
    await db.commit()


async def _tested_max(client, headers, kg: float, days_ago: int):
    performed = (datetime.now(UTC) - timedelta(days=days_ago)).replace(microsecond=0)
    return await client.post(
        "/v1/benchmarks/strength-evidence",
        json={"benchmark_code": _CODE, "collection_mode": "retest", "method": "tested_max",
              "value_kg": kg, "performed_at": performed.isoformat()},
        headers=headers,
    )


async def _benchmark_weak_points(db, user_id: int) -> list[WeakPoint]:
    db.expire_all()
    return list((await db.execute(
        select(WeakPoint).where(
            WeakPoint.user_id == user_id, WeakPoint.source == WeakPointSource.BENCHMARK
        )
    )).scalars().all())


# ── the reported bug: a 1RM entered in Settings ──────────────────────────────────


async def test_a_below_average_tested_max_saves_and_flags_a_weak_point(http_client, async_db):
    await _squat_as_seeded(async_db)
    headers, user_id = await _athlete(http_client, async_db, "tz-low@test.com")

    resp = await _tested_max(http_client, headers, 80.0, days_ago=1)  # normalized ~19 (< 40)
    assert resp.status_code == 200, resp.text

    flagged = await _benchmark_weak_points(async_db, user_id)
    assert flagged, "a deficit measurement flags a benchmark weak point"
    assert all(wp.detected_at.tzinfo is None and wp.resolved_at is None for wp in flagged)


async def test_a_strong_retest_resolves_the_weak_point_and_saves(http_client, async_db):
    await _squat_as_seeded(async_db)
    headers, user_id = await _athlete(http_client, async_db, "tz-high@test.com")
    assert (await _tested_max(http_client, headers, 80.0, days_ago=3)).status_code == 200

    resp = await _tested_max(http_client, headers, 240.0, days_ago=1)  # normalized ~95 (> 65)
    assert resp.status_code == 200, resp.text

    resolved = await _benchmark_weak_points(async_db, user_id)
    assert resolved and all(wp.resolved_at is not None for wp in resolved)
    assert all(wp.resolved_at.tzinfo is None for wp in resolved if wp.resolved_at)


# ── siblings: request and provider timestamps ─────────────────────────────────────


async def test_an_observation_with_an_offset_is_stored_as_utc(http_client, async_db):
    async_db.add(BenchmarkDefinition(
        code="sprint_300m_time", name="300 m time", domain="running", metric_type="time",
        unit="seconds", better_direction="lower", observation_weight=1.0,
        standardization_rules={"floor": 55.0, "cap": 32.0},
    ))
    await async_db.commit()
    headers, user_id = await _athlete(http_client, async_db, "tz-obs@test.com")

    resp = await http_client.post(
        "/v1/benchmarks/observations",
        json={"benchmark_code": "sprint_300m_time", "raw_value": 44.0,
              "observed_at": "2026-09-20T06:00:00-04:00"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    row = (await async_db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.user_id == user_id)
    )).scalar_one()
    # Converted to UTC, not truncated: 06:00 at -04:00 is 10:00 UTC.
    assert row.observed_at == datetime(2026, 9, 20, 10, 0)


async def test_a_wellness_sample_with_an_offset_is_stored_as_utc(http_client, async_db):
    headers, user_id = await _athlete(http_client, async_db, "tz-wellness@test.com")

    resp = await http_client.post(
        "/v1/wellness",
        json={"date": "2026-09-20", "source": "manual", "hrv_ms": 60.0,
              "measured_at": "2026-09-20T06:00:00-04:00"},
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text

    async_db.expire_all()
    row = (await async_db.execute(
        select(WellnessSample).where(WellnessSample.user_id == user_id)
    )).scalar_one()
    assert row.measured_at == datetime(2026, 9, 20, 10, 0)


# ── the Oura connection: the first OAuth connect and every token refresh ───────────


class _ExpiringOura:
    """Returns tokens the way the real adapter does: expiry as an aware UTC datetime."""

    provider = "oura"

    def build_authorize_url(self, state: str) -> str:
        return f"https://fake.oura/authorize?state={state}"

    async def exchange_code(self, code: str) -> TokenBundle:
        return TokenBundle(access_token="acc", refresh_token="ref",
                           expires_at=datetime.now(UTC) + timedelta(days=1))

    async def refresh_tokens(self, refresh_token: str) -> TokenBundle:
        return TokenBundle(access_token="acc2", refresh_token="ref2",
                           expires_at=datetime.now(UTC) + timedelta(days=1))

    async def fetch_daily_wellness(self, access_token, start, end):
        return [NormalizedWellness(day=date(2026, 9, 20), hrv_ms=65.0, raw={})]


@pytest.fixture
def _oura(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    crypto._fernet_for.cache_clear()
    monkeypatch.setattr(wearable_service, "_adapter", lambda provider="oura": _ExpiringOura())
    yield
    crypto._fernet_for.cache_clear()


async def _connection(db, user_id: int) -> WearableConnection:
    db.expire_all()
    return (await db.execute(
        select(WearableConnection).where(WearableConnection.user_id == user_id)
    )).scalar_one()


async def test_the_oauth_callback_stores_a_token_expiry(http_client, async_db, _oura):
    _, user_id = await _athlete(http_client, async_db, "tz-oauth@test.com")

    resp = await http_client.get(
        "/v1/integrations/oura/callback",
        params={"code": "c", "state": wearable_service.sign_state(user_id)},
    )
    assert resp.status_code == 302
    assert resp.headers["location"].endswith("?oura=connected")
    conn = await _connection(async_db, user_id)
    assert conn.expires_at is not None and conn.expires_at.tzinfo is None


async def test_a_token_refresh_during_sync_stores_its_expiry(http_client, async_db, _oura):
    headers, user_id = await _athlete(http_client, async_db, "tz-refresh@test.com")
    await http_client.get(
        "/v1/integrations/oura/callback",
        params={"code": "c", "state": wearable_service.sign_state(user_id)},
    )
    conn = await _connection(async_db, user_id)
    conn.expires_at = datetime(2020, 1, 1)  # expired, so the sync must refresh first
    await async_db.commit()

    resp = await http_client.post("/v1/integrations/oura/sync", headers=headers)
    assert resp.status_code == 200, resp.text
    conn = await _connection(async_db, user_id)
    assert conn.expires_at is not None and conn.expires_at > datetime(2026, 1, 1)
    assert conn.expires_at.tzinfo is None
