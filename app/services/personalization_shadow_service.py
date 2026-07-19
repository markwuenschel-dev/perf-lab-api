"""Per-athlete recovery-β personalization shadow service (ADR-0043) — capture-only.

On a wellness ingest, estimate the athlete's own recovery response from a bounded window of
their wellness + fatigue history, **partial-pool** it toward the Q2 population prior, and log
how the resulting personalized fatigue-clearance multipliers would differ from the population
ones — with the shrinkage ``w_i``, observation count ``n_i``, and parameter uncertainty
``tr(P^θ)``. Applies NOTHING to production (``decision_impact="none_shadow_only"``); best-effort.

A new/sparse athlete has ``n_i`` below the activation floor, so ``w_i=0`` and personalized ≡
population — exactly what partial pooling prescribes. Personalization sharpens as their own
data accumulates.
"""
from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.vectors import FatigueState
from app.engine.parameter_overrides import apply_parameter_overrides, load_namespace_override
from app.engine.parameters import default_parameters
from app.logic.personalization.hierarchical import (
    experience_prior_scale,
    partial_pool_with_sampling_var,
)
from app.logic.recovery_telemetry import multipliers_by_axis, wellness_snapshot
from app.logic.wellness_signals import SIGNAL_CONFIG
from app.ml.personalization.partial_pool_fit import fit_athlete
from app.models.athlete_state import AthleteState
from app.models.personalization_shadow import PersonalizationShadowLog
from app.models.user import AthleteProfile
from app.models.wellness import WellnessSample
from app.repositories.athlete_context_repository import AthleteContextRepository
from app.services.telemetry_common import best_effort_write

_NAMESPACE = "q2_recovery"
_PERSONALIZED_SIGNALS = ("hrv", "rhr")  # signals we personalize (sleep/stress kept as prior)
_SIGNAL_FIELD = {"sleep": "sleep_hours", "hrv": "hrv_ms", "rhr": "resting_hr"}
_FIT_SIGNALS = ("sleep", "hrv", "rhr")

# Population priors for the hierarchical shrinkage (from the offline personalization eval).
_POP_TAU2 = 0.02
_POP_WITHIN = 0.09
_MIN_OBS = 8              # below this, personalization stays at the population prior
_WINDOW_DAYS = 180
_SCALE_CLAMP = (0.5, 2.0)


def _zscore(field: str, value: float, clip: float) -> float:
    direction, base, norm = SIGNAL_CONFIG[field]
    z = direction * (value - base) / norm
    return max(-clip, min(clip, z))


def _mean_fatigue(row: AthleteState) -> float:
    parts = (row.f_met_systemic, row.f_nm_peripheral, row.f_nm_central, row.f_struct_damage)
    return sum(float(p) for p in parts) / len(parts)


async def _build_recovery_frame(
    db: AsyncSession, user_id: int, clip: float
) -> tuple[np.ndarray, np.ndarray]:
    """Paired (z-signals, next-day mean-fatigue clearance) rows from the athlete's history.

    Returns ``(Z (n, 3), y (n,))`` — z-scored [sleep, hrv, rhr] on day d against the clearance
    ``meanF(d) − meanF(d+1)``. Rows with a missing signal or no consecutive-day fatigue pair are
    dropped; an athlete without enough aligned data yields an empty frame.
    """
    w_rows = (await db.execute(
        select(WellnessSample).where(WellnessSample.user_id == user_id).order_by(WellnessSample.date)
    )).scalars().all()
    s_rows = await AthleteContextRepository(db).list_states_ascending(user_id)
    if not w_rows or not s_rows:
        return np.empty((0, 3)), np.empty((0,))

    # Latest mean-fatigue per calendar day.
    fat_by_day: dict[Any, float] = {}
    for r in s_rows:
        fat_by_day[r.timestamp.date()] = _mean_fatigue(r)
    latest = max(w_rows, key=lambda r: r.date).date
    cutoff = latest - timedelta(days=_WINDOW_DAYS)

    z_list: list[list[float]] = []
    y_list: list[float] = []
    for r in w_rows:
        d = r.date
        if d < cutoff:
            continue
        nxt = d + timedelta(days=1)
        if d not in fat_by_day or nxt not in fat_by_day:
            continue
        raw = [getattr(r, _SIGNAL_FIELD[s], None) for s in _FIT_SIGNALS]
        vals = [v for v in raw if v is not None]
        if len(vals) != len(_FIT_SIGNALS):
            continue
        z_list.append([_zscore(_SIGNAL_FIELD[s], float(v), clip) for s, v in zip(_FIT_SIGNALS, vals, strict=True)])
        y_list.append(fat_by_day[d] - fat_by_day[nxt])
    return np.array(z_list, dtype=float), np.array(y_list, dtype=float)


async def record_personalization_shadow(db: AsyncSession, user_id: int, wellness: object) -> None:
    """Write one per-athlete personalization shadow row. Never raises to the caller."""
    async with best_effort_write(db, f"personalization shadow log for user {user_id}"):
        params = default_parameters()
        artifact = load_namespace_override(_NAMESPACE)
        if artifact is None:
            return  # no population recovery prior → nothing to personalize against
        population = apply_parameter_overrides(params, artifact, allow_shadow=True)
        model_version = str(artifact["version"])
        mu0_resp = dict(artifact.get("training", {}).get("learned_response", {}))

        profile = (await db.execute(
            select(AthleteProfile).where(AthleteProfile.user_id == user_id)
        )).scalars().first()
        scale = experience_prior_scale(profile.experience_level if profile else None)

        clip = params.recovery_zscore_scale
        Z, y = await _build_recovery_frame(db, user_id, clip)
        n = int(y.shape[0])

        personalized = copy.deepcopy(population)
        w_values: list[float] = []
        theta_trace = 0.0
        if n >= _MIN_OBS:
            beta_hat, _within, _n_fit, gram_inv_diag = fit_athlete(Z, y)
            idx_of = {sig: i for i, sig in enumerate(_FIT_SIGNALS)}
            for sig in _PERSONALIZED_SIGNALS:
                i = idx_of[sig]
                mu = scale * float(mu0_resp.get(f"z_{sig}", 0.0))
                # Gram-based sampling variance σ²·(ZᵀZ)⁻¹_jj (calibrated P^θ, ADR-0043 follow-up).
                sampling_var = _POP_WITHIN * float(gram_inv_diag[i])
                ps = partial_pool_with_sampling_var(mu, float(beta_hat[i]), sampling_var, _POP_TAU2, n=n)
                w_values.append(ps.weight)
                theta_trace += ps.p_theta * len(FatigueState.KEYS)
                if abs(mu) > 1e-6:
                    ratio = max(_SCALE_CLAMP[0], min(_SCALE_CLAMP[1], ps.value / mu))
                    for axis in FatigueState.KEYS:
                        axis_beta = personalized.recovery_clearance_beta.get(axis)
                        if axis_beta and sig in axis_beta:
                            axis_beta[sig] = axis_beta[sig] * ratio

        w_mean = float(np.mean(w_values)) if w_values else 0.0
        db.add(
            PersonalizationShadowLog(
                user_id=user_id,
                parameter="recovery_beta",
                model_version=model_version,
                n_obs=n,
                shrinkage_weight=round(w_mean, 4),
                theta_trace=round(theta_trace, 6),
                wellness=wellness_snapshot(wellness),
                population_multiplier=multipliers_by_axis(population, wellness),
                personalized_multiplier=multipliers_by_axis(personalized, wellness),
                decision_impact="none_shadow_only",
            )
        )
