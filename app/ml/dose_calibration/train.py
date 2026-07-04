"""Train dose-law calibration: weak POPULATION priors on the session dose weights.

Fits a REGULARIZED LINEAR model (ridge, alpha by athlete-grouped CV) of the next-session
RPE residual on the standardized volume-proxy components, then maps the learned aggregate
response onto SMALL MULTIPLICATIVE NUDGES of the engine's ``dose_volume_weights`` and
``dose_shape_six_by_modality`` multipliers — clamped so a near-zero signal reproduces the
current defaults (exactly like Q2's weak-prior mapping). The result is emitted as the frozen
``dose_calibration_priors_v1`` artifact (an ``engine_overrides`` block) that
``app.engine.parameter_overrides`` consumes on the dose path.

Design choices (see ``model_card``):
* Population priors, NOT per-athlete personalization — one aggregate response reused across
  athletes.
* Changes are WEAK: each weight moves by at most ``_MAX_WEIGHT_NUDGE`` and each modality's
  shape multipliers by at most ``_MAX_SHAPE_NUDGE``, both driven by a clamped effect ratio,
  so synthetic-noise fits cannot meaningfully move the dose law.
* ``shadow_only: true`` — the loader refuses to apply it on a production path.
* NO GBM / deep learning.

Run ``python -m app.ml.dose_calibration.train`` to (re)write the safe untrained v0 placeholder.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error
from sklearn.model_selection import GroupKFold

from app.engine.parameters import default_parameters
from app.ml.dose_calibration.build_training_frame import (
    COMPONENT_FEATURES,
    COMPONENT_TO_WEIGHT,
    DOSE_COLUMN,
    GROUP_COLUMN,
    LABEL_COLUMN,
    MODALITY_TO_SHAPE,
    build_frame,
    grouped_time_split,
    modeled_doses,
    synthesize_sessions,
)
from app.ml.dose_calibration.model_card import MODEL_CARD

ARTIFACT_VERSION = "dose_calibration_priors_v1"
NAMESPACE = "dose_calibration"

_ALPHAS: tuple[float, ...] = (0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 100.0)
# A standardized effect size of _EFFECT_REF earns a component/modality its FULL allowed
# nudge; a near-zero learned effect therefore yields a near-default weight (weak prior).
_EFFECT_REF = 0.20
_RATIO_CAP = 1.0
# Hard caps on how far a weak prior may move the dose law from its literature defaults.
_MAX_WEIGHT_NUDGE = 0.15   # +/-15% on any dose_volume_weight
_MAX_SHAPE_NUDGE = 0.10    # +/-10% on a modality's shape multipliers

_DEFAULT_ARTIFACT_PATH = (
    Path(__file__).resolve().parents[2]
    / "engine"
    / "param_overrides"
    / f"{ARTIFACT_VERSION}.json"
)


def _standardize(y: pd.Series | np.ndarray) -> tuple[np.ndarray, float, float]:
    """Center + scale to unit variance; return (standardized, mean, std)."""
    arr = np.asarray(y, dtype=float)
    mean = float(arr.mean())
    std = float(arr.std(ddof=0)) or 1.0
    return (arr - mean) / std, mean, std


def _select_alpha_grouped_cv(
    x: np.ndarray, y: np.ndarray, groups: np.ndarray, n_groups: int
) -> float:
    """Pick the ridge alpha by athlete-grouped K-fold CV (min mean validation MSE)."""
    if n_groups < 2:
        return 1.0
    cv = GroupKFold(n_splits=min(5, n_groups))
    best_alpha, best_mse = 1.0, np.inf
    for alpha in _ALPHAS:
        fold_mse: list[float] = []
        for tr_idx, va_idx in cv.split(x, y, groups):
            model = Ridge(alpha=alpha)
            model.fit(x[tr_idx], y[tr_idx])
            fold_mse.append(float(mean_squared_error(y[va_idx], model.predict(x[va_idx]))))
        mean_mse = float(np.mean(fold_mse))
        if mean_mse < best_mse:
            best_alpha, best_mse = alpha, mean_mse
    return best_alpha


def fit_component_response(frame: pd.DataFrame) -> dict[str, Any]:
    """Fit the aggregate ridge response of the label on the volume components."""
    x = frame.loc[:, list(COMPONENT_FEATURES)].to_numpy(dtype=float)
    y_std, _, _ = _standardize(frame[LABEL_COLUMN])
    groups = frame[GROUP_COLUMN].to_numpy()
    n_groups = int(np.unique(groups).size)
    alpha = _select_alpha_grouped_cv(x, y_std, groups, n_groups)

    model = Ridge(alpha=alpha)
    model.fit(x, y_std)
    coefs = {f: float(c) for f, c in zip(COMPONENT_FEATURES, model.coef_, strict=True)}
    return {"alpha": alpha, "coefficients": coefs, "n_rows": int(len(frame)), "n_athletes": n_groups}


def _weak_factor(effect: float, max_nudge: float) -> float:
    """Map a standardized effect size to a clamped multiplicative factor near 1.0."""
    ratio = float(np.clip(effect / _EFFECT_REF, -_RATIO_CAP, _RATIO_CAP))
    return 1.0 + max_nudge * ratio


def map_response_to_volume_weights(coefs: dict[str, float]) -> dict[str, float]:
    """Nudge each ``dose_volume_weight`` by its component's clamped learned effect.

    A larger positive effect (that component's magnitude predicts higher next-session cost)
    nudges its weight UP; a near-zero effect leaves the literature default untouched.
    """
    defaults = default_parameters().dose_volume_weights
    weights: dict[str, float] = {}
    for feat, weight_name in COMPONENT_TO_WEIGHT.items():
        factor = _weak_factor(coefs.get(feat, 0.0), _MAX_WEIGHT_NUDGE)
        weights[weight_name] = round(float(defaults[weight_name]) * factor, 6)
    return weights


def fit_modality_calibration(frame: pd.DataFrame) -> dict[str, Any]:
    """Per-shape-modality standardized slope of the label on the modeled dose (+ pooled).

    Measures how well the current modeled dose already tracks the outcome for each modality;
    a modality whose dose tracks BETTER than the pooled average is emphasized slightly, one
    that tracks worse is de-emphasized — a relative, self-cancelling calibration.
    """
    shape_key = frame["modality"].map(MODALITY_TO_SHAPE)
    y_all, _, _ = _standardize(frame[LABEL_COLUMN])
    d_all, _, _ = _standardize(frame[DOSE_COLUMN])
    g_global = float(Ridge(alpha=1.0).fit(d_all.reshape(-1, 1), y_all).coef_[0])

    by_modality: dict[str, float] = {}
    for mod in sorted(set(shape_key.dropna())):
        mask = (shape_key == mod).to_numpy()
        if int(mask.sum()) < 10:
            continue
        y_m, _, _ = _standardize(frame.loc[mask, LABEL_COLUMN])
        d_m, _, _ = _standardize(frame.loc[mask, DOSE_COLUMN])
        by_modality[mod] = float(Ridge(alpha=1.0).fit(d_m.reshape(-1, 1), y_m).coef_[0])
    return {"global": g_global, "by_modality": by_modality}


def map_modality_to_shape(
    by_modality: dict[str, float], g_global: float
) -> dict[str, dict[str, float]]:
    """Nudge each calibrated modality's six shape multipliers by a clamped relative factor."""
    defaults = default_parameters().dose_shape_six_by_modality
    shape: dict[str, dict[str, float]] = {}
    for mod, g in by_modality.items():
        if mod not in defaults:
            continue
        factor = _weak_factor(g - g_global, _MAX_SHAPE_NUDGE)
        shape[mod] = {ax: round(float(mult) * factor, 6) for ax, mult in defaults[mod].items()}
    return shape


def train(frame: pd.DataFrame, *, source: str = "synthetic:dose-sessions") -> dict[str, Any]:
    """Train and return the frozen-schema ``dose_calibration_priors_v1`` artifact dict."""
    fit = fit_component_response(frame)
    volume_weights = map_response_to_volume_weights(fit["coefficients"])
    modality_cal = fit_modality_calibration(frame)
    shape = map_modality_to_shape(modality_cal["by_modality"], modality_cal["global"])

    engine_overrides: dict[str, Any] = {"dose_volume_weights": volume_weights}
    if shape:
        engine_overrides["dose_shape_six_by_modality"] = shape

    return {
        "version": ARTIFACT_VERSION,
        "namespace": NAMESPACE,
        "source": source,
        "shadow_only": True,
        "engine_overrides": engine_overrides,
        "training": {
            "model": "ridge",
            "alpha": fit["alpha"],
            "learned_response": fit["coefficients"],
            "modality_slopes": modality_cal["by_modality"],
            "modality_slope_global": modality_cal["global"],
            "n_rows": fit["n_rows"],
            "n_athletes": fit["n_athletes"],
            "label": "next-session RPE residual (per-athlete demeaned)",
            "population_priors": True,
            "max_weight_nudge": _MAX_WEIGHT_NUDGE,
            "max_shape_nudge": _MAX_SHAPE_NUDGE,
            "note": "Synthetic source: SHAPE/weak-prior only. See model_card.MODEL_CARD.",
        },
    }


def placeholder_artifact() -> dict[str, Any]:
    """Safe untrained v0 = current engine defaults verbatim (a ZERO-CHANGE override)."""
    p = default_parameters()
    return {
        "version": ARTIFACT_VERSION,
        "namespace": NAMESPACE,
        "source": "untrained-v0-placeholder:defaults",
        "shadow_only": True,
        "engine_overrides": {
            "dose_volume_weights": dict(p.dose_volume_weights),
            "dose_shape_six_by_modality": {m: dict(a) for m, a in p.dose_shape_six_by_modality.items()},
        },
        "training": {
            "model": "none",
            "note": "Untrained placeholder equal to engine defaults; applying it changes nothing.",
            "population_priors": True,
        },
    }


def _outcome_map_mae(
    dose_tr: np.ndarray, y_tr: np.ndarray, dose_te: np.ndarray, y_te: np.ndarray
) -> float:
    """MAE of a 1-D ridge dose->outcome map fit on train, scored on the held-out test."""
    model = Ridge(alpha=1.0).fit(dose_tr.reshape(-1, 1), y_tr)
    pred = model.predict(dose_te.reshape(-1, 1))
    return float(np.mean(np.abs(y_te - pred)))


def holdout_mae(
    frame: pd.DataFrame, artifact: dict[str, Any], *, holdout_frac: float = 0.25
) -> tuple[float, float]:
    """MAE of the CALIBRATED dose vs the DEFAULT dose at predicting the outcome (held out).

    A 1-D ridge maps the modeled dose to the standardized next-session-RPE residual; the
    dose is recomputed under both the default weights and the artifact's calibrated weights
    via the engine. Returns ``(mae_calibrated, mae_default)`` — calibration helps iff the
    former is smaller.
    """
    from app.engine.parameter_overrides import apply_dose_overrides

    train_df, test_df = grouped_time_split(frame, holdout_frac=holdout_frac)
    y_tr, y_mean, y_std = _standardize(train_df[LABEL_COLUMN])
    y_te = (test_df[LABEL_COLUMN].to_numpy(dtype=float) - y_mean) / y_std

    default_p = default_parameters()
    calibrated_p = apply_dose_overrides(default_p, artifact, allow_shadow=True)

    out: list[float] = []
    for params in (calibrated_p, default_p):
        d_tr_raw = modeled_doses(train_df, params)
        d_te_raw = modeled_doses(test_df, params)
        d_std, d_mean, d_sd = _standardize(d_tr_raw)
        d_te = (d_te_raw - d_mean) / d_sd
        out.append(_outcome_map_mae(d_std, y_tr, d_te, y_te))
    return out[0], out[1]


def write_artifact(artifact: dict[str, Any], path: str | Path = _DEFAULT_ARTIFACT_PATH) -> Path:
    """Validate against the loader and write the artifact JSON to ``path``."""
    from app.engine.parameter_overrides import load_override_artifact

    load_override_artifact(artifact)  # fail loudly if it drifts from the frozen schema
    p = Path(path)
    p.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return p


def main() -> None:
    # Ship the SAFE placeholder (zero change) as the committed artifact; the trained
    # weak prior stays a shadow experiment until real first-party dose outcomes exist.
    artifact = placeholder_artifact()
    out = write_artifact(artifact)
    frame = build_frame(synthesize_sessions())
    trained = train(frame)
    mae_cal, mae_def = holdout_mae(frame, trained)
    print(MODEL_CARD)
    print(f"\nwrote placeholder artifact -> {out}")
    print(f"holdout MAE calibrated={mae_cal:.4f} default={mae_def:.4f} (on a synthetic frame)")
    print(json.dumps(artifact, indent=2))


if __name__ == "__main__":
    main()
