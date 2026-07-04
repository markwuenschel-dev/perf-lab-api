"""Parameter-override loader (Q2 recovery priors, Rail 2).

Non-DB: proves the override hook is non-invasive (copy, not mutate), clip-enforced,
schema-validated, and refuses to apply a shadow_only artifact to a production caller.
"""
import copy

import pytest

from app.engine.parameter_overrides import (
    OverrideError,
    apply_dose_overrides,
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


# --- Dose-calibration engine_overrides path (additive; must not touch the recovery path) --

def _dose_artifact(**over):
    base = {
        "version": "dose_test_v1",
        "namespace": "dose_calibration",
        "source": "unit_test",
        "shadow_only": True,
        "engine_overrides": {
            "dose_volume_weights": {"duration": 1.1, "sets": 1.9},
            "dose_shape_six_by_modality": {"Running": {"volume": 0.40}},
        },
    }
    base.update(over)
    return base


def test_dose_artifact_validates_without_recovery_keys():
    a = load_override_artifact(_dose_artifact())
    assert a["namespace"] == "dose_calibration"
    assert "recovery_clearance_beta" not in a


def test_dose_overrides_merge_additively_and_non_invasively():
    params = default_parameters()
    before_vw = copy.deepcopy(params.dose_volume_weights)
    before_shape = copy.deepcopy(params.dose_shape_six_by_modality)

    merged = apply_dose_overrides(params, _dose_artifact(), allow_shadow=True)

    assert merged is not params
    # Input params untouched (copy-not-mutate).
    assert params.dose_volume_weights == before_vw
    assert params.dose_shape_six_by_modality == before_shape
    # Specified keys merged...
    assert merged.dose_volume_weights["duration"] == 1.1
    assert merged.dose_volume_weights["sets"] == 1.9
    # ...unspecified keys keep defaults (additive, not replace).
    assert merged.dose_volume_weights["volume_load"] == before_vw["volume_load"]
    # Nested shape merge only touches the named modality/axis.
    assert merged.dose_shape_six_by_modality["Running"]["volume"] == 0.40
    assert (
        merged.dose_shape_six_by_modality["Running"]["intensity"]
        == before_shape["Running"]["intensity"]
    )
    assert merged.dose_shape_six_by_modality["strength"] == before_shape["strength"]


def test_dose_scalar_field_merges():
    merged = apply_dose_overrides(
        default_parameters(),
        _dose_artifact(engine_overrides={"dose_novelty_floor": 0.3, "dose_w_phi_floor": 0.4}),
        allow_shadow=True,
    )
    assert merged.dose_novelty_floor == 0.3
    assert merged.dose_w_phi_floor == 0.4


def test_dose_shadow_only_refused_for_production_caller():
    with pytest.raises(OverrideError, match="shadow_only"):
        apply_dose_overrides(default_parameters(), _dose_artifact(shadow_only=True))
    ok = apply_dose_overrides(default_parameters(), _dose_artifact(shadow_only=False))
    assert ok.dose_volume_weights["duration"] == 1.1


@pytest.mark.parametrize(
    "eo",
    [
        {"recovery_clearance_beta": {"cns": {"sleep": 0.1}}},   # non-dose field name
        {"tau_fatigue_hours": {"cns": 100.0}},                  # real param, not a dose field
        {"dose_volume_weights": {"bogus": 1.0}},                # unknown sub-key
        {"dose_shape_six_by_modality": {"Yoga": {"volume": 0.4}}},   # unknown modality
        {"dose_shape_six_by_modality": {"Running": {"vibes": 0.4}}}, # unknown axis
        {"dose_novelty_floor": "high"},                         # non-numeric scalar
        {},                                                     # empty engine_overrides
    ],
)
def test_dose_whitelist_rejects_bad_fields(eo):
    with pytest.raises(OverrideError):
        load_override_artifact(_dose_artifact(engine_overrides=eo))


def test_dose_artifact_missing_keys_rejected():
    with pytest.raises(OverrideError, match="missing keys"):
        load_override_artifact({"version": "x", "namespace": "dose_calibration",
                                "engine_overrides": {"dose_novelty_floor": 0.3}})


def test_dose_path_leaves_recovery_defaults_untouched():
    merged = apply_dose_overrides(default_parameters(), _dose_artifact(), allow_shadow=True)
    assert merged.recovery_clearance_beta == default_parameters().recovery_clearance_beta
    assert merged.recovery_clearance_min == default_parameters().recovery_clearance_min


def test_committed_dose_placeholder_is_zero_change():
    a = load_namespace_override("dose_calibration")
    assert a is not None and a["shadow_only"] is True
    merged = apply_dose_overrides(default_parameters(), a, allow_shadow=True)
    # The committed v0 equals engine defaults: applying it changes nothing.
    assert merged.dose_volume_weights == default_parameters().dose_volume_weights
    assert merged.dose_shape_six_by_modality == default_parameters().dose_shape_six_by_modality
