"""Offline validation gate for the Q9 interference suppression alphas.

Per interference pair: fit the suppression alpha on held-in athletes, then on WHOLE
held-out athletes ask whether the LEARNED alpha predicts adaptation efficiency better than
the engine DEFAULT alpha (MAE improvement), under the ADR-0037 guardrails that gate
promotion OUT of shadow:

* a minimum MAE improvement of predicted gain_efficiency (learned vs default),
* a real suppression SIGNAL is present (test corr(z, efficiency) is meaningfully negative),
* the ADR-0037 INTERFERENCE-FLOOR GUARDRAIL: the learned curve must keep strong suppression
  at high concurrent load — efficiency at z = Z_REF (1.0) must stay <= MAX_EFFICIENCY_AT_REF
  (0.80). A learned alpha too small implies interference barely bites, which would
  implausibly weaken the reviewed interference floor; promotion is refused (echoes the C4
  finding that a naive unified exponential fit drifts to ~0.87 > 0.80 and guts the guardrail),
* learned alpha within a plausible band,
* the sparse-athlete subgroup is no worse.

On the no-signal null (effect=0) the fit collapses to alpha ~= 0 (a flat curve), which fails
the floor guardrail and the suppression-signal check — the honest ``stay_shadow``. Fit is
leakage-clean: alpha is fit on held-in athletes and scored on WHOLE held-out athletes.
Run ``python -m app.ml.q9_interference.evaluate`` for the current verdict.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from app.engine.parameters import default_parameters
from app.ml.q9_interference.build_training_frame import (
    GROUP_COLUMN,
    LABEL_COLUMN,
    PAIR_COLUMN,
    build_frame,
    grouped_time_split,
    synthetic_interference_rows,
)
from app.ml.q9_interference.train import (
    ALPHA_MAX,
    PAIR_TO_PARAM,
    fit_alpha,
    pair_defaults,
    suppression_efficiency,
)

# Promotion thresholds — deliberately conservative; a learned alpha must clearly help AND
# preserve the ADR-0037 interference-floor guardrail.
MIN_MAE_IMPROVEMENT = 0.005      # MAE_default - MAE_learned, in efficiency units
MIN_SUPPRESSION_CORR = 0.10      # test corr(z, efficiency) must be <= -this (real suppression)
Z_REF = 1.0                      # reference high concurrent-load fraction for the guardrail
MAX_EFFICIENCY_AT_REF = 0.80     # ADR-0037: efficiency at full concurrent load must stay strong
ALPHA_MIN_PLAUSIBLE = 0.30       # below this, interference barely bites -> implausible
SPARSE_TOLERANCE = 0.01          # sparse subgroup MAE may be at most this much worse
SPARSE_OBS_THRESHOLD = 8         # athletes with < this many test rows are "sparse"


@dataclass
class PairEval:
    pair: str
    engine_param: str
    n_test_rows: int
    n_test_athletes: int
    floor: float
    default_alpha: float
    learned_alpha: float
    mae_default: float
    mae_learned: float
    mae_improvement: float          # default - learned (positive = learned helps)
    suppression_corr: float         # test corr(z, efficiency); negative = real suppression
    efficiency_at_ref: float        # learned efficiency at z=Z_REF (guardrail target)
    sparse_mae_improvement: float
    verdict: str                    # "promote" | "stay_shadow"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_corr(z: np.ndarray, eff: np.ndarray) -> float:
    if len(z) < 3 or np.std(z) < 1e-9 or np.std(eff) < 1e-9:
        return 0.0
    return float(np.corrcoef(z, eff)[0, 1])


def evaluate_pair(
    frame: pd.DataFrame, pair: str, *, params: Any | None = None, holdout_frac: float = 0.25
) -> PairEval:
    """Fit alpha on held-in athletes, score held-out athletes, and return the pair gate."""
    params = params or default_parameters()
    field, default_alpha, floor = pair_defaults(pair, params=params)
    sub = frame[frame[PAIR_COLUMN] == pair]
    train_df, test_df = grouped_time_split(sub, holdout_frac=holdout_frac)

    z_tr = train_df["z_interfering_load"].to_numpy(dtype=float)
    eff_tr = train_df[LABEL_COLUMN].to_numpy(dtype=float)
    learned = fit_alpha(z_tr, eff_tr, floor=floor, default_alpha=default_alpha)

    z_te = test_df["z_interfering_load"].to_numpy(dtype=float)
    eff_te = test_df[LABEL_COLUMN].to_numpy(dtype=float)
    pred_learned = suppression_efficiency(z_te, learned, floor)
    pred_default = suppression_efficiency(z_te, default_alpha, floor)
    mae_learned = float(np.mean(np.abs(eff_te - pred_learned))) if len(eff_te) else float("nan")
    mae_default = float(np.mean(np.abs(eff_te - pred_default))) if len(eff_te) else float("nan")
    improvement = mae_default - mae_learned

    corr = _safe_corr(z_te, eff_te)
    efficiency_at_ref = float(suppression_efficiency(np.array([Z_REF]), learned, floor)[0])

    counts = test_df.groupby(GROUP_COLUMN).size()
    sparse_ids = set(counts[counts < SPARSE_OBS_THRESHOLD].index.tolist())
    sp = test_df[GROUP_COLUMN].isin(sparse_ids).to_numpy()
    if sp.any():
        sparse_mae_improvement = float(
            np.mean(np.abs(eff_te[sp] - pred_default[sp]))
            - np.mean(np.abs(eff_te[sp] - pred_learned[sp]))
        )
    else:
        sparse_mae_improvement = improvement

    reasons: list[str] = []
    if not (improvement >= MIN_MAE_IMPROVEMENT):
        reasons.append(f"mae_improvement {improvement:.4f} < {MIN_MAE_IMPROVEMENT}")
    if not (corr <= -MIN_SUPPRESSION_CORR):
        reasons.append(f"no suppression signal (corr {corr:.3f} > -{MIN_SUPPRESSION_CORR})")
    if efficiency_at_ref > MAX_EFFICIENCY_AT_REF:
        reasons.append(
            f"ADR-0037 floor guardrail: efficiency@z={Z_REF} {efficiency_at_ref:.3f} "
            f"> {MAX_EFFICIENCY_AT_REF} (alpha too weak)"
        )
    if not (ALPHA_MIN_PLAUSIBLE <= learned <= ALPHA_MAX):
        reasons.append(f"learned alpha {learned:.3f} outside [{ALPHA_MIN_PLAUSIBLE}, {ALPHA_MAX}]")
    if sparse_mae_improvement < -SPARSE_TOLERANCE:
        reasons.append(f"sparse subgroup worse ({sparse_mae_improvement:.4f})")

    return PairEval(
        pair=pair,
        engine_param=field,
        n_test_rows=len(test_df),
        n_test_athletes=int(test_df[GROUP_COLUMN].nunique()),
        floor=round(floor, 4),
        default_alpha=round(default_alpha, 4),
        learned_alpha=round(learned, 4),
        mae_default=round(mae_default, 4),
        mae_learned=round(mae_learned, 4),
        mae_improvement=round(improvement, 4),
        suppression_corr=round(corr, 4),
        efficiency_at_ref=round(efficiency_at_ref, 4),
        sparse_mae_improvement=round(sparse_mae_improvement, 4),
        verdict="promote" if not reasons else "stay_shadow",
        reasons=reasons,
    )


def evaluate(frame: pd.DataFrame, *, params: Any | None = None, holdout_frac: float = 0.25) -> dict[str, Any]:
    """Evaluate every interference pair present; overall promotes only if ALL pairs do."""
    params = params or default_parameters()
    pairs = [p for p in PAIR_TO_PARAM if (frame[PAIR_COLUMN] == p).any()]
    results = {p: evaluate_pair(frame, p, params=params, holdout_frac=holdout_frac) for p in pairs}
    overall = "promote" if results and all(r.verdict == "promote" for r in results.values()) else "stay_shadow"
    return {
        "overall_verdict": overall,
        "pairs": {p: r.as_dict() for p, r in results.items()},
    }


def main() -> None:
    frame = build_frame(synthetic_interference_rows())
    report = evaluate(frame)
    print(json.dumps(report, indent=2))
    print(f"\nVERDICT: {report['overall_verdict']}")


if __name__ == "__main__":
    main()
