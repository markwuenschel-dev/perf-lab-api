"""Runnable shadow-EKF calibration gate (ADR-0041).

Postgres-free: runs the synthetic replay (``ekf_replay``), computes the NIS/coverage
calibration report + the EKF-vs-scalar prediction margin, prints the JSON + verdict, and
writes a versioned artifact. Nothing here promotes or changes production — it produces the
honest verdict a future promotion would require.

Run:  python -m app.ml.q10_confidence.evaluate_ekf
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.ml.q10_confidence.ekf_calibration import (
    EkfUpdateRecord,
    calibration_report,
    prediction_comparison,
)
from app.ml.q10_confidence.ekf_replay import run_replay

ARTIFACT = Path(__file__).parent / "artifacts" / "q10_ekf_calibration_v1.json"

# Pool over a fixed range of seeds → a robust, reproducible verdict that doesn't hinge on
# one lucky/unlucky draw (a single seed's ~50 updates leaves NIS/coverage and especially
# the prediction margin noisy).
N_SEEDS = 8


def run(*, n_seeds: int = N_SEEDS, **replay_kwargs: Any) -> dict[str, Any]:
    """Run the replay across seeds, pool the records, and return the report payload."""
    all_records: list[EkfUpdateRecord] = []
    per_seed: list[dict[str, Any]] = []
    for seed in range(n_seeds):
        records = run_replay(seed=seed, **replay_kwargs).records
        all_records.extend(records)
        per_seed.append({"seed": seed, "improvement": prediction_comparison(records)["improvement"]})

    report = calibration_report(all_records)
    return {
        "artifact": "q10_ekf_calibration_v1",
        "shadow_only": True,
        "n_seeds": n_seeds,
        "n_updates": report.nis.get("n_updates"),
        "verdict": report.verdict,
        "reasons": report.reasons,
        "warnings": report.warnings,
        "nis": report.nis,
        "coverage": {str(k): v for k, v in report.coverage.items()},
        "prediction": report.prediction,
        "per_seed_improvement": per_seed,
    }


def main() -> None:
    payload = run()
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(payload, indent=2))
    print(json.dumps(payload, indent=2))
    print(f"\nVERDICT: {payload['verdict']}")


if __name__ == "__main__":
    main()
