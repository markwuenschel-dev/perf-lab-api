"""Which dose model is allowed to be production, and what "density" is measured from.

Two dose engines exist. They are not two implementations of one model — they compute a
DIFFERENT density variable, so their fitted coefficients are not interchangeable:

* ``v0`` — density is elapsed minutes per set (session) / sets per minute of rest (exercise).
  Semantically wrong, but its coefficients were tuned around it, so it is coherent.
* ``v1`` — density is work per unit elapsed time (the 2026-09-18 ruling). Semantically right,
  and **uncalibrated**: replacing ``x`` with ``1/x`` changes the response surface nonlinearly,
  so ``dose_beta`` and every density-dependent coefficient must be re-fit before v1 can
  describe adaptation.

**v1 is not broken. v1's mapping from dose to adaptation is not yet established.** Keeping
those two statements apart is the whole point of this module: the semantics are tested
(tests/properties/test_density_semantics.py), the calibration is not, and only the calibration
gates production.

Activation is therefore keyed on a CALIBRATION IDENTIFIER, not on a boolean flag: a model with
no calibration cannot be selected for production even if someone points the setting at it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class DoseModelStatus(Enum):
    """Where a dose model sits relative to production use."""

    #: Fitted and in service.
    PRODUCTION = "production"
    #: Semantically correct, coefficients not yet fitted. May be computed and compared; may
    #: never drive state or a recommendation.
    EXPERIMENTAL_UNCALIBRATED = "experimental_uncalibrated"
    #: Fitted and validated against held-out data, awaiting activation.
    VALIDATED = "validated"


class UncalibratedModelError(RuntimeError):
    """Raised when an uncalibrated dose model is asked to act as production."""


@dataclass(frozen=True)
class DoseModel:
    name: str
    status: DoseModelStatus
    #: The calibration artifact whose coefficients this model was fitted with. ``None`` means
    #: NOT FITTED — the condition that blocks production, checked rather than trusted.
    calibration: str | None
    module: str
    note: str = ""

    @property
    def is_calibrated(self) -> bool:
        return self.calibration is not None


DOSE_MODELS: dict[str, DoseModel] = {
    "v0": DoseModel(
        name="v0",
        status=DoseModelStatus.PRODUCTION,
        calibration="legacy_v0",
        module="app.logic.dose_engine_v0",
        note=(
            "Density is minutes-per-set (session) and sets-per-rest-minute (exercise) — "
            "reciprocal quantities under one name. Retained as production because its "
            "coefficients were tuned around that variable, and retained forever for replay: "
            "stored states must stay reproducible under the engine that produced them."
        ),
    ),
    "v1": DoseModel(
        name="v1",
        status=DoseModelStatus.EXPERIMENTAL_UNCALIBRATED,
        calibration=None,
        module="app.logic.dose_engine_v1",
        note=(
            "Density is work per unit elapsed time (correct semantics, phase 1.2). "
            "UNCALIBRATED: measured v1/v0 dose ratios range from 0.36x to 3.08x at equal "
            "work, so the adaptation coefficients fitted for v0 do not describe it. "
            "Phase 8 fits, validates on held-out longitudinal data, then activates."
        ),
    ),
}

#: The model production uses. Deliberately a literal rather than "latest": a new model must be
#: activated by an explicit, reviewed edit here plus a calibration identifier, never by being
#: newer than what came before.
PRODUCTION_DOSE_MODEL = "v0"


def select_production_dose_model(name: str | None = None) -> DoseModel:
    """The dose model allowed to drive state and recommendations.

    Refuses an uncalibrated model even when explicitly asked for it. A semantically correct
    predictor with inherited coefficients is not a better model — it is an unvalidated one
    wearing the old model's parameters.
    """
    key = name or PRODUCTION_DOSE_MODEL
    try:
        model = DOSE_MODELS[key]
    except KeyError:
        raise UncalibratedModelError(f"unknown dose model {key!r}") from None

    if not model.is_calibrated:
        raise UncalibratedModelError(
            f"dose model {model.name!r} is {model.status.value} and has no calibration "
            f"identifier; an uncalibrated dose model cannot be used in production. {model.note}"
        )
    if model.status is DoseModelStatus.EXPERIMENTAL_UNCALIBRATED:
        raise UncalibratedModelError(
            f"dose model {model.name!r} is marked experimental and cannot be production."
        )
    return model


# --- What a density value was measured from -----------------------------------------
#
# A density of 1.0 must never be readable as "average density". It is the multiplicative
# identity the dose law uses when density is NOT MODELLED for this session — a different
# statement from "this athlete trained at the reference pace", and the basis says which.

DensityBasis = Literal[
    #: Working sets divided by elapsed minutes (v1, set-counted modalities).
    "sets_per_elapsed_minute",
    #: A real endurance work rate — pace, power, structured intervals. Phase 5 introduces it.
    "structured_endurance_work",
    #: Density is not modelled for this session: no reported set count, or a continuous effort
    #: whose work the set count cannot describe. NOT an observation of average density.
    "not_applicable",
    #: v0's elapsed-minutes-per-set quantity, kept only so replayed doses can say what they were.
    "legacy_minutes_per_set",
]


@dataclass(frozen=True)
class DensityMeasurement:
    """A density value and what it was measured from.

    ``value is None`` means not modelled. The dose law then uses the multiplicative identity,
    which is why the basis travels with it: a future reader must not mistake the identity for
    an observed endurance density.
    """

    value: float | None
    basis: DensityBasis

    @property
    def factor(self) -> float:
        """What the dose law multiplies by: the measured value, or the identity when absent."""
        return 1.0 if self.value is None else self.value


NOT_MODELLED = DensityMeasurement(value=None, basis="not_applicable")
