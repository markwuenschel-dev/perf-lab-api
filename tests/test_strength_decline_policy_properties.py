"""Property-based invariant tests for ``app.logic.strength_decline_policy`` (INT-16).

Kept as a separate family from the matrix/numerical properties in
``test_numerics_properties.py`` — these are scalar policy invariants over a
pure, versioned, import-light module, not linear algebra. Complements — does
not replace — ``test_strength_decline_policy.py``'s example-based unit tests.
"""
from __future__ import annotations

import os

from hypothesis import given, settings
from hypothesis import strategies as st

from app.logic import strength_decline_policy as p

settings.register_profile("ci", deadline=None, max_examples=200, derandomize=True)
settings.register_profile("dev", deadline=None, max_examples=50)
settings.load_profile("ci" if os.environ.get("CI") else "dev")

FINITE = st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6)
NONNEG = st.floats(allow_nan=False, allow_infinity=False, min_value=0.0, max_value=1e6)
GAIN_BELOW_ONE = st.floats(min_value=0.0, max_value=0.999999, allow_nan=False)


# --- bounded_posterior: hardens INT-02's FAIL-OVERWRITE-UPDATE ----------------

@given(prior=FINITE, observed=FINITE, k=GAIN_BELOW_ONE)
def test_bounded_posterior_stays_within_prior_and_observation(prior, observed, k):
    post = p.bounded_posterior(prior, observed, k)
    lo, hi = min(prior, observed), max(prior, observed)
    assert lo - 1e-6 <= post <= hi + 1e-6


@given(prior=FINITE, observed=FINITE, k=GAIN_BELOW_ONE)
def test_bounded_posterior_never_overwrites_with_observation(prior, observed, k):
    """With gain < 1, the posterior never equals the observation outright —
    unless the observation already equals the prior (nothing to overwrite)."""
    post = p.bounded_posterior(prior, observed, k)
    if abs(prior - observed) > 1e-9:
        assert abs(post - observed) > 1e-9


# --- posterior_gain ------------------------------------------------------------

@given(pv=FINITE, ov=FINITE)
def test_posterior_gain_is_bounded_for_arbitrary_variances(pv, ov):
    """Bounded in [0, 1] even for negative, zero, or huge variances."""
    g = p.posterior_gain(pv, ov)
    assert 0.0 <= g <= 1.0


@given(
    pv=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
    ov_small=st.floats(min_value=0.0, max_value=1e5, allow_nan=False),
    ov_delta=st.floats(min_value=1e-9, max_value=1e5, allow_nan=False),
)
def test_posterior_gain_is_monotone_decreasing_in_observation_variance(pv, ov_small, ov_delta):
    ov_large = ov_small + ov_delta
    g_small = p.posterior_gain(pv, ov_small)
    g_large = p.posterior_gain(pv, ov_large)
    assert g_large <= g_small + 1e-9


@given(
    ov=st.floats(min_value=0.0, max_value=1e6, allow_nan=False),
    pv_small=st.floats(min_value=0.0, max_value=1e5, allow_nan=False),
    pv_delta=st.floats(min_value=1e-9, max_value=1e5, allow_nan=False),
)
def test_posterior_gain_is_monotone_increasing_in_prior_variance(ov, pv_small, pv_delta):
    pv_large = pv_small + pv_delta
    g_small = p.posterior_gain(pv_small, ov)
    g_large = p.posterior_gain(pv_large, ov)
    assert g_large >= g_small - 1e-9


# --- temporary_ceiling ----------------------------------------------------------

@given(observed=FINITE, me=FINITE)
def test_temporary_ceiling_never_below_observed_value(observed, me):
    assert p.temporary_ceiling(observed, me) >= observed - 1e-9


# --- is_material_decline / classify_transition: total, mutually exclusive -----

@given(delta=FINITE, thr=NONNEG)
def test_classify_transition_trichotomy_is_total(delta, thr):
    outcome = p.classify_transition(delta, thr)
    assert outcome in (p.STABLE, p.DECLINE_CANDIDATE, p.SEVERE_DECLINE)


@given(delta=FINITE, thr=NONNEG)
def test_is_material_decline_agrees_with_classify_transition(delta, thr):
    material = p.is_material_decline(delta, thr)
    outcome = p.classify_transition(delta, thr)
    assert material == (outcome != p.STABLE)


@given(delta=FINITE, thr=NONNEG, severe_multiple=st.floats(min_value=1.0, max_value=10.0, allow_nan=False))
def test_severe_decline_is_mutually_exclusive_with_stable(delta, thr, severe_multiple):
    outcome = p.classify_transition(delta, thr, severe_multiple=severe_multiple)
    if outcome == p.SEVERE_DECLINE:
        assert p.is_material_decline(delta, thr) is True
    if outcome == p.STABLE:
        assert p.is_material_decline(delta, thr) is False


# --- bounded update cannot overshoot both prior and observation -----------------

@given(prior=FINITE, observed=FINITE, pv=NONNEG, ov=NONNEG)
def test_bounded_update_via_posterior_gain_cannot_overshoot_both_bounds(prior, observed, pv, ov):
    gain = p.posterior_gain(pv, ov)
    post = p.bounded_posterior(prior, observed, gain)
    lo, hi = min(prior, observed), max(prior, observed)
    assert lo - 1e-6 <= post <= hi + 1e-6
