"""Per-axis capacity confidence (ADR-0036).

Confidence is a per-capacity-axis variance proxy: seeded as a weak (high-variance)
prior, grown by elapsed time, and persisted inside the engine_state JSONB payload
(schema v2) — no Alembic migration. The benchmark *shrink* lands in ADR-0034.
"""

from datetime import timedelta
from types import SimpleNamespace

from app.domain.vectors import SEED_CAPACITY_VARIANCE, CapacityConfidence
from app.engine.parameters import default_parameters
from app.engine.simulate import baseline_state, make_log, rest_dose
from app.engine.state_bridge import (
    athlete_state_kwargs_from_unified,
    unified_from_athlete_row,
)
from app.logic.state_update_v0 import kalman_gain, update_athlete_state


def test_seed_prior_is_weak_high_variance():
    cc = CapacityConfidence()
    for key in CapacityConfidence.KEYS:
        assert getattr(cc, key) == SEED_CAPACITY_VARIANCE


def test_variance_grows_with_elapsed_time():
    s0 = baseline_state()
    s0.capacity_confidence.max_strength = 0.1  # pretend a recent measurement
    log = make_log(s0.timestamp + timedelta(days=30), "Strength")
    s1 = update_athlete_state(s0, rest_dose(), timedelta(days=30), log)
    assert s1.capacity_confidence.max_strength > 0.1


def test_variance_capped_at_max():
    s0 = baseline_state()
    p = default_parameters()
    log = make_log(s0.timestamp + timedelta(days=3650), "Strength")
    s1 = update_athlete_state(s0, rest_dose(), timedelta(days=3650), log)
    for key in CapacityConfidence.KEYS:
        assert getattr(s1.capacity_confidence, key) <= p.confidence_max_variance + 1e-9


def test_kalman_gain_monotonic():
    # Less confident prior (higher variance) → larger correction.
    assert kalman_gain(1.0, 0.1) > kalman_gain(0.2, 0.1)
    # More trustworthy measurement (lower variance) → larger correction.
    assert kalman_gain(1.0, 0.05) > kalman_gain(1.0, 0.5)
    assert 0.0 <= kalman_gain(1.0, 0.1) < 1.0


def test_confidence_roundtrips_through_engine_state():
    s = baseline_state()
    s.capacity_confidence.aerobic = 0.33
    s.capacity_confidence.max_strength = 0.12
    row = SimpleNamespace(**athlete_state_kwargs_from_unified(s))
    restored = unified_from_athlete_row(row)
    assert restored.capacity_confidence.aerobic == 0.33
    assert restored.capacity_confidence.max_strength == 0.12


def test_legacy_v1_payload_migrates_to_weak_confidence():
    """A v1 engine_state (no "c") loads with a weak-prior confidence (lazy migrate)."""
    s = baseline_state()
    kwargs = athlete_state_kwargs_from_unified(s)
    eng = dict(kwargs["engine_state"])
    eng.pop("c")        # simulate a pre-confidence (v1) payload
    eng["version"] = 1
    kwargs["engine_state"] = eng
    restored = unified_from_athlete_row(SimpleNamespace(**kwargs))
    for key in CapacityConfidence.KEYS:
        assert getattr(restored.capacity_confidence, key) == SEED_CAPACITY_VARIANCE
