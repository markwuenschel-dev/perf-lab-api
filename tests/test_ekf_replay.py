"""Runnable shadow-EKF calibration gate — replay harness + entrypoint (ADR-0041).

DB-free. Proves the gate produces an honest, reproducible verdict on a self-consistent
synthetic scenario, and that the joint-covariance EKF tracks benchmarks at least as well
as the production scalar path.
"""
from __future__ import annotations

import json
import math

from app.ml.q10_confidence import evaluate_ekf
from app.ml.q10_confidence.ekf_calibration import (
    calibration_report,
    interval_coverage,
    nis_consistency,
    prediction_comparison,
)
from app.ml.q10_confidence.ekf_replay import run_replay


def test_replay_is_deterministic():
    a = run_replay(seed=3).records
    b = run_replay(seed=3).records
    assert len(a) == len(b) and len(a) > 0
    for ra, rb in zip(a, b, strict=True):
        assert ra.nis == rb.nis
        assert ra.predicted_mean == rb.predicted_mean
        assert ra.realized == rb.realized
        assert ra.axis == rb.axis


def test_records_carry_calibration_and_comparison_payload():
    recs = run_replay(seed=0).records
    r = recs[0]
    assert r.n_obs == 1
    for field in (r.predicted_std, r.predicted_mean, r.realized, r.scalar_pred, r.true_score):
        assert field is not None
    assert r.axis in ("max_strength", "hypertrophy")


def test_pooled_calibration_is_consistent_and_covered():
    """NIS must not be overconfident and the 95% interval must not be under-covered."""
    records = [rec for s in range(8) for rec in run_replay(seed=s).records]
    nis = nis_consistency(records)
    assert 0.6 <= nis["ratio"] <= 1.6
    assert nis["overconfident"] is False  # the dangerous direction must not trip
    cov = interval_coverage(records)
    assert cov[0.95] >= 0.90  # tails not under-covered
    assert cov[0.80] >= 0.70


def test_ekf_tracks_benchmarks_at_least_as_well_as_scalar():
    """Pooled one-step prediction RMSE: EKF not materially worse than the scalar path."""
    records = [rec for s in range(8) for rec in run_replay(seed=s).records]
    pc = prediction_comparison(records)
    assert pc["ekf_rmse"] is not None and pc["scalar_rmse"] is not None
    assert pc["improvement"] >= -0.02, f"EKF materially worse: {pc}"


def test_verdict_promotes_on_the_synthetic_gate():
    records = [rec for s in range(8) for rec in run_replay(seed=s).records]
    report = calibration_report(records)
    assert report.verdict == "promote", report.reasons


def test_entrypoint_run_payload_schema_and_verdict():
    payload = evaluate_ekf.run(n_seeds=8)
    assert payload["verdict"] == "promote"
    assert payload["shadow_only"] is True
    assert payload["n_updates"] == 400
    for key in ("nis", "coverage", "prediction", "per_seed_improvement", "reasons", "warnings"):
        assert key in payload
    # coverage keys are stringified for JSON
    assert set(payload["coverage"]) == {"0.5", "0.8", "0.95"}


def test_entrypoint_writes_parseable_artifact():
    """`main()` writes a valid, deterministic artifact (the dir is a gitignored build output)."""
    evaluate_ekf.main()
    assert evaluate_ekf.ARTIFACT.exists()
    saved = json.loads(evaluate_ekf.ARTIFACT.read_text())
    assert saved["artifact"] == "q10_ekf_calibration_v1"
    assert saved["verdict"] == "promote"
    # deterministic: an independent run reproduces the written verdict + update count
    fresh = evaluate_ekf.run(n_seeds=saved["n_seeds"])
    assert fresh["verdict"] == saved["verdict"]
    assert fresh["n_updates"] == saved["n_updates"]
    assert math.isclose(fresh["nis"]["ratio"], saved["nis"]["ratio"], rel_tol=1e-9)
