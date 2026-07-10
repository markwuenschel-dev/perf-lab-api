"""Dose-law external load vs internal effort — ADR-0039 Model A + ADR-0056.

External intensity ``I`` is load relative to capacity, computed by the canonical
``strength_calibration`` service and fed into the session dose base as a scalar. It is
independent of internal effort ``F`` (a heavy triple and a high-rep set to failure
differ in load), and it is never a bare number — every reading carries provenance.
"""

from datetime import datetime

from app.logic import strength_calibration as sc
from app.logic.dose_engine_v0 import (
    SetIntensitySample,
    build_session_external_intensity,
    calculate_stress_dose,
)
from app.schemas.workouts import ExternalIntensity, WorkoutLog

# ── canonical calibration service (ADR-0056 golden cases) ─────────────────────

def test_single_to_failure_is_one_rm():
    # A single @ RPE10 / 0 RIR resolves to 100%, everywhere.
    assert sc.percent_1rm_for_prescription(1, 10.0).value == 1.0
    chart = sc.external_intensity_for_set(
        reps=1, load_kg=None, rpe=10.0, rir=None, e1rm_pre=None, to_failure=True
    )
    assert chart.value == 1.0
    assert chart.source == sc.SRC_RPE_RIR_CHART


def test_chart_orders_rpe_and_maps_rir_to_rpe():
    # 5 @ RPE8 is lighter than 5 @ RPE10 …
    assert sc.percent_1rm_for_prescription(5, 8.0).value < sc.percent_1rm_for_prescription(5, 10.0).value
    # … and 5 @ 2RIR ≈ 5 @ RPE8 (RIR maps to RPE = 10 - RIR).
    by_rir = sc.external_intensity_for_set(
        reps=5, load_kg=None, rpe=None, rir=2.0, e1rm_pre=None, to_failure=False
    )
    by_rpe = sc.external_intensity_for_set(
        reps=5, load_kg=None, rpe=8.0, rir=None, e1rm_pre=None, to_failure=False
    )
    assert abs(by_rir.value - by_rpe.value) < 1e-9
    assert by_rpe.value == sc.percent_1rm_for_prescription(5, 8.0).value  # dose ⇄ prescription agree


def test_relative_load_is_primary_when_e1rm_present():
    # With a pre-log e1RM, I = load / e1RM_pre — the highest-fidelity rung.
    r = sc.external_intensity_for_set(
        reps=5, load_kg=100.0, rpe=8.0, rir=None, e1rm_pre=125.0, to_failure=False
    )
    assert r.source == sc.SRC_RELATIVE_LOAD
    assert r.value == 100.0 / 125.0
    assert r.e1rm_pre == 125.0
    assert r.confidence > 0


def test_epley_only_when_to_failure():
    # No effort logged, not to failure → honest neutral, not fake precision.
    neutral = sc.external_intensity_for_set(
        reps=8, load_kg=None, rpe=None, rir=None, e1rm_pre=None, to_failure=False
    )
    assert neutral.source == sc.SRC_NEUTRAL_MISSING
    assert neutral.value == 1.0 and neutral.confidence == 0.0
    # Same set marked to-failure → Epley reps-beyond-first.
    failed = sc.external_intensity_for_set(
        reps=8, load_kg=None, rpe=None, rir=None, e1rm_pre=None, to_failure=True
    )
    assert failed.source == sc.SRC_EPLEY_FAILURE
    assert sc.PCT_MIN <= failed.value <= sc.PCT_MAX_DOSE


def test_heavy_low_rep_is_higher_intensity_than_high_rep():
    heavy = sc.external_intensity_for_set(
        reps=3, load_kg=None, rpe=9.0, rir=None, e1rm_pre=None, to_failure=False
    )
    light = sc.external_intensity_for_set(
        reps=15, load_kg=None, rpe=9.0, rir=None, e1rm_pre=None, to_failure=False
    )
    assert heavy.value > light.value


def test_relative_load_effort_fidelity_lowers_confidence():
    set_level = sc.external_intensity_for_set(
        reps=5, load_kg=100.0, rpe=8.0, rir=None, e1rm_pre=125.0, to_failure=False,
        effort_fidelity="set_level",
    )
    group_level = sc.external_intensity_for_set(
        reps=5, load_kg=100.0, rpe=8.0, rir=None, e1rm_pre=125.0, to_failure=False,
        effort_fidelity="group_level",
    )
    assert group_level.confidence < set_level.confidence
    assert group_level.value == set_level.value  # only confidence changes


# ── session aggregation (set → exercise → session, w = reps·load) ─────────────

def _sample(*, ex_id, value, source, weight, conf=0.5):
    return SetIntensitySample(
        exercise_id=ex_id,
        exercise_name=f"ex{ex_id}",
        result=sc.CalibrationResult(value=value, source=source, confidence=conf),
        weight=weight,
    )


def test_session_intensity_is_reps_load_weighted():
    # Heavy exercise (big w) should dominate the session scalar.
    samples = [
        _sample(ex_id=1, value=0.9, source=sc.SRC_RELATIVE_LOAD, weight=900.0),
        _sample(ex_id=2, value=0.5, source=sc.SRC_RELATIVE_LOAD, weight=100.0),
    ]
    ext = build_session_external_intensity(samples)
    expected = (0.9 * 900 + 0.5 * 100) / 1000
    assert abs(ext.value - expected) < 1e-6
    assert len(ext.contributions) == 2
    assert ext.known_limitation  # ADR-0054 routing caveat present


def test_no_loaded_sets_degrades_to_labeled_neutral():
    ext = build_session_external_intensity([])
    assert ext.value == 1.0
    assert ext.source == sc.SRC_NEUTRAL_MISSING
    assert ext.confidence == 0.0
    assert ext.known_limitation is None


# ── dose integration: equal volume, different intensity → different dose ──────

def _session_log(volume: float) -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime(2026, 7, 10, 12, 0, 0),
        modality="Strength",
        duration_minutes=45.0,
        session_rpe=8.0,
        total_volume_load=volume,
        estimated_sets=5.0,
    )


def _ext(value: float) -> ExternalIntensity:
    return ExternalIntensity(
        value=value, source=sc.SRC_RELATIVE_LOAD, model_version=sc.MODEL_VERSION,
        confidence=0.9, fallback_path=sc.SRC_RELATIVE_LOAD,
    )


def test_equal_volume_different_intensity_differ_and_order():
    # 5×5 @ 50 / 75 / 90% — identical volume, three intensities → monotone dose.
    log = _session_log(volume=2500.0)
    low = calculate_stress_dose(log, external_intensity=_ext(0.50))
    mid = calculate_stress_dose(log, external_intensity=_ext(0.75))
    high = calculate_stress_dose(log, external_intensity=_ext(0.90))
    assert low.dose_six.volume < mid.dose_six.volume < high.dose_six.volume
    assert low.dose_six != high.dose_six


def test_dose_emits_intensity_provenance():
    log = _session_log(volume=2500.0)
    dose = calculate_stress_dose(log, external_intensity=_ext(0.80))
    assert dose.external_intensity is not None
    assert dose.external_intensity.value == 0.80
    assert dose.external_intensity.model_version == sc.MODEL_VERSION


def test_no_external_intensity_is_labeled_neutral_not_bare_one():
    # The engine must record a neutral rather than a silent hardcoded 1.0.
    dose = calculate_stress_dose(_session_log(volume=2500.0))
    assert dose.external_intensity is not None
    assert dose.external_intensity.value == 1.0
    assert dose.external_intensity.source == sc.SRC_NEUTRAL_MISSING
    assert dose.external_intensity.confidence == 0.0
