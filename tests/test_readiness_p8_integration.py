"""DB-backed integration for the P8 honesty layer (ADR-0049/0052/0053).

Proves the load-bearing behaviors end-to-end against a real Postgres session:
- a bad night lowers the readiness *score*;
- an omitted-but-tracked signal lowers *confidence* but NOT the score;
- an untracked signal incurs no penalty;
- a stale sample is never used as if fresh (modeled-only + surfaced as stale).
"""
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio

from app.engine.state_bridge import sync_legacy_from_vectors
from app.models.user import AthleteProfile, User
from app.models.wellness import WellnessSample
from app.schemas.engine_vectors import CapacityState, FatigueState, TissueState
from app.schemas.state import UnifiedStateVector
from app.services import readiness_service

pytestmark = pytest.mark.asyncio

TODAY = date(2026, 7, 8)


def _fixed_state() -> UnifiedStateVector:
    cx = CapacityState(aerobic=300.0, max_strength=60.0, hypertrophy=50.0, skill=50.0, mobility=50.0)
    f = FatigueState(cns=20.0, muscular=30.0, grip=10.0)
    t = TissueState(lumbar=8.0, knee=12.0, hip=25.0)
    leg = sync_legacy_from_vectors(cx, f, t)
    return UnifiedStateVector(
        timestamp=datetime.now(UTC), capacity_x=cx, fatigue_f=f, tissue_t=t,
        s_struct_signal=0.0, habit_strength=0.5, skill_state={}, **leg,
    )


@pytest_asyncio.fixture
async def user_id(async_db):
    user = User(email="p8_readiness@test.com", hashed_password="x")
    async_db.add(user)
    await async_db.flush()
    async_db.add(AthleteProfile(user_id=user.id))
    await async_db.commit()
    return user.id


@pytest.fixture(autouse=True)
def _fixed_modeled_state(monkeypatch):
    """Pin the modeled state so these tests isolate the wellness signal, not state loading.

    Tracks the strict loader as of INT-15 2B2 — readiness gates prescription, so it no
    longer reaches the permissive one.
    """
    async def _load(_db, _uid):
        return _fixed_state()
    monkeypatch.setattr(readiness_service, "load_current_state_strict", _load)


async def _add_sample(async_db, user_id, on_date, **metrics):
    async_db.add(WellnessSample(user_id=user_id, date=on_date, source="manual", **metrics))
    await async_db.commit()


async def test_bad_night_lowers_score(async_db, user_id):
    modeled = (await readiness_service.compute_readiness(async_db, user_id, today=TODAY)).modeled
    # A bad night today: poor HRV/sleep, high soreness.
    await _add_sample(async_db, user_id, TODAY, hrv_ms=25.0, sleep_hours=4.0, soreness=9.0)
    rs = await readiness_service.compute_readiness(async_db, user_id, today=TODAY)
    assert rs.score is not None and rs.score < modeled
    assert rs.wellness_delta < 0
    assert rs.note is None  # fresh


async def test_omitted_tracked_signal_lowers_confidence_not_score(async_db, user_id):
    # History establishes hrv+sleep+soreness as tracked (provided before).
    await _add_sample(async_db, user_id, TODAY - timedelta(days=1),
                      hrv_ms=60.0, sleep_hours=8.0, soreness=3.0)
    # Today: same sleep+soreness at baseline, but HRV omitted (tracked → unknown-today).
    await _add_sample(async_db, user_id, TODAY, sleep_hours=8.0, soreness=3.0)
    rs = await readiness_service.compute_readiness(async_db, user_id, today=TODAY)

    # Score reflects only measured signals (both at baseline → ~modeled), NOT a penalty.
    assert rs.score == pytest.approx(rs.modeled, abs=0.2)
    # Confidence records the gap without penalizing the score.
    assert "hrv" in rs.confidence.signal_summary.unknown_today
    assert "hrv_unknown_today" in rs.confidence.reasons
    assert rs.confidence.status == "partial_data"
    assert rs.confidence.recommendation_gate.enforced is False


async def test_untracked_signal_no_penalty(async_db, user_id):
    # Only ever logs sleep + soreness; explicitly marks HRV untracked.
    prof = await async_db.get(AthleteProfile, user_id)
    prof.untracked_wellness_signals = ["hrv", "rhr", "mood", "stress"]
    await async_db.commit()
    await _add_sample(async_db, user_id, TODAY, sleep_hours=8.0, soreness=3.0)
    rs = await readiness_service.compute_readiness(async_db, user_id, today=TODAY)
    assert "hrv" in rs.confidence.signal_summary.untracked
    assert "hrv" not in rs.confidence.signal_summary.unknown_today
    assert rs.confidence.status == "well_supported"


async def test_stale_sample_is_modeled_only(async_db, user_id):
    # Latest sample is from days ago — must NOT feed the modifier (no carry-forward).
    await _add_sample(async_db, user_id, TODAY - timedelta(days=3),
                      hrv_ms=25.0, sleep_hours=4.0, soreness=9.0)
    rs = await readiness_service.compute_readiness(async_db, user_id, today=TODAY)
    assert rs.wellness_delta == 0.0
    assert rs.score == pytest.approx(rs.modeled)
    assert rs.note == "stale_wellness_sample"
    assert rs.confidence.status == "stale_data"
    assert set(rs.confidence.signal_summary.stale) == {"sleep", "hrv", "soreness"}
