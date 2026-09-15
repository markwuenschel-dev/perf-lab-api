"""
app/schemas/profile.py

Read/update schemas for the athlete profile. Field names mirror OnboardRequest
(``*_kg`` suffix on lifts/biometrics) so the frontend speaks one vocabulary;
the endpoint maps those to the AthleteProfile columns (``squat_1rm`` etc.).

Squat, bench and deadlift are read-only here (S2 decision 3): ``ProfileRead`` shows them,
but they are a projection of the athlete's characterized strength evidence and change
only through ``POST /v1/benchmarks/strength-evidence``.
"""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.logic.exercise_slot import EQUIPMENT_PREFERENCE_LOAD_TYPES
from app.logic.onboarding_state import validate_dob
from app.logic.wellness_registry import coverage_signals
from app.schemas.benchmarks import refuse_bare_canonical_lift_numbers


class ProfileRead(BaseModel):
    display_name: str | None
    primary_goal: str | None
    date_of_birth: date | None = None
    experience_years: float
    experience_level: str
    available_days_per_week: int
    session_duration_minutes: int
    # Equipment the athlete has — a hard filter. [] = not set (nothing is filtered);
    # ["bodyweight"] = bodyweight only; otherwise the equipment tags they own.
    equipment: list[str]
    # A tie-break among movements the athlete can do: barbell | dumbbell | machine (machines
    # include cables). [] = no preference. It never limits or widens what can be prescribed.
    equipment_preference: list[str] = Field(default_factory=list)
    # Projections of characterized strength evidence (S2); legacy values are pre-O1 seeds.
    squat_1rm_kg: float | None
    deadlift_1rm_kg: float | None
    bench_1rm_kg: float | None
    overhead_1rm_kg: float | None
    pullup_max_reps: int | None
    run_5k_seconds: float | None
    run_1p5mi_seconds: float | None
    bodyweight_kg: float | None
    height_cm: float | None
    # Wellness signals the athlete explicitly marked "I don't track this" (ADR-0049);
    # hidden from the check-in and never expected. Missing-but-tracked stays an honest gap.
    untracked_wellness_signals: list[str] = Field(default_factory=list)


class ProfileUpdate(BaseModel):
    """Partial update — only fields present in the request body are written.

    Nullable fields (biometrics) accept an explicit ``null`` to clear a previously stored
    value; omitting a field leaves it untouched. Squat, bench and deadlift are refused:
    they are characterized strength evidence, not profile numbers (S2).
    """

    display_name: str | None = None
    primary_goal: str | None = None
    date_of_birth: date | None = None
    experience_years: float | None = Field(None, ge=0)
    experience_level: str | None = None
    available_days_per_week: int | None = Field(None, ge=1, le=7)
    session_duration_minutes: int | None = Field(None, ge=1)
    equipment: list[str] | None = None
    # Full replacement when present; [] clears it back to "no preference".
    equipment_preference: list[str] | None = None
    overhead_1rm_kg: float | None = Field(None, gt=0)
    pullup_max_reps: int | None = Field(None, ge=0)
    run_5k_seconds: float | None = Field(None, gt=0)
    run_1p5mi_seconds: float | None = Field(None, gt=0)
    bodyweight_kg: float | None = Field(None, gt=0)
    height_cm: float | None = Field(None, gt=0)
    # Full replacement of the explicit "don't track" opt-out list when present.
    untracked_wellness_signals: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _refuse_bare_lift_numbers(cls, data: Any) -> Any:
        return refuse_bare_canonical_lift_numbers(
            data, instead="report them via POST /v1/benchmarks/strength-evidence."
        )

    @field_validator("date_of_birth")
    @classmethod
    def _check_dob(cls, v: date | None) -> date | None:
        if v is not None:
            validate_dob(v, date.today())
        return v

    @field_validator("equipment_preference")
    @classmethod
    def _check_equipment_preference(cls, v: list[str] | None) -> list[str] | None:
        """Accept only preferences the resolver understands, normalised and in a fixed order.

        An unknown value is refused rather than stored: the resolver would ignore it while this
        API echoed it back, reporting a preference that never applies (the INT-A5 shape).
        ``None`` passes through so PATCH semantics (omitted = untouched) are unaffected.
        """
        if v is None:
            return None
        chosen = {s.strip().lower() for s in v}
        unknown = sorted(chosen - set(EQUIPMENT_PREFERENCE_LOAD_TYPES))
        if unknown:
            raise ValueError(
                f"unknown equipment preference(s) {unknown}; "
                f"known preferences are {list(EQUIPMENT_PREFERENCE_LOAD_TYPES)}"
            )
        return [p for p in EQUIPMENT_PREFERENCE_LOAD_TYPES if p in chosen]

    @field_validator("untracked_wellness_signals")
    @classmethod
    def _check_untracked_wellness_signals(cls, v: list[str] | None) -> list[str] | None:
        """Reject signal names the coverage engine does not recognize.

        Without this the write boundary accepts anything: readiness intersects
        unknown names away against ``coverage_signals()`` while this API echoes the
        stored raw list back, so the client is told about an opt-out the engine
        never honours. Refuse the write rather than report state that isn't real.
        Duplicates are collapsed and order is preserved; ``None`` passes through so
        PATCH semantics (omitted = untouched) are unaffected.
        """
        if v is None:
            return None
        known = set(coverage_signals())
        unknown = [s for s in v if s not in known]
        if unknown:
            raise ValueError(
                f"unknown wellness signal(s) {sorted(set(unknown))}; "
                f"known signals are {sorted(known)}"
            )
        return list(dict.fromkeys(v))
