"""C1b adapter: a planned session -> the synthetic WorkoutLog it is projected as (ADR-0073)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.engine.simulate import INTENSITY_BANDS, SESSION_BASELINES
from app.logic.planned_session_log import (
    intensity_band_for_rpe,
    planned_session_to_log,
    prescribed_rpe,
    session_basis,
)
from app.models.mesocycle import BlockGoal, MesocycleBlock, PlannedSession, SessionStatus

WHEN = datetime(2026, 9, 28, 12, 0)


def _block(**kw) -> MesocycleBlock:
    params = {
        "id": 1,
        "user_id": 1,
        "goal": BlockGoal.STRENGTH,
        "start_date": date(2026, 9, 28),
        "duration_weeks": 4,
        "deload_volume_factor": 0.6,
        "target_session_minutes": None,
    }
    params.update(kw)
    return MesocycleBlock(**params)


def _session(**kw) -> PlannedSession:
    params = {
        "id": 10,
        "block_id": 1,
        "user_id": 1,
        "scheduled_date": date(2026, 9, 28),
        "week_number": 1,
        "day_of_week": 1,
        "category": "Heavy Lower",
        "modality": "Strength",
        "domain": "strength",
        "status": SessionStatus.PENDING,
        "is_deload": False,
        "prescribed_content": None,
    }
    params.update(kw)
    return PlannedSession(**params)


def _rx(*caps: float | None) -> dict:
    return {
        "type": "Strength",
        "focus": "Squat",
        "rationale": "r",
        "duration_min": 60,
        "exercises": [{"name": f"ex{i}", "rpe_cap": c} for i, c in enumerate(caps)],
    }


def test_template_estimate_uses_balanced_band_and_modality_baseline() -> None:
    log = planned_session_to_log(_session(), _block(), WHEN)

    assert session_basis(_session()) == "template_estimate"
    assert log.modality == "Strength"
    assert log.session_rpe == INTENSITY_BANDS["balanced"]["rpe"]
    assert log.duration_minutes == pytest.approx(SESSION_BASELINES["Strength"]["duration_minutes"])
    assert log.timestamp == WHEN


def test_prescribed_session_takes_its_band_from_the_max_rpe_cap() -> None:
    session = _session(prescribed_content=_rx(6.0, 8.5, None))

    assert session_basis(session) == "prescribed"
    assert prescribed_rpe(session.prescribed_content) == 8.5
    assert planned_session_to_log(session, _block(), WHEN).session_rpe == INTENSITY_BANDS["hard"]["rpe"]


def test_prescription_without_caps_is_prescribed_basis_but_balanced_band() -> None:
    session = _session(prescribed_content=_rx(None))

    assert prescribed_rpe(session.prescribed_content) is None  # never guessed
    assert session_basis(session) == "prescribed"
    assert planned_session_to_log(session, _block(), WHEN).session_rpe == INTENSITY_BANDS["balanced"]["rpe"]


@pytest.mark.parametrize(
    ("rpe", "band"),
    [(None, "balanced"), (5.0, "easy"), (6.2, "easy"), (7.0, "balanced"), (7.7, "balanced"),
     (8.0, "hard"), (10.0, "hard")],
)
def test_rpe_band_is_the_nearest_band(rpe, band) -> None:
    assert intensity_band_for_rpe(rpe) == band


def test_deload_session_scales_duration_by_the_block_factor() -> None:
    block = _block(target_session_minutes=70, deload_volume_factor=0.5)
    normal = planned_session_to_log(_session(), block, WHEN)
    deload = planned_session_to_log(_session(is_deload=True), block, WHEN)

    assert normal.duration_minutes == pytest.approx(70.0)
    assert deload.duration_minutes == pytest.approx(35.0)


def test_missing_domain_falls_back_to_the_block_goal() -> None:
    running_block = _block(goal=BlockGoal.RUNNING)
    log = planned_session_to_log(_session(domain=None, modality="Strength"), running_block, WHEN)

    # The display label says Strength; the block goal (Running) is what prescription uses.
    assert log.modality == "Running"


def test_malformed_prescription_content_yields_no_rpe() -> None:
    assert prescribed_rpe({"exercises": "nope"}) is None
    assert prescribed_rpe({"exercises": [1, {"rpe_cap": "8"}, {"rpe_cap": True}]}) is None
    assert prescribed_rpe(None) is None
