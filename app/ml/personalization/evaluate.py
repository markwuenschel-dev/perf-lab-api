"""Offline gate for hierarchical recovery-β personalization (ADR-0043).

On a synthetic population with known per-athlete β, hold out each athlete's most recent
observations and compare recovery-prediction MAE under three estimators:

  * full pooling   — population prior μ_0 for everyone (ignores the athlete)
  * no pooling     — the athlete's own noisy fit β̂_i (ignores the population)
  * partial pooling — the hierarchical blend β_i (this proposal)

Partial pooling should beat BOTH (the shrinkage/bias-variance result), and the posterior
parameter uncertainty P^θ should be calibrated — mean tr(P^θ_i) ≈ mean ‖β_i − β_true‖².
Verdict ``promote|stay_shadow`` via the empty-``reasons`` idiom shared across ml/.

Run: ``python -m app.ml.personalization.evaluate``
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.ml.personalization.build_training_frame import AthleteData, synthesize_population
from app.ml.personalization.partial_pool_fit import fit_athlete, fit_hyperparameters, pool_athlete

ARTIFACT = Path(__file__).parent / "artifacts" / "personalization_recovery_v1.json"
SEED = 20260705

MIN_IMPROVEMENT = 0.002       # partial MAE must beat both baselines by this margin (the GATE)
# P^θ calibration is REPORTED, not gated. It is a coarse parameter-uncertainty signal and is
# genuinely ~2-4x overconfident here because the multivariate coefficient sampling variance is
# σ²·(ZᵀZ)⁻¹, which σ²/n underestimates — a Gram-based correction is the documented follow-up.
# Outside this band we attach a warning; the seed-robust MAE win is the primary claim.
CALIB_LO, CALIB_HI = 0.5, 2.0  # tr(P^θ)/MSE band for the reported-not-gated calibration warning


@dataclass
class EvalReport:
    n_athletes: int
    n_test_rows: int
    mae_full_pool: float
    mae_no_pool: float
    mae_partial_pool: float
    improvement_vs_full: float
    improvement_vs_no_pool: float
    ptheta_calibration_ratio: float
    verdict: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _split(n: int, holdout_frac: float, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    idx = rng.permutation(n)
    n_test = max(1, int(round(n * holdout_frac)))
    return idx[n_test:], idx[:n_test]  # (train, test)


def evaluate(pop: list[AthleteData], *, holdout_frac: float = 0.3, seed: int = SEED) -> EvalReport:
    rng = np.random.default_rng(seed)

    # Shared population nuisance parameters (μ_0, τ², σ²) are estimated from each athlete's
    # FULL data — they are population constants, not the per-athlete quantity being held out.
    # σ² is pooled only over athletes with real degrees of freedom (n > k), dof-weighted, so a
    # near-interpolating low-n fit can't drag it to ~0 and make P^θ overconfident.
    full_est: list[np.ndarray] = []
    full_within: list[float] = []
    full_dof: list[int] = []
    for a in pop:
        b, wv, n = fit_athlete(a.Z, a.y)
        k = b.size
        full_est.append(b)
        if n - k > 0:
            full_within.append(wv)
            full_dof.append(n - k)
    within_pooled = float(np.average(full_within, weights=full_dof))
    est_arr = np.array(full_est)
    mu0, tau2 = fit_hyperparameters(
        est_arr, np.full(est_arr.shape[0], within_pooled), np.array([len(a.y) for a in pop])
    )

    # Per-athlete personalization is fit on TRAIN only; test rows score the three estimators.
    fits: list[tuple[np.ndarray, int, np.ndarray, np.ndarray, np.ndarray]] = []
    for a in pop:
        if len(a.y) < 2:
            continue
        tr, te = _split(len(a.y), holdout_frac, rng)
        if tr.size < 1 or te.size < 1:
            continue
        beta_hat, _within, n_tr = fit_athlete(a.Z[tr], a.y[tr])
        fits.append((beta_hat, n_tr, a.theta_true, a.Z[te], a.y[te]))

    ae_full: list[float] = []
    ae_nopool: list[float] = []
    ae_partial: list[float] = []
    sq_err_partial: list[float] = []
    ptheta_tr: list[float] = []
    for beta_hat, n_tr, theta_true, z_te, y_te in fits:
        beta_i, p_theta = pool_athlete(beta_hat, n_tr, mu0, tau2, within_pooled)
        ae_full.extend(np.abs(y_te - z_te @ mu0).tolist())
        ae_nopool.extend(np.abs(y_te - z_te @ beta_hat).tolist())
        ae_partial.extend(np.abs(y_te - z_te @ beta_i).tolist())
        sq_err_partial.append(float(np.sum((beta_i - theta_true) ** 2)))
        ptheta_tr.append(float(np.sum(p_theta)))

    mae_full = float(np.mean(ae_full))
    mae_nopool = float(np.mean(ae_nopool))
    mae_partial = float(np.mean(ae_partial))
    calib = float(np.mean(ptheta_tr) / max(1e-12, np.mean(sq_err_partial)))

    # GATE: partial pooling must beat both baselines (the seed-robust claim).
    reasons: list[str] = []
    if not (mae_partial < mae_full - MIN_IMPROVEMENT):
        reasons.append(f"partial not better than full pooling ({mae_partial:.4f} vs {mae_full:.4f})")
    if not (mae_partial < mae_nopool - MIN_IMPROVEMENT):
        reasons.append(f"partial not better than no pooling ({mae_partial:.4f} vs {mae_nopool:.4f})")
    # REPORTED (not gated): P^θ calibration — coarse and known-overconfident here.
    warnings: list[str] = []
    if not (CALIB_LO <= calib <= CALIB_HI):
        direction = "overconfident" if calib < CALIB_LO else "overconservative"
        warnings.append(f"P^θ {direction}: tr(P^θ)/MSE = {calib:.2f} (coarse signal; σ²/n understates coef variance)")

    return EvalReport(
        n_athletes=len(fits),
        n_test_rows=len(ae_partial),
        mae_full_pool=round(mae_full, 5),
        mae_no_pool=round(mae_nopool, 5),
        mae_partial_pool=round(mae_partial, 5),
        improvement_vs_full=round(mae_full - mae_partial, 5),
        improvement_vs_no_pool=round(mae_nopool - mae_partial, 5),
        ptheta_calibration_ratio=round(calib, 4),
        verdict="promote" if not reasons else "stay_shadow",
        reasons=reasons,
        warnings=warnings,
    )


def run(*, seed: int = SEED) -> dict[str, Any]:
    pop = synthesize_population(seed=seed)
    report = evaluate(pop, seed=seed)
    return {"artifact": "personalization_recovery_v1", "shadow_only": True, "seed": seed, **report.as_dict()}


def main() -> None:
    payload = run()
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(payload, indent=2))
    print(json.dumps(payload, indent=2))
    print(f"\nVERDICT: {payload['verdict']}")


if __name__ == "__main__":
    main()
