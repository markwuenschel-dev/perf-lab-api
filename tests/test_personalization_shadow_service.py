"""Integration tests for the per-athlete personalization shadow service (ADR-0043).

A wellness ingest with enough history writes one capture-only row comparing population vs
personalized clearance multipliers; a sparse athlete falls back to the population prior; and a
failure in the estimator never breaks the ingest.

Requires a live PostgreSQL instance (async_db fixture).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.domain.vectors import FatigueState
from app.engine.simulate import baseline_state
from app.engine.state_bridge import athlete_state_kwargs_from_unified
from app.models.athlete_state import AthleteState
from app.models.personalization_shadow import PersonalizationShadowLog
from app.models.user import AthleteProfile, User
from app.models.wellness import WellnessSample
from app.services.personalization_shadow_service import record_personalization_shadow

pytestmark = pytest.mark.asyncio

_START = date(2026, 1, 1)


async def _mk_user(db, level="advanced", email="personalization@test.com") -> User:
    u = User(email=email, hashed_password="h", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    db.add(AthleteProfile(user_id=u.id, experience_level=level, experience_years=5.0))
    await db.commit()
    return u


def _state_row(user_id: int, when: datetime, fat: float) -> AthleteState:
    s = baseline_state(when=when)
    for k in FatigueState.KEYS:
        setattr(s.fatigue_f, k, fat)
    s.f_met_systemic = s.f_nm_peripheral = s.f_nm_central = s.f_struct_damage = fat
    return AthleteState(user_id=user_id, **athlete_state_kwargs_from_unified(s))


async def _seed_history(db, user_id: int, n_days: int) -> WellnessSample:
    """Seed n_days of daily wellness + fatigue states; return the latest wellness sample."""
    last: WellnessSample | None = None
    for i in range(n_days):
        d = _START + timedelta(days=i)
        # wellness varies day to day so the per-signal regression is identifiable
        w = WellnessSample(
            user_id=user_id, date=d, source="manual",
            sleep_hours=7.0 + (i % 3) * 0.6, hrv_ms=55.0 + (i % 4) * 5.0, resting_hr=58.0 - (i % 3) * 3.0,
        )
        db.add(w)
        last = w
        # fatigue oscillates so consecutive-day clearance is non-trivial
        fat = 45.0 + 8.0 * ((i % 5) - 2)
        db.add(_state_row(user_id, datetime(d.year, d.month, d.day, 12), fat))
    await db.commit()
    assert last is not None
    return last


async def _rows(db, user_id: int) -> list[PersonalizationShadowLog]:
    res = await db.execute(select(PersonalizationShadowLog).where(PersonalizationShadowLog.user_id == user_id))
    return list(res.scalars().all())


async def test_personalization_activates_with_enough_history(async_db):
    user = await _mk_user(async_db)
    last = await _seed_history(async_db, user.id, n_days=16)
    await record_personalization_shadow(async_db, user.id, last)

    rows = await _rows(async_db, user.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.decision_impact == "none_shadow_only"
    assert row.n_obs >= 8
    assert row.shrinkage_weight > 0.0          # personalization active
    assert row.theta_trace > 0.0               # P^θ recorded
    assert len(row.population_multiplier) == 6 and len(row.personalized_multiplier) == 6
    assert row.personalized_multiplier != row.population_multiplier  # it actually differs


async def test_sparse_athlete_falls_back_to_population(async_db):
    user = await _mk_user(async_db, email="sparse@test.com")
    # only 3 days → fewer than _MIN_OBS pairs
    last = await _seed_history(async_db, user.id, n_days=3)
    await record_personalization_shadow(async_db, user.id, last)

    rows = await _rows(async_db, user.id)
    assert len(rows) == 1
    assert rows[0].n_obs < 8
    assert rows[0].shrinkage_weight == 0.0
    assert rows[0].personalized_multiplier == rows[0].population_multiplier  # ≡ population


async def test_estimator_failure_never_breaks_ingest(async_db, monkeypatch):
    user = await _mk_user(async_db, email="pfail@test.com")
    uid = user.id
    last = await _seed_history(async_db, uid, n_days=16)

    def _boom(*a, **k):
        raise RuntimeError("simulated estimator failure")

    monkeypatch.setattr("app.services.personalization_shadow_service.fit_athlete", _boom)
    await record_personalization_shadow(async_db, uid, last)  # must not raise

    count = (await async_db.execute(
        select(func.count()).where(PersonalizationShadowLog.user_id == uid)
    )).scalar()
    assert count == 0
