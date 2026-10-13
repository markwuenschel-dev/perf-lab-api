"""a061: what the database itself allows an OAuth authorization attempt to look like (P6a).

Every row here is written with raw SQL, as a future writer, a manual repair or a buggy service
would, so these pin the schema rather than any code path. Malformed shapes, broken time order,
illegal transitions and edits to inputs or terminal outcomes must all be refused by Postgres.
"""
import hashlib
from collections.abc import Iterator
from datetime import datetime, timedelta
from itertools import count

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 10, 13, 9, 0, 0)
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # RFC 7636 appendix B
_seq: Iterator[int] = count(1)


def _state_hash() -> str:
    return hashlib.sha256(f"state-{next(_seq)}".encode()).hexdigest()


async def _user(db: AsyncSession) -> int:
    return (await db.execute(text(
        "INSERT INTO users (email, hashed_password, is_active, created_at) "
        "VALUES (:e, 'h', true, now()) RETURNING id"
    ), {"e": f"a061-{next(_seq)}@test.com"})).scalar_one()


async def _connection(db: AsyncSession, user_id: int) -> int:
    # Exactly the columns today's writer sets: generation comes from the default.
    return (await db.execute(text(
        "INSERT INTO wearable_connections (user_id, provider, auth_type, access_token_enc, "
        "created_at, updated_at) VALUES (:u, 'oura', 'oauth', 'enc', now(), now()) RETURNING id"
    ), {"u": user_id})).scalar_one()


def _row(user_id: int, **over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "user_id": user_id, "provider": "oura", "state_hash": _state_hash(),
        "code_challenge": CHALLENGE, "redirect_uri": "https://perflab.test/settings/oura-callback",
        "status": "pending", "created_at": T0, "expires_at": T0 + timedelta(minutes=10),
        "claimed_at": None, "outcome_at": None, "error": None,
        "receipt_connection_id": None, "receipt_generation": None, "connection_id": None,
    }
    row.update(over)
    return row


async def _insert(db: AsyncSession, row: dict[str, object]) -> int:
    cols = ", ".join(row)
    vals = ", ".join(f":{c}" for c in row)
    return (await db.execute(
        text(f"INSERT INTO oauth_authorizations ({cols}) VALUES ({vals}) RETURNING id"), row
    )).scalar_one()


async def assert_refused(db: AsyncSession, sql: str, params: dict[str, object], match: str) -> None:
    with pytest.raises((IntegrityError, DBAPIError), match=match):
        async with db.begin_nested():
            await db.execute(text(sql), params)


def _claimed(uid: int) -> dict[str, object]:
    return {"status": "claimed", "claimed_at": T0 + timedelta(minutes=1)}


def _succeeded(cid: int) -> dict[str, object]:
    return {"status": "succeeded", "claimed_at": T0 + timedelta(minutes=1),
            "outcome_at": T0 + timedelta(minutes=2), "receipt_connection_id": cid,
            "receipt_generation": 1, "connection_id": cid}


def _failed(**over: object) -> dict[str, object]:
    return {"status": "failed", "outcome_at": T0 + timedelta(minutes=2), "error": "cancelled",
            **over}


async def test_today_s_connection_writer_gets_generation_1(async_db: AsyncSession) -> None:
    uid = await _user(async_db)
    cid = await _connection(async_db, uid)
    gen = (await async_db.execute(
        text("SELECT generation FROM wearable_connections WHERE id = :c"), {"c": cid}
    )).scalar_one()
    assert gen == 1
    await assert_refused(async_db, "UPDATE wearable_connections SET generation = 0 WHERE id = :c",
                   {"c": cid}, "ck_wearable_connections_generation")


async def test_each_status_accepts_its_own_shape(async_db: AsyncSession) -> None:
    uid = await _user(async_db)
    cid = await _connection(async_db, uid)
    await _insert(async_db, _row(uid))
    await _insert(async_db, _row(uid, **_claimed(uid)))
    await _insert(async_db, _row(uid, **_succeeded(cid)))
    # A success whose connection was later deleted: the live reference is NULL, the receipt stays.
    await _insert(async_db, _row(uid, **{**_succeeded(cid), "connection_id": None}))
    # Cancelled or expired before any claim, and failed after one.
    await _insert(async_db, _row(uid, **_failed()))
    await _insert(async_db, _row(uid, **_failed(claimed_at=T0 + timedelta(minutes=1),
                                                error="exchange_failed")))
    stored = (await async_db.execute(text(
        "SELECT status, connection_id IS NULL FROM oauth_authorizations WHERE user_id = :u "
        "ORDER BY id"), {"u": uid})).all()
    assert [tuple(r) for r in stored] == [
        ("pending", True), ("claimed", True), ("succeeded", False), ("succeeded", True),
        ("failed", True), ("failed", True),
    ]


MALFORMED = {
    "pending with a claim": {"claimed_at": T0},
    "pending with an outcome": {"outcome_at": T0},
    "pending with an error": {"error": "cancelled"},
    "pending with a receipt": {"receipt_generation": 1},
    "claimed without claimed_at": {"status": "claimed"},
    "claimed with an outcome": {"status": "claimed", "claimed_at": T0, "outcome_at": T0},
    "claimed with an error": {"status": "claimed", "claimed_at": T0, "error": "x"},
    "succeeded without a claim": {"status": "succeeded", "outcome_at": T0,
                                  "receipt_connection_id": 1, "receipt_generation": 1},
    "succeeded without outcome_at": {"status": "succeeded", "claimed_at": T0,
                                     "receipt_connection_id": 1, "receipt_generation": 1},
    "succeeded with a NULL receipt generation": {
        "status": "succeeded", "claimed_at": T0, "outcome_at": T0, "receipt_connection_id": 1},
    "succeeded with receipt generation 0": {
        "status": "succeeded", "claimed_at": T0, "outcome_at": T0, "receipt_connection_id": 1,
        "receipt_generation": 0},
    "succeeded without a receipt connection": {
        "status": "succeeded", "claimed_at": T0, "outcome_at": T0, "receipt_generation": 1},
    "succeeded with an error": {
        "status": "succeeded", "claimed_at": T0, "outcome_at": T0, "receipt_connection_id": 1,
        "receipt_generation": 1, "error": "x"},
    "failed without outcome_at": {"status": "failed", "error": "cancelled"},
    "failed without an error": {"status": "failed", "outcome_at": T0},
    "failed with a receipt": {"status": "failed", "outcome_at": T0, "error": "x",
                              "receipt_connection_id": 1, "receipt_generation": 1},
    "an unknown status": {"status": "done"},
    "expiry not after creation": {"expires_at": T0},
    "claim before creation": {"status": "claimed", "claimed_at": T0 - timedelta(seconds=1)},
    "failed-before-claim outcome before creation": {
        "status": "failed", "outcome_at": T0 - timedelta(seconds=1), "error": "expired"},
    "outcome before claim": {"status": "failed", "claimed_at": T0 + timedelta(minutes=2),
                             "outcome_at": T0 + timedelta(minutes=1), "error": "x"},
    "a state hash that is not sha256 hex": {"state_hash": "not-a-hash"},
    "a challenge that is not S256 base64url": {"code_challenge": "short"},
    "an error that is provider text": {"status": "failed", "outcome_at": T0,
                                       "error": "Oura said: invalid_grant"},
    "an empty provider": {"provider": ""},
}


@pytest.mark.parametrize("case", sorted(MALFORMED))
async def test_a_malformed_attempt_is_refused(async_db: AsyncSession, case: str) -> None:
    uid = await _user(async_db)
    row = _row(uid, **MALFORMED[case])
    cols = ", ".join(row)
    vals = ", ".join(f":{c}" for c in row)
    await assert_refused(async_db, f"INSERT INTO oauth_authorizations ({cols}) VALUES ({vals})", row,
                   "ck_oauth_authorizations")


async def test_a_succeeded_live_reference_must_match_its_receipt(async_db: AsyncSession) -> None:
    uid = await _user(async_db)
    cid = await _connection(async_db, uid)
    other = await _connection(async_db, await _user(async_db))
    row = _row(uid, **{**_succeeded(cid), "connection_id": other})
    cols = ", ".join(row)
    vals = ", ".join(f":{c}" for c in row)
    await assert_refused(async_db, f"INSERT INTO oauth_authorizations ({cols}) VALUES ({vals})", row,
                   "ck_oauth_authorizations_status_shape")


@pytest.mark.parametrize("column, value", [
    ("user_id", None), ("provider", "garmin"), ("state_hash", "0" * 64),
    ("code_challenge", "A" * 43), ("redirect_uri", "https://evil.test/cb"),
    ("created_at", T0 - timedelta(minutes=1)), ("expires_at", T0 + timedelta(hours=1)),
])
async def test_attempt_inputs_never_change(async_db: AsyncSession, column: str, value) -> None:
    uid = await _user(async_db)
    aid = await _insert(async_db, _row(uid))
    if column == "user_id":
        value = await _user(async_db)
    await assert_refused(async_db, f"UPDATE oauth_authorizations SET {column} = :v WHERE id = :a",
                   {"v": value, "a": aid}, "attempt inputs are immutable")


@pytest.mark.parametrize("start, target", [
    ("pending", "succeeded"), ("claimed", "pending"), ("succeeded", "failed"),
    ("failed", "pending"), ("failed", "claimed"), ("succeeded", "claimed"),
])
async def test_only_forward_transitions(async_db: AsyncSession, start: str, target: str) -> None:
    uid = await _user(async_db)
    cid = await _connection(async_db, uid)
    shapes = {"pending": {}, "claimed": _claimed(uid), "succeeded": _succeeded(cid),
              "failed": _failed()}
    aid = await _insert(async_db, _row(uid, **shapes[start]))
    # Move to the target's full shape, so only the transition rule (not the CHECK) can object.
    sets = {k: v for k, v in _row(uid, **shapes[target]).items()
            if k not in ("user_id", "state_hash", "provider", "code_challenge", "redirect_uri",
                         "created_at", "expires_at")}
    assignments = ", ".join(f"{k} = :{k}" for k in sets)
    await assert_refused(async_db, f"UPDATE oauth_authorizations SET {assignments} WHERE id = :a",
                   {**sets, "a": aid}, "is not a transition|is final")


async def test_a_claim_time_is_set_once(async_db: AsyncSession) -> None:
    uid = await _user(async_db)
    aid = await _insert(async_db, _row(uid, **_claimed(uid)))
    await assert_refused(async_db, "UPDATE oauth_authorizations SET claimed_at = :t WHERE id = :a",
                   {"t": T0 + timedelta(minutes=5), "a": aid}, "claimed_at is set once")


@pytest.mark.parametrize("column, value", [
    ("error", "other"), ("outcome_at", T0 + timedelta(minutes=9)), ("receipt_generation", 2),
    ("receipt_connection_id", 999),
])
async def test_terminal_outcomes_never_change(async_db: AsyncSession, column: str, value) -> None:
    uid = await _user(async_db)
    cid = await _connection(async_db, uid)
    shape = _failed() if column in ("error", "outcome_at") else _succeeded(cid)
    aid = await _insert(async_db, _row(uid, **shape))
    await assert_refused(async_db, f"UPDATE oauth_authorizations SET {column} = :v WHERE id = :a",
                   {"v": value, "a": aid}, "is final|ck_oauth_authorizations")


async def test_deleting_the_connection_keeps_the_receipt(async_db: AsyncSession) -> None:
    """The old disconnect (a hard DELETE) still works; only the live reference goes NULL."""
    uid = await _user(async_db)
    cid = await _connection(async_db, uid)
    aid = await _insert(async_db, _row(uid, **_succeeded(cid)))
    await async_db.execute(text("DELETE FROM wearable_connections WHERE id = :c"), {"c": cid})
    row = (await async_db.execute(text(
        "SELECT status, connection_id, receipt_connection_id, receipt_generation "
        "FROM oauth_authorizations WHERE id = :a"), {"a": aid})).one()
    assert tuple(row) == ("succeeded", None, cid, 1)
    # A terminal row's live reference can go NULL but never be pointed at something again.
    new = await _connection(async_db, uid)
    await assert_refused(async_db, "UPDATE oauth_authorizations SET connection_id = :c WHERE id = :a",
                   {"c": new, "a": aid}, "is final|ck_oauth_authorizations")


async def test_the_forward_transitions_are_allowed(async_db: AsyncSession) -> None:
    uid = await _user(async_db)
    cid = await _connection(async_db, uid)
    a = await _insert(async_db, _row(uid))
    await async_db.execute(text(
        "UPDATE oauth_authorizations SET status='claimed', claimed_at=:t WHERE id=:a"),
        {"t": T0 + timedelta(minutes=1), "a": a})
    await async_db.execute(text(
        "UPDATE oauth_authorizations SET status='succeeded', outcome_at=:t, connection_id=:c, "
        "receipt_connection_id=:c, receipt_generation=1 WHERE id=:a"),
        {"t": T0 + timedelta(minutes=2), "c": cid, "a": a})
    b = await _insert(async_db, _row(uid))
    await async_db.execute(text(
        "UPDATE oauth_authorizations SET status='failed', outcome_at=:t, error='cancelled' "
        "WHERE id=:a"), {"t": T0 + timedelta(minutes=2), "a": b})
    c = await _insert(async_db, _row(uid, **_claimed(uid)))
    await async_db.execute(text(
        "UPDATE oauth_authorizations SET status='failed', outcome_at=:t, error='claim_stale' "
        "WHERE id=:a"), {"t": T0 + timedelta(minutes=3), "a": c})
    final = (await async_db.execute(text(
        "SELECT id, status, error, receipt_generation FROM oauth_authorizations "
        "WHERE id IN (:a, :b, :c) ORDER BY id"), {"a": a, "b": b, "c": c})).all()
    assert [tuple(r[1:]) for r in final] == [
        ("succeeded", None, 1), ("failed", "cancelled", None), ("failed", "claim_stale", None),
    ]


async def test_a_state_hash_is_used_once(async_db: AsyncSession) -> None:
    uid = await _user(async_db)
    row = _row(uid)
    await _insert(async_db, row)
    again = _row(uid, state_hash=row["state_hash"])
    cols = ", ".join(again)
    vals = ", ".join(f":{c}" for c in again)
    await assert_refused(async_db, f"INSERT INTO oauth_authorizations ({cols}) VALUES ({vals})", again,
                   "uq_oauth_authorizations_state_hash")
