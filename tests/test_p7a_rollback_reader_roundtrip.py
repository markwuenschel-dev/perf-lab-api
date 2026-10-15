"""A rollback from P7b to P7a: P7a's own writers, then its reader (writer-to-reader round trip).

The athlete has P7b-shaped linked history (inserted directly, as P7b writes it). A manual
check-in then runs P7a's real recovery and personalization writers, which append unlinked rows.
Those are the athlete's newest output and must be what the shadow summary shows. P7b replaces
these writers, so this file belongs to P7a only.
"""
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot
from app.models.personalization_shadow import PersonalizationShadowLog
from app.models.recovery_shadow import RecoveryShadowLog
from app.models.user import User
from app.models.wellness import WellnessSample
from app.services import shadow_summary_service
from app.services.shadow_evidence import RECORDED, inputs_hash, latest_current_evidence

pytestmark = pytest.mark.asyncio


async def _athlete(client, db: AsyncSession, email: str) -> tuple[dict[str, str], int]:
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token", data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    uid = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, uid


async def _checkin(client, login, day: date, hrv: float) -> None:
    body = {"date": day.isoformat(), "source": "manual", "sleep_hours": 7.0, "hrv_ms": hrv,
            "resting_hr": 52.0}
    r = await client.post("/v1/wellness", json=body, headers=login)
    assert r.status_code == 200, r.text


async def test_p7a_writers_output_after_a_rollback_is_current(http_client, async_db):
    login, uid = await _athlete(http_client, async_db, "p7a-rollback@test.com")
    day = date(2026, 10, 15)
    await _checkin(http_client, login, day, 60.0)  # P7a writes unlinked rows (pre-P7b history)
    sample = (await async_db.execute(
        select(WellnessSample).where(WellnessSample.user_id == uid))).scalar_one()
    digest = inputs_hash(WellnessTelemetrySnapshot.from_sample(sample))
    mult = {"f_met_systemic": 1.0}
    async_db.add_all([  # P7b's linked computations for that sample
        RecoveryShadowLog(user_id=uid, model_version="v1", wellness={"hrv_ms": 60.0},
                          fatigue_before={}, baseline_clearance_multiplier=mult,
                          learned_clearance_multiplier=mult, decision_impact=RECORDED,
                          source_wellness_sample_id=sample.id, content_hash=digest),
        PersonalizationShadowLog(user_id=uid, parameter="recovery_beta", model_version="v1",
                                 n_obs=0, shrinkage_weight=0.0, theta_trace=0.0,
                                 wellness={"hrv_ms": 60.0}, population_multiplier=mult,
                                 personalized_multiplier=mult, decision_impact=RECORDED,
                                 source_wellness_sample_id=sample.id, content_hash=digest),
    ])
    await async_db.commit()

    # Rolled back to P7a: the same sample corrected to 72 through the real route.
    await _checkin(http_client, login, day, 72.0)
    async_db.expire_all()
    for model in (RecoveryShadowLog, PersonalizationShadowLog):
        newest = (await async_db.execute(select(model).where(model.user_id == uid)
                                         .order_by(model.id.desc()).limit(1))).scalar_one()
        assert newest.source_wellness_sample_id is None  # P7a's writer: unlinked
        current = await latest_current_evidence(async_db, model, uid)
        assert current is not None and current.id == newest.id
        assert current.wellness["hrv_ms"] == 72.0
    summary = await shadow_summary_service.athlete_shadow_summary(async_db, uid)
    assert summary["recovery"] is not None and summary["personalization"] is not None
