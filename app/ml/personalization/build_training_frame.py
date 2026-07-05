"""Synthetic multi-athlete recovery population with KNOWN per-athlete β (ADR-0043).

Each athlete i has a true response ``θ_i ~ N(μ_0, τ²·I)`` over recovery signals; their
observations are ``y = Z·θ_i + N(0, σ²)`` (clearance regressed on z-scored signals). Athlete
counts ``n_i`` span a wide range (some very sparse) so the partial-pooling win is visible where
it matters most. Left DB-free so the gate runs in CI, mirroring Q2's ``synthesize_*`` pattern.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Signals mirror the recovery-β subset the engine actually z-scores (sleep, hrv, rhr).
DEFAULT_MU0 = np.array([0.10, -0.026, -0.015])  # ~ the Q2 population response magnitudes
DEFAULT_TAU2 = 0.02
DEFAULT_WITHIN_SIGMA = 0.30


@dataclass
class AthleteData:
    athlete_id: int
    Z: np.ndarray          # (n, k) z-scored signals
    y: np.ndarray          # (n,) observed clearance
    theta_true: np.ndarray  # (k,) the athlete's true response


def synthesize_population(
    *,
    seed: int,
    n_athletes: int = 250,
    mu0: np.ndarray | None = None,
    tau2: float = DEFAULT_TAU2,
    within_sigma: float = DEFAULT_WITHIN_SIGMA,
    n_range: tuple[int, int] = (4, 45),
) -> list[AthleteData]:
    """Draw a population of athletes with known per-athlete β and heterogeneous n_i."""
    rng = np.random.default_rng(seed)
    mu = np.asarray(DEFAULT_MU0 if mu0 is None else mu0, dtype=float)
    k = mu.size
    tau = float(np.sqrt(max(0.0, tau2)))
    pop: list[AthleteData] = []
    for i in range(n_athletes):
        theta = mu + rng.normal(0.0, tau, size=k)
        n = int(rng.integers(n_range[0], n_range[1] + 1))
        Z = rng.normal(0.0, 1.0, size=(n, k))
        y = Z @ theta + rng.normal(0.0, within_sigma, size=n)
        pop.append(AthleteData(athlete_id=i, Z=Z, y=y, theta_true=theta))
    return pop
