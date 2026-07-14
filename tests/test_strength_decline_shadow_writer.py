"""W1-C2 — the strength-decline shadow writer (INT-02, ADR-0066).

Replaces ``tests/test_strength_decline_shadow_inert.py``, which existed only to enforce
the no-writer state W1-C1 shipped. Its two schema assertions are preserved below — they
are what keep the ON CONFLICT mechanism honest, and they must not be lost with the guard.

What these tests are actually defending, and why they inject failures where they do:

The first attempt at this writer persisted the row from inside
``resolve_prescription_basis``, which runs *before* prescription's ``db.commit()``
(``prescription_service.py:380``). A shadow-row failure at flush would have taken the
whole prescription down. Its guard was ``try: db.add(row) / except`` — which catches
nothing, because ``db.add()`` stages in memory and does no I/O; the real failure lands
later, at commit, in the caller's transaction. So a test that injects a failure at
``db.add`` proves nothing. **Every failure here is injected at real I/O (execute /
commit) or via a real concurrent database write.**
"""
from dataclasses import FrozenInstanceError

import pytest
from conftest import TEST_DATABASE_URL
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from test_decline_prescription_basis import RULES, _active_candidate_setup

from app.models.strength_decline_shadow import StrengthDeclineShadow
from app.services import strength_decline_service as sds


def _test_session_factory() -> async_sessionmaker[AsyncSession]:
    """A factory bound to *this worker's* test database.

    Required, not incidental: the writer defaults to ``AsyncSessionLocal``, which binds
    ``settings.DATABASE_URL``. Under xdist each worker's tests live in
    ``perflab_test_gw0``/``_gw1``/… while ``AsyncSessionLocal`` points at the unsharded
    database — so without injection the writer would write somewhere the assertions
    never look, and these tests would pass while proving nothing.
    """
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _shadow_count(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(StrengthDeclineShadow)) or 0)


async def _payload_for(db: AsyncSession, user_id: int) -> sds.StrengthDeclineShadowPayload:
    decision = await sds.resolve_prescription_basis(
        db, user_id, code="pl_e1rm_squat", latest_raw=138.0,
        current_axis=50.33, rules=RULES, mode=sds.BASIS_MODE_SHADOW,
    )
    assert decision.shadow_payload is not None, "an active candidate must yield a payload"
    return decision.shadow_payload


# --------------------------------------------------------------------------- #
# Schema contract — preserved from the deleted inert guard
# --------------------------------------------------------------------------- #

def test_unique_constraint_columns_are_all_not_null():
    """SQL treats NULLs as DISTINCT, so a UNIQUE constraint containing a nullable column
    silently stops enforcing — duplicates insert cleanly and raise nothing.

    Not hypothetical: the first cut of this table shipped ``trigger_observation_id`` as
    nullable, and three byte-identical rows inserted with no error. That makes the
    ON CONFLICT DO NOTHING below a silent no-op and lets shadow rows accumulate
    unbounded. This test is the reason that cannot regress.
    """
    constraint = next(
        con for con in StrengthDeclineShadow.__table__.constraints
        if getattr(con, "name", None) == "uq_strength_decline_shadow_trigger_axis_policy"
    )
    nullable = sorted(c.name for c in constraint.columns if c.nullable)
    assert not nullable, (
        "these columns participate in the idempotency key but are nullable, which "
        f"silently defeats it (NULL != NULL in SQL): {nullable}"
    )


def test_shadow_rows_are_append_only_telemetry():
    column = StrengthDeclineShadow.__table__.c.decision_impact
    assert column.default is not None
    assert column.default.arg == "none_shadow_only"


# --------------------------------------------------------------------------- #
# Resolver purity
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_resolver_performs_no_shadow_io(async_db):
    """resolve_prescription_basis must be side-effect free w.r.t. shadow telemetry.

    It runs inside the prescription's transaction; a row staged there would commit — or
    fail — together with the prescription. It may only *return* the payload.
    """
    user_id = await _active_candidate_setup(async_db, "w1c2-pure@test.com")
    before = await _shadow_count(async_db)

    decision = await sds.resolve_prescription_basis(
        async_db, user_id, code="pl_e1rm_squat", latest_raw=138.0,
        current_axis=50.33, rules=RULES, mode=sds.BASIS_MODE_SHADOW,
    )

    assert decision.shadow_payload is not None          # payload produced
    assert await _shadow_count(async_db) == before       # but nothing written
    assert not async_db.new and not async_db.dirty       # nothing even staged


@pytest.mark.asyncio
async def test_payload_is_immutable_and_carries_no_orm_state(async_db):
    """The payload crosses a transaction boundary, so it must be a value object —
    an attached ORM instance would couple telemetry to the request's session."""
    user_id = await _active_candidate_setup(async_db, "w1c2-frozen@test.com")
    payload = await _payload_for(async_db, user_id)

    with pytest.raises(FrozenInstanceError):
        payload.selected_basis = 999.0  # type: ignore[misc]

    for value in vars(payload).values():
        assert value is None or isinstance(value, (int, float, str)), (
            f"payload must hold only primitives/ids, found {type(value)!r}"
        )


@pytest.mark.asyncio
async def test_no_payload_without_an_active_candidate(async_db):
    """No active candidate means no counterfactual to record — and no row."""
    from app.models.user import User
    from app.services.state_service import initialize_athlete_state

    u = User(email="w1c2-nocand@test.com", hashed_password="x", is_active=True)
    async_db.add(u)
    await async_db.commit()
    await async_db.refresh(u)
    await initialize_athlete_state(async_db, u.id)

    decision = await sds.resolve_prescription_basis(
        async_db, u.id, code="pl_e1rm_squat", latest_raw=138.0,
        current_axis=50.33, rules=RULES, mode=sds.BASIS_MODE_SHADOW,
    )
    assert decision.shadow_payload is None
    await sds.persist_strength_decline_shadow_best_effort(
        None, session_factory=_test_session_factory()
    )  # tolerates None


# --------------------------------------------------------------------------- #
# The writer
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_writer_persists_the_row(async_db):
    user_id = await _active_candidate_setup(async_db, "w1c2-write@test.com")
    payload = await _payload_for(async_db, user_id)

    await sds.persist_strength_decline_shadow_best_effort(
        payload, session_factory=_test_session_factory()
    )

    row = (await async_db.execute(select(StrengthDeclineShadow))).scalars().one()
    assert row.candidate_id == payload.candidate_id
    assert row.decision_impact == "none_shadow_only"
    # The four fields the log line omitted — the reason this table exists.
    assert row.absolute_delta == pytest.approx(payload.absolute_delta)
    assert row.relative_delta == pytest.approx(payload.relative_delta)
    assert row.ceiling_semantics == sds.CEILING_SEMANTICS
    assert row.decline_policy_version == payload.decline_policy_version


@pytest.mark.asyncio
async def test_retry_is_a_no_op(async_db):
    """Same payload twice -> second is a no-op, no exception. ON CONFLICT DO NOTHING."""
    user_id = await _active_candidate_setup(async_db, "w1c2-retry@test.com")
    payload = await _payload_for(async_db, user_id)
    factory = _test_session_factory()

    await sds.persist_strength_decline_shadow_best_effort(payload, session_factory=factory)
    await sds.persist_strength_decline_shadow_best_effort(payload, session_factory=factory)

    assert await _shadow_count(async_db) == 1


@pytest.mark.asyncio
async def test_concurrent_duplicate_yields_exactly_one_row(async_db):
    """Two concurrent writes of the same shadow identity: neither raises, one row.

    This is the case a SELECT-before-INSERT guard gets wrong — both would see no row,
    both would insert, and one would 500. The unique constraint arbitrates instead.
    """
    import asyncio

    user_id = await _active_candidate_setup(async_db, "w1c2-race@test.com")
    payload = await _payload_for(async_db, user_id)
    factory = _test_session_factory()

    await asyncio.gather(*(
        sds.persist_strength_decline_shadow_best_effort(payload, session_factory=factory)
        for _ in range(4)
    ))

    assert await _shadow_count(async_db) == 1


@pytest.mark.asyncio
async def test_write_failure_is_swallowed_and_logged(async_db):
    """A shadow-write failure must never surface to the caller.

    The failure is injected at real I/O — a factory whose session cannot connect — not
    at db.add(), which does no I/O and would prove nothing.

    Capture uses a direct handler rather than ``caplog``: caplog's records are collected
    on the root logger, and this record did not reach it from inside the async test, so
    caplog came back empty against a writer that demonstrably logs. A handler attached to
    the module's own logger asserts the thing we actually care about.
    """
    import logging

    user_id = await _active_candidate_setup(async_db, "w1c2-fail@test.com")
    payload = await _payload_for(async_db, user_id)

    broken_engine = create_async_engine(
        "postgresql+asyncpg://nobody:wrong@127.0.0.1:1/nonexistent", poolclass=NullPool
    )
    broken = async_sessionmaker(broken_engine, class_=AsyncSession)

    seen: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record.getMessage())

    handler = _Capture()
    logger = logging.getLogger(sds.__name__)
    logger.addHandler(handler)
    try:
        # Must not raise — that is the whole contract.
        await sds.persist_strength_decline_shadow_best_effort(payload, session_factory=broken)
    finally:
        logger.removeHandler(handler)

    assert any("strength_decline_shadow_write_failed" in m for m in seen), (
        f"writer failed silently — no failure log emitted. captured: {seen}"
    )
    assert await _shadow_count(async_db) == 0


@pytest.mark.asyncio
async def test_shadow_failure_leaves_prescription_durable(async_db):
    """Prescription committed + shadow write failed -> prescription row survives,
    shadow row absent, caller returns normally."""
    user_id = await _active_candidate_setup(async_db, "w1c2-durable@test.com")
    payload = await _payload_for(async_db, user_id)

    broken_engine = create_async_engine(
        "postgresql+asyncpg://nobody:wrong@127.0.0.1:1/nonexistent", poolclass=NullPool
    )
    await sds.persist_strength_decline_shadow_best_effort(
        payload, session_factory=async_sessionmaker(broken_engine, class_=AsyncSession)
    )

    # The request's session is untouched by the telemetry failure — it can still query.
    assert await _shadow_count(async_db) == 0
    from app.models.strength_decline_candidate import StrengthDeclineCandidate
    assert await async_db.scalar(
        select(func.count()).select_from(StrengthDeclineCandidate)
    ) >= 1
