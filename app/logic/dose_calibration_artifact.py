"""Activation gate for a fitted dose model: the artifact must prove what it is (phase 8.5).

``app/logic/dose_model.py`` refused an uncalibrated model by checking that a calibration
identifier was non-empty. Any string passed. This module is what the identifier now has to
point at: a committed calibration artifact whose bytes match a pinned sha256 and whose
receipt says it was fitted on real shadow-log data, under the policy, feature schema and
model version this code implements, and passed held-out validation with whole athletes
held out.

Fail closed, in a fixed order, naming the first failed check. The parameters a model runs
with come only from the bytes that were hashed: the file is read once, hashed, and parsed
from that same buffer.

What activation does NOT check yet: a minimum amount of data. The receipt records athletes,
pairs and pairs per athlete; the pass criterion's minimum N is decided when 8B has data to
judge it on (docs/simulations/phase-8.md), not invented before.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from app.engine.parameter_overrides import (
    OverrideError,
    apply_parameter_overrides,
    load_override_artifact,
)
from app.engine.parameters import EngineParameters, default_parameters
from app.logic.dose_fit_policy import (
    DATA_SOURCE_SHADOW,
    FEATURE_SCHEMA_VERSION,
    FIT_POLICY_VERSION,
    VALIDATION_SPLIT,
    pairing_rule,
)

if TYPE_CHECKING:
    from app.logic.dose_model import DoseModel

#: Where committed calibration artifacts live (the same directory as the other priors).
ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "engine" / "param_overrides"

#: The receipt fields an activatable artifact must carry, beyond the checked ones.
RECEIPT_EVIDENCE = ("frame_fingerprint", "code_sha", "counts")


class CalibrationArtifactError(RuntimeError):
    """A calibration artifact failed a named activation check."""

    def __init__(self, check: str, detail: str) -> None:
        super().__init__(f"calibration check {check!r} failed: {detail}")
        self.check = check


@dataclass(frozen=True)
class VerifiedCalibration:
    sha256: str
    receipt: dict[str, Any]
    #: default_parameters() with the artifact's overrides merged: the ONLY parameters an
    #: activated model may run with.
    parameters: EngineParameters


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def live_model_version(model: DoseModel) -> str | None:
    """The dose model version the model's engine module computes, or None if it says none."""
    version = getattr(import_module(model.module), "DOSE_MODEL_VERSION", None)
    return version if isinstance(version, str) else None


def verify_calibration_artifact(
    model: DoseModel, *, artifact_dir: Path | None = None
) -> VerifiedCalibration:
    """Every activation check, in order. Raises ``CalibrationArtifactError`` on the first."""

    def fail(check: str, detail: str) -> CalibrationArtifactError:
        return CalibrationArtifactError(check, f"dose model {model.name!r}: {detail}")

    if not model.calibration_artifact:
        raise fail("artifact_declared", "the registry names no calibration artifact")
    if not model.calibration_sha256:
        raise fail("sha256_pinned", "the registry pins no sha256 for its artifact")

    path = (artifact_dir or ARTIFACT_DIR) / model.calibration_artifact
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise fail("artifact_exists", f"{path} does not exist") from None

    digest = sha256_bytes(data)
    if digest != model.calibration_sha256:
        raise fail("sha256_matches", f"{path.name} hashes to {digest}, pinned {model.calibration_sha256}")

    try:
        artifact = load_override_artifact(cast(dict[str, Any], json.loads(data)))
    except (json.JSONDecodeError, OverrideError) as e:
        raise fail("artifact_valid", str(e)) from None
    if artifact["shadow_only"]:
        raise fail("not_shadow_only", "the artifact is marked shadow_only")

    receipt_raw = artifact.get("calibration")
    if not isinstance(receipt_raw, dict):
        raise fail("receipt_present", "the artifact carries no calibration receipt")
    receipt = cast(dict[str, Any], receipt_raw)

    if receipt.get("calibration_id") != model.calibration:
        raise fail(
            "calibration_id_matches",
            f"receipt says {receipt.get('calibration_id')!r}, registry says {model.calibration!r}",
        )
    live = live_model_version(model)
    if live is None or receipt.get("model_version") != live:
        raise fail(
            "model_version_matches",
            f"fitted for {receipt.get('model_version')!r}, the engine computes {live!r}",
        )
    if receipt.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise fail(
            "feature_schema_matches",
            f"fitted on {receipt.get('feature_schema_version')!r}, the frame is {FEATURE_SCHEMA_VERSION!r}",
        )
    if receipt.get("fit_policy_version") != FIT_POLICY_VERSION:
        raise fail(
            "fit_policy_matches",
            f"fitted under {receipt.get('fit_policy_version')!r}, the policy is {FIT_POLICY_VERSION!r}",
        )
    if receipt.get("pairing_rule") != pairing_rule():
        raise fail("pairing_rule_matches", "the receipt's pairing rule is not the current one")
    if receipt.get("data_source") != DATA_SOURCE_SHADOW:
        raise fail(
            "real_data",
            f"fitted on {receipt.get('data_source')!r}; only {DATA_SOURCE_SHADOW!r} can activate",
        )
    validation_raw = receipt.get("validation")
    validation = cast(dict[str, Any], validation_raw) if isinstance(validation_raw, dict) else {}
    if validation.get("split") != VALIDATION_SPLIT:
        raise fail(
            "athlete_grouped_split",
            f"validated with split {validation.get('split')!r}, not {VALIDATION_SPLIT!r}",
        )
    if validation.get("status") != "pass":
        raise fail("validation_passed", f"validation status is {validation.get('status')!r}")
    missing = [k for k in RECEIPT_EVIDENCE if not receipt.get(k)]
    if missing:
        raise fail("receipt_complete", f"the receipt lacks {missing}")

    return VerifiedCalibration(
        sha256=digest,
        receipt=receipt,
        parameters=apply_parameter_overrides(default_parameters(), artifact),
    )
