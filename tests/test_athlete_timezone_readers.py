"""B1: per-athlete "today" through the real readers, routes and background paths.

Every test pins the one clock every default "today" reads (``athlete_calendar.utc_now``) and
gives athletes one of three zones: unknown (NULL: UTC, grace kept), explicit ``UTC`` (known,
grace 0) and a named zone. The instants are chosen so the athlete's day and the UTC day differ.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from planning_helpers import block_day_of
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import db as core_db
from app.core.config import settings
from app.integrations.base import NormalizedWellness
from app.logic import athlete_calendar
from app.models.mesocycle import (
    BlockGoal,
    BlockStatus,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.models.recovery_shadow import RecoveryShadowLog
from app.models.user import AthleteProfile, User
from app.models.wellness import WellnessSample
from app.schemas.planning import PlannedSessionUpdateRequest
from app.schemas.wellness import WellnessIngestIn, WellnessSampleIn
from app.schemas.workouts import WorkoutLog
from app.scripts import sync_wearables
from app.services import (
    dashboard_service,
    planning_service,
    readiness_service,
    state_service,
    wearable_service,
    wellness_ingest_service,
)
from app.services.missed_session_service import reconcile_missed_for_athlete
from app.services.shadow_evidence import RECORDED, SKIPPED_NOT_AS_OF_DAY
from app.services.wellness_shadow_hooks import run_wellness_shadows

LA, NY, LONDON, BRISBANE = (
    "America/Los_Angeles", "America/New_York", "Europe/London", "Australia/Brisbane")
P, M = SessionStatus.PENDING, SessionStatus.MISSED


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch):
    """Set the instant every default "today" is read at."""
    now = {"t": datetime(2026, 1, 15, 3, 0, tzinfo=UTC)}
    monkeypatch.setattr(athlete_calendar, "utc_now", lambda: now["t"])

    def set_(t: datetime) -> None:
        now["t"] = t
    return set_


@pytest.fixture
def reconcile_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RECONCILE_MISSED_SESSIONS", True)


async def _athlete(db: AsyncSession, email: str, zone: str | None,
                   source: str = "athlete") -> int:
    user = User(email=email, hashed_password="h", is_active=True)
    db.add(user)
    await db.commit()
    db.add(AthleteProfile(user_id=user.id, timezone=zone,
                          timezone_source=source if zone is not None else None))
    await db.commit()
    return user.id


async def _block(db: AsyncSession, uid: int, start: date) -> int:
    block = MesocycleBlock(
        user_id=uid, goal=BlockGoal.STRENGTH, duration_weeks=4, sessions_per_week=3,
        start_date=start, deload_every_n_weeks=4, status=BlockStatus.ACTIVE,
    )
    db.add(block)
    await db.commit()
    return block.id


async def _session(db: AsyncSession, uid: int, bid: int, day: date, start: date,
                   status: SessionStatus = P) -> int:
    row = PlannedSession(
        block_id=bid, user_id=uid, scheduled_date=day, week_number=1,
        block_day=block_day_of(start, day), day_of_week=day.isoweekday(),
        category="Heavy Lower", modality="Strength", domain="strength", status=status,
    )
    db.add(row)
    await db.commit()
    return row.id


async def _statuses(db: AsyncSession, ids: dict[date, int]) -> dict[date, SessionStatus]:
    out = {}
    for d, sid in ids.items():
        row = await db.get(PlannedSession, sid, populate_existing=True)
        assert row is not None
        out[d] = SessionStatus(row.status)
    return out


async def _days_around(db, uid, today: date) -> dict[date, int]:
    start = today - timedelta(days=10)
    bid = await _block(db, uid, start)
    return {d: await _session(db, uid, bid, d, start)
            for d in (today - timedelta(days=2), today - timedelta(days=1), today)}


# --- missed reconciliation: zone, grace, UTC midnight, DST ------------------------------------


@pytest.mark.parametrize(("zone", "now", "athlete_day", "missed_through"), [
    # UTC-8 after UTC midnight: Jan 15 03:00Z is Jan 14 in LA. Grace 0: only Jan 13 is missed.
    (LA, datetime(2026, 1, 15, 3, 0, tzinfo=UTC), date(2026, 1, 14), date(2026, 1, 13)),
    # UTC+10 before UTC midnight: Jul 15 15:00Z is Jul 16 in Brisbane.
    (BRISBANE, datetime(2026, 7, 15, 15, 0, tzinfo=UTC), date(2026, 7, 16), date(2026, 7, 15)),
    # New York spring-forward night: Mar 9 04:30Z is 00:30 EDT on Mar 9 (a fixed EST offset
    # would still say 23:30 on Mar 8).
    (NY, datetime(2026, 3, 9, 4, 30, tzinfo=UTC), date(2026, 3, 9), date(2026, 3, 8)),
    # New York fall-back night: Nov 2 04:30Z is 23:30 EST on Nov 1 (a fixed EDT offset would
    # say 00:30 on Nov 2).
    (NY, datetime(2026, 11, 2, 4, 30, tzinfo=UTC), date(2026, 11, 1), date(2026, 10, 31)),
    # London in BST: Jul 1 23:30Z is already Jul 2.
    (LONDON, datetime(2026, 7, 1, 23, 30, tzinfo=UTC), date(2026, 7, 2), date(2026, 7, 1)),
    # Explicit UTC is a known zone: grace 0 on the UTC day.
    ("UTC", datetime(2026, 1, 15, 3, 0, tzinfo=UTC), date(2026, 1, 15), date(2026, 1, 14)),
])
async def test_known_zone_reconciles_on_the_athlete_day_with_no_grace(
    async_db, clock, reconcile_on, zone, now, athlete_day, missed_through,
) -> None:
    clock(now)
    uid = await _athlete(async_db, f"b1-missed-{zone}-{now:%m%d}@test.com".lower(), zone)
    ids = await _days_around(async_db, uid, athlete_day)
    await reconcile_missed_for_athlete(async_db, uid)
    assert await _statuses(async_db, ids) == {
        d: (M if d <= missed_through else P) for d in ids}


async def test_unknown_zone_reconciles_on_the_utc_day_and_keeps_the_grace_day(
    async_db, clock, reconcile_on,
) -> None:
    clock(datetime(2026, 1, 15, 3, 0, tzinfo=UTC))  # Jan 15 in UTC
    uid = await _athlete(async_db, "b1-missed-unknown@test.com", None)
    ids = await _days_around(async_db, uid, date(2026, 1, 15))
    await reconcile_missed_for_athlete(async_db, uid)
    # Grace 1: Jan 13 is missed, Jan 14 (yesterday in UTC, maybe today for the athlete) is not.
    assert await _statuses(async_db, ids) == {
        date(2026, 1, 13): M, date(2026, 1, 14): P, date(2026, 1, 15): P}


async def test_the_sessions_route_reconciles_on_the_athlete_day(
    http_client: AsyncClient, async_db, clock, reconcile_on,
) -> None:
    clock(datetime(2026, 7, 15, 15, 0, tzinfo=UTC))  # Jul 16 in Brisbane, Jul 15 in UTC
    headers = await _register(http_client, "b1-route@test.com")
    uid = await _uid(async_db, "b1-route@test.com")
    assert (await http_client.patch("/v1/profile", json={"timezone": BRISBANE}, headers=headers)
            ).status_code == 200
    ids = await _days_around(async_db, uid, date(2026, 7, 16))
    rows = (await http_client.get("/v1/planning/sessions", headers=headers)).json()
    by_id = {r["id"]: r["status"] for r in rows}
    # Jul 14 and 15 are past on Brisbane's Jul 16 (grace 0). On the UTC day with the unknown
    # zone's grace (cutoff Jul 14) nothing would be missed, so this proves the zone was used.
    assert [by_id[ids[d]] for d in sorted(ids)] == ["missed", "missed", "pending"]


# --- the reopen floor and today's session ------------------------------------------------------


async def test_a_missed_session_reopens_onto_the_athlete_today(async_db, clock) -> None:
    clock(datetime(2026, 1, 15, 3, 0, tzinfo=UTC))  # Jan 14 in LA, Jan 15 in UTC
    uid = await _athlete(async_db, "b1-reopen@test.com", LA)
    start = date(2026, 1, 4)
    sid = await _session(async_db, uid, await _block(async_db, uid, start), date(2026, 1, 10),
                         start, status=M)
    moved = await planning_service.update_session(
        async_db, uid, sid,
        PlannedSessionUpdateRequest(status=P, scheduled_date=date(2026, 1, 14)))
    assert moved is not None and moved.scheduled_date == date(2026, 1, 14)

    unknown = await _athlete(async_db, "b1-reopen-unknown@test.com", None)
    usid = await _session(async_db, unknown, await _block(async_db, unknown, start),
                          date(2026, 1, 10), start, status=M)
    with pytest.raises(HTTPException) as refused:  # the UTC day is Jan 15: Jan 14 is past
        await planning_service.update_session(
            async_db, unknown, usid,
            PlannedSessionUpdateRequest(status=P, scheduled_date=date(2026, 1, 14)))
    assert refused.value.status_code == 409


async def test_today_session_is_the_athlete_day(async_db, clock) -> None:
    clock(datetime(2026, 7, 15, 15, 0, tzinfo=UTC))  # Jul 16 in Brisbane, Jul 15 in UTC
    uid = await _athlete(async_db, "b1-today@test.com", BRISBANE)
    ids = await _days_around(async_db, uid, date(2026, 7, 16))
    today = await planning_service.get_today_session(async_db, uid)
    assert today is not None and today.id == ids[date(2026, 7, 16)]


# --- matching a log to its planned session -----------------------------------------------------


async def test_an_evening_log_west_of_utc_matches_its_own_day(async_db, clock) -> None:
    uid = await _athlete(async_db, "b1-match@test.com", LA)
    ids = await _days_around(async_db, uid, date(2026, 1, 15))
    # 19:00 in LA on Jan 14, sent as the web sends it: a UTC instant.
    log = WorkoutLog(timestamp=datetime(2026, 1, 15, 3, 0, tzinfo=UTC), modality="Running",
                     duration_minutes=30.0, session_rpe=6.0)
    matched = await state_service._match_planned_session(async_db, uid, log)
    assert matched is not None and matched.id == ids[date(2026, 1, 14)]
    await async_db.rollback()

    unknown = await _athlete(async_db, "b1-match-unknown@test.com", None)
    uids = await _days_around(async_db, unknown, date(2026, 1, 15))
    matched = await state_service._match_planned_session(async_db, unknown, log)
    assert matched is not None and matched.id == uids[date(2026, 1, 15)]  # wall clock as sent
    await async_db.rollback()


# --- readiness, the dashboard and the wellness paths -----------------------------------------


async def test_readiness_counts_the_athlete_day_as_fresh(async_db, clock) -> None:
    clock(datetime(2026, 1, 15, 3, 0, tzinfo=UTC))
    for zone, fresh in ((LA, True), (None, False)):
        uid = await _athlete(async_db, f"b1-ready-{zone or 'unknown'}@test.com".lower(), zone)
        await readiness_service.upsert_wellness_sample(
            async_db, uid, WellnessSampleIn(date=date(2026, 1, 14), source="manual", hrv_ms=60.0))
        score = await readiness_service.compute_readiness(async_db, uid)
        summary = score.confidence.signal_summary
        assert ("hrv" in summary.provided, "hrv" in summary.stale) == (fresh, not fresh), zone


async def test_dashboard_days_are_the_athlete_days(async_db, clock) -> None:
    clock(datetime(2026, 1, 15, 7, 30, tzinfo=UTC))  # Jan 14 23:30 in LA
    for zone, streak in ((LA, 1), (None, 2)):
        uid = await _athlete(async_db, f"b1-dash-{zone or 'unknown'}@test.com".lower(), zone)
        # 01:00 and 23:00 on Jan 14 in LA: one LA day, two UTC days.
        for ts in (datetime(2026, 1, 14, 9, 0), datetime(2026, 1, 15, 7, 0)):
            await async_db.execute(text(
                "INSERT INTO workout_logs (user_id, logged_at, session_timestamp, modality, "
                "duration_minutes, session_rpe) VALUES (:u, now(), :t, 'Running', 30, 6)"), {"u": uid, "t": ts})
        await async_db.commit()
        metrics = await dashboard_service.overview_metrics(async_db, uid)
        assert metrics.adherence.streak_days == streak, zone


async def test_phone_ingest_without_a_date_lands_on_the_athlete_day(async_db, clock) -> None:
    clock(datetime(2026, 1, 15, 3, 0, tzinfo=UTC))
    for zone, day in ((LA, date(2026, 1, 14)), (None, date(2026, 1, 15))):
        uid = await _athlete(async_db, f"b1-ingest-{zone or 'unknown'}@test.com".lower(), zone)
        _, token = await wellness_ingest_service.create_token(async_db, uid, "phone")
        row = await wellness_ingest_service.authenticate(async_db, token)
        assert row is not None
        out = await wellness_ingest_service.ingest(
            async_db, row, WellnessIngestIn(source="apple_watch", hrv_ms=50.0,
                                            measured_at=datetime(2026, 1, 15, 3, 0, tzinfo=UTC)))
        assert out.date == day, zone


async def test_the_recovery_shadow_records_for_the_athlete_day(async_db, clock) -> None:
    """No request: the shadows resolve the athlete's day themselves, in their own session."""
    as_of = datetime(2026, 1, 15, 3, 0, tzinfo=UTC)
    for zone, kind in ((LA, "recorded"), (None, "skipped_not_as_of_day")):
        uid = await _athlete(async_db, f"b1-shadow-{zone or 'unknown'}@test.com".lower(), zone)
        sample = await readiness_service.upsert_wellness_sample(
            async_db, uid, WellnessSampleIn(date=date(2026, 1, 14), source="manual", hrv_ms=60.0))
        outcomes = await run_wellness_shadows(async_db, sample, as_of=as_of,
                                              assimilate_soreness=False)
        assert outcomes.recovery == kind, zone
        logged = (await async_db.execute(select(RecoveryShadowLog.decision_impact).where(
            RecoveryShadowLog.user_id == uid))).scalars().all()
        assert logged == [RECORDED if kind == "recorded" else SKIPPED_NOT_AS_OF_DAY]


# --- the wearable sync CLI: a background job with no request ---------------------------------


class _WindowOura:
    def __init__(self) -> None:
        self.windows: list[tuple[date, date]] = []

    async def fetch_daily_wellness(self, access_token, start, end):
        self.windows.append((start, end))
        return [NormalizedWellness(day=end, hrv_ms=55.0, raw={})]


async def test_the_sync_cli_fetches_through_the_athlete_day(async_db, clock, monkeypatch) -> None:
    from cryptography.fernet import Fernet

    from app.core import crypto
    monkeypatch.setattr(settings, "APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    crypto._fernet_for.cache_clear()
    fake = _WindowOura()
    monkeypatch.setattr(wearable_service, "_adapter", lambda provider="oura": fake)
    clock(datetime(2026, 7, 15, 15, 0, tzinfo=UTC))  # Jul 16 in Brisbane, Jul 15 in UTC
    try:
        users = {}
        for zone in (BRISBANE, None, LA):
            uid = await _athlete(async_db, f"b1-sync-{zone or 'unknown'}@test.com".lower(), zone)
            await wearable_service.upsert_connection(
                async_db, uid, provider="oura", auth_type="pat",
                access_enc=crypto.encrypt("tok"), refresh_enc=None, expires_at=None, scope=None)
            await async_db.commit()
            users[uid] = zone
        engine_sessions = core_db.AsyncSessionLocal
        monkeypatch.setattr(core_db, "AsyncSessionLocal", _bound_to(async_db, engine_sessions))
        assert await sync_wearables._run(days=3, user_id=None) == 0
    finally:
        crypto._fernet_for.cache_clear()
    ends = sorted(end for _, end in fake.windows)
    # Brisbane reaches its own Jul 16; unknown and LA (behind UTC) end on the UTC Jul 15, so no
    # athlete's window is ever narrower than the old UTC one.
    assert ends == [date(2026, 7, 15), date(2026, 7, 15), date(2026, 7, 16)]
    stored = (await async_db.execute(select(WellnessSample.user_id, WellnessSample.date))).all()
    assert {(users[u], d) for u, d in stored} == {
        (BRISBANE, date(2026, 7, 16)), (None, date(2026, 7, 15)), (LA, date(2026, 7, 15))}


def _bound_to(async_db: AsyncSession, factory):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    return async_sessionmaker(async_db.bind, expire_on_commit=False, autoflush=False)


# --- history: no backfill, and a zone change mid-history -------------------------------------


async def test_a_zone_change_rewrites_no_stored_date(
    http_client: AsyncClient, async_db, clock, reconcile_on,
) -> None:
    clock(datetime(2026, 1, 15, 3, 0, tzinfo=UTC))
    headers = await _register(http_client, "b1-history@test.com")
    uid = await _uid(async_db, "b1-history@test.com")
    ids = await _days_around(async_db, uid, date(2026, 1, 15))
    await readiness_service.upsert_wellness_sample(
        async_db, uid, WellnessSampleIn(date=date(2026, 1, 14), source="manual", hrv_ms=60.0))
    await async_db.execute(text(
        "INSERT INTO workout_logs (user_id, logged_at, session_timestamp, modality, "
        "duration_minutes, session_rpe) VALUES (:u, now(), '2026-01-15 03:00', 'Running', 30, 6)"), {"u": uid})
    await async_db.commit()

    async def stored() -> tuple:
        sessions = (await async_db.execute(text(
            "SELECT id, scheduled_date FROM planned_sessions WHERE user_id = :u ORDER BY id"),
            {"u": uid})).all()
        wellness = (await async_db.execute(text(
            "SELECT date FROM wellness_samples WHERE user_id = :u"), {"u": uid})).all()
        logs = (await async_db.execute(text(
            "SELECT session_timestamp FROM workout_logs WHERE user_id = :u"), {"u": uid})).all()
        return [tuple(r) for r in sessions], [tuple(r) for r in wellness], [tuple(r) for r in logs]

    before = await stored()
    # Unknown: the UTC day with grace. Then the athlete moves to LA, then to Brisbane.
    await http_client.get("/v1/planning/sessions", headers=headers)
    assert (await _statuses(async_db, ids))[date(2026, 1, 14)] == P
    for zone, missed in ((LA, {date(2026, 1, 13)}), (BRISBANE, {date(2026, 1, 13), date(2026, 1, 14)})):
        r = await http_client.patch("/v1/profile", json={"timezone": zone}, headers=headers)
        assert r.status_code == 200 and r.json()["timezone"] == zone
        await http_client.get("/v1/planning/sessions", headers=headers)
        assert {d for d, s in (await _statuses(async_db, ids)).items() if s == M} == missed
        assert await stored() == before  # no stored date or instant moved


# --- the profile API: capture-when-unknown, choose, validate ---------------------------------


async def test_browser_capture_only_fills_an_unknown_zone(http_client: AsyncClient) -> None:
    headers = await _register(http_client, "b1-capture@test.com")
    got = (await http_client.get("/v1/profile", headers=headers)).json()
    assert (got["timezone"], got["timezone_source"]) == (None, None)

    first = await http_client.post("/v1/profile/timezone/detected",
                                   json={"timezone": NY}, headers=headers)
    assert first.status_code == 200
    assert (first.json()["timezone"], first.json()["timezone_source"]) == (NY, "browser")
    # A later login from another zone (a trip, another device) never replaces it.
    again = await http_client.post("/v1/profile/timezone/detected",
                                   json={"timezone": LA}, headers=headers)
    assert (again.json()["timezone"], again.json()["timezone_source"]) == (NY, "browser")

    chosen = await http_client.patch("/v1/profile", json={"timezone": "UTC"}, headers=headers)
    assert (chosen.json()["timezone"], chosen.json()["timezone_source"]) == ("UTC", "athlete")
    after = await http_client.post("/v1/profile/timezone/detected",
                                   json={"timezone": LA}, headers=headers)
    assert (after.json()["timezone"], after.json()["timezone_source"]) == ("UTC", "athlete")


async def test_capture_creates_a_missing_profile(http_client: AsyncClient, async_db) -> None:
    headers = await _register(http_client, "b1-capture-new@test.com")
    uid = await _uid(async_db, "b1-capture-new@test.com")
    await async_db.execute(text("DELETE FROM athlete_profiles WHERE user_id = :u"), {"u": uid})
    await async_db.commit()
    r = await http_client.post("/v1/profile/timezone/detected", json={"timezone": LONDON},
                               headers=headers)
    assert r.status_code == 200 and r.json()["timezone"] == LONDON


@pytest.mark.parametrize("bad", ["Mars/Olympus", "", "localtime", "+05:00", None])
async def test_zones_are_validated_on_both_writes(http_client: AsyncClient, bad) -> None:
    headers = await _register(http_client, f"b1-bad-{abs(hash(bad))}@test.com")
    assert (await http_client.post("/v1/profile/timezone/detected", json={"timezone": bad},
                                   headers=headers)).status_code == 422
    assert (await http_client.patch("/v1/profile", json={"timezone": bad},
                                    headers=headers)).status_code == 422
    got = (await http_client.get("/v1/profile", headers=headers)).json()
    assert got["timezone"] is None


async def test_a_stored_zone_zoneinfo_cannot_load_reads_as_unknown(async_db, clock) -> None:
    from app.services.athlete_calendar_service import calendar_for
    uid = await _athlete(async_db, "b1-bad-stored@test.com", "Atlantis/Capital")
    cal = await calendar_for(async_db, uid)
    assert not cal.known and cal.missed_grace_days == 1


# --- helpers ---------------------------------------------------------------------------------


async def _register(client: AsyncClient, email: str) -> dict[str, str]:
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token", data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert tok.status_code == 200, tok.text
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}


async def _uid(db: AsyncSession, email: str) -> int:
    return (await db.execute(select(User.id).where(User.email == email))).scalar_one()


# --- review round 1: the constraint engine's days, one clock, watermarks, races -------------


def test_back_to_back_quality_days_are_counted_on_the_athlete_days() -> None:
    """07:00 Monday and 18:00 Tuesday in LA are back to back, though their UTC dates (Mon 15:00Z,
    Wed 02:00Z) are two days apart."""
    from app.logic.athlete_calendar import AthleteCalendar
    from app.logic.constraint_engine.constraints_impl import (
        run_no_back_to_back_threshold_and_vo2_days,
    )
    from app.logic.constraint_engine.types import ConstraintContext

    la = AthleteCalendar(LA)
    tue = datetime(2026, 1, 14, 2, 0, tzinfo=UTC)   # Tue Jan 13 18:00 in LA
    mon = datetime(2026, 1, 12, 15, 0, tzinfo=UTC)  # Mon Jan 12 07:00 in LA
    rows = [
        {"session_timestamp": tue.isoformat(), "tags": ["threshold_or_vo2"]},
        {"session_timestamp": mon.isoformat(), "tags": ["threshold_or_vo2"]},
    ]
    candidate = {"intensity_bucket": "high", "tags": ["threshold_or_vo2"]}
    with_days = [
        dict(r, athlete_day=la.day_of(datetime.fromisoformat(r["session_timestamp"])).isoformat())
        for r in rows
    ]
    result = run_no_back_to_back_threshold_and_vo2_days(
        candidate, ConstraintContext(goal="Endurance", recent_sessions=with_days))  # type: ignore[arg-type]
    assert not result.passed
    # Without an athlete calendar the rule reads the timestamps' own dates, as before B1.
    result = run_no_back_to_back_threshold_and_vo2_days(
        candidate, ConstraintContext(goal="Endurance", recent_sessions=rows))  # type: ignore[arg-type]
    assert result.passed


async def test_recent_summaries_carry_the_athlete_day(async_db, clock) -> None:
    from app.logic.workout_history import recent_workout_summaries
    from app.services.athlete_calendar_service import calendar_for

    uid = await _athlete(async_db, "b1-summaries@test.com", LA)
    await async_db.execute(text(
        "INSERT INTO workout_logs (user_id, logged_at, session_timestamp, modality, "
        "duration_minutes, session_rpe) VALUES (:u, now(), '2026-01-14 02:00', 'Running', 30, 8)"),
        {"u": uid})
    await async_db.commit()
    calendar = await calendar_for(async_db, uid)
    summaries = await recent_workout_summaries(async_db, uid, days=36500, calendar=calendar)
    assert [s["athlete_day"] for s in summaries] == ["2026-01-13"]
    plain = await recent_workout_summaries(async_db, uid, days=36500)
    assert "athlete_day" not in plain[0]


async def test_a_prescription_reads_the_clock_once(async_db, monkeypatch) -> None:
    """The day the planning route resolved reaches the objective taper and readiness unchanged:
    nothing in the context reads the clock a second time."""
    from app.services import prescription_service

    uid = await _athlete(async_db, "b1-one-clock@test.com", BRISBANE)
    seen: dict[str, date | None] = {}

    async def signals(db, user_id, *, today=None):
        seen["objective"] = today
        return {"taper": False, "domain": None}

    async def readiness(db, user_id, *, today=None):
        seen["readiness"] = today
        raise RuntimeError("stop after the context is gathered")

    monkeypatch.setattr(prescription_service, "active_objective_signals", signals)
    monkeypatch.setattr(prescription_service.readiness_service, "compute_readiness", readiness)
    with pytest.raises(RuntimeError, match="stop after"):
        await prescription_service._gather_prescription_context(
            async_db, uid, None, None, date(2030, 1, 1))
    assert seen == {"objective": date(2030, 1, 1), "readiness": date(2030, 1, 1)}


@pytest.mark.parametrize(("zone", "watermark", "window"), [
    # Brisbane: the watermark's UTC date (Jul 15) is earlier than its local one (Jul 16); the
    # window starts from the earlier and ends on Brisbane's Jul 16.
    (BRISBANE, datetime(2026, 7, 15, 14, 30), (date(2026, 7, 14), date(2026, 7, 16))),
    # LA: the watermark is Jul 14 locally, Jul 15 in UTC: the window starts a day earlier than
    # the old UTC rule (Jul 14) would, and ends on the UTC Jul 15.
    (LA, datetime(2026, 7, 15, 2, 0), (date(2026, 7, 13), date(2026, 7, 15))),
    (None, datetime(2026, 7, 15, 2, 0), (date(2026, 7, 14), date(2026, 7, 15))),
])
async def test_a_watermarked_sync_starts_from_the_earlier_reading(
    async_db, clock, monkeypatch, zone, watermark, window,
) -> None:
    from cryptography.fernet import Fernet

    from app.core import crypto
    monkeypatch.setattr(settings, "APP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    crypto._fernet_for.cache_clear()
    fake = _WindowOura()
    monkeypatch.setattr(wearable_service, "_adapter", lambda provider="oura": fake)
    clock(datetime(2026, 7, 15, 15, 0, tzinfo=UTC))
    try:
        uid = await _athlete(async_db, f"b1-wm-{zone or 'unknown'}@test.com".lower(), zone)
        await wearable_service.upsert_connection(
            async_db, uid, provider="oura", auth_type="pat",
            access_enc=crypto.encrypt("tok"), refresh_enc=None, expires_at=None, scope=None)
        await async_db.execute(text(
            "UPDATE wearable_connections SET last_sync_at = :t WHERE user_id = :u"),
            {"t": watermark, "u": uid})
        await async_db.commit()
        monkeypatch.setattr(core_db, "AsyncSessionLocal", _bound_to(async_db, None))
        assert await sync_wearables._run(days=3, user_id=uid) == 0
    finally:
        crypto._fernet_for.cache_clear()
    assert fake.windows == [window]


@pytest.fixture
async def concurrent_http(async_db):
    """The real app with a fresh session per request, so two requests really run at once."""
    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.db import get_db
    from app.main import app
    factory = async_sessionmaker(async_db.bind, expire_on_commit=False, autoflush=False)

    async def _get_db():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def test_racing_captures_and_choices_never_put_a_capture_over_a_known_zone(
    concurrent_http,
) -> None:
    import asyncio

    from app.core.rate_limit import auth_limiter
    for round_ in range(6):
        auth_limiter.reset()  # six registrations from one client address in one test
        headers = await _register(concurrent_http, f"b1-race-{round_}@test.com")

        def capture(zone: str, h: dict[str, str] = headers):
            return concurrent_http.post(
                "/v1/profile/timezone/detected", json={"timezone": zone}, headers=h)

        # Two tabs capture at once: exactly one wins, and it is a capture.
        a, b = await asyncio.gather(capture(NY), capture(LA))
        assert a.status_code == b.status_code == 200
        first = (await concurrent_http.get("/v1/profile", headers=headers)).json()
        assert first["timezone"] in (NY, LA) and first["timezone_source"] == "browser"
        # A capture racing the athlete's choice: the choice is what remains.
        choice, late = await asyncio.gather(
            concurrent_http.patch("/v1/profile", json={"timezone": LONDON}, headers=headers),
            capture(BRISBANE))
        assert choice.status_code == late.status_code == 200
        final = (await concurrent_http.get("/v1/profile", headers=headers)).json()
        assert (final["timezone"], final["timezone_source"]) == (LONDON, "athlete")


async def test_an_old_client_round_trips_a_profile_without_touching_the_zone(
    http_client: AsyncClient,
) -> None:
    """A client built before B1 sends no zone fields: its PATCH keeps the stored zone and every
    other field it did not send."""
    headers = await _register(http_client, "b1-old-client@test.com")
    await http_client.patch("/v1/profile", json={"timezone": LA}, headers=headers)
    before = (await http_client.get("/v1/profile", headers=headers)).json()
    r = await http_client.patch(
        "/v1/profile", json={"display_name": "Old Web", "available_days_per_week": 5},
        headers=headers)
    assert r.status_code == 200
    after = r.json()
    assert (after["timezone"], after["timezone_source"]) == (LA, "athlete")
    sent = {"display_name", "available_days_per_week"}
    assert {k: v for k, v in after.items() if k not in sent} == {
        k: v for k, v in before.items() if k not in sent}
    unknown = await _register(http_client, "b1-old-client-unknown@test.com")
    r = await http_client.patch("/v1/profile", json={"display_name": "x"}, headers=unknown)
    assert (r.json()["timezone"], r.json()["timezone_source"]) == (None, None)


# --- zone-differing readers: Brisbane's Jul 16 vs the UTC Jul 15 ----------------------------

_JUL15_15Z = datetime(2026, 7, 15, 15, 0, tzinfo=UTC)  # Jul 16 in Brisbane, Jul 15 in UTC


async def test_objective_countdown_and_taper_use_the_athlete_day(async_db, clock) -> None:
    from app.schemas.objective import ObjectiveCreate
    from app.services import objective_service
    clock(_JUL15_15Z)
    # Jul 30 is 14 days from Brisbane's Jul 16 (inside the 14-day taper window), 15 from Jul 15.
    for zone, days, taper in ((BRISBANE, 14, True), (None, 15, False)):
        uid = await _athlete(async_db, f"b1-obj-{zone or 'unknown'}@test.com".lower(), zone)
        obj = await objective_service.create_objective(
            async_db, uid, ObjectiveCreate(label="Race", target_date=date(2026, 7, 30)))
        read = await objective_service.to_read_schema(async_db, obj)
        assert read.days_to_go == days, zone
        signals = await objective_service.active_objective_signals(async_db, uid)
        assert signals["taper"] is taper, zone


async def test_macrocycle_phase_progress_and_start_use_the_athlete_day(async_db, clock) -> None:
    from app.schemas.macrocycle import MacrocycleCreate
    from app.schemas.objective import ObjectiveCreate
    from app.services import macrocycle_service, objective_service
    clock(_JUL15_15Z)
    for zone, phase, week, start in ((BRISBANE, "completed", 2, date(2026, 7, 16)),
                                     (None, "current", 1, date(2026, 7, 15))):
        uid = await _athlete(async_db, f"b1-macro-{zone or 'unknown'}@test.com".lower(), zone)
        obj = await objective_service.create_objective(
            async_db, uid, ObjectiveCreate(label="Race", target_date=date(2026, 9, 1)))
        defaulted = await macrocycle_service.create_macrocycle(
            async_db, uid, MacrocycleCreate(objective_id=obj.id))
        assert defaulted is not None and defaulted.start_date == start, zone
        macro = await macrocycle_service.create_macrocycle(
            async_db, uid, MacrocycleCreate(objective_id=obj.id, start_date=date(2026, 7, 9)))
        assert macro is not None
        async_db.add(MesocycleBlock(
            user_id=uid, goal=BlockGoal.STRENGTH, duration_weeks=1, sessions_per_week=3,
            start_date=date(2026, 7, 9), deload_every_n_weeks=4, status=BlockStatus.ACTIVE,
            macrocycle_id=macro.id))
        await async_db.commit()
        read = await macrocycle_service.to_read_schema(async_db, macro)
        assert read.blocks[0].phase == phase, zone
        assert read.week_progress.current_week == week, zone


async def test_week_review_and_projection_default_to_the_athlete_day(async_db, clock) -> None:
    from app.services import planning_projection_service, week_review_service
    clock(_JUL15_15Z)
    for zone, week, refused in ((BRISBANE, 2, True), (None, 1, False)):
        uid = await _athlete(async_db, f"b1-week-{zone or 'unknown'}@test.com".lower(), zone)
        await _block(async_db, uid, date(2026, 7, 9))
        review = await week_review_service.build_week_review(async_db, uid)
        assert review.window is not None and review.window.week_number == week, zone
        # `through` = the UTC Jul 15 is in Brisbane's past: refused there, fine while unknown.
        if refused:
            with pytest.raises(planning_projection_service.ProjectionWindowError):
                await planning_projection_service.planned_week_projection(
                    async_db, uid, date(2026, 7, 15))
        else:
            await planning_projection_service.planned_week_projection(
                async_db, uid, date(2026, 7, 15))


async def test_minor_flag_turns_over_on_the_athlete_birthday(async_db, clock) -> None:
    from app.services import onboarding_service
    clock(_JUL15_15Z)
    # Born 2010-07-16: 16 (no longer a minor, MINOR_AGE_YEARS = 16) on Brisbane's Jul 16.
    for zone, minor in ((BRISBANE, False), (None, True)):
        uid = await _athlete(async_db, f"b1-minor-{zone or 'unknown'}@test.com".lower(), zone)
        await async_db.execute(text(
            "UPDATE athlete_profiles SET date_of_birth = '2010-07-16' WHERE user_id = :u"),
            {"u": uid})
        await async_db.commit()
        state = await onboarding_service.get_onboarding_state(async_db, uid)
        assert state.is_minor is minor, zone
