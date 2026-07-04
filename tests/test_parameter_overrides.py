"""Parameter-override loader (Q2 recovery priors, Rail 2).

Non-DB: proves the override hook is non-invasive (copy, not mutate), clip-enforced,
schema-validated, and refuses to apply a shadow_only artifact to a production caller.
"""
import copy

import pytest

from app.engine.parameter_overrides import (
    OverrideError,
    apply_parameter_overrides,
    load_namespace_override,
    load_override_artifact,
)
from app.engine.parameters import default_parameters


def _artifact(**over):
    base = {
        "version": "test_v1",
        "namespace": "q2_recovery",
        "source": "unit_test",
        "shadow_only": True,
        "recovery_clearance_beta": {"cns": {"sleep": 0.2, "hrv": 0.05, "rhr": -0.03}},
        "clip": {"min": 0.6, "max": 1.5},
    }
    base.update(over)
    return base


def test_placeholder_artifact_loads_and_validates():
    a = load_namespace_override("q2_recovery")
    assert a is not None and a["namespace"] == "q2_recovery"
    assert a["shadow_only"] is True
    assert set(a["recovery_clearance_beta"]) == {
        "cns", "muscular", "metabolic", "structural", "tendon", "grip"
    }


def test_apply_is_non_invasive_copy():
    params = default_parameters()
    before = copy.deepcopy(params.recovery_clearance_beta)
    merged = apply_parameter_overrides(params, _artifact(), allow_shadow=True)
    assert merged is not params
    assert params.recovery_clearance_beta == before, "input params must not be mutated"
    # The provided axis is replaced with the learned signal weights.
    assert merged.recovery_clearance_beta["cns"] == {"sleep": 0.2, "hrv": 0.05, "rhr": -0.03}
    # Axes absent from the artifact keep their defaults.
    assert merged.recovery_clearance_beta["muscular"] == params.recovery_clearance_beta["muscular"]


def test_clip_bounds_come_from_artifact():
    merged = apply_parameter_overrides(
        default_parameters(), _artifact(clip={"min": 0.7, "max": 1.4}), allow_shadow=True
    )
    assert merged.recovery_clearance_min == 0.7
    assert merged.recovery_clearance_max == 1.4


def test_shadow_only_artifact_refused_for_production_caller():
    with pytest.raises(OverrideError, match="shadow_only"):
        apply_parameter_overrides(default_parameters(), _artifact(shadow_only=True))
    # A non-shadow artifact applies without opt-in.
    ok = apply_parameter_overrides(default_parameters(), _artifact(shadow_only=False))
    assert ok.recovery_clearance_beta["cns"]["sleep"] == 0.2


@pytest.mark.parametrize(
    "bad",
    [
        {"clip": {"min": 1.5, "max": 0.6}},                       # min !< max
        {"recovery_clearance_beta": {"legs": {"sleep": 0.1}}},    # unknown axis
        {"recovery_clearance_beta": {"cns": {"vibes": 0.1}}},     # unknown signal
        {"recovery_clearance_beta": {}},                          # empty
    ],
)
def test_malformed_artifacts_rejected(bad):
    with pytest.raises(OverrideError):
        load_override_artifact(_artifact(**bad))


def test_missing_keys_rejected():
    with pytest.raises(OverrideError, match="missing keys"):
        load_override_artifact({"version": "x", "namespace": "q2_recovery"})
