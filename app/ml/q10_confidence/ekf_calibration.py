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
COVERAGE_TOL = 0.10          # allowed |empirical − nominal| per interval level
CHI2_ALPHA = 0.05            # two-sided χ² band for the aggregate NIS test

# Standard-normal quantiles for the two-sided predictive intervals.
_Z = {0.50: 0.674, 0.80: 1.282, 0.95: 1.960}


@dataclass
class EkfUpdateRecord:
    """One EKF measurement update's calibration payload.

    ``nis``/``n_obs`` are always present (from the log). ``predicted_std``,
    ``predicted_mean`` and ``realized`` are per-observation and only available in replay,
    where interval coverage is computed.
    """

    nis: float
    n_obs: int
    predicted_std: float | None = None
    predicted_mean: float | None = None
    realized: float | None = None


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


@dataclass
class CalibrationReport:
    verdict: str
    reasons: list[str] = field(default_factory=list)
    nis: dict[str, Any] = field(default_factory=dict)
    coverage: dict[float, float] = field(default_factory=dict)


def calibration_report(records: list[EkfUpdateRecord]) -> CalibrationReport:
    """Combine NIS + coverage into a promote / stay_shadow verdict."""
    nis = nis_consistency(records)
    cov = interval_coverage(records)
    reasons: list[str] = []

    if nis["n_updates"] < MIN_UPDATES:
        reasons.append(f"insufficient updates ({nis['n_updates']} < {MIN_UPDATES})")
    ratio = nis.get("ratio")
    if ratio is None or not (NIS_RATIO_LO <= ratio <= NIS_RATIO_HI):
        reasons.append(f"NIS ratio out of band ({ratio})")
    for lvl, emp in cov.items():
        if not math.isnan(emp) and abs(emp - lvl) > COVERAGE_TOL:
            reasons.append(f"{int(lvl * 100)}% coverage off ({emp:.2f} vs {lvl:.2f})")

    verdict = "promote" if not reasons else "stay_shadow"
    return CalibrationReport(verdict=verdict, reasons=reasons, nis=nis, coverage=cov)
