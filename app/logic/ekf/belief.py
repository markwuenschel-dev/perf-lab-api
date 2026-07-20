"""``EkfBelief`` — the shadow estimator's belief state ``b = (mean, cov)``.

``mean`` is the normalized 22-dim state; ``cov`` is the 22x22 joint covariance ``P``.
Seeded block-diagonal from a production ``UnifiedStateVector`` (capacity variances taken
from the live ``capacity_confidence``), it becomes dense as the predict step propagates
cross-axis correlations. Serializes to/from plain JSON for the ``ekf_shadow_log`` table.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from app.engine.parameters import EngineParameters
from app.schemas.state import UnifiedStateVector

from .numerics import nearest_psd
from .params_vectors import seed_variance_vector
from .state_packing import N_STATE, STATE_KEYS, pack

EKF_MODEL_VERSION = "ekf-v1"


@dataclass
class EkfBelief:
    """Belief state of the shadow EKF: normalized mean (22) + joint covariance (22x22)."""

    mean: np.ndarray
    cov: np.ndarray
    timestamp: datetime
    model_version: str = EKF_MODEL_VERSION

    @classmethod
    def seed_from_unified(
        cls,
        state: UnifiedStateVector,
        params: EngineParameters,
        model_version: str = EKF_MODEL_VERSION,
    ) -> EkfBelief:
        """Seed a belief from a production state: mean = normalized X/F/T, P block-diagonal."""
        mean = pack(state)
        cap_var = {k: float(getattr(state.capacity_confidence, k)) for k in state.capacity_confidence.KEYS}
        diag = seed_variance_vector(params, cap_var)
        cov = np.diag(diag)
        return cls(mean=mean, cov=nearest_psd(cov), timestamp=state.timestamp, model_version=model_version)

    def variances(self) -> np.ndarray:
        """Per-axis marginal variances (the diagonal of P)."""
        return np.diag(self.cov)

    def trace(self) -> float:
        """Total uncertainty tr(P) — a scalar summary of belief spread."""
        return float(np.trace(self.cov))

    # --- serialization for ekf_shadow_log (plain JSON-able structures) ---

    def mean_map(self) -> dict[str, float]:
        """Normalized mean as a ``{"domain.key": value}`` map."""
        return {f"{d}.{k}": float(self.mean[i]) for i, (d, k) in enumerate(STATE_KEYS)}

    def variance_map(self) -> dict[str, float]:
        """Per-axis variance as a ``{"domain.key": variance}`` map."""
        v = self.variances()
        return {f"{d}.{k}": float(v[i]) for i, (d, k) in enumerate(STATE_KEYS)}

    def cov_list(self) -> list[list[float]]:
        """Full covariance as a nested list (22x22) for JSONB storage."""
        return self.cov.tolist()

    @classmethod
    def from_row(
        cls,
        *,
        mean_map: dict[str, float],
        cov_list: list[list[float]],
        timestamp: datetime,
        model_version: str,
    ) -> EkfBelief:
        """Rehydrate a belief from a persisted ``ekf_shadow_log`` row."""
        mean = np.array([float(mean_map[f"{d}.{k}"]) for d, k in STATE_KEYS], dtype=float)
        cov = np.array(cov_list, dtype=float)
        if cov.shape != (N_STATE, N_STATE):
            raise ValueError(f"covariance must be {N_STATE}x{N_STATE}, got {cov.shape}")
        return cls(mean=mean, cov=cov, timestamp=timestamp, model_version=model_version)
