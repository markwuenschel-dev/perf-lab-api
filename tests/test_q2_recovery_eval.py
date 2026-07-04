"""Rail 4 offline validation gate for Q2 recovery priors.

Non-DB: builds synthetic frames directly (no CSV) and proves the gate PROMOTES when a
real recovery signal is planted and STAYS SHADOW on pure noise — plus the saturation
guardrail. Requires pandas/numpy/scikit-learn (dev extra).
"""
import numpy as np
import pandas as pd

from app.ml.q2_recovery.evaluate import (
    MIN_IMPROVEMENT,
    MIN_SIGN_ACCURACY,
    EvalReport,
    _saturation_fraction,
    evaluate,
)


def _frame(effect: float, noise: float, *, n_athletes: int = 24, n_days: int = 30, seed: int = 0):
    """A frame where next-day clearance = effect*z_sleep + noise, per-athlete residualized."""
    rng = np.random.default_rng(seed)
    base = pd.Timestamp("2026-01-01")
    rows = []
    for a in range(n_athletes):
        zs, zh, zr = (rng.normal(0, 1, n_days) for _ in range(3))
        clr = effect * zs + rng.normal(0, noise, n_days)
        for d in range(n_days):
            rows.append({
                "user_id": a, "date": base + pd.Timedelta(days=d),
                "z_sleep": float(zs[d]), "z_hrv": float(zh[d]), "z_rhr": float(zr[d]),
                "recovery_clearance": float(clr[d]),
            })
    df = pd.DataFrame(rows)
    df["label"] = df["recovery_clearance"] - df.groupby("user_id")["recovery_clearance"].transform("mean")
    return df


def test_planted_signal_promotes():
    report = evaluate(_frame(effect=0.6, noise=0.3))
    assert isinstance(report, EvalReport)
    assert report.improvement > MIN_IMPROVEMENT
    assert report.sign_accuracy > MIN_SIGN_ACCURACY
    assert report.sparse_improvement >= 0.0
    assert report.verdict == "promote", report.reasons


def test_pure_noise_stays_shadow():
    report = evaluate(_frame(effect=0.0, noise=1.0))
    assert report.improvement < MIN_IMPROVEMENT
    assert report.verdict == "stay_shadow"
    assert report.reasons  # must explain why


def test_report_serializes_with_expected_keys():
    d = evaluate(_frame(effect=0.4, noise=0.5)).as_dict()
    assert {
        "mae_baseline", "mae_learned", "improvement", "sign_accuracy",
        "calibration_error", "sparse_improvement", "saturation_fraction", "verdict", "reasons",
    } <= set(d)


def test_saturation_fraction_low_for_weak_prior_high_for_strong():
    frame = _frame(effect=0.5, noise=0.5)
    weak = {"recovery_clearance_beta": {"cns": {"sleep": 0.1, "hrv": -0.01, "rhr": -0.005}},
            "clip": {"min": 0.6, "max": 1.5}}
    strong = {"recovery_clearance_beta": {"cns": {"sleep": 1.5, "hrv": 1.5, "rhr": 1.5}},
              "clip": {"min": 0.6, "max": 1.5}}
    assert _saturation_fraction(frame, weak) <= 0.05
    assert _saturation_fraction(frame, strong) > _saturation_fraction(frame, weak)
