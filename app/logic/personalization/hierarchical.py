"""Closed-form empirical-Bayes partial pooling for a scalar parameter (ADR-0043).

Gaussian hierarchical model: athlete parameters ``θ_i ~ N(μ_0, τ²)`` (between-athlete spread
``τ²``), observed through a data estimate ``β̂_i`` with sampling variance ``σ²/n_i`` (within-
athlete noise ``σ²`` over ``n_i`` observations). The posterior mean is the precision-weighted
blend

    β_i = (1 − w_i)·μ_i + w_i·β̂_i,   w_i = τ² / (τ² + σ²/n_i)

and the posterior variance ``P^θ_i = (1 − w_i)·τ²`` shrinks with data. ``n_i → 0`` ⇒ β_i = the
population prior (full pooling); ``n_i → ∞`` ⇒ β_i = the athlete's own estimate (no pooling).
``τ²``/``μ_0`` are estimated across the population by method of moments.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Light covariate prior (hand-set, not fitted): more-trained athletes tend to clear fatigue a
# little faster, so their prior recovery-β magnitude is scaled up modestly. Applied to μ_0
# before pooling; the athlete's own data still dominates once n_i is large.
_EXPERIENCE_RECOVERY_SCALE: dict[str, float] = {
    "beginner": 0.90,
    "intermediate": 1.00,
    "advanced": 1.10,
    "elite": 1.15,
}


@dataclass
class PooledScalar:
    """Partial-pooling posterior for one scalar parameter."""

    value: float      # β_i — posterior mean
    p_theta: float    # P^θ_i — posterior variance (parameter uncertainty)
    weight: float     # w_i — shrinkage toward the data estimate, in [0, 1]
    n: int            # observations backing the data estimate


def experience_prior_scale(experience_level: str | None) -> float:
    """Covariate prior multiplier on μ_0 from experience level (defaults to 1.0)."""
    if not experience_level:
        return 1.0
    return _EXPERIENCE_RECOVERY_SCALE.get(experience_level.strip().lower(), 1.0)


def partial_pool_with_sampling_var(
    prior_mean: float,
    data_estimate: float,
    sampling_var: float,
    between_var: float,
    *,
    n: int = 0,
) -> PooledScalar:
    """Partial-pool one scalar given the data estimate's actual sampling variance.

    ``sampling_var`` is Var(β̂) — for a regression coefficient this is ``σ²·(ZᵀZ)⁻¹_jj``, NOT the
    ``σ²/n`` approximation (which understates it and makes ``P^θ`` overconfident). Use this form
    whenever the design matrix is available.
    """
    if between_var <= 0.0:
        return PooledScalar(value=prior_mean, p_theta=max(0.0, between_var), weight=0.0, n=n)
    if sampling_var <= 0.0:
        return PooledScalar(value=data_estimate, p_theta=0.0, weight=1.0, n=n)
    weight = between_var / (between_var + sampling_var)  # = τ² / (τ² + Var(β̂))
    value = (1.0 - weight) * prior_mean + weight * data_estimate
    return PooledScalar(value=value, p_theta=(1.0 - weight) * between_var, weight=weight, n=n)


def partial_pool_scalar(
    prior_mean: float,
    data_estimate: float,
    n: int,
    between_var: float,
    within_var: float,
) -> PooledScalar:
    """Partial-pool one scalar with the ``σ²/n`` sampling-variance approximation.

    Convenience wrapper over ``partial_pool_with_sampling_var`` for the case where only a count
    and a residual variance are available (no design matrix). ``n ≤ 0`` ⇒ the population prior.
    """
    if n <= 0:
        return PooledScalar(value=prior_mean, p_theta=max(0.0, between_var), weight=0.0, n=max(0, n))
    return partial_pool_with_sampling_var(
        prior_mean, data_estimate, max(1e-12, within_var) / n, between_var, n=n
    )


def estimate_hyperparameters(
    data_estimates: Sequence[float],
    within_vars: Sequence[float],
    ns: Sequence[int],
) -> tuple[float, float]:
    """Method-of-moments (μ_0, τ²) across a population of per-athlete estimates.

    ``τ² = Var(β̂_i) − mean(σ²_i / n_i)`` — the observed spread of estimates minus the part
    explained by sampling noise, floored at 0. ``μ_0`` is the population mean of the estimates.
    """
    est = [float(e) for e in data_estimates]
    m = len(est)
    if m == 0:
        return 0.0, 0.0
    mu0 = sum(est) / m
    if m == 1:
        return mu0, 0.0
    total_var = sum((e - mu0) ** 2 for e in est) / (m - 1)
    sampling_var = sum(
        float(wv) / max(1, int(nn)) for wv, nn in zip(within_vars, ns, strict=True)
    ) / m
    return mu0, max(0.0, total_var - sampling_var)


def partial_pool_beta(
    prior_beta: dict[str, dict[str, float]],
    data_beta: dict[str, dict[str, float]],
    n: int,
    *,
    between_var: float,
    within_var: float,
    prior_scale: float = 1.0,
) -> dict[str, dict[str, PooledScalar]]:
    """Partial-pool a full recovery-β table (axis → signal → weight) scalar-by-scalar.

    Every (axis, signal) present in ``prior_beta`` is pooled from its (covariate-scaled) prior
    toward the athlete's ``data_beta`` estimate under the shared ``n``/variances. Signals absent
    from ``data_beta`` keep the prior (weight 0).
    """
    out: dict[str, dict[str, PooledScalar]] = {}
    for axis, signals in prior_beta.items():
        out[axis] = {}
        for signal, prior_w in signals.items():
            mu = prior_scale * float(prior_w)
            data_w = data_beta.get(axis, {}).get(signal)
            if data_w is None:
                out[axis][signal] = PooledScalar(value=mu, p_theta=max(0.0, between_var), weight=0.0, n=0)
            else:
                out[axis][signal] = partial_pool_scalar(mu, float(data_w), n, between_var, within_var)
    return out


def pooled_beta_values(pooled: dict[str, dict[str, PooledScalar]]) -> dict[str, dict[str, float]]:
    """Extract the posterior-mean β table from a pooled result (for the clearance math)."""
    return {axis: {sig: ps.value for sig, ps in signals.items()} for axis, signals in pooled.items()}


def pooled_theta_trace(pooled: dict[str, dict[str, PooledScalar]]) -> float:
    """Total parameter uncertainty tr(P^θ) — sum of per-scalar posterior variances."""
    return float(sum(ps.p_theta for signals in pooled.values() for ps in signals.values()))
