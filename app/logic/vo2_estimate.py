"""The 1.5-mile run time trial and its display-only VO₂max estimate (B2).

The estimate model, its coefficients, its source and its population are kept here, in one place.

Source: George JD, Vehrs PR, Allsen PE, Fellingham GW, Fisher AG. VO2max estimation from a
submaximal 1-mile track jog for fit college-age individuals. Med Sci Sports Exerc
1993;25(3):401-406. PMID 8455458. The model is Table 2, "1.5-Mile Run (N = 49)", relative VO₂max
column, p. 404:

    VO₂max (ml·kg⁻¹·min⁻¹) = 88.02 + 3.716·GENDER − 0.1656·BODY MASS (kg) − 2.767·ELAPSED TIME (min)

* Reported verified against the primary text on 2026-10-21; see the #107 audit,
  docs/audits/2026-10-21-run-vo2-field-test-audit.md ("Primary-source verification").
  The gate below stays closed until the owner confirms that verification.
* Body mass is in kilograms (the row label is "Body mass (kg)"). A mass entered in pounds is
  converted exactly (1 lb = 0.45359237 kg) and the kg coefficient applied. The "−0.0753 per lb"
  form seen in secondary sources is a later, rounded derivation and is deliberately not encoded.
* Elapsed time is in minutes (Table 1 footnote: "expressed as minutes"). Converting seconds to
  decimal minutes is inferred.
* GENDER coding: the paper prints "0 = female; 1 = male" only for its 1-mile jog model. The
  same coding for the 1.5-mile model is INFERRED from the same paper (positive coefficient,
  higher male VO₂max).
* There is no heart-rate term. The legacy calculator (app/api/v1/legacy.py ``vo2_from_1p5``) used
  2.76 and applied the per-kg coefficient to a hard-coded 163 with sex fixed at male. It is not
  this model.

The protocol is a maximal 1.5-mile run: "run the prescribed distance as fast as possible",
self-paced, on a measured track. The population is 149 relatively fit college students aged
18-29. The authors caution against use outside 18-29, and say the model "appears best suited for
individuals who are accustomed to sustained high intensity exercise". Age is therefore NECESSARY
for the study population but not SUFFICIENT, and nothing here claims an in-range result is
validated for the individual.

Every estimate is display-only. The time-trial observation carries no capacity authority
(observation_authority.DISPLAY_ONLY_PROTOCOL_CODES; benchmark_service enforces it), and nothing in
this module is read by the state, prescription or objective paths.

A saved estimate is reproduced from the inputs captured on its own observation
(``protocol_metadata``), never from the live profile: see :func:`estimate_view`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

# The verification gate (owner decision D1). False: no estimate is computed or shown anywhere,
# and a run is still saved with its performance intact. Set it True only once the owner has
# confirmed COEFFICIENTS against the primary text, and record the date and source here.
GEORGE_1993_COEFFICIENTS_VERIFIED = False

#: The benchmark definition this module serves. Must equal the display-only code that
#: observation_authority enforces (pinned by a test).
PROTOCOL_CODE = "run_1p5mi_time_trial"
PROTOCOL_VERSION = 1

MODEL_ID = "george1993_1p5mi_run"
MODEL_VERSION = 1
MODEL_SOURCE = (
    "George JD, Vehrs PR, Allsen PE, Fellingham GW, Fisher AG. Med Sci Sports Exerc "
    "1993;25(3):401-406. PMID 8455458. Table 2, 1.5-Mile Run (N = 49), relative VO2max, p. 404."
)

UNITS = "ml/kg/min"
LB_TO_KG = 0.45359237
SECONDS_PER_MINUTE = 60.0

#: A 1.5 mi time this model can be given: 3 min - 4 h, the same envelope as the legacy calculator
#: (app/api/v1/legacy.py TIME_1P5MI_BOUNDS). Outside it the time is refused, not saved.
ELAPSED_TIME_BOUNDS_S: tuple[float, float] = (180.0, 14400.0)


@dataclass(frozen=True)
class George1993Coefficients:
    intercept: float
    sex: float  # per unit of the sex input (1 = male, 0 = female; inferred coding)
    body_mass_kg: float  # per kilogram
    elapsed_time_min: float  # per minute


COEFFICIENTS = George1993Coefficients(
    intercept=88.02,
    sex=3.716,
    body_mass_kg=-0.1656,
    elapsed_time_min=-2.767,
)

#: The model's sex input. 1 = male, 0 = female, inferred for the 1.5-mile model (see above).
SEX_MODEL_INPUT: dict[str, int] = {"male": 1, "female": 0}

STUDY_AGE_RANGE: tuple[int, int] = (18, 29)

#: The reported error scale, kept on every record. It is in VO₂ units and is NOT a calibrated
#: variance on the capacity scale (D4).
UNCERTAINTY: dict[str, Any] = {
    "kind": "cross_validated_standard_error_of_estimate",
    "see": 3.9,
    "units": UNITS,
    "see_percent_of_vo2max": 8.0,
    "n": 47,
    "r_adj": 0.82,
    "validation": {"n": 49, "r_adj": 0.90, "see": 2.8, "units": UNITS},
}

HEADLINE = "Estimated VO₂max — not used for your plan."
EXTRAPOLATED_LABEL = "Extrapolated and unvalidated"
WITHIN_AGE_RANGE_LABEL = "Within the study's age range (18–29)"

PopulationStatus = Literal["within_study_age_range", "outside_study_age_range", "age_unknown"]
NotEstimatedReason = Literal[
    "coefficients_unverified", "missing_inputs", "non_positive_result", "unsupported_model"
]
MassUnit = Literal["kg", "lb"]
SexInput = Literal["female", "male"]


def body_mass_kg(value: float, unit: str) -> float:
    """The mass in kilograms. Pounds convert exactly; any other unit is refused."""
    if unit == "kg":
        return float(value)
    if unit == "lb":
        return float(value) * LB_TO_KG
    raise ValueError(f"body mass unit must be 'kg' or 'lb', got {unit!r}")


def vo2max(
    *,
    sex_model_input: int,
    body_mass_kg: float,
    elapsed_time_min: float,
    coefficients: George1993Coefficients = COEFFICIENTS,
) -> float:
    """George 1993's 1.5-mile model, in ml/kg/min. Every input is explicit; nothing defaults."""
    if sex_model_input not in (0, 1):
        raise ValueError("the sex input is 1 (male) or 0 (female)")
    return (
        coefficients.intercept
        + coefficients.sex * sex_model_input
        + coefficients.body_mass_kg * body_mass_kg
        + coefficients.elapsed_time_min * elapsed_time_min
    )


def age_in_full_years(date_of_birth: date, on: date) -> int:
    """Completed years of age on ``on``."""
    years = on.year - date_of_birth.year
    if (on.month, on.day) < (date_of_birth.month, date_of_birth.day):
        years -= 1
    return years


def population_status(age_at_performance: int | None) -> PopulationStatus:
    """Whether the athlete was within the study's age range when they ran. Necessary, not
    sufficient: being 18-29 does not make the athlete like the study's fit college students."""
    if age_at_performance is None:
        return "age_unknown"
    low, high = STUDY_AGE_RANGE
    if low <= age_at_performance <= high:
        return "within_study_age_range"
    return "outside_study_age_range"


def check_elapsed_time(elapsed_time_s: float) -> float:
    low, high = ELAPSED_TIME_BOUNDS_S
    if not math.isfinite(elapsed_time_s) or not low <= elapsed_time_s <= high:
        raise ValueError(
            f"a 1.5 mi time must be between {low:g} and {high:g} seconds, got {elapsed_time_s:g}"
        )
    return float(elapsed_time_s)


def _outcome(
    *, sex_model_input: int | None, mass_kg: float | None, elapsed_time_s: float
) -> dict[str, Any]:
    missing = [
        name
        for name, value in (("body_mass", mass_kg), ("sex", sex_model_input))
        if value is None
    ]
    if not GEORGE_1993_COEFFICIENTS_VERIFIED:
        return {"status": "not_estimated", "reason": "coefficients_unverified",
                "missing_inputs": missing, "vo2max": None, "units": UNITS}
    if missing or sex_model_input is None or mass_kg is None:
        return {"status": "not_estimated", "reason": "missing_inputs",
                "missing_inputs": missing, "vo2max": None, "units": UNITS}
    value = vo2max(
        sex_model_input=sex_model_input,
        body_mass_kg=mass_kg,
        elapsed_time_min=elapsed_time_s / SECONDS_PER_MINUTE,
    )
    if not math.isfinite(value) or value <= 0:
        return {"status": "not_estimated", "reason": "non_positive_result",
                "missing_inputs": [], "vo2max": None, "units": UNITS}
    return {"status": "estimated", "reason": None, "missing_inputs": [], "vo2max": value,
            "units": UNITS}


def assess(
    *,
    elapsed_time_s: float,
    body_mass_value: float | None = None,
    body_mass_unit: str | None = None,
    sex: str | None = None,
    age_at_performance: int | None = None,
    performed_on: date | None = None,
) -> dict[str, Any]:
    """The ``protocol_metadata`` of one time trial: the captured inputs, the model, the outcome,
    the population flag and the error scale. Missing inputs never refuse the run; the estimate
    is then absent, with its reason."""
    elapsed = check_elapsed_time(elapsed_time_s)
    if (body_mass_value is None) != (body_mass_unit is None):
        raise ValueError("body mass needs both a value and a unit")
    mass_kg = (
        body_mass_kg(body_mass_value, body_mass_unit)
        if body_mass_value is not None and body_mass_unit is not None
        else None
    )
    if sex is not None and sex not in SEX_MODEL_INPUT:
        raise ValueError(f"sex must be 'female' or 'male', got {sex!r}")
    sex_input = SEX_MODEL_INPUT[sex] if sex is not None else None
    status = population_status(age_at_performance)
    return {
        "protocol": {"code": PROTOCOL_CODE, "version": PROTOCOL_VERSION},
        "estimate_model": {"id": MODEL_ID, "version": MODEL_VERSION, "source": MODEL_SOURCE},
        "inputs": {
            "elapsed_time_s": elapsed,
            "body_mass_kg": mass_kg,
            "body_mass_entered": (
                {"value": float(body_mass_value), "unit": body_mass_unit}
                if body_mass_value is not None
                else None
            ),
            "sex_model_input": sex_input,
            "age_at_performance_years": age_at_performance,
            "performed_on": performed_on.isoformat() if performed_on is not None else None,
        },
        "outcome": _outcome(sex_model_input=sex_input, mass_kg=mass_kg, elapsed_time_s=elapsed),
        "population": {
            "status": status,
            "study_age_range": list(STUDY_AGE_RANGE),
            "extrapolated": status != "within_study_age_range",
        },
        "uncertainty": dict(UNCERTAINTY),
        "authority": "display_only",
    }


@dataclass(frozen=True)
class EstimateView:
    """What an athlete is shown for one saved time trial."""

    status: Literal["estimated", "not_estimated"]
    reason: NotEstimatedReason | None
    missing_inputs: list[str]
    vo2max: float | None
    population_status: PopulationStatus
    extrapolated: bool
    population_label: str
    elapsed_time_s: float


def estimate_view(metadata: dict[str, Any]) -> EstimateView:
    """The estimate for a saved time trial, recomputed from ITS OWN captured inputs.

    Never reads the profile: a later body-mass or date-of-birth change does not move a saved
    estimate. The verification gate applies here as well, so a closed gate hides every estimate.
    """
    inputs: dict[str, Any] = metadata.get("inputs") or {}
    elapsed = float(inputs["elapsed_time_s"])
    model: dict[str, Any] = metadata.get("estimate_model") or {}
    age: Any = inputs.get("age_at_performance_years")
    status = population_status(age if isinstance(age, int) else None)
    extrapolated = status != "within_study_age_range"
    label = (
        WITHIN_AGE_RANGE_LABEL
        if not extrapolated
        else f"{EXTRAPOLATED_LABEL}: "
        + (
            "your age when you ran is unknown"
            if status == "age_unknown"
            else "you were outside the study's age range (18–29) when you ran"
        )
    )
    if model.get("id") != MODEL_ID or model.get("version") != MODEL_VERSION:
        outcome: dict[str, Any] = {"status": "not_estimated", "reason": "unsupported_model",
                                   "missing_inputs": [], "vo2max": None}
    else:
        sex_input: Any = inputs.get("sex_model_input")
        mass: Any = inputs.get("body_mass_kg")
        outcome = _outcome(
            sex_model_input=int(sex_input) if sex_input is not None else None,
            mass_kg=float(mass) if mass is not None else None,
            elapsed_time_s=elapsed,
        )
    return EstimateView(
        status=outcome["status"],
        reason=outcome["reason"],
        missing_inputs=list(outcome["missing_inputs"]),
        vo2max=outcome["vo2max"],
        population_status=status,
        extrapolated=extrapolated,
        population_label=label,
        elapsed_time_s=elapsed,
    )
