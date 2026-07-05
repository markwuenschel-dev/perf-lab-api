"""Per-athlete fits + population hyperparameters for partial pooling (ADR-0043).

Fits each athlete's own response by ridge (stable at low n), estimates the population prior
``μ_0`` and between-athlete variance ``τ²`` by method of moments across athletes, then pools
each athlete's coefficients scalar-by-scalar via the shared hierarchical estimator.
"""
from __future__ import annotations

import numpy as np

from app.logic.personalization.hierarchical import (
    estimate_hyperparameters,
    partial_pool_with_sampling_var,
)

_RIDGE_LAMBDA = 1e-3


def fit_athlete(Z: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float, int, np.ndarray]:
    """Ridge fit → (coefficients (k,), within-athlete residual variance, n, Gram-inverse diag).

    The Gram-inverse diagonal ``(ZᵀZ+λI)⁻¹_jj`` gives each coefficient's sampling variance as
    ``σ²·(ZᵀZ)⁻¹_jj`` — the quantity ``σ²/n`` understates, which is what makes P^θ overconfident.
    """
    n, k = Z.shape
    gram_inv = np.linalg.inv(Z.T @ Z + _RIDGE_LAMBDA * np.eye(k))
    beta = gram_inv @ (Z.T @ y)
    resid = y - Z @ beta
    dof = max(1, n - k)
    within_var = float(resid @ resid / dof)
    return beta, within_var, n, np.diag(gram_inv).copy()


def fit_hyperparameters(
    estimates: np.ndarray,   # (m, k) per-athlete coefficient estimates
    within_vars: np.ndarray,  # (m,)
    ns: np.ndarray,           # (m,)
) -> tuple[np.ndarray, np.ndarray]:
    """Per-coefficient (μ_0 (k,), τ² (k,)) by method of moments across the population."""
    k = estimates.shape[1]
    mu0 = np.empty(k)
    tau2 = np.empty(k)
    for j in range(k):
        m, t = estimate_hyperparameters(estimates[:, j].tolist(), within_vars.tolist(), ns.tolist())
        mu0[j], tau2[j] = m, t
    return mu0, tau2


def pool_athlete(
    beta_hat: np.ndarray,      # (k,)
    within_var: float,         # shared σ²
    gram_inv_diag: np.ndarray,  # (k,) — (ZᵀZ+λI)⁻¹_jj from fit_athlete
    mu0: np.ndarray,           # (k,)
    tau2: np.ndarray,          # (k,)
    prior_scale: float = 1.0,
    n: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """Partial-pool a coefficient vector using the true sampling variance ``σ²·(ZᵀZ)⁻¹_jj``."""
    k = beta_hat.size
    value = np.empty(k)
    p_theta = np.empty(k)
    for j in range(k):
        sampling_var = within_var * float(gram_inv_diag[j])
        ps = partial_pool_with_sampling_var(
            prior_scale * float(mu0[j]), float(beta_hat[j]), sampling_var, float(tau2[j]), n=n
        )
        value[j], p_theta[j] = ps.value, ps.p_theta
    return value, p_theta
