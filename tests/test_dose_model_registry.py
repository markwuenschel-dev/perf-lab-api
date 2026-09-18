"""Which dose model may be production, and why v1 may not be yet.

The distinction this file exists to keep sharp:

* **v1's semantics are established.** Density direction, cross-level consistency, not-modelled
  handling and numerical validity are all proven in
  tests/properties/test_density_semantics.py.
* **v1's physiological calibration is not established.** Measured v1/v0 dose ratios run from
  0.36x to 3.08x at equal work, so the coefficients fitted for v0's variable do not describe
  v1's. That, and only that, is what blocks production.

So there is no test here called "v1 is wrong". v1 is uncalibrated, which is a statement about
the mapping from dose to adaptation, not about the dose variable.
"""
from __future__ import annotations

import pytest

from app.logic.dose_model import (
    DOSE_MODELS,
    PRODUCTION_DOSE_MODEL,
    DensityMeasurement,
    DoseModelStatus,
    UncalibratedModelError,
    select_production_dose_model,
)


def test_production_is_pinned_to_a_named_model_not_to_the_newest() -> None:
    """"Latest" must never resolve to production by itself."""
    assert PRODUCTION_DOSE_MODEL == "v0"
    assert select_production_dose_model().name == "v0"


def test_v1_is_declared_experimental_and_uncalibrated() -> None:
    v1 = DOSE_MODELS["v1"]

    assert v1.status is DoseModelStatus.EXPERIMENTAL_UNCALIBRATED
    assert v1.calibration is None
    assert not v1.is_calibrated


def test_v1_cannot_be_selected_for_production_even_when_asked_for_explicitly() -> None:
    """The guard is a check, not a convention — pointing a setting at v1 is not enough."""
    with pytest.raises(UncalibratedModelError) as excinfo:
        select_production_dose_model("v1")

    assert "uncalibrated" in str(excinfo.value).lower()


def test_an_unknown_model_is_refused_rather_than_defaulted() -> None:
    with pytest.raises(UncalibratedModelError):
        select_production_dose_model("v2_does_not_exist")


def test_every_production_status_model_carries_a_calibration_identifier() -> None:
    """A model cannot claim production status without naming what it was fitted with."""
    for name, model in DOSE_MODELS.items():
        if model.status is DoseModelStatus.PRODUCTION:
            assert model.calibration, f"{name} is production with no calibration identifier"


def test_activation_requires_editing_the_registry_not_flipping_a_boolean() -> None:
    """Regression guard on the shape of the gate itself.

    If a future change makes activation a bare flag, this test is the thing that should be
    updated deliberately — the point being that it has to be noticed.
    """
    v1 = DOSE_MODELS["v1"]

    assert v1.calibration is None
    # Fitting alone is not activation: status must move too.
    calibrated_but_not_activated = type(v1)(
        name=v1.name,
        status=DoseModelStatus.EXPERIMENTAL_UNCALIBRATED,
        calibration="dose_calibration_v2",
        module=v1.module,
    )
    with pytest.raises(UncalibratedModelError):
        DOSE_MODELS["_tmp"] = calibrated_but_not_activated
        try:
            select_production_dose_model("_tmp")
        finally:
            DOSE_MODELS.pop("_tmp", None)


# ── "not modelled" is not "average" ───────────────────────────────────────────

def test_a_not_modelled_density_contributes_the_identity_but_says_it_is_absent() -> None:
    """1.0 in the dose product must never be readable as an observed density."""
    absent = DensityMeasurement(value=None, basis="not_applicable")

    assert absent.factor == 1.0, "the law needs a multiplicative identity"
    assert absent.value is None, "but the measurement itself must stay absent"
    assert absent.basis == "not_applicable"


def test_an_observed_density_of_one_is_distinguishable_from_an_absent_one() -> None:
    """The exact confusion the basis field prevents."""
    observed = DensityMeasurement(value=1.0, basis="sets_per_elapsed_minute")
    absent = DensityMeasurement(value=None, basis="not_applicable")

    assert observed.factor == absent.factor
    assert observed != absent
    assert observed.value is not None and absent.value is None
