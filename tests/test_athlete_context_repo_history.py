"""AUD-C15: repository history reads + the ``state_service.load_recent_state_snapshots`` loader.

Locks the contracts the ``/v1/state-history`` and ``/v1/workouts`` routes now delegate to,
behind the repository seam instead of inline in the route (CONTEXT.md): ordering, limit,
athlete scoping, and empty-result behavior.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.vectors import FatigueState
from app.engine.simulate import baseline_state
from app.engine.state_bridge import athlete_state_kwargs_from_unified
from app.models.athlete_state import AthleteState
from app.models.user import User
from app.models.workout_log import WorkoutLog
from app.repositories.athlete_context_repository import AthleteContextRepository
from app.schemas.history import WorkoutLogSummary
from app.services import state_service

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 1, 1, 12, 0, 0)


async def _mk_user(db, email: str) -> User:
    u = User(email=email, hashed_password="h", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _state_row(user_id: int, when: datetime, aerobic: float | None = None) -> AthleteState:
    s = baseline_state(when=when)
    for k in FatigueState.KEYS:
        setattr(s.fatigue_f, k, 10.0)
    if aerobic is not None:
        s.capacity_x.aerobic = aerobic  # marker to identify a row through the loader
    return AthleteState(user_id=user_id, **athlete_state_kwargs_from_unified(s))


def _workout(user_id: int, when: datetime) -> WorkoutLog:
    return WorkoutLog(
        user_id=user_id,
        logged_at=when,
        session_timestamp=when,
        modality="Mixed",
        duration_minutes=30.0,
        session_rpe=5.0,
    )


async def _seed_states(db, user_id: int, days: list[int]) -> None:
    for d in days:
        db.add(_state_row(user_id, _T0 + timedelta(days=d)))
    await db.commit()


async def test_list_recent_states_newest_first_and_limited(async_db):
    user = await _mk_user(async_db, "hist_states@test.com")
    await _seed_states(async_db, user.id, [0, 1, 2, 3, 4])
    recent = await AthleteContextRepository(async_db).list_recent_states(user.id, limit=3)

    ts = [r.timestamp for r in recent]
    assert len(ts) == 3
    assert ts == sorted(ts, reverse=True)  # newest first
    assert ts[0] == _T0 + timedelta(days=4)


async def test_list_states_ascending_returns_full_history_oldest_first(async_db):
    user = await _mk_user(async_db, "hist_asc@test.com")
    await _seed_states(async_db, user.id, [2, 0, 1])  # insert out of order
    rows = await AthleteContextRepository(async_db).list_states_ascending(user.id)

    ts = [r.timestamp for r in rows]
    assert len(ts) == 3
    assert ts == sorted(ts)  # oldest first


async def test_load_recent_state_snapshots_returns_oldest_to_newest(async_db):
    user = await _mk_user(async_db, "hist_loader@test.com")
    await _seed_states(async_db, user.id, [0, 1, 2, 3])
    vectors = await state_service.load_recent_state_snapshots(async_db, user.id, limit=2)

    # limit=2 -> the two most recent rows, returned oldest->newest (chart order)
    assert len(vectors) == 2
    assert vectors[0].timestamp < vectors[1].timestamp
    assert vectors[1].timestamp == _T0 + timedelta(days=3)


async def test_list_recent_states_breaks_timestamp_ties_by_id_desc(async_db):
    """Two snapshots at the SAME timestamp must order deterministically by id DESC —
    row-indexed scrubbing needs a total order, not timestamp-only (which ties)."""
    user = await _mk_user(async_db, "hist_tie@test.com")
    same = _T0 + timedelta(days=5)
    r1 = _state_row(user.id, same, aerobic=301.0)  # inserted first → smaller id
    r2 = _state_row(user.id, same, aerobic=302.0)  # inserted second → larger id
    async_db.add(r1)
    async_db.add(r2)
    await async_db.commit()
    await async_db.refresh(r1)
    await async_db.refresh(r2)
    assert r1.id < r2.id

    recent = await AthleteContextRepository(async_db).list_recent_states(user.id, limit=10)
    tie_ids = [r.id for r in recent if r.timestamp == same]
    assert tie_ids == [r2.id, r1.id]  # id DESC on the tie

    # The loader reverses to chart order → the tie comes out id ASC, stably.
    vectors = await state_service.load_recent_state_snapshots(async_db, user.id, limit=10)
    tie_aerobic = [v.capacity_x.aerobic for v in vectors if v.timestamp == same]
    assert tie_aerobic == [301.0, 302.0]  # r1 (smaller id) first


async def test_load_recent_state_snapshots_carry_ids_in_chart_order(async_db):
    """The history projection carries each row's persisted id as snapshot_id, in
    oldest→newest chart order — the durable identity the deep-link/scrub key on."""
    user = await _mk_user(async_db, "hist_snap_ids@test.com")
    await _seed_states(async_db, user.id, [0, 1, 2])
    repo_newest_first = await AthleteContextRepository(async_db).list_recent_states(user.id, limit=10)

    snaps = await state_service.load_recent_state_snapshots(async_db, user.id, limit=10)
    ids = [s.snapshot_id for s in snaps]
    # oldest→newest == reverse of the repo's newest-first ids
    assert ids == [r.id for r in reversed(repo_newest_first)]
    # every projection carries the full 8-axis confidence-status map
    assert all(len(s.capacity_confidence_status) == 8 for s in snaps)


async def test_history_reads_are_user_scoped_and_empty_is_empty(async_db):
    a = await _mk_user(async_db, "hist_a@test.com")
    b = await _mk_user(async_db, "hist_b@test.com")
    await _seed_states(async_db, a.id, [0, 1])
    repo = AthleteContextRepository(async_db)

    assert len(await repo.list_recent_states(b.id, limit=10)) == 0
    assert len(await repo.list_states_ascending(b.id)) == 0
    assert await state_service.load_recent_state_snapshots(async_db, b.id, 10) == []


async def test_list_recent_workouts_newest_first_limited_and_scoped(async_db):
    a = await _mk_user(async_db, "hist_wko_a@test.com")
    b = await _mk_user(async_db, "hist_wko_b@test.com")
    for d in [0, 1, 2]:
        async_db.add(_workout(a.id, _T0 + timedelta(days=d)))
    async_db.add(_workout(b.id, _T0))
    await async_db.commit()
    repo = AthleteContextRepository(async_db)

    recent = await repo.list_recent_workouts(a.id, limit=2)
    logged = [w.logged_at for w in recent]
    assert len(logged) == 2
    assert logged == sorted(logged, reverse=True)  # newest first
    assert logged[0] == _T0 + timedelta(days=2)
    assert len(await repo.list_recent_workouts(b.id, limit=10)) == 1  # scoped


def _distinct_workout(user_id: int, when: datetime, n: int) -> WorkoutLog:
    """A workout whose every ``WorkoutLogSummary`` field carries a value unique to ``n``.

    The loader-vs-repository comparison below is only as strong as the data it runs on: if
    every seeded row shared a modality/duration/RPE, a projection that read the wrong row —
    or the wrong field — would still compare equal on all but ``logged_at``. Varying every
    field makes each assertion able to fail independently.
    """
    return WorkoutLog(
        user_id=user_id,
        logged_at=when,
        session_timestamp=when + timedelta(minutes=n),
        modality=f"Modality-{n}",
        duration_minutes=30.0 + n,
        session_rpe=5.0 + n * 0.5,
        distance_meters=100.0 * n,
        total_volume_load=1000.0 + n,
        is_benchmark=bool(n % 2),
    )


async def test_load_recent_workouts_loader_matches_the_repository(async_db):
    """``state_service.load_recent_workouts`` returns exactly the repository's rows, projected.

    Calls ``AthleteContextRepository.list_recent_workouts`` and the loader against the same
    seeded data, same user, same limit, and asserts row-for-row correspondence.

    The two sides are not directly comparable objects: the repository returns ``WorkoutLog``
    ORM rows and the loader returns ``WorkoutLogSummary`` schema objects, so the comparison
    projects the schema side onto its source row — for each field in
    ``WorkoutLogSummary.model_fields``, the loader's value must equal that attribute on the
    paired ORM row. The field list is derived from the model rather than hand-listed, so a
    field added to the schema later is compared automatically instead of silently escaping.
    Reading the expected values off the ORM row (not off literals, and not by re-running the
    loader's own projection) is what makes this a check of the loader against the repository
    rather than a restatement of the loader's implementation.

    Also holds the loader to the repository's ordering, limit, and athlete scoping.
    """
    a = await _mk_user(async_db, "hist_loader_a@test.com")
    b = await _mk_user(async_db, "hist_loader_b@test.com")
    for n, d in enumerate([0, 1, 2]):
        async_db.add(_distinct_workout(a.id, _T0 + timedelta(days=d), n + 1))
    async_db.add(_distinct_workout(b.id, _T0, 9))
    await async_db.commit()

    repo_rows = await AthleteContextRepository(async_db).list_recent_workouts(a.id, limit=2)
    loaded = await state_service.load_recent_workouts(async_db, a.id, 2)

    # Same number of rows, and the same rows in the same order (identity via primary key).
    assert len(loaded) == len(repo_rows) == 2
    assert [s.id for s in loaded] == [r.id for r in repo_rows]

    # Row-for-row, field-for-field: every WorkoutLogSummary field matches its source row.
    compared: set[str] = set()
    for summary, row in zip(loaded, repo_rows, strict=True):
        for field in WorkoutLogSummary.model_fields:
            assert getattr(summary, field) == getattr(row, field), (
                f"loader/repository mismatch on {field!r} for workout id={row.id}: "
                f"loader={getattr(summary, field)!r} repository={getattr(row, field)!r}"
            )
            compared.add(field)
    # Guard the comparison: prove it actually visited every field of the schema.
    assert compared == set(WorkoutLogSummary.model_fields)

    # Ordering is the repository's contract, restated on the loader's own output.
    assert [s.logged_at for s in loaded] == [
        _T0 + timedelta(days=2),
        _T0 + timedelta(days=1),
    ]

    # Athlete scoping and the empty case, both loader-vs-repository.
    b_rows = await AthleteContextRepository(async_db).list_recent_workouts(b.id, limit=10)
    b_loaded = await state_service.load_recent_workouts(async_db, b.id, 10)
    assert [s.id for s in b_loaded] == [r.id for r in b_rows] == [b_rows[0].id]

    absent = b.id + 999
    assert await AthleteContextRepository(async_db).list_recent_workouts(absent, limit=10) == []
    assert await state_service.load_recent_workouts(async_db, absent, 10) == []


async def _register_and_get_token(client, email: str, password: str) -> str:
    """Register a user and return a Bearer token string (pattern from test_dashboard_routes)."""
    reg = await client.post("/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert tok.status_code == 200, tok.text
    return tok.json()["access_token"]


async def test_get_workouts_route_returns_summaries_most_recent_first(http_client, async_db):
    """GET /v1/workouts: 200 + the athlete's own workout summaries, newest first.

    Locks the wire contract of the route across the delegation change — the handler now calls
    ``state_service.load_recent_workouts`` instead of building the repository itself, and the
    payload must be identical.
    """
    email = "hist_route_wko@test.com"
    token = await _register_and_get_token(http_client, email, "securepass1")
    me = (await async_db.execute(select(User).where(User.email == email))).scalar_one()

    other = await _mk_user(async_db, "hist_route_other@test.com")
    for d in [0, 2, 1]:  # inserted out of order on purpose
        async_db.add(_workout(me.id, _T0 + timedelta(days=d)))
    async_db.add(_workout(other.id, _T0 + timedelta(days=5)))
    await async_db.commit()

    resp = await http_client.get(
        "/v1/workouts", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 3  # athlete-scoped: the other user's workout is absent

    logged = [datetime.fromisoformat(w["logged_at"]) for w in body]
    assert logged == [
        _T0 + timedelta(days=2),
        _T0 + timedelta(days=1),
        _T0,
    ]
    first = body[0]
    assert set(WorkoutLogSummary.model_fields) <= set(first)
    assert first["modality"] == "Mixed"
    assert first["duration_minutes"] == 30.0
    assert first["session_rpe"] == 5.0


async def test_get_workouts_route_respects_limit(http_client, async_db):
    """The route's ``limit`` query param still reaches the repository through the loader."""
    email = "hist_route_limit@test.com"
    token = await _register_and_get_token(http_client, email, "securepass1")
    me = (await async_db.execute(select(User).where(User.email == email))).scalar_one()
    for d in [0, 1, 2, 3]:
        async_db.add(_workout(me.id, _T0 + timedelta(days=d)))
    await async_db.commit()

    resp = await http_client.get(
        "/v1/workouts", params={"limit": 2}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [datetime.fromisoformat(w["logged_at"]) for w in body] == [
        _T0 + timedelta(days=3),
        _T0 + timedelta(days=2),
    ]
