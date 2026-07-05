"""EKF measurement update: assimilate a benchmark into the joint belief.

A benchmark's backend-normalized score ``score01`` is treated as a direct observation
of each mapped **capacity** axis (in normalized space), with per-axis measurement
variance ``R_eff / mapping_strength²``. This choice makes the single-axis EKF reduce
*exactly* to the production scalar residual anchor (``_apply_capacity_residual``):

    production gain  K = P·m / (m²P + R_eff)   ⇒   Δs_k = m·K·residual
                                              ⇒   Δs_k = P·residual / (P + R_eff/m²)

i.e. a scalar Kalman update with ``H = e_k`` and effective noise ``R_eff/m²``. The full
22x22 ``P`` then does what the production per-axis loop cannot: a benchmark that maps to
several capacity axes corrects them jointly, and correlated capacity/fatigue axes shrink
too. Fatigue/tissue mappings (legacy additive nudges in production) are not modeled by
the EKF update in v1 and are skipped.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.engine.parameters import EngineParameters
from app.logic.benchmark_validity import BenchmarkValidityProfile, effective_variance
from app.logic.wellness_signals import SIGNAL_CONFIG
from app.schemas.state import UnifiedStateVector

from .belief import EkfBelief
from .numerics import stabilize
from .params_vectors import variance_bounds
from .state_packing import INDEX_OF_KEY, N_STATE


@dataclass
class MappingSpec:
    """The minimal, ORM-detached mapping data the EKF update needs."""

    target_vector: str
    target_key: str
    coefficient: float


@dataclass
class Observation:
    """A stacked linear-Gaussian observation of one benchmark on capacity axes."""

    H: np.ndarray
    y: np.ndarray
    R: np.ndarray
    benchmark_code: str
    axis_keys: tuple[str, ...]


@dataclass
class UpdateResult:
    belief: EkfBelief
    innovation: np.ndarray
    gain_norm: float
    trace_pre: float
    trace_post: float
    nis: float  # normalized innovation squared νᵀS⁻¹ν — the EKF consistency statistic


def mapping_specs_from_orm(mappings: Sequence[Any]) -> list[MappingSpec]:
    """Snapshot ORM ``ObservationMapping`` rows into detached specs (safe across commit)."""
    return [
        MappingSpec(
            target_vector=str(m.target_vector),
            target_key=str(m.target_key),
            coefficient=float(m.coefficient),
        )
        for m in mappings
    ]


def build_observation(
    mappings: Sequence[MappingSpec],
    profile: BenchmarkValidityProfile,
    state: UnifiedStateVector,
    score01: float | None,
) -> Observation | None:
    """Assemble ``(H, y, R)`` for a benchmark. Returns None if nothing is observable.

    ``R_eff`` (fatigue/tissue-inflated measurement variance) is reused verbatim from
    ``benchmark_validity.effective_variance``; per-axis it is divided by
    ``mapping_strength²`` so a weakly-mapped axis is trusted less.
    """
    if score01 is None:
        return None
    r_eff = effective_variance(profile, state)
    rows: list[np.ndarray] = []
    ys: list[float] = []
    rs: list[float] = []
    keys: list[str] = []
    for m in mappings:
        if m.target_vector != "capacity":
            continue  # fatigue/tissue legacy nudges are not modeled by the EKF (v1)
        idx = INDEX_OF_KEY.get(("capacity", m.target_key))
        if idx is None:
            continue
        strength = profile.mapping_strength.get(m.target_key, float(m.coefficient))
        strength = max(1e-3, float(strength))
        row = np.zeros(N_STATE, dtype=float)
        row[idx] = 1.0
        rows.append(row)
        ys.append(float(score01))
        rs.append(r_eff / (strength * strength))
        keys.append(m.target_key)
    if not rows:
        return None
    return Observation(
        H=np.vstack(rows),
        y=np.array(ys, dtype=float),
        R=np.diag(rs),
        benchmark_code=profile.benchmark_code,
        axis_keys=tuple(keys),
    )


def _fatigue_row(axis: str, y_val: float, r: float) -> tuple[np.ndarray, float, float, str] | None:
    idx = INDEX_OF_KEY.get(("fatigue", axis))
    if idx is None:
        return None
    row = np.zeros(N_STATE, dtype=float)
    row[idx] = 1.0
    return row, y_val, r, axis


def _autonomic_fatigue(wellness: object, scale: float) -> float | None:
    """Observed CNS fatigue in [0,1] from HRV + resting HR, or None if unavailable.

    Low HRV / high resting HR (below-baseline autonomic readiness) ⇒ high CNS fatigue. Uses the
    shared per-signal z-score convention; ``z=+scale`` (well recovered) ⇒ 0, ``z=−scale`` ⇒ 1.
    """
    zs: list[float] = []
    for field in ("hrv_ms", "resting_hr"):
        val = getattr(wellness, field, None)
        if val is None:
            continue
        direction, base, norm = SIGNAL_CONFIG[field]
        z = max(-scale, min(scale, direction * (float(val) - base) / norm))
        zs.append(z)
    if not zs:
        return None
    z_readiness = sum(zs) / len(zs)
    return max(0.0, min(1.0, 0.5 - 0.5 * z_readiness / max(1e-6, scale)))


def build_wellness_observation(wellness: object, params: EngineParameters) -> Observation | None:
    """Wellness signals as noisy observations of fatigue axes (ADR-0041 extension).

    - Soreness (0–10) → ``muscular`` and ``structural`` fatigue (``soreness/10``).
    - HRV + resting HR → ``cns`` (autonomic) fatigue.

    This lets the EKF's fatigue block actually be *observed* (its variance shrinks, correlated
    axes corrected via P) rather than only inflated by predict. Returns None with no usable signal.
    """
    built: list[tuple[np.ndarray, float, float, str]] = []

    soreness = getattr(wellness, "soreness", None)
    if soreness is not None:
        y_val = max(0.0, min(1.0, float(soreness) / 10.0))
        for axis in ("muscular", "structural"):
            b = _fatigue_row(axis, y_val, float(params.ekf_soreness_variance))
            if b is not None:
                built.append(b)

    cns_fat = _autonomic_fatigue(wellness, params.recovery_zscore_scale)
    if cns_fat is not None:
        b = _fatigue_row("cns", cns_fat, float(params.ekf_autonomic_variance))
        if b is not None:
            built.append(b)

    if not built:
        return None
    return Observation(
        H=np.vstack([b[0] for b in built]),
        y=np.array([b[1] for b in built], dtype=float),
        R=np.diag([b[2] for b in built]),
        benchmark_code="wellness",
        axis_keys=tuple(b[3] for b in built),
    )


def update(belief: EkfBelief, obs: Observation, params: EngineParameters) -> UpdateResult:
    """Joseph-form EKF measurement update. PSD-stable across many updates."""
    H, y, R = obs.H, obs.y, obs.R
    P = belief.cov
    trace_pre = float(np.trace(P))

    innovation = y - H @ belief.mean
    S = H @ P @ H.T + R
    # K = P Hᵀ S⁻¹, computed via a solve (S symmetric) — avoids an explicit inverse.
    K = np.linalg.solve(S, H @ P).T
    # Normalized innovation squared: for a well-calibrated filter E[NIS] = dim(y).
    nis = float(innovation @ np.linalg.solve(S, innovation))

    mean_new = np.clip(belief.mean + K @ innovation, 0.0, 1.0)

    ident = np.eye(N_STATE)
    IKH = ident - K @ H
    P_new = IKH @ P @ IKH.T + K @ R @ K.T  # Joseph form

    lo, hi = variance_bounds(params)
    P_new = stabilize(P_new, lo, hi)

    new_belief = EkfBelief(
        mean=mean_new,
        cov=P_new,
        timestamp=belief.timestamp,
        model_version=belief.model_version,
    )
    return UpdateResult(
        belief=new_belief,
        innovation=innovation,
        gain_norm=float(np.linalg.norm(K)),
        trace_pre=trace_pre,
        trace_post=float(np.trace(P_new)),
        nis=nis,
    )
