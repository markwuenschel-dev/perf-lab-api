"""The 1.5 mi time-trial writer and its display-only VO₂max estimate (B2).

A time trial becomes one ``run_1p5mi_time_trial`` observation through the one benchmark path
(``benchmark_service.stage_observation``), which enforces that it carries no authority. This
module adds what that path cannot know: the inputs the athlete gave for the estimate, the age at
performance (from the profile's date of birth AT WRITE TIME), and the estimate itself, all
captured on the observation's ``protocol_metadata``. A saved estimate is later rebuilt from those
captured inputs only (:func:`estimate_read`), so a profile edit never moves it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic import observation_authority as oa
from app.logic import vo2_estimate as vo2
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.schemas.benchmarks import (
    BenchmarkObservationCreate,
    RunTimeTrialReport,
    Vo2EstimateRead,
)
from app.services import benchmark_service
from app.services.athlete_calendar_service import calendar_for

NOT_ESTIMATED_MESSAGES: dict[str, str] = {
    "coefficients_unverified": "Your run is saved. VO₂max estimates are not available yet.",
    "missing_inputs": "Your run is saved. Add your body mass and sex with a run to see an "
                      "estimated VO₂max.",
    "non_positive_result": "Your run is saved. This time is outside what the estimate can use.",
    "unsupported_model": "Your run is saved. This record's estimate model is no longer shown.",
}


async def check_time_trial(
    db: AsyncSession, user_id: int, trial: RunTimeTrialReport, *, date_of_birth: date | None
) -> None:
    """Refuse an impossible run before anything is written: a date after the athlete's today, or
    before their date of birth."""
    if trial.performed_on is None:
        return
    today = (await calendar_for(db, user_id)).today()
    if trial.performed_on > today:
        raise ValueError("the 1.5 mi run's date is in the future")
    if date_of_birth is not None and trial.performed_on < date_of_birth:
        raise ValueError("the 1.5 mi run's date is before your date of birth")


def protocol_metadata(trial: RunTimeTrialReport, *, date_of_birth: date | None) -> dict[str, Any]:
    """The inputs captured for this run and the estimate made from them, as of now."""
    age = (
        vo2.age_in_full_years(date_of_birth, trial.performed_on)
        if date_of_birth is not None and trial.performed_on is not None
        else None
    )
    return vo2.assess(
        elapsed_time_s=trial.elapsed_seconds,
        body_mass_value=trial.body_mass,
        body_mass_unit=trial.body_mass_unit,
        sex=trial.sex_for_estimate,
        age_at_performance=age,
        performed_on=trial.performed_on,
    )


@dataclass(frozen=True)
class StagedTimeTrial:
    observation: BenchmarkObservation
    staged: benchmark_service.StagedObservation | None  # None: an identical run already recorded


async def _already_recorded(
    db: AsyncSession, user_id: int, *, collection_mode: str, metadata: dict[str, Any]
) -> BenchmarkObservation | None:
    rows = (await db.execute(
        select(BenchmarkObservation)
        .join(BenchmarkDefinition)
        .where(
            BenchmarkObservation.user_id == user_id,
            BenchmarkDefinition.code == vo2.PROTOCOL_CODE,
            BenchmarkObservation.provenance_operation == oa.OP_RUN_TIME_TRIAL,
            BenchmarkObservation.collection_mode == collection_mode,
            BenchmarkObservation.raw_value == metadata["inputs"]["elapsed_time_s"],
        )
        .order_by(BenchmarkObservation.id)
    )).scalars().all()
    for row in rows:
        if (row.protocol_metadata or {}).get("inputs") == metadata["inputs"]:
            return row
    return None


async def stage_time_trial(
    db: AsyncSession,
    user_id: int,
    trial: RunTimeTrialReport,
    *,
    date_of_birth: date | None,
    collection_mode: str,
    skip_if_recorded: bool = False,
) -> StagedTimeTrial:
    """Stage the run in the caller's transaction (the caller commits, then completes ``staged``).

    ``date_of_birth`` is the profile's value as the caller resolved it for this write; it is read
    once, here, and the age it gives is captured on the record."""
    metadata = protocol_metadata(trial, date_of_birth=date_of_birth)
    if skip_if_recorded:
        prior = await _already_recorded(
            db, user_id, collection_mode=collection_mode, metadata=metadata
        )
        if prior is not None:
            return StagedTimeTrial(observation=prior, staged=None)
    calendar = await calendar_for(db, user_id)
    performed_at = (
        calendar.day_start_utc(trial.performed_on) if trial.performed_on is not None else None
    )
    staged = await benchmark_service.stage_observation(
        db,
        user_id,
        BenchmarkObservationCreate(
            benchmark_code=vo2.PROTOCOL_CODE,
            raw_value=trial.elapsed_seconds,
            source="manual",
            collection_mode=collection_mode,
        ),
        performed_at=performed_at,
        provenance_operation=oa.OP_RUN_TIME_TRIAL,
        server_protocol_metadata=metadata,
    )
    return StagedTimeTrial(observation=staged.observation, staged=staged)


def estimate_read(obs: BenchmarkObservation) -> Vo2EstimateRead:
    """The athlete-facing read of one saved run, from the inputs captured on it."""
    metadata = obs.protocol_metadata or {}
    if not metadata.get("inputs"):
        # A run saved without server metadata cannot happen through benchmark_service; refuse
        # to invent inputs for one rather than read the profile.
        metadata = vo2.assess(elapsed_time_s=obs.raw_value)
    view = vo2.estimate_view(metadata)
    inputs = metadata["inputs"]
    performed_on = inputs.get("performed_on")
    model: dict[str, Any] = metadata.get("estimate_model") or {}
    return Vo2EstimateRead(
        observation_id=obs.id,
        benchmark_code=vo2.PROTOCOL_CODE,
        performed_on=date.fromisoformat(performed_on) if performed_on else None,
        recorded_at=obs.observed_at,
        elapsed_seconds=view.elapsed_time_s,
        status=view.status,
        reason=view.reason,
        missing_inputs=view.missing_inputs,
        vo2max_ml_kg_min=view.vo2max,
        headline=(
            vo2.HEADLINE if view.status == "estimated"
            else NOT_ESTIMATED_MESSAGES[view.reason or "missing_inputs"]
        ),
        population_status=view.population_status,
        extrapolated=view.extrapolated,
        population_label=view.population_label,
        see_ml_kg_min=float(vo2.UNCERTAINTY["see"]),
        model_id=str(model.get("id", vo2.MODEL_ID)),
        model_version=int(model.get("version", vo2.MODEL_VERSION)),
    )


async def list_estimates(db: AsyncSession, user_id: int) -> list[Vo2EstimateRead]:
    """The athlete's saved 1.5 mi runs, newest first, each with its own estimate."""
    rows = (await db.execute(
        select(BenchmarkObservation)
        .join(BenchmarkDefinition)
        .where(
            BenchmarkObservation.user_id == user_id,
            BenchmarkDefinition.code == vo2.PROTOCOL_CODE,
        )
        .order_by(BenchmarkObservation.observed_at.desc(), BenchmarkObservation.id.desc())
    )).scalars().all()
    return [estimate_read(row) for row in rows]
