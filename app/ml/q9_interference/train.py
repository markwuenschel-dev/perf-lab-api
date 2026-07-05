"""Train Q9 interference priors: fit the ADR-0037 suppression alpha per interference pair.

Fits ``alpha`` in ``gain_efficiency = floor + (1 - floor) * exp(-alpha * z)`` with the
per-axis floor held FIXED at the engine default, via a CONSTRAINED, REGULARIZED nonlinear
least-squares solve (scipy ``least_squares``, alpha bounded to [0, ALPHA_MAX], plus a weak
ridge pull toward the reviewed engine-default alpha). Emits a frozen ``shadow_only``
artifact recording learned-vs-default alphas and the UNWIRED binding to
``EngineParameters.interference_*_alpha`` — nothing here applies an override (there is no
parameter_overrides schema for the interference alphas; the value is recorded for review
only). NO gradient boosting, NO deep learning.

Run ``python -m app.ml.q9_interference.train`` to build a planted fixture -> fit -> print
the artifact reproducibly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from app.engine.parameters import default_parameters
from app.ml.common.artifact import write_artifact as _write_artifact
from app.ml.q9_interference.build_training_frame import (
    GROUP_COLUMN,
    LABEL_COLUMN,
    PAIR_COLUMN,
    build_frame,
    synthetic_interference_rows,
)
from app.ml.q9_interference.model_card import MODEL_CARD

ARTIFACT_VERSION = "q9_interference_priors_v1"
NAMESPACE = "q9_interference"

# Upper bound + regularization for the constrained alpha fit.
ALPHA_MAX = 12.0
# Ridge pull toward the engine default (weak: data dominates once there are enough blocks).
RIDGE_LAMBDA = 0.05

# Each interference pair maps to (engine alpha field, target adaptation axis for the floor).
# The axis picks the floor from EngineParameters.interference_floor_by_axis.
PAIR_TO_PARAM: dict[str, str] = {
    "endurance_on_strength": "interference_e_on_strength_alpha",
    "endurance_on_power": "interference_e_on_power_alpha",
    "cns_on_power": "interference_cns_on_power_alpha",
    "cns_on_skill": "interference_cns_on_skill_alpha",
    "structural_on_aerobic": "interference_structural_on_endurance_quality_alpha",
}
PAIR_TO_AXIS: dict[str, str] = {
    "endurance_on_strength": "max_strength",
    "endurance_on_power": "power",
    "cns_on_power": "power",
    "cns_on_skill": "skill",
    "structural_on_aerobic": "aerobic",
}

_DEFAULT_ARTIFACT_PATH = Path(__file__).resolve().parent / f"{ARTIFACT_VERSION}.json"


def suppression_efficiency(z: np.ndarray, alpha: float, floor: float) -> np.ndarray:
    """Predicted adaptation efficiency floor + (1-floor)*exp(-alpha*z) (mirrors the engine)."""
    z = np.clip(z, 0.0, None)
    return floor + (1.0 - floor) * np.exp(-float(alpha) * z)


def pair_defaults(pair: str, *, params: Any | None = None) -> tuple[str, float, float]:
    """Return ``(engine_param_field, default_alpha, floor)`` for an interference pair."""
    params = params or default_parameters()
    field = PAIR_TO_PARAM[pair]
    axis = PAIR_TO_AXIS[pair]
    default_alpha = float(getattr(params, field))
    floor = float(params.interference_floor_by_axis.get(axis, 0.30))
    return field, default_alpha, floor


def fit_alpha(
    z: np.ndarray,
    efficiency: np.ndarray,
    *,
    floor: float,
    default_alpha: float,
    ridge_lambda: float = RIDGE_LAMBDA,
) -> float:
    """Constrained, ridge-regularized NLS fit of the suppression alpha (floor fixed).

    Minimizes ``sum_i (model(alpha, z_i) - eff_i)^2 + ridge_lambda*(alpha - default)^2``
    with ``alpha`` bounded to ``[0, ALPHA_MAX]``. The ridge residual is a weak pull toward
    the reviewed engine default so a thin/noisy slice does not swing alpha wildly.
    """
    z = np.asarray(z, dtype=float)
    efficiency = np.asarray(efficiency, dtype=float)
    if z.size == 0:
        return float(default_alpha)
    sqrt_lam = float(np.sqrt(max(ridge_lambda, 0.0)))

    def residuals(params: np.ndarray) -> np.ndarray:
        alpha = params[0]
        data_res = suppression_efficiency(z, alpha, floor) - efficiency
        prior_res = np.array([sqrt_lam * (alpha - default_alpha)])
        return np.concatenate([data_res, prior_res])

    x0 = float(np.clip(default_alpha, 0.0, ALPHA_MAX))
    result = least_squares(residuals, x0=[x0], bounds=([0.0], [ALPHA_MAX]))
    return float(result.x[0])


def fit_pair(frame: pd.DataFrame, pair: str, *, params: Any | None = None) -> dict[str, Any]:
    """Fit the suppression alpha for one interference pair over the whole frame."""
    params = params or default_parameters()
    sub = frame[frame[PAIR_COLUMN] == pair]
    field, default_alpha, floor = pair_defaults(pair, params=params)
    z = sub["z_interfering_load"].to_numpy(dtype=float)
    eff = sub[LABEL_COLUMN].to_numpy(dtype=float)
    learned = fit_alpha(z, eff, floor=floor, default_alpha=default_alpha)
    return {
        "engine_param": field,
        "target_axis": PAIR_TO_AXIS[pair],
        "floor": round(floor, 4),
        "default_alpha": round(default_alpha, 4),
        "learned_alpha": round(learned, 4),
        "alpha_delta": round(learned - default_alpha, 4),
        "n_rows": int(len(sub)),
        "n_athletes": int(sub[GROUP_COLUMN].nunique()),
    }


def train(
    frame: pd.DataFrame, *, source: str = "synthetic:planted-interference-fixture", params: Any | None = None
) -> dict[str, Any]:
    """Train and return the ``q9_interference_priors_v1`` shadow-only artifact dict."""
    params = params or default_parameters()
    pairs = [p for p in PAIR_TO_PARAM if (frame[PAIR_COLUMN] == p).any()]
    pair_fits = {p: fit_pair(frame, p, params=params) for p in pairs}
    return {
        "version": ARTIFACT_VERSION,
        "namespace": NAMESPACE,
        "source": source,
        "shadow_only": True,
        "formula": "gain_efficiency = floor + (1 - floor) * exp(-alpha * z)",
        "target": {
            "module": "app.engine.parameters",
            "symbol": "EngineParameters.interference_*_alpha",
            "binding": (
                "learned per-pair suppression alpha would recalibrate the ADR-0037 "
                "interference alphas (see pairs[*].engine_param); NOT wired — there is no "
                "parameter_overrides schema for the interference alphas, so this is recorded "
                "for review ONLY. Applying it is gated by the ADR-0037 interference-floor "
                "guardrail in evaluate.py."
            ),
        },
        "pairs": pair_fits,
        "training": {
            "model": "constrained_nls_exp_suppression",
            "regularization": f"ridge pull to engine default (lambda={RIDGE_LAMBDA})",
            "alpha_bounds": [0.0, ALPHA_MAX],
            "floor_policy": "fixed at EngineParameters.interference_floor_by_axis (alpha-only calibration)",
            "population_priors": True,
            "note": "Synthetic source: SHAPE / alpha-recovery only. See model_card.MODEL_CARD.",
        },
    }


def write_artifact(artifact: dict[str, Any], path: str | Path = _DEFAULT_ARTIFACT_PATH) -> Path:
    """Write the shadow-only artifact JSON. No engine loader (Q9 is not wired)."""
    return _write_artifact(artifact, path)


def main() -> None:
    frame = build_frame(synthetic_interference_rows())
    artifact = train(frame)
    out = write_artifact(artifact)
    print(MODEL_CARD)
    print(f"\nwrote artifact -> {out}")
    print(json.dumps(artifact, indent=2))


if __name__ == "__main__":
    main()
