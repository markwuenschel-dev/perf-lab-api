from __future__ import annotations

import numpy as np

from app.logic.personalization.hierarchical import (
    estimate_hyperparameters,
    experience_prior_scale,
    partial_pool_beta,
    partial_pool_scalar,
    pooled_beta_values,
    pooled_theta_trace,
)


def test_no_data_falls_back_to_prior():
    r = partial_pool_scalar(prior_mean=0.10, data_estimate=0.50, n=0, between_var=0.01, within_var=1.0)
    assert r.value == 0.10 and r.weight == 0.0


def test_lots_of_data_approaches_estimate():
    r = partial_pool_scalar(0.10, 0.50, n=100000, between_var=0.01, within_var=1.0)
    assert r.weight > 0.99
    assert abs(r.value - 0.50) < 1e-3


def test_partial_pool_is_between_prior_and_estimate():
    r = partial_pool_scalar(0.10, 0.50, n=10, between_var=0.01, within_var=1.0)
    assert 0.10 < r.value < 0.50
    assert 0.0 < r.weight < 1.0


def test_p_theta_shrinks_monotonically_with_n():
    vs = [partial_pool_scalar(0.1, 0.5, n, between_var=0.02, within_var=1.0).p_theta for n in (1, 5, 20, 100)]
    assert all(a > b for a, b in zip(vs, vs[1:], strict=False))
    assert vs[0] <= 0.02  # never exceeds the prior variance


def test_p_theta_equals_one_minus_weight_times_between_var():
    r = partial_pool_scalar(0.1, 0.5, n=8, between_var=0.02, within_var=1.5)
    assert abs(r.p_theta - (1.0 - r.weight) * 0.02) < 1e-9


def test_method_of_moments_recovers_planted_between_variance():
    rng = np.random.default_rng(0)
    mu_true, tau2_true, within, n = 0.10, 0.02, 1.0, 40
    thetas = rng.normal(mu_true, np.sqrt(tau2_true), size=400)
    # each athlete's estimate = true theta + sampling noise (var = within/n)
    ests = [float(t + rng.normal(0.0, np.sqrt(within / n))) for t in thetas]
    mu0, tau2 = estimate_hyperparameters(ests, [within] * len(ests), [n] * len(ests))
    assert abs(mu0 - mu_true) < 0.02
    assert abs(tau2 - tau2_true) < 0.01


def test_experience_covariate_shifts_prior_direction():
    assert experience_prior_scale("beginner") < experience_prior_scale("intermediate")
    assert experience_prior_scale("elite") > experience_prior_scale("advanced")
    assert experience_prior_scale(None) == 1.0
    assert experience_prior_scale("unknown") == 1.0


def test_partial_pool_beta_table_and_traces():
    prior = {"cns": {"sleep": 0.10, "stress": 0.08}, "muscular": {"sleep": 0.08}}
    data = {"cns": {"sleep": 0.30}}  # only cns.sleep has a data estimate
    pooled = partial_pool_beta(prior, data, n=15, between_var=0.02, within_var=1.0, prior_scale=1.1)
    vals = pooled_beta_values(pooled)
    # cns.sleep pooled between scaled prior (0.11) and data (0.30)
    assert 0.11 < vals["cns"]["sleep"] < 0.30
    # cns.stress had no data → stays at scaled prior
    assert abs(vals["cns"]["stress"] - 1.1 * 0.08) < 1e-9
    assert pooled["cns"]["stress"].weight == 0.0
    assert pooled_theta_trace(pooled) > 0.0
