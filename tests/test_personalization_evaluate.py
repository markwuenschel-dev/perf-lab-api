from __future__ import annotations

import json

from app.ml.personalization import evaluate as ev
from app.ml.personalization.build_training_frame import synthesize_population


def test_partial_pooling_beats_both_baselines_across_seeds():
    """The seed-robust scientific claim: shrinkage beats full- and no-pooling."""
    for seed in range(5):
        r = ev.evaluate(synthesize_population(seed=seed), seed=seed)
        assert r.improvement_vs_full > 0.0, f"seed {seed}: {r}"
        assert r.improvement_vs_no_pool > 0.0, f"seed {seed}: {r}"
        assert r.verdict == "promote"


def test_partial_pool_mae_is_lowest():
    r = ev.evaluate(synthesize_population(seed=1), seed=1)
    assert r.mae_partial_pool < r.mae_full_pool
    assert r.mae_partial_pool < r.mae_no_pool


def test_ptheta_is_calibrated_and_gated_after_gram_correction():
    """With the Gram-based sampling variance, tr(P^θ)/MSE lands ~1.0 and is gated on."""
    for seed in range(5):
        r = ev.evaluate(synthesize_population(seed=seed), seed=seed)
        assert 0.5 <= r.ptheta_calibration_ratio <= 2.0, f"seed {seed}: {r.ptheta_calibration_ratio}"
        assert not r.warnings  # in band → no calibration warning
        assert r.verdict == "promote"


def test_entrypoint_run_payload_and_artifact():
    payload = ev.run()
    assert payload["verdict"] == "promote"
    assert payload["shadow_only"] is True
    for key in ("mae_partial_pool", "mae_full_pool", "mae_no_pool", "ptheta_calibration_ratio"):
        assert key in payload

    ev.main()  # writes the gitignored artifact
    assert ev.ARTIFACT.exists()
    saved = json.loads(ev.ARTIFACT.read_text())
    assert saved["artifact"] == "personalization_recovery_v1"
    assert saved["verdict"] == "promote"
