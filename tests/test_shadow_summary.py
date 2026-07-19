"""Read-only shadow-inspection summary (ADR-0041/0042/0043)."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.logic.ekf.wellness_input import build_wellness_shadow_input
from app.models.mpc_shadow import MpcShadowLog
from app.models.personalization_shadow import PersonalizationShadowLog
from app.models.recovery_shadow import RecoveryShadowLog
from app.models.user import User
from app.models.wellness import WellnessSample
from app.services import ekf_shadow_service
from app.services.shadow_summary_service import athlete_shadow_summary
from app.services.state_service import initialize_athlete_state

pytestmark = pytest.mark.asyncio


async def _mk_user(db, email) -> User:
    u = User(email=email, hashed_password="h", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def test_empty_athlete_has_all_sections_null(async_db):
    user = await _mk_user(async_db, "shadow-empty@test.com")
    summary = await athlete_shadow_summary(async_db, user.id)
    assert summary["user_id"] == user.id
    assert summary["ekf"] is None
    assert summary["mpc"] is None
    assert summary["personalization"] is None
    assert summary["recovery"] is None


async def test_summary_aggregates_every_subsystem(async_db):
    user = await _mk_user(async_db, "shadow-full@test.com")
    uid = user.id
    await initialize_athlete_state(async_db, uid)

    # EKF: a wellness observation writes an update row.
    w = WellnessSample(user_id=uid, date=date(2026, 1, 1), source="manual", soreness=6.0)
    async_db.add(w)
    await async_db.commit()
    await async_db.refresh(w)
    await ekf_shadow_service.record_ekf_wellness_observation(
        async_db, uid, build_wellness_shadow_input(uid, w.id, 6.0), observed_at=datetime.now(UTC)
    )
    # Seed one row in each of the other shadow logs.
    async_db.add(RecoveryShadowLog(
        user_id=uid, model_version="q2_v1",
        baseline_clearance_multiplier={"cns": 1.0}, learned_clearance_multiplier={"cns": 1.1},
    ))
    async_db.add(PersonalizationShadowLog(
        user_id=uid, model_version="p_v1", n_obs=12, shrinkage_weight=0.6, theta_trace=0.1,
        population_multiplier={"cns": 1.0, "muscular": 1.0},
        personalized_multiplier={"cns": 1.05, "muscular": 0.98},
    ))
    async_db.add(MpcShadowLog(
        user_id=uid, goal="Strength", horizon_days=14, agreement=True,
        greedy_type="Max Strength", mpc_type="Max Strength", belief_trace=8.0,
    ))
    async_db.add(MpcShadowLog(
        user_id=uid, goal="Strength", horizon_days=14, agreement=False,
        greedy_type="Max Strength", mpc_type="Recovery", belief_trace=9.0,
    ))
    await async_db.commit()

    s = await athlete_shadow_summary(async_db, uid)
    assert s["ekf"] is not None and s["ekf"]["n_update"] >= 1
    assert s["mpc"]["n_decisions"] == 2
    assert s["mpc"]["agreement_rate"] == 0.5
    assert s["mpc"]["latest"]["mpc_type"] == "Recovery"  # most recent
    assert s["personalization"]["active"] is True
    assert s["personalization"]["n_obs"] == 12
    assert s["personalization"]["multiplier_delta"]["cns"] == pytest.approx(0.05)
    assert s["recovery"]["learned_clearance_multiplier"] == {"cns": 1.1}
