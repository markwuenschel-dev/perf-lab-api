"""Build a dose-model calibration artifact with its receipt (the 8B entry point, phase 8.5).

NOT RUN in phase 8: the production census of 2026-09-27 had one athlete, one shadow row and
no label-able pair (docs/simulations/phase-8.md). It exists so that the only way to produce
an activatable artifact is this path, and so the activation gate
(``app/logic/dose_calibration_artifact.py``) is tested against artifacts this pipeline
actually emits.

What it does, in order:
1. takes a ``ShadowFrame`` (frame + manifest) from ``build_training_frame``;
2. splits by athlete, fits the weak prior on the held-in athletes only, scores the held-out
   athletes (``evaluate.evaluate_and_fit``);
3. emits the held-in prior plus a receipt: calibration id, model / feature-schema /
   fit-policy versions, pairing rule, data source, frame fingerprint, code SHA, counts,
   exclusions, recompute fidelity and the validation result.

An artifact is ``shadow_only`` unless validation passed. Passing is necessary, not
sufficient: the gate also refuses any data source but the real shadow log, and a model is
activated only by a reviewed registry edit that pins the artifact's sha256.
"""
from __future__ import annotations

from typing import Any

from app.ml.dose_calibration.build_training_frame import ShadowFrame
from app.ml.dose_calibration.evaluate import evaluate_and_fit


def build_calibration_artifact(
    shadow: ShadowFrame, *, calibration_id: str, code_sha: str, holdout_frac: float = 0.25
) -> dict[str, Any]:
    """The artifact for ``shadow``: the held-in prior and a complete receipt."""
    if not calibration_id or not code_sha:
        raise ValueError("a calibration artifact needs a calibration_id and a code_sha")
    if shadow.frame.empty:
        raise ValueError("no labelled rows: there is nothing to fit (see the census)")

    manifest = shadow.manifest
    report, prior = evaluate_and_fit(shadow.frame, holdout_frac=holdout_frac)
    passed = report.verdict == "promote"
    artifact = dict(prior)
    artifact["source"] = f"{manifest['data_source']}:{manifest['frame_fingerprint'][:16]}"
    artifact["shadow_only"] = not passed
    artifact["calibration"] = {
        "calibration_id": calibration_id,
        "model_version": manifest["model_version"],
        "feature_schema_version": manifest["feature_schema_version"],
        "fit_policy_version": manifest["fit_policy_version"],
        "pairing_rule": manifest["pairing_rule"],
        "data_source": manifest["data_source"],
        "frame_fingerprint": manifest["frame_fingerprint"],
        "code_sha": code_sha,
        "counts": {
            "rows": manifest["n_rows"],
            "athletes": manifest["n_athletes"],
            "pairs_per_athlete": manifest["pairs_per_athlete"],
            "train_athletes": report.n_train_athletes,
            "train_rows": report.n_train_rows,
            "test_athletes": report.n_test_athletes,
            "test_rows": report.n_test_rows,
        },
        "excluded": manifest["excluded"],
        "recompute_fidelity": manifest["recompute_fidelity"],
        "validation": {
            "status": "pass" if passed else "fail",
            "split": manifest["split"],
            "holdout_frac": holdout_frac,
            "report": report.as_dict(),
        },
    }
    return artifact
