from __future__ import annotations

from datetime import UTC, datetime

from app.logic.constraint_engine.candidate import SessionCandidate
from app.logic.mpc.candidate_dose import (
    candidate_to_dose,
    candidate_to_log,
    intensity_for_candidate,
    modality_for_candidate,
    modality_for_domain,
)
from app.schemas.engine_vectors import StressDoseSix

_WHEN = datetime(2026, 1, 1, tzinfo=UTC)


def _cand(**kw) -> SessionCandidate:
    base = {"type": "Session", "focus": "", "rationale": "", "duration_min": 60, "branch_id": "b"}
    base.update(kw)
    return SessionCandidate(**base)  # type: ignore[arg-type]


def _dose_mag(dose) -> float:
    return sum(getattr(dose.dose_six, k) for k in StressDoseSix.KEYS)


def test_modality_from_domain():
    assert modality_for_domain("strength") == "Strength"
    assert modality_for_domain("running") == "Running"
    assert modality_for_domain("power") == "Power"
    assert modality_for_domain("") == "Mixed"


def test_modality_from_candidate_falls_back_to_type_text():
    assert modality_for_candidate(_cand(domain="strength")) == "Strength"
    assert modality_for_candidate(_cand(domain="", type="Tempo Run")) == "Running"
    assert modality_for_candidate(_cand(domain="", type="Something")) == "Mixed"


def test_intensity_heuristic():
    assert intensity_for_candidate(_cand(type="Max Strength")) == "hard"
    assert intensity_for_candidate(_cand(type="Recovery", focus="easy mobility")) == "easy"
    assert intensity_for_candidate(_cand(type="Accessory Volume")) == "balanced"


def test_candidate_to_dose_is_nonzero():
    dose = candidate_to_dose(_cand(type="Max Strength", domain="strength"), _WHEN)
    assert _dose_mag(dose) > 0.0


def test_hard_candidate_carries_more_dose_than_easy():
    hard = candidate_to_dose(_cand(type="Max Strength", domain="strength", duration_min=70), _WHEN)
    easy = candidate_to_dose(_cand(type="Recovery", domain="strength", duration_min=30), _WHEN)
    assert _dose_mag(hard) > _dose_mag(easy)


def test_candidate_to_log_scales_duration():
    long_log = candidate_to_log(_cand(domain="strength", duration_min=90), _WHEN)
    short_log = candidate_to_log(_cand(domain="strength", duration_min=30), _WHEN)
    assert long_log.duration_minutes > short_log.duration_minutes
