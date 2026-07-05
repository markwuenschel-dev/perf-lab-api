"""Integration tests for the shadow MPC planner service (ADR-0042).

A real prescription writes one capture-only ``mpc_shadow_log`` row (MPC-vs-greedy +
per-candidate objective breakdown), and — critically — a failure in the shadow planner
never alters or blocks the returned prescription.

Requires a live PostgreSQL instance (async_db fixture).
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.mpc_shadow import MpcShadowLog
from app.models.user import AthleteProfile, User
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT
from app.services.prescription_service import prescribe_for_athlete
from app.services.state_service import initialize_athlete_state

pytestmark = pytest.mark.asyncio


async def _mk_user(db, email="mpc-shadow@test.com") -> User:
    u = User(email=email, hashed_password="h", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    db.add(AthleteProfile(user_id=u.id, equipment=["barbell"]))
    await db.commit()
    await initialize_athlete_state(db, u.id)
    return u


async def _rows(db, user_id: int) -> list[MpcShadowLog]:
    res = await db.execute(select(MpcShadowLog).where(MpcShadowLog.user_id == user_id))
    return list(res.scalars().all())


async def test_prescription_writes_one_mpc_shadow_row(async_db):
    user = await _mk_user(async_db)
    rx = await prescribe_for_athlete(async_db, user.id, TRAINING_GOAL_DEFAULT)
    assert rx is not None

    rows = await _rows(async_db, user.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.decision_impact == "none_shadow_only"
    assert isinstance(row.agreement, bool)
    assert row.greedy_branch_id and row.mpc_branch_id
    assert row.horizon_days > 0
    assert len(row.candidate_scores_json) >= 1
    top = row.candidate_scores_json[0]
    assert "J" in top and "goal" in top and "fatigue" in top
    # the MPC choice must be one of the scored candidates
    assert row.mpc_branch_id in {c["branch_id"] for c in row.candidate_scores_json}


async def test_shadow_failure_never_blocks_prescription(async_db, monkeypatch):
    user = await _mk_user(async_db, email="mpc-fail@test.com")
    uid = user.id  # capture before the best-effort rollback expires the ORM object

    def _boom(*a, **k):
        raise RuntimeError("simulated MPC failure")

    monkeypatch.setattr("app.services.mpc_shadow_service.evaluate_candidates", _boom)

    rx = await prescribe_for_athlete(async_db, uid, TRAINING_GOAL_DEFAULT)
    assert rx is not None  # prescription still returned
    count = (await async_db.execute(
        select(func.count()).where(MpcShadowLog.user_id == uid)
    )).scalar()
    assert count == 0  # no shadow row on failure
