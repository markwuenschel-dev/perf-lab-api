"""Recovery shadow-telemetry multiplier math (Q2 recovery priors, Rail 3).

Non-DB: pins the pure clearance-multiplier computation and proves that the untrained
placeholder override yields learned == baseline (zero shadow divergence until a real
v1 prior is fitted), that recovery signals move the multiplier the right way, and that
the clip envelope is enforced.
"""
from app.domain.vectors import FatigueState
from app.engine.parameter_overrides import (
    apply_parameter_overrides,
    load_namespace_override,
    load_override_artifact,
)
from app.engine.parameters import default_parameters
from app.logic.recovery_telemetry import (
    clearance_multiplier,
    multipliers_by_axis,
    wellness_snapshot,
)
from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot


def _wellness(sleep_hours=8.0, hrv_ms=60.0, resting_hr=55.0, soreness=3.0, mood=6.0):
    # Defaults are the neutral baselines → every z-score is 0.
    return WellnessTelemetrySnapshot(
        sleep_hours=sleep_hours, hrv_ms=hrv_ms, resting_hr=resting_hr,
        soreness=soreness, mood=mood,
    )


def test_neutral_wellness_gives_unit_multiplier():
    p = default_parameters()
    for axis in FatigueState.KEYS:
        assert clearance_multiplier(p, axis, _wellness()) == 1.0


def test_good_recovery_speeds_clearance():
    p = default_parameters()
    good = _wellness(sleep_hours=9.5, resting_hr=48.0)  # more sleep, lower RHR
    assert clearance_multiplier(p, "cns", good) > 1.0


def test_poor_recovery_slows_clearance():
    p = default_parameters()
    poor = _wellness(sleep_hours=5.0)
    assert clearance_multiplier(p, "cns", poor) < 1.0


def test_multiplier_respects_clip_envelope():
    p = default_parameters()
    extreme = _wellness(sleep_hours=100.0, hrv_ms=1000.0, resting_hr=1.0)
    for axis in FatigueState.KEYS:
        m = clearance_multiplier(p, axis, extreme)
        assert p.recovery_clearance_min <= m <= p.recovery_clearance_max


def test_placeholder_override_has_zero_divergence_from_baseline():
    # The untrained placeholder extends the signal set with hrv/rhr/soreness=0, so the
    # learned multiplier must equal the production baseline for every wellness sample.
    base = default_parameters()
    placeholder = load_override_artifact("q2_recovery_priors_v0_placeholder.json")
    learned = apply_parameter_overrides(base, placeholder, allow_shadow=True)
    for w in (_wellness(), _wellness(sleep_hours=9.0, hrv_ms=75.0, resting_hr=50.0), _wellness(sleep_hours=6.0)):
        assert multipliers_by_axis(base, w) == multipliers_by_axis(learned, w)


def test_active_override_is_a_weak_bounded_prior():
    # The trained v1 prior (whatever load_namespace_override resolves to) may diverge from
    # baseline, but must stay a WEAK, clip-bounded nudge — never a large excursion.
    base = default_parameters()
    artifact = load_namespace_override("q2_recovery")
    assert artifact is not None
    learned = apply_parameter_overrides(base, artifact, allow_shadow=True)
    for w in (_wellness(), _wellness(sleep_hours=9.0, hrv_ms=80.0, resting_hr=48.0), _wellness(sleep_hours=6.0, hrv_ms=45.0)):
        bmul = multipliers_by_axis(base, w)
        lmul = multipliers_by_axis(learned, w)
        for axis in FatigueState.KEYS:
            assert base.recovery_clearance_min <= lmul[axis] <= base.recovery_clearance_max
            assert abs(lmul[axis] - bmul[axis]) < 0.1, f"{axis}: learned prior must be a weak nudge"


def test_wellness_snapshot_shape():
    snap = wellness_snapshot(_wellness())
    assert set(snap) == {"sleep_hours", "hrv_ms", "resting_hr", "soreness", "mood"}


def test_missing_signals_are_skipped():
    p = default_parameters()
    partial = WellnessTelemetrySnapshot(
        sleep_hours=None, hrv_ms=None, resting_hr=None, soreness=None, mood=None
    )
    # No usable signals → neutral multiplier.
    assert clearance_multiplier(p, "cns", partial) == 1.0
    assert wellness_snapshot(partial) == {
        "sleep_hours": None, "hrv_ms": None, "resting_hr": None, "soreness": None, "mood": None
    }
