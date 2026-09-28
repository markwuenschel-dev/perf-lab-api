"""Phone-pushed wellness: the ingest token and POST /v1/wellness/ingest (2026-09-28).

Apple Watch data can only leave the iPhone through something running on it. The seam is
generic (a Shortcut today, any HealthKit exporter later): a personal token that is
user-bound, hashed at rest, revocable, and able ONLY to write wellness; an idempotent
per-day write; and a fixed same-day rule between devices, Oura over Apple Watch.
"""
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.logic.wellness_source_authority import SignalCandidate, resolve_signal_source
from app.models.user import User
from app.models.wellness import WellnessSample
from app.models.wellness_ingest_token import WellnessIngestToken
from app.services.readiness_service import _resolve_day

pytestmark = pytest.mark.asyncio

_DAY = "2026-09-28"


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


async def _new_token(client, login: dict[str, str]) -> tuple[int, dict[str, str]]:
    resp = await client.post("/v1/wellness/ingest-tokens", json={"label": "iPhone"}, headers=login)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["id"], {"Authorization": f"Bearer {body['token']}"}


def _push(**over):
    return {"source": "apple_watch", "date": _DAY, "hrv_ms": 48.0, "resting_hr": 52.0,
            "sleep_hours": 7.1, "measured_at": "2026-09-28T06:30:00-04:00", **over}


async def _rows(db, user_id: int) -> list[WellnessSample]:
    db.expire_all()
    return list((await db.execute(
        select(WellnessSample).where(WellnessSample.user_id == user_id)
    )).scalars().all())


# ── the token ─────────────────────────────────────────────────────────────────────


async def test_a_token_is_shown_once_and_stored_only_as_a_hash(http_client, async_db):
    login, user_id = await _athlete(http_client, async_db, "ing-create@test.com")
    resp = await http_client.post("/v1/wellness/ingest-tokens", json={}, headers=login)
    assert resp.status_code == 201, resp.text
    token = resp.json()["token"]
    assert token.startswith("plw_") and len(token) > 40

    row = (await async_db.execute(
        select(WellnessIngestToken).where(WellnessIngestToken.user_id == user_id)
    )).scalar_one()
    assert row.token_hash != token and len(row.token_hash) == 64
    assert token.startswith(row.token_prefix)

    listed = (await http_client.get("/v1/wellness/ingest-tokens", headers=login)).json()
    assert [t["label"] for t in listed] == ["Apple Watch"]
    assert "token" not in listed[0] and listed[0]["last_used_at"] is None


async def test_a_push_writes_the_day_as_apple_watch_sdnn_in_utc(http_client, async_db):
    login, user_id = await _athlete(http_client, async_db, "ing-push@test.com")
    _, ingest = await _new_token(http_client, login)

    resp = await http_client.post("/v1/wellness/ingest", json=_push(), headers=ingest)
    assert resp.status_code == 200, resp.text

    (row,) = await _rows(async_db, user_id)
    assert (row.source, row.date, row.hrv_ms, row.hrv_metric) == (
        "apple_watch", date(2026, 9, 28), 48.0, "sdnn"
    )
    assert (row.resting_hr, row.sleep_hours) == (52.0, 7.1)
    assert row.measured_at == datetime(2026, 9, 28, 10, 30)  # converted, not truncated
    # The subjective signals are not the phone's to report.
    assert row.soreness is None and row.mood is None and row.stress is None

    listed = (await http_client.get("/v1/wellness/ingest-tokens", headers=login)).json()
    assert listed[0]["last_used_at"] is not None, "Settings shows the last successful sync"


async def test_a_retrying_automation_replaces_the_day_instead_of_duplicating(http_client, async_db):
    login, user_id = await _athlete(http_client, async_db, "ing-retry@test.com")
    _, ingest = await _new_token(http_client, login)

    for hrv in (48.0, 48.0, 51.0):
        resp = await http_client.post("/v1/wellness/ingest", json=_push(hrv_ms=hrv), headers=ingest)
        assert resp.status_code == 200, resp.text

    rows = await _rows(async_db, user_id)
    assert len(rows) == 1 and rows[0].hrv_ms == 51.0


# ── write-only, revocable, user-bound ─────────────────────────────────────────────


async def test_an_ingest_token_can_do_nothing_but_ingest(http_client, async_db):
    login, _ = await _athlete(http_client, async_db, "ing-scope@test.com")
    _, ingest = await _new_token(http_client, login)

    assert (await http_client.get("/v1/wellness", headers=ingest)).status_code == 401
    assert (await http_client.post(
        "/v1/wellness", json={"date": _DAY, "hrv_ms": 50.0}, headers=ingest
    )).status_code == 401
    assert (await http_client.get("/v1/profile", headers=ingest)).status_code == 401
    assert (await http_client.get("/v1/wellness/ingest-tokens", headers=ingest)).status_code == 401
    assert (await http_client.post(
        "/v1/wellness/ingest-tokens", json={}, headers=ingest
    )).status_code == 401


async def test_the_ingest_route_accepts_no_login_and_no_unknown_token(http_client, async_db):
    login, _ = await _athlete(http_client, async_db, "ing-login@test.com")
    assert (await http_client.post("/v1/wellness/ingest", json=_push(), headers=login)).status_code == 401
    assert (await http_client.post("/v1/wellness/ingest", json=_push())).status_code == 401
    bogus = {"Authorization": "Bearer plw_not-a-real-token"}
    assert (await http_client.post("/v1/wellness/ingest", json=_push(), headers=bogus)).status_code == 401


async def test_a_revoked_token_stops_working_and_only_its_owner_can_revoke(http_client, async_db):
    login, user_id = await _athlete(http_client, async_db, "ing-revoke@test.com")
    other, _ = await _athlete(http_client, async_db, "ing-other@test.com")
    token_id, ingest = await _new_token(http_client, login)

    assert (await http_client.delete(f"/v1/wellness/ingest-tokens/{token_id}", headers=other)).status_code == 404
    assert (await http_client.post("/v1/wellness/ingest", json=_push(), headers=ingest)).status_code == 200

    assert (await http_client.delete(f"/v1/wellness/ingest-tokens/{token_id}", headers=login)).status_code == 204
    assert (await http_client.post("/v1/wellness/ingest", json=_push(), headers=ingest)).status_code == 401
    assert (await http_client.get("/v1/wellness/ingest-tokens", headers=login)).json() == []


async def test_a_token_writes_only_for_its_own_athlete(http_client, async_db):
    login_a, user_a = await _athlete(http_client, async_db, "ing-a@test.com")
    login_b, user_b = await _athlete(http_client, async_db, "ing-b@test.com")
    id_a, ingest_a = await _new_token(http_client, login_a)
    id_b, _ = await _new_token(http_client, login_b)

    await http_client.post("/v1/wellness/ingest", json=_push(), headers=ingest_a)
    assert len(await _rows(async_db, user_a)) == 1
    assert await _rows(async_db, user_b) == []
    # Each athlete lists only their own tokens.
    assert [t["id"] for t in (await http_client.get("/v1/wellness/ingest-tokens", headers=login_a)).json()] == [id_a]
    assert [t["id"] for t in (await http_client.get("/v1/wellness/ingest-tokens", headers=login_b)).json()] == [id_b]


@pytest.mark.parametrize(
    "body",
    [
        _push(source="manual"),       # a phone does not speak for the athlete
        _push(source="oura"),         # nor impersonate a cloud-synced device
        {"source": "apple_watch", "date": _DAY},  # no reading at all
        _push(soreness=3.0),          # subjective signals are not accepted
        _push(hrv_ms=-1.0),
    ],
)
async def test_a_malformed_push_is_refused_and_writes_nothing(http_client, async_db, body):
    login, user_id = await _athlete(http_client, async_db, "ing-bad@test.com")
    _, ingest = await _new_token(http_client, login)
    assert (await http_client.post("/v1/wellness/ingest", json=body, headers=ingest)).status_code == 422
    assert await _rows(async_db, user_id) == []


# ── Oura over Apple Watch on the same day; Apple over the athlete's slider ─────────

_T = datetime(2026, 9, 28, 6, 0)


def test_a_cloud_device_outranks_a_pushed_one_even_when_older() -> None:
    chosen = resolve_signal_source("hrv_ms", [
        SignalCandidate("oura", 62.0, ingested_at=_T),
        SignalCandidate("apple_watch", 48.0, ingested_at=_T + timedelta(hours=3)),
    ])
    assert chosen == ("oura", 62.0)


def test_a_pushed_device_still_outranks_a_hand_entered_value() -> None:
    chosen = resolve_signal_source("resting_hr", [
        SignalCandidate("manual", 60.0, ingested_at=_T + timedelta(hours=3)),
        SignalCandidate("apple_watch", 52.0, ingested_at=_T),
    ])
    assert chosen == ("apple_watch", 52.0)


def test_the_athlete_still_outranks_every_device_on_felt_signals() -> None:
    chosen = resolve_signal_source("stress", [
        SignalCandidate("apple_watch", 8.0, ingested_at=_T),
        SignalCandidate("manual", 3.0, ingested_at=_T),
    ])
    assert chosen == ("manual", 3.0)


async def test_readiness_uses_the_ring_when_it_reported_and_the_watch_where_it_did_not(
    http_client, async_db
):
    """Per signal: the ring's HRV wins the day, the watch's sleep fills what the ring lacked."""
    login, user_id = await _athlete(http_client, async_db, "ing-authority@test.com")
    _, ingest = await _new_token(http_client, login)
    now = datetime.now(UTC)
    day = now.date()
    # The ring synced first; the watch pushed later. Recency alone would pick the watch.
    async_db.add(WellnessSample(
        user_id=user_id, date=day, source="oura", hrv_ms=62.0,
        created_at=datetime.combine(day, datetime.min.time()),
    ))
    await async_db.commit()

    resp = await http_client.post(
        "/v1/wellness/ingest",
        json=_push(date=day.isoformat(), hrv_ms=48.0, sleep_hours=7.1,
                   measured_at=now.isoformat()),
        headers=ingest,
    )
    assert resp.status_code == 200, resp.text

    values, sources, _ = await _resolve_day(async_db, user_id, day)
    assert (values["hrv_ms"], sources["hrv_ms"]) == (62.0, "oura")
    assert (values["sleep_hours"], sources["sleep_hours"]) == (7.1, "apple_watch")
    assert (await async_db.execute(
        select(func.count()).select_from(WellnessSample).where(WellnessSample.user_id == user_id)
    )).scalar_one() == 2
