"""
Extended scenario tests covering multi-session dynamics and prescriber context injection.

These are service-layer tests (no HTTP) — they test the full control loop over
multiple sessions in a way that exercises behaviour not covered by unit tests.
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.engine.state_bridge import unified_from_athlete_row
from app.logic.prescriber import recommend_next_session
from app.models.athlete_state import AthleteState
from app.models.user import User
from app.schemas.workouts import WorkoutLog
from app.services.state_service import initialize_athlete_state, process_new_workout

pytestmark = pytest.mark.asyncio

_T0 = datetime(2026, 1, 1, 9, 0, 0, tzinfo=UTC)


async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="hash", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


def _log(ts: datetime, modality: str = "Strength", rpe: float = 8.0) -> WorkoutLog:
    return WorkoutLog(
        timestamp=ts,
        modality=modality,
        duration_minutes=70.0,
        session_rpe=rpe,
        total_volume_load=5000.0,
        estimated_sets=14.0,
        sleep_quality=7.0,
        life_stress_inverse=7.0,
    )


# ── Fatigue accumulation over a hard week ─────────────────────────────────────

async def test_four_consecutive_hard_sessions_accumulate_fatigue(async_db):
    """
    Four sessions at RPE 9 logged within 48 h should produce substantially
    higher composite fatigue than after the first session alone.
    """
    user = await _user(async_db, "four_sessions@test.com")
    await initialize_athlete_state(async_db, user.id)

    state_after_1 = await process_new_workout(async_db, user.id, _log(_T0, rpe=9.0))
    f1 = state_after_1.fatigue_f.cns + state_after_1.fatigue_f.muscular + state_after_1.fatigue_f.metabolic

    for i in range(1, 4):
        await process_new_workout(async_db, user.id, _log(_T0 + timedelta(hours=i * 10), rpe=9.0))

    final_state = (await async_db.execute(
        select(AthleteState)
        .where(AthleteState.user_id == user.id)
        .order_by(AthleteState.timestamp.desc())
        .limit(1)
    )).scalars().first()

    from app.engine.state_bridge import unified_from_athlete_row
    sv = unified_from_athlete_row(final_state)
    f4 = sv.fatigue_f.cns + sv.fatigue_f.muscular + sv.fatigue_f.metabolic

    assert f4 > f1, (
        f"Fatigue after 4 hard sessions ({f4:.2f}) should exceed fatigue after 1 ({f1:.2f})"
    )


# ── State row count over multiple sessions ────────────────────────────────────

async def test_state_row_count_matches_session_count(async_db):
    """
    N workout logs should produce N+1 AthleteState rows (1 init + N updates),
    all with strictly ascending timestamps.
    """
    user = await _user(async_db, "rowcount@test.com")
    await initialize_athlete_state(async_db, user.id)

    n = 6
    for i in range(n):
        await process_new_workout(async_db, user.id, _log(_T0 + timedelta(days=i + 1)))

    rows = (await async_db.execute(
        select(AthleteState)
        .where(AthleteState.user_id == user.id)
        .order_by(AthleteState.id.asc())
    )).scalars().all()

    assert len(rows) == n + 1, f"Expected {n + 1} rows, got {len(rows)}"
    timestamps = [r.timestamp for r in rows]
    for i in range(1, len(timestamps)):
        assert timestamps[i] > timestamps[i - 1], (
            f"Timestamps not ascending at index {i}"
        )


# ── Recovery: fatigue should decay with rest ──────────────────────────────────

async def test_fatigue_decays_after_rest(async_db):
    """
    A very hard session followed by 72 h of no training should leave lower
    fatigue than immediately after the session (decay over time).
    """
    user = await _user(async_db, "decay@test.com")
    await initialize_athlete_state(async_db, user.id)

    # Hard session
    state_post = await process_new_workout(async_db, user.id, _log(_T0, rpe=9.5))
    f_immediately = state_post.fatigue_f.cns + state_post.fatigue_f.muscular

    # Easy aerobic session 72 h later (forces a state update with time elapsed)
    easy = WorkoutLog(
        timestamp=_T0 + timedelta(hours=72),
        modality="Running",
        duration_minutes=30.0,
        session_rpe=4.0,
        distance_meters=5000.0,
        sleep_quality=9.0,
        life_stress_inverse=9.0,
    )
    state_after_rest = await process_new_workout(async_db, user.id, easy)
    f_after = state_after_rest.fatigue_f.cns + state_after_rest.fatigue_f.muscular

    assert f_after < f_immediately, (
        f"Fatigue should decay with rest: post-session={f_immediately:.2f}, after 72h={f_after:.2f}"
    )


# ── Weak-point tags appear in prescription constraints ────────────────────────

async def test_weak_point_tags_appear_in_prescription_constraints(async_db):
    """
    When a user has active weak points, the prescriber should include
    'weak_point:{tag}' entries in constraints_applied.
    """
    user = await _user(async_db, "weak_point_rx@test.com")
    await initialize_athlete_state(async_db, user.id)
    state = unified_from_athlete_row(
        (await async_db.execute(
            select(AthleteState)
            .where(AthleteState.user_id == user.id)
            .order_by(AthleteState.timestamp.desc())
            .limit(1)
        )).scalars().first()
    )

    tags = ["grip", "posterior_chain"]
    rx = recommend_next_session(
        state,
        goal="Strength",
        active_weak_points=tags,
    )

    assert rx.why is not None
    applied = rx.why.constraints_applied
    for tag in tags:
        assert f"weak_point:{tag}" in applied, (
            f"Expected 'weak_point:{tag}' in constraints_applied, got: {applied}"
        )


# ── No weak points → no weak_point constraints ───────────────────────────────

async def test_no_weak_points_no_weak_point_constraints(async_db):
    """Without active weak points, constraints_applied should have no weak_point entries."""
    user = await _user(async_db, "no_weak@test.com")
    await initialize_athlete_state(async_db, user.id)
    state = unified_from_athlete_row(
        (await async_db.execute(
            select(AthleteState)
            .where(AthleteState.user_id == user.id)
            .order_by(AthleteState.timestamp.desc())
            .limit(1)
        )).scalars().first()
    )

    rx = recommend_next_session(state, goal="Strength", active_weak_points=[])

    if rx.why:
        wp_entries = [c for c in rx.why.constraints_applied if c.startswith("weak_point:")]
        assert wp_entries == [], f"Unexpected weak_point entries: {wp_entries}"


# ── Running modality creates different state profile ─────────────────────────

async def test_running_workout_updates_aerobic_channel(async_db):
    """A running workout should primarily affect metabolic fatigue, not CNS."""
    user = await _user(async_db, "running_aerobic@test.com")
    await initialize_athlete_state(async_db, user.id)

    run_log = WorkoutLog(
        timestamp=_T0,
        modality="Running",
        duration_minutes=50.0,
        session_rpe=6.5,
        distance_meters=8000.0,
        sleep_quality=8.0,
        life_stress_inverse=8.0,
    )
    state = await process_new_workout(async_db, user.id, run_log)

    # Metabolic fatigue should exceed CNS fatigue for an aerobic session
    assert state.fatigue_f.metabolic >= state.fatigue_f.cns, (
        f"Running should drive metabolic fatigue ({state.fatigue_f.metabolic:.2f}) "
        f">= CNS ({state.fatigue_f.cns:.2f})"
    )


# ── Deload signal: high fatigue → prescriber recommends lower intensity ───────

async def test_high_fatigue_deload_candidate_selected(async_db):
    """
    A sustained block of heavy, high-volume strength sessions with insufficient
    recovery drives structural-damage fatigue past the prescriber's hard safety
    threshold (f_struct_damage > 70), forcing a recovery / reduced-intensity
    session rather than Max Strength.

    Note the engine's design: CNS fatigue alone is only a *soft, overridable*
    readiness redirect — the *hard* deload stops key off tissue / structural /
    systemic fatigue (see prescriber._safety_candidates). So a guaranteed deload
    scenario must drive one of those axes, which a heavy high-volume block does.
    """
    user = await _user(async_db, "deload_signal@test.com")
    await initialize_athlete_state(async_db, user.id)

    # A genuine overreaching block: 15 heavy, high-volume sessions ~8h apart,
    # under poor sleep/stress — enough to push structural-damage fatigue past the
    # safety-override threshold.
    for i in range(15):
        hard = WorkoutLog(
            timestamp=_T0 + timedelta(hours=i * 8),
            modality="Strength",
            duration_minutes=90.0,
            session_rpe=10.0,
            total_volume_load=12000.0,
            estimated_sets=28.0,
            sleep_quality=6.0,
            life_stress_inverse=6.0,
        )
        await process_new_workout(async_db, user.id, hard)

    final_row = (await async_db.execute(
        select(AthleteState)
        .where(AthleteState.user_id == user.id)
        .order_by(AthleteState.timestamp.desc())
        .limit(1)
    )).scalars().first()
    state = unified_from_athlete_row(final_row)

    rx = recommend_next_session(state, goal="Strength")

    # Under a hard safety override the prescription is a recovery/reduced-intensity
    # type (or, failing that, a clearly shortened session).
    reduced_intensity_types = {
        "Recovery", "Tissue Deload", "Deload", "Active Recovery",
        "Mobility", "Skill Acquisition", "Technical Volume",
    }
    assert rx.type in reduced_intensity_types or rx.duration_min <= 50, (
        f"Expected a reduced-intensity prescription under high fatigue, got: type='{rx.type}', "
        f"duration={rx.duration_min}min, f_struct_damage={state.f_struct_damage:.1f}, "
        f"f_nm_central={state.f_nm_central:.1f}"
    )
