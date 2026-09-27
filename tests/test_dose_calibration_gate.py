"""Activation fails closed on the artifact, not on a string (phase 8.5).

Before 8.5, ``select_production_dose_model`` activated any model whose calibration
identifier was non-empty. Now a non-legacy model needs a committed artifact that hashes to a
pinned sha256 and whose receipt matches the live model version, feature schema, fit policy
and pairing rule, came from the real shadow log, and passed athlete-grouped held-out
validation. Each check below is a separate mutation: the artifact is right in every respect
but one, and the gate must name that one.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.engine.parameters import default_parameters
from app.logic import dose_engine, dose_engine_v1
from app.logic.dose_calibration_artifact import (
    CalibrationArtifactError,
    sha256_bytes,
    verify_calibration_artifact,
)
from app.logic.dose_fit_policy import (
    DATA_SOURCE_SHADOW,
    FEATURE_SCHEMA_VERSION,
    FIT_POLICY_VERSION,
    pairing_rule,
)
from app.logic.dose_model import (
    DOSE_MODELS,
    DoseModel,
    DoseModelStatus,
    UncalibratedModelError,
    resolve_production_dose_model,
)

CAL_ID = "dose_calibration_v1_2_test"
LIVE = dose_engine_v1.DOSE_MODEL_VERSION


def _good_artifact() -> dict[str, Any]:
    weights = dict(default_parameters().dose_volume_weights)
    weights["sets"] = round(weights["sets"] * 1.05, 6)
    return {
        "kind": "dose_overrides",
        "version": "dose_calibration_priors_v1",
        "namespace": "dose_calibration",
        "source": "dose_model_shadow_log:abc",
        "shadow_only": False,
        "engine_overrides": {"dose_volume_weights": weights},
        "calibration": {
            "calibration_id": CAL_ID,
            "model_version": LIVE,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "fit_policy_version": FIT_POLICY_VERSION,
            "pairing_rule": pairing_rule(),
            "data_source": DATA_SOURCE_SHADOW,
            "frame_fingerprint": "f" * 64,
            "code_sha": "c29daf9",
            "counts": {"rows": 400, "athletes": 30},
            "validation": {"status": "pass", "split": "athlete_grouped"},
        },
    }


def _pin(tmp_path: Path, artifact: dict[str, Any], *, name: str = "cal.json") -> DoseModel:
    data = json.dumps(artifact, indent=2).encode("utf-8")
    (tmp_path / name).write_bytes(data)
    return DoseModel(
        name="v1",
        status=DoseModelStatus.VALIDATED,
        calibration=CAL_ID,
        module="app.logic.dose_engine_v1",
        calibration_artifact=name,
        calibration_sha256=sha256_bytes(data),
    )


def test_a_complete_artifact_activates_and_its_parameters_are_the_ones_it_carries(
    tmp_path: Path,
) -> None:
    artifact = _good_artifact()
    verified = verify_calibration_artifact(_pin(tmp_path, artifact), artifact_dir=tmp_path)
    assert verified.parameters.dose_volume_weights == artifact["engine_overrides"]["dose_volume_weights"]
    assert verified.parameters.dose_volume_weights != default_parameters().dose_volume_weights
    assert verified.receipt["calibration_id"] == CAL_ID


def _mutated(**path_values: Any) -> dict[str, Any]:
    a = copy.deepcopy(_good_artifact())
    for dotted, value in path_values.items():
        *parents, leaf = dotted.split("__")
        target = a
        for key in parents:
            target = target[key]
        if value is _DELETE:
            del target[leaf]
        else:
            target[leaf] = value
    return a


_DELETE = object()


@pytest.mark.parametrize(
    ("mutation", "check"),
    [
        ({"shadow_only": True}, "not_shadow_only"),
        ({"calibration": _DELETE}, "receipt_present"),
        ({"calibration__calibration_id": "someone_else"}, "calibration_id_matches"),
        ({"calibration__model_version": "v1.1"}, "model_version_matches"),
        ({"calibration__feature_schema_version": "dose-features-1"}, "feature_schema_matches"),
        ({"calibration__fit_policy_version": "fit-policy-0"}, "fit_policy_matches"),
        ({"calibration__pairing_rule": {"gap_days": [0, 7]}}, "pairing_rule_matches"),
        ({"calibration__data_source": "synthetic"}, "real_data"),
        ({"calibration__data_source": "seeded"}, "real_data"),
        ({"calibration__validation__split": "row_random"}, "athlete_grouped_split"),
        ({"calibration__validation__status": "fail"}, "validation_passed"),
        ({"calibration__validation": _DELETE}, "athlete_grouped_split"),
        ({"calibration__frame_fingerprint": ""}, "receipt_complete"),
        ({"calibration__code_sha": _DELETE}, "receipt_complete"),
        ({"engine_overrides": {"not_a_dose_field": 1.0}}, "artifact_valid"),
    ],
)
def test_each_wrong_field_is_refused_by_name(
    tmp_path: Path, mutation: dict[str, Any], check: str
) -> None:
    model = _pin(tmp_path, _mutated(**mutation))
    with pytest.raises(CalibrationArtifactError) as e:
        verify_calibration_artifact(model, artifact_dir=tmp_path)
    assert e.value.check == check


def test_bytes_changed_after_pinning_are_refused(tmp_path: Path) -> None:
    model = _pin(tmp_path, _good_artifact())
    (tmp_path / "cal.json").write_text(json.dumps(_good_artifact()), encoding="utf-8")
    with pytest.raises(CalibrationArtifactError) as e:
        verify_calibration_artifact(model, artifact_dir=tmp_path)
    assert e.value.check == "sha256_matches"


def test_a_missing_file_undeclared_artifact_or_unpinned_hash_is_refused(tmp_path: Path) -> None:
    model = _pin(tmp_path, _good_artifact())
    for broken, check in (
        (replace(model, calibration_artifact="gone.json"), "artifact_exists"),
        (replace(model, calibration_artifact=None), "artifact_declared"),
        (replace(model, calibration_sha256=None), "sha256_pinned"),
    ):
        with pytest.raises(CalibrationArtifactError) as e:
            verify_calibration_artifact(broken, artifact_dir=tmp_path)
        assert e.value.check == check


# --- the registry ------------------------------------------------------------------------


def test_production_is_still_the_legacy_v0_on_engine_defaults() -> None:
    resolved = resolve_production_dose_model()
    assert resolved.model.name == "v0"
    assert resolved.parameters is None
    # The facade hands out v0's own function, not a wrapper: production is byte-identical.
    from app.logic import dose_engine_v0

    assert dose_engine.calculate_stress_dose is dose_engine_v0.calculate_stress_dose


def test_v1_in_the_registry_cannot_activate() -> None:
    with pytest.raises(UncalibratedModelError):
        resolve_production_dose_model("v1")


def test_a_calibration_string_alone_no_longer_activates(monkeypatch) -> None:
    """The pre-8.5 loophole: any non-empty identifier on a non-experimental model passed."""
    monkeypatch.setitem(
        DOSE_MODELS,
        "v1_string_only",
        DoseModel(
            name="v1_string_only",
            status=DoseModelStatus.VALIDATED,
            calibration="dose_calibration_v2",
            module="app.logic.dose_engine_v1",
        ),
    )
    with pytest.raises(UncalibratedModelError, match="artifact_declared"):
        resolve_production_dose_model("v1_string_only")


def test_only_the_exact_legacy_pair_skips_the_artifact(monkeypatch) -> None:
    monkeypatch.setitem(
        DOSE_MODELS,
        "v0_impostor",
        DoseModel(name="v0_impostor", status=DoseModelStatus.PRODUCTION,
                  calibration="legacy_v0", module="app.logic.dose_engine_v0"),
    )
    with pytest.raises(UncalibratedModelError, match="artifact_declared"):
        resolve_production_dose_model("v0_impostor")


# --- artifacts the real pipeline emits ---------------------------------------------------


def _pipeline_artifact(data_source: str) -> dict[str, Any]:
    from app.ml.dose_calibration.build_training_frame import (
        ShadowFrame,
        build_frame,
        frame_fingerprint,
        synthesize_sessions,
    )
    from app.ml.dose_calibration.calibrate import build_calibration_artifact

    frame = build_frame(synthesize_sessions(n_athletes=24, n_sessions=20, seed=11))
    manifest = {
        "data_source": data_source, "model_version": LIVE,
        "fit_policy_version": FIT_POLICY_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION, "pairing_rule": pairing_rule(),
        "split": "athlete_grouped", "n_rows": len(frame),
        "n_athletes": int(frame["user_id"].nunique()),
        "pairs_per_athlete": {}, "excluded": {}, "recompute_fidelity": {},
        "frame_fingerprint": frame_fingerprint(frame),
    }
    return build_calibration_artifact(
        ShadowFrame(frame=frame, manifest=manifest), calibration_id=CAL_ID, code_sha="c29daf9"
    )


def test_a_pipeline_artifact_from_synthetic_data_can_never_activate(tmp_path: Path) -> None:
    artifact = _pipeline_artifact("synthetic")
    receipt = artifact["calibration"]
    assert receipt["validation"]["split"] == "athlete_grouped"
    counts = receipt["counts"]
    assert counts["train_athletes"] + counts["test_athletes"] == counts["athletes"]

    model = _pin(tmp_path, artifact)
    with pytest.raises(CalibrationArtifactError) as e:
        verify_calibration_artifact(model, artifact_dir=tmp_path)
    # shadow_only when validation failed; otherwise the data source stops it.
    assert e.value.check in {"not_shadow_only", "real_data"}
    if receipt["validation"]["status"] == "pass":
        assert e.value.check == "real_data"


def test_a_pipeline_artifact_is_shadow_only_unless_validation_passed() -> None:
    artifact = _pipeline_artifact(DATA_SOURCE_SHADOW)
    passed = artifact["calibration"]["validation"]["status"] == "pass"
    assert artifact["shadow_only"] is (not passed)


def test_an_empty_frame_cannot_produce_an_artifact() -> None:
    from app.ml.dose_calibration.build_training_frame import build_shadow_frame
    from app.ml.dose_calibration.calibrate import build_calibration_artifact

    empty = build_shadow_frame([], [], {}, model_version=LIVE)
    with pytest.raises(ValueError, match="nothing to fit"):
        build_calibration_artifact(empty, calibration_id=CAL_ID, code_sha="c29daf9")
