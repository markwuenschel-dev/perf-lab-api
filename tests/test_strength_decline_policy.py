"""T1 — pure decline-policy unit tests (INT-02, ADR-0066).

No DB. Exercises the locked numerical contract: variance-aware materiality,
threshold provenance/precedence, bounded (non-overwrite) posterior, and the
conservative ceiling. Values are in raw e1RM units for legibility.
"""
import math

import pytest

from app.logic import strength_decline_policy as p

# --- materiality: inside vs beyond the measurement error -----------------------

def test_small_drop_inside_mdc_is_immaterial():
    # prior 150, retest 149; MDC ≈ 6.3 (CV 4.2% of 150). 1kg drop is noise.
    thr = p.material_decline_threshold(
        prior_mean=150.0, prior_variance=0.0, observation_variance=0.0,
        error=p.MeasurementError(mdc=6.3),
    )
    delta = p.downward_residual(150.0, 149.0)
    assert p.is_material_decline(delta, thr) is False
    assert p.classify_transition(delta, thr) == p.STABLE


def test_large_drop_beyond_mdc_is_material():
    # prior 150, retest 138; 12kg drop exceeds the ~6.3 error band.
    thr = p.material_decline_threshold(
        prior_mean=150.0, prior_variance=0.0, observation_variance=0.0,
        error=p.MeasurementError(mdc=6.3),
    )
    delta = p.downward_residual(150.0, 138.0)
    assert p.is_material_decline(delta, thr) is True
    assert p.classify_transition(delta, thr) == p.DECLINE_CANDIDATE


def test_upward_observation_is_never_a_decline():
    thr = p.material_decline_threshold(
        prior_mean=150.0, prior_variance=1.0, observation_variance=1.0,
        error=p.MeasurementError(mdc=6.3),
    )
    delta = p.downward_residual(150.0, 160.0)  # negative delta (an increase)
    assert delta < 0
    assert p.is_material_decline(delta, thr) is False
    assert p.classify_transition(delta, thr) == p.STABLE


# --- the threshold is the MAX of measurement error and state uncertainty -------

def test_uncertainty_dominates_when_variance_is_large():
    # No protocol error → fallback CV; but large variances make the z_down term win.
    thr = p.material_decline_threshold(
        prior_mean=100.0, prior_variance=25.0, observation_variance=25.0,
    )
    expected_uncertainty = p.DEFAULT_Z_DOWN * math.sqrt(50.0)
    assert thr.uncertainty_component == pytest.approx(expected_uncertainty)
    assert thr.threshold == pytest.approx(expected_uncertainty)  # > fallback 4.2
    assert thr.measurement_error_source == p.ME_SOURCE_FALLBACK


def test_measurement_error_dominates_when_variance_is_small():
    thr = p.material_decline_threshold(
        prior_mean=200.0, prior_variance=0.01, observation_variance=0.01,
        error=p.MeasurementError(mdc=15.0),
    )
    assert thr.threshold == pytest.approx(15.0)
    assert thr.measurement_error_component == pytest.approx(15.0)


# --- threshold provenance / precedence (fork A) --------------------------------

def test_sem_derives_mdc95_when_mdc_absent():
    sem = 3.0
    thr = p.material_decline_threshold(
        prior_mean=150.0, prior_variance=0.0, observation_variance=0.0,
        error=p.MeasurementError(sem=sem),
    )
    assert thr.measurement_error_component == pytest.approx(1.96 * math.sqrt(2.0) * sem)
    assert thr.measurement_error_source == p.ME_SOURCE_SEM


def test_mdc_governs_and_warns_when_inconsistent_with_sem():
    # MDC 6.3 vs SEM-derived MDC95 (1.96·√2·10 ≈ 27.7): materially inconsistent.
    thr = p.material_decline_threshold(
        prior_mean=150.0, prior_variance=0.0, observation_variance=0.0,
        error=p.MeasurementError(mdc=6.3, sem=10.0),
    )
    assert thr.measurement_error_component == pytest.approx(6.3)  # MDC governs
    assert thr.measurement_error_source == p.ME_SOURCE_MDC
    assert thr.warnings and "inconsistent" in thr.warnings[0]


def test_fallback_is_flagged_provisional_not_calibrated():
    thr = p.material_decline_threshold(
        prior_mean=150.0, prior_variance=0.0, observation_variance=0.0,
    )
    assert thr.measurement_error_source == p.ME_SOURCE_FALLBACK
    assert thr.calibration_basis == p.CALIBRATION_BASIS_FALLBACK
    assert thr.measurement_error_component == pytest.approx(p.FALLBACK_MEASUREMENT_CV * 150.0)
    assert thr.policy_version == "strength_decline_policy_v1"


# --- severe unexplained drop routes distinctly ---------------------------------

def test_severe_drop_classified_for_safety_routing():
    thr = p.material_decline_threshold(
        prior_mean=150.0, prior_variance=0.0, observation_variance=0.0,
        error=p.MeasurementError(mdc=6.3),
    )
    delta = p.downward_residual(150.0, 125.0)  # 25kg ≥ 3 × 6.3 = 18.9
    assert p.classify_transition(delta, thr) == p.SEVERE_DECLINE


# --- bounded posterior never overwrites with the low value ---------------------

def test_bounded_posterior_stays_between_prior_and_observation():
    post = p.bounded_posterior(150.0, 138.0, gain=0.5)
    assert post == pytest.approx(144.0)
    assert 138.0 < post < 150.0  # strictly between — not an overwrite


def test_posterior_gain_is_bounded_and_scaled():
    assert 0.0 <= p.posterior_gain(1.0, 1.0) <= 1.0
    # corroboration/authority narrowing only shrinks the move
    strong = p.posterior_gain(1.0, 1.0, authority_scale=1.0, corroboration_scale=1.0)
    weak = p.posterior_gain(1.0, 1.0, authority_scale=0.5, corroboration_scale=0.5)
    assert weak < strong
    assert p.bounded_posterior(150.0, 100.0, p.posterior_gain(0.0, 5.0)) == pytest.approx(150.0)  # zero prior var → no move


# --- conservative ceiling is bracketed -----------------------------------------

def test_temporary_ceiling_is_between_low_value_and_prior():
    observed, prior, me = 138.0, 150.0, 6.3
    ceiling = p.temporary_ceiling(observed, me)
    assert ceiling == pytest.approx(144.3)
    assert observed < ceiling < prior  # neither the raw low nor the old max
