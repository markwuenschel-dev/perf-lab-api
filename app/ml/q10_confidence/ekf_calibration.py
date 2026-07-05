"""EKF track of Q10 — is the shadow full-covariance filter *calibrated*? (ADR-0041)

The scalar Q10 gate asks whether per-axis process noise makes predicted variance track
observed residuals. This EKF track asks the joint-covariance analogue with two standard
filter-consistency checks:

- **NIS** (normalized innovation squared, ``νᵀS⁻¹ν``): for a calibrated filter
  ``E[NIS] = dim(y)``. Aggregated over updates, the total NIS is χ²-distributed with dof
  = Σ n_obs; we check it falls inside a two-sided χ² band. A ratio ≫ 1 means the filter
  is over-confident (P/R too small); ≪ 1 means under-confident.
- **Interval coverage**: the fraction of realized benchmark scores falling within the
  50/80/95% predictive intervals should match the nominal levels.

Verdict follows the existing model-card contract (``promote`` | ``stay_shadow``). Promotion
is *out of scope* for this arc — the estimator stays shadow regardless — but the verdict is
the honest gate that a future promotion would require.

NIS consistency runs on production data (``ekf_shadow_log`` stores ``nis``/``n_obs`` per
update); interval coverage needs the full predictive std and is produced by the replay
harness, which holds the in-memory belief.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from scipy.stats import chi2

# --- Verdict thresholds — conservative; a mis-calibrated filter must not promote. ---
MIN_UPDATES = 40
NIS_RATIO_LO = 0.6            # aggregate NIS/dof band (over/under-confidence guard)
NIS_RATIO_HI = 1.6
# Coverage tolerance is ASYMMETRIC: under-coverage (empirical < nominal) means an
# OVER-confident filter — the dangerous failure — and is penalized tightly. Mild
# over-coverage (a conservative filter) is the safe direction and tolerated more.
COVERAGE_UNDER_TOL = 0.10    # empirical below nominal by more than this → fail
COVERAGE_OVER_TOL = 0.15     # empirical above nominal by more than this → fail (extreme)
CHI2_ALPHA = 0.05            # two-sided χ² band for the aggregate NIS test

# Standard-normal quantiles for the two-sided predictive intervals.
_Z = {0.50: 0.674, 0.80: 1.282, 0.95: 1.960}


@dataclass
class EkfUpdateRecord:
    """One EKF measurement update's calibration payload.

    ``nis``/``n_obs`` are always present (from the log). ``predicted_std``,
    ``predicted_mean`` and ``realized`` are per-observation and only available in replay,
    where interval coverage is computed. ``scalar_pred`` (production scalar path's
    one-step prediction) and ``true_score`` (ground-truth latent) are replay-only and
    drive the EKF-vs-scalar head-to-head.
    """

    nis: float
    n_obs: int
    predicted_std: float | None = None
    predicted_mean: float | None = None
    realized: float | None = None
    scalar_pred: float | None = None
    true_score: float | None = None
    axis: str | None = None


def nis_consistency(records: list[EkfUpdateRecord]) -> dict[str, Any]:
    """Aggregate NIS χ² consistency across updates."""
    usable = [r for r in records if r.nis is not None and r.n_obs]
    total_nis = float(sum(r.nis for r in usable))
    dof = int(sum(r.n_obs for r in usable))
    if dof == 0:
        return {"n_updates": 0, "dof": 0, "total_nis": 0.0, "ratio": None, "within_chi2": False}
    lo = float(chi2.ppf(CHI2_ALPHA / 2.0, dof))
    hi = float(chi2.ppf(1.0 - CHI2_ALPHA / 2.0, dof))
    return {
        "n_updates": len(usable),
        "dof": dof,
        "total_nis": total_nis,
        "ratio": total_nis / dof,
        "chi2_lo": lo,
        "chi2_hi": hi,
        "within_chi2": lo <= total_nis <= hi,
        # Split the two-sided test by direction: overconfident (P/R too small) is the
        # dangerous failure the gate must catch; underconfident (conservative) is safe and
        # only trips the very-powerful χ² test at high dof.
        "overconfident": total_nis > hi,
        "underconfident": total_nis < lo,
    }


def interval_coverage(
    records: list[EkfUpdateRecord],
    levels: tuple[float, ...] = (0.50, 0.80, 0.95),
) -> dict[float, float]:
    """Empirical coverage: fraction of realized scores within each predictive interval."""
    usable = [
        r for r in records
        if r.predicted_std is not None and r.predicted_mean is not None and r.realized is not None
    ]
    out: dict[float, float] = {}
    if not usable:
        return {lvl: float("nan") for lvl in levels}
    for lvl in levels:
        z = _Z.get(lvl, 1.960)
        hits = sum(
            1
            for r in usable
            if abs(r.realized - r.predicted_mean) <= z * max(1e-9, r.predicted_std)  # type: ignore[operator]
        )
        out[lvl] = hits / len(usable)
    return out


def prediction_comparison(records: list[EkfUpdateRecord]) -> dict[str, Any]:
    """One-step benchmark-prediction RMSE: EKF belief mean vs production scalar path.

    Compares each estimator's pre-update prediction against the ground-truth latent
    ``true_score`` (replay-only). ``improvement`` > 0 means the joint-covariance EKF
    tracks benchmarks more accurately than the per-axis scalar path.
    """
    usable = [
        r for r in records
        if r.true_score is not None and r.predicted_mean is not None and r.scalar_pred is not None
    ]
    if not usable:
        return {"n": 0, "ekf_rmse": None, "scalar_rmse": None, "improvement": None}
    ekf_sq = [(r.predicted_mean - r.true_score) ** 2 for r in usable]  # type: ignore[operator]
    sca_sq = [(r.scalar_pred - r.true_score) ** 2 for r in usable]  # type: ignore[operator]
    ekf_rmse = math.sqrt(sum(ekf_sq) / len(usable))
    scalar_rmse = math.sqrt(sum(sca_sq) / len(usable))
    return {
        "n": len(usable),
        "ekf_rmse": ekf_rmse,
        "scalar_rmse": scalar_rmse,
        "improvement": scalar_rmse - ekf_rmse,
    }


@dataclass
class CalibrationReport:
    verdict: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    nis: dict[str, Any] = field(default_factory=dict)
    coverage: dict[float, float] = field(default_factory=dict)
    prediction: dict[str, Any] = field(default_factory=dict)


def calibration_report(records: list[EkfUpdateRecord]) -> CalibrationReport:
    """Combine NIS + coverage into a promote / stay_shadow verdict.

    NIS χ² consistency and interval coverage are the hard gate (both backed by the
    generative model). The EKF-vs-scalar prediction margin is *reported* and, if the EKF
    is worse, surfaced as a soft warning — it is scenario-dependent and does not gate.
    """
    nis = nis_consistency(records)
    cov = interval_coverage(records)
    pred = prediction_comparison(records)
    reasons: list[str] = []
    warnings: list[str] = []

    if nis["n_updates"] < MIN_UPDATES:
        reasons.append(f"insufficient updates ({nis['n_updates']} < {MIN_UPDATES})")
    ratio = nis.get("ratio")
    if ratio is None or not (NIS_RATIO_LO <= ratio <= NIS_RATIO_HI):
        reasons.append(f"NIS ratio out of band ({ratio})")
    elif nis.get("overconfident"):
        # χ² upper-tail breach with an in-band ratio ⇒ subtle overconfidence at high dof.
        reasons.append(f"NIS overconfident (total {nis['total_nis']:.1f} > χ²_hi {nis['chi2_hi']:.1f})")
    for lvl, emp in cov.items():
        if math.isnan(emp):
            continue
        if lvl - emp > COVERAGE_UNDER_TOL:
            reasons.append(f"{int(lvl * 100)}% under-covered ({emp:.2f} < {lvl:.2f}) — overconfident")
        elif emp - lvl > COVERAGE_OVER_TOL:
            reasons.append(f"{int(lvl * 100)}% over-covered ({emp:.2f} > {lvl:.2f}) — overconservative")

    imp = pred.get("improvement")
    if imp is not None and imp < 0:
        warnings.append(f"EKF prediction RMSE worse than scalar by {-imp:.4f}")

    verdict = "promote" if not reasons else "stay_shadow"
    return CalibrationReport(
        verdict=verdict, reasons=reasons, warnings=warnings, nis=nis, coverage=cov, prediction=pred
    )
