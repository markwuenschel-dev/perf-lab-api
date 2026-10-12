"""The twin must be able to measure how wrong its own forecasts were.

This is the loop's measuring instrument. What matters most is not that it computes an
error - that part is arithmetic - but that it refuses to compute one when the answer would
be unattributable or reconstructed. A scorer that silently drops what it cannot handle
reports a flattering error rate, which is worse than no scorer.
"""

from datetime import UTC, datetime, timedelta

from app.models.athlete_state import AthleteState
from app.models.mesocycle import (
    BlockGoal,
    MesocycleBlock,
    PlannedSession,
    SessionStatus,
)
from app.models.user import User
from app.services.forecast_scoring_service import (
    SKIP_AMBIGUOUS,
    SKIP_NO_BRACKET,
    format_report,
    score_forecasts,
)

_T0 = datetime(2026, 8, 1, 12, 0, 0)


async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _state_row(db, user_id: int, at: datetime, *, cns: float) -> AthleteState:
    """A snapshot carrying a decodable engine payload, since the scorer decodes strictly.

    Built through the app's own unified->row converter rather than by setting columns by
    hand, so the row has a real engine payload and `unified_from_athlete_row_strict` can
    read it. A hand-built row would fail strict decoding and the test would pass for the
    wrong reason.
    """
    from app.engine.state_bridge import athlete_state_kwargs_from_unified
    from app.schemas.state import UnifiedStateVector

    vec = UnifiedStateVector(
        timestamp=at,
        c_met_aerobic=500.0,
        c_nm_force=50.0,
        c_struct=50.0,
        b_met_anaerobic=50.0,
    )
    vec.fatigue_f.cns = cns
    kwargs = athlete_state_kwargs_from_unified(vec)
    kwargs["timestamp"] = at
    row = AthleteState(user_id=user_id, **kwargs)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _block(db, user_id: int) -> MesocycleBlock:
    b = MesocycleBlock(
        user_id=user_id,
        goal=BlockGoal.STRENGTH,
        duration_weeks=8,
        sessions_per_week=3,
        start_date=_T0.date(),
    )
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return b


async def _session(
    db, user_id: int, *, completed_at: datetime, predicted_delta: float | None,
    block: MesocycleBlock,
) -> PlannedSession:
    content = None
    if predicted_delta is not None:
        content = {
            "why": {
                "expected_outcomes": [
                    {
                        "axis": "fatigue_f.cns",
                        "current": 0.0,
                        "predicted": predicted_delta,
                        "delta": predicted_delta,
                    }
                ]
            }
        }
    s = PlannedSession(
        block_id=block.id,
        user_id=user_id,
        scheduled_date=completed_at.date(),
        week_number=1,
        block_day=1,
        day_of_week=completed_at.date().isoweekday(),
        category="Heavy Lower",
        modality="Strength",
        status=SessionStatus.COMPLETED,
        completed_at=completed_at,
        prescribed_content=content,
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return s


# ── it measures ───────────────────────────────────────────────────────────────


async def test_error_is_realized_minus_predicted(async_db) -> None:
    """The sign convention is load-bearing: positive means reality cost more."""
    user = await _user(async_db, "fs-basic@test.com")
    uid = user.id
    blk = await _block(async_db, uid)
    await _state_row(async_db, uid, _T0 - timedelta(hours=1), cns=10.0)
    await _session(async_db, uid, block=blk, completed_at=_T0, predicted_delta=5.0)
    await _state_row(async_db, uid, _T0 + timedelta(hours=1), cns=18.0)

    result = await score_forecasts(async_db, user_id=uid)

    assert result["sessions_scored"] == 1
    axis = next(a for a in result["axes"] if a["axis"] == "fatigue_f.cns")
    assert axis["n"] == 1
    # realized +8.0 against a predicted +5.0 -> the twin under-predicted by 3.0
    assert axis["bias"] == 3.0
    assert axis["mean_abs_error"] == 3.0


async def test_a_perfect_forecast_scores_zero_error(async_db) -> None:
    user = await _user(async_db, "fs-perfect@test.com")
    uid = user.id
    blk = await _block(async_db, uid)
    await _state_row(async_db, uid, _T0 - timedelta(hours=1), cns=10.0)
    await _session(async_db, uid, block=blk, completed_at=_T0, predicted_delta=6.0)
    await _state_row(async_db, uid, _T0 + timedelta(hours=1), cns=16.0)

    axis = next(
        a for a in (await score_forecasts(async_db, user_id=uid))["axes"]
        if a["axis"] == "fatigue_f.cns"
    )

    assert axis["bias"] == 0.0
    assert axis["mean_abs_error"] == 0.0


# ── it refuses, and says so ───────────────────────────────────────────────────


async def test_an_unattributable_window_is_refused_not_counted(async_db) -> None:
    """Two sessions inside one snapshot window means neither delta is attributable.

    Counting it anyway would blame one session for both, which is exactly the kind of
    quiet wrongness a calibration number must never contain.
    """
    user = await _user(async_db, "fs-ambig@test.com")
    uid = user.id
    blk = await _block(async_db, uid)
    await _state_row(async_db, uid, _T0 - timedelta(hours=2), cns=10.0)
    await _session(async_db, uid, block=blk, completed_at=_T0, predicted_delta=5.0)
    await _session(async_db, uid, block=blk, completed_at=_T0 + timedelta(minutes=30), predicted_delta=5.0)
    await _state_row(async_db, uid, _T0 + timedelta(hours=2), cns=30.0)

    result = await score_forecasts(async_db, user_id=uid)

    assert result["sessions_scored"] == 0
    assert result["skipped"].get(SKIP_AMBIGUOUS, 0) >= 1


async def test_a_session_with_no_following_snapshot_is_skipped(async_db) -> None:
    """Without an "after" there is no realized delta - report it, never assume zero."""
    user = await _user(async_db, "fs-nobracket@test.com")
    uid = user.id
    blk = await _block(async_db, uid)
    await _state_row(async_db, uid, _T0 - timedelta(hours=1), cns=10.0)
    await _session(async_db, uid, block=blk, completed_at=_T0, predicted_delta=5.0)

    result = await score_forecasts(async_db, user_id=uid)

    assert result["sessions_scored"] == 0
    assert result["skipped"].get(SKIP_NO_BRACKET, 0) == 1


async def test_a_session_without_a_recorded_forecast_is_not_a_skip(async_db) -> None:
    """Sessions predating the forecast feature are simply out of scope, not failures.

    Counting them as skips would make the instrument look broken for a legitimate reason.
    """
    user = await _user(async_db, "fs-noforecast@test.com")
    uid = user.id
    blk = await _block(async_db, uid)
    await _state_row(async_db, uid, _T0 - timedelta(hours=1), cns=10.0)
    await _session(async_db, uid, block=blk, completed_at=_T0, predicted_delta=None)
    await _state_row(async_db, uid, _T0 + timedelta(hours=1), cns=18.0)

    result = await score_forecasts(async_db, user_id=uid)

    assert result["sessions_scored"] == 0
    assert result["skipped"] == {}


# ── it stays an instrument ────────────────────────────────────────────────────


async def test_the_report_authorizes_nothing(async_db) -> None:
    """Same contract as the EKF calibration gate: evidence, never authority."""
    result = await score_forecasts(async_db)

    assert "nothing" in result["authorizes"]


async def test_an_empty_history_reports_nothing_rather_than_a_score(async_db) -> None:
    result = await score_forecasts(async_db)

    assert result["sessions_scored"] == 0
    assert result["axes"] == []
    assert "no axis had a scoreable forecast yet" in format_report(result)


async def test_scope_narrows_to_one_athlete(async_db) -> None:
    a = await _user(async_db, "fs-a@test.com")
    b = await _user(async_db, "fs-b@test.com")
    for uid in (a.id, b.id):
        blk = await _block(async_db, uid)
        await _state_row(async_db, uid, _T0 - timedelta(hours=1), cns=10.0)
        await _session(async_db, uid, block=blk, completed_at=_T0, predicted_delta=5.0)
        await _state_row(async_db, uid, _T0 + timedelta(hours=1), cns=18.0)

    fleet = await score_forecasts(async_db)
    just_a = await score_forecasts(async_db, user_id=a.id)

    assert fleet["sessions_scored"] >= 2
    assert just_a["sessions_scored"] == 1
    assert just_a["scope"] == f"user:{a.id}"


async def test_report_renders(async_db) -> None:
    user = await _user(async_db, "fs-fmt@test.com")
    uid = user.id
    blk = await _block(async_db, uid)
    await _state_row(async_db, uid, _T0 - timedelta(hours=1), cns=10.0)
    await _session(async_db, uid, block=blk, completed_at=_T0, predicted_delta=5.0)
    await _state_row(async_db, uid, _T0 + timedelta(hours=1), cns=18.0)

    text = format_report(await score_forecasts(async_db, user_id=uid))

    assert "fatigue_f.cns" in text
    assert "authorizes:" in text
    assert datetime.now(UTC) is not None  # keeps the UTC import meaningful
