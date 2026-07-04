"""Macrocycle spine wiring (Phase 5, requires a live DB).

Task 2 — a new block auto-links to the user's single active macrocycle.
Task 3 — ``active_objective_signals`` derives from the macrocycle anchor when
one is active, and falls back to the all-objectives scan otherwise.

DB-gated (skips locally + in CI on the conftest event-loop issue). Proven
end-to-end by the standalone Postgres harness; kept here for coverage. Mirrors
the async_db + service-call pattern used elsewhere in the suite.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.objective import Objective
from app.models.user import User
from app.schemas.macrocycle import MacrocycleCreate
from app.schemas.objective import ObjectiveCreate
from app.schemas.planning import BlockCreateRequest
from app.services import macrocycle_service
from app.services.macrocycle_service import to_read_schema
from app.services.objective_service import active_objective_signals, create_objective
from app.services.planning_service import create_block_with_sessions

pytestmark = pytest.mark.asyncio


async def _mk_user(db: AsyncSession, email: str) -> User:
    u = User(email=email, hashed_password="h", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _block_req() -> BlockCreateRequest:
    return BlockCreateRequest(
        goal="Strength",  # type: ignore[arg-type]
        start_date=date.today(),
        duration_weeks=2,
        sessions_per_week=3,
    )


async def _mk_objective(
    db: AsyncSession,
    user_id: int,
    *,
    label: str,
    target_in_days: int | None,
    domain: str | None = None,
) -> Objective:
    payload = ObjectiveCreate(
        label=label,
        domain=domain,
        priority=1,
        target_date=(date.today() + timedelta(days=target_in_days)) if target_in_days is not None else None,
    )
    return await create_objective(db, user_id, payload)


# ---------------------------------------------------------------------------
# Task 2 — block auto-link
# ---------------------------------------------------------------------------

async def test_block_links_to_single_active_macrocycle(async_db: AsyncSession) -> None:
    user = await _mk_user(async_db, "spine_one@test.com")
    obj = await _mk_objective(async_db, user.id, label="Meet", target_in_days=28, domain="strength")
    macro = await macrocycle_service.create_macrocycle(
        async_db, user.id, MacrocycleCreate(objective_id=obj.id, start_date=date.today())
    )
    assert macro is not None

    block = await create_block_with_sessions(async_db, user.id, _block_req())
    assert block.macrocycle_id == macro.id

    read = await to_read_schema(async_db, macro)
    assert read.block_count == 1


async def test_block_unlinked_when_no_macrocycle(async_db: AsyncSession) -> None:
    user = await _mk_user(async_db, "spine_none@test.com")
    block = await create_block_with_sessions(async_db, user.id, _block_req())
    assert block.macrocycle_id is None


async def test_block_unlinked_when_multiple_active_macrocycles(async_db: AsyncSession) -> None:
    user = await _mk_user(async_db, "spine_many@test.com")
    obj_a = await _mk_objective(async_db, user.id, label="A", target_in_days=28)
    obj_b = await _mk_objective(async_db, user.id, label="B", target_in_days=40)
    for obj in (obj_a, obj_b):
        assert await macrocycle_service.create_macrocycle(
            async_db, user.id, MacrocycleCreate(objective_id=obj.id, start_date=date.today())
        ) is not None

    block = await create_block_with_sessions(async_db, user.id, _block_req())
    # Ambiguous which program owns the block → left NULL.
    assert block.macrocycle_id is None


# ---------------------------------------------------------------------------
# Task 3 — anchor-driven signals with scan fallback
# ---------------------------------------------------------------------------

async def test_signals_use_macrocycle_anchor(async_db: AsyncSession) -> None:
    user = await _mk_user(async_db, "sig_anchor@test.com")
    # Anchor: near target, domain running. A competing higher-signal objective
    # (nearer date, different domain) exists but must be ignored once a
    # macrocycle anchors the program.
    anchor = await _mk_objective(async_db, user.id, label="A-race", target_in_days=10, domain="running")
    await _mk_objective(async_db, user.id, label="Distraction", target_in_days=3, domain="strength")

    await macrocycle_service.create_macrocycle(
        async_db, user.id, MacrocycleCreate(objective_id=anchor.id, start_date=date.today())
    )

    sig = await active_objective_signals(async_db, user.id)
    assert sig == {"taper": True, "domain": "running"}


async def test_signals_fall_back_to_scan_without_macrocycle(async_db: AsyncSession) -> None:
    user = await _mk_user(async_db, "sig_scan@test.com")
    # Highest priority (=1) drives domain; nearest upcoming drives taper.
    await _mk_objective(async_db, user.id, label="Far", target_in_days=90, domain="strength")
    near = ObjectiveCreate(
        label="Near", domain="running", priority=2, target_date=date.today() + timedelta(days=5)
    )
    await create_objective(async_db, user.id, near)

    sig = await active_objective_signals(async_db, user.id)
    assert sig["taper"] is True  # nearest upcoming within window
    assert sig["domain"] == "strength"  # priority 1 objective


async def test_signals_anchor_far_target_does_not_taper(async_db: AsyncSession) -> None:
    user = await _mk_user(async_db, "sig_far@test.com")
    anchor = await _mk_objective(async_db, user.id, label="Far goal", target_in_days=90, domain="hypertrophy")
    macro = await macrocycle_service.create_macrocycle(
        async_db, user.id, MacrocycleCreate(objective_id=anchor.id, start_date=date.today())
    )
    assert macro is not None

    sig = await active_objective_signals(async_db, user.id)
    assert sig == {"taper": False, "domain": "hypertrophy"}
