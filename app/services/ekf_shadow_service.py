"""Shadow EKF service (ADR-0041) — a parallel full-covariance estimator.

Runs alongside the production deterministic engine, consuming the same dose/observation
stream and writing belief snapshots to ``ekf_shadow_log``. It applies NOTHING to
production state or prescriptions (``decision_impact="none_shadow_only"``) and is
best-effort: a failure here must never break workout ingest or benchmark assimilation.

Ordering: both entry points run *after* the production commit, so ``load_current_state``
returns the already-advanced state. On the first call for an athlete we seed the belief
from it (no propagation); thereafter we load the prior belief and advance it exactly once
via ``predict`` — a proper filter chain, no double-applied workout.

v1 approximations (surfaced by offline calibration, not hidden): the transition template's
auxiliary fields (``s_struct_signal``) come from the post-step production state, and step
timing is measured against production state rows; both are negligible for a shadow
covariance estimator and are documented in ADR-0041.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.parameters import EngineParameters, default_parameters
from app.logic.benchmark_validity import get_validity_profile
from app.logic.ekf.belief import EkfBelief
from app.logic.ekf.observation import (
    MappingSpec,
    Observation,
    build_observation,
    build_wellness_observation,
    update,
)
from app.logic.ekf.transition import TransitionContext, predict
from app.logic.ekf.wellness_input import WellnessShadowInput
from app.models.ekf_shadow import EkfShadowLog
from app.schemas.workouts import StressDose, WorkoutLog
from app.services.state_service import load_current_state
from app.services.telemetry_common import best_effort_write

logger = logging.getLogger(__name__)


async def _load_latest_belief(db: AsyncSession, user_id: int) -> EkfShadowLog | None:
    res = await db.execute(
        select(EkfShadowLog)
        .where(EkfShadowLog.user_id == user_id)
        .order_by(EkfShadowLog.id.desc())
        .limit(1)
    )
    return res.scalars().first()


def _belief_from_row(row: EkfShadowLog) -> EkfBelief:
    return EkfBelief.from_row(
        mean_map=dict(row.mean_json),
        cov_list=list(row.covariance_json),
        timestamp=row.belief_at,
        model_version=row.model_version,
    )


async def record_ekf_predict(
    db: AsyncSession,
    user_id: int,
    dose: StressDose,
    time_delta: timedelta,
    log: WorkoutLog,
) -> None:
    """Write one EKF predict row for a workout ingest. Never raises to the caller."""
    async with best_effort_write(db, f"ekf predict for user {user_id}"):
        params = default_parameters()
        template = await load_current_state(db, user_id)
        if template is None:
            return  # nothing to seed from yet

        prior_row = await _load_latest_belief(db, user_id)
        if prior_row is None:
            # First EKF step: seed the belief from the current production state; do not
            # propagate (there is no prior belief to advance).
            belief = EkfBelief.seed_from_unified(template, params)
        else:
            ctx = TransitionContext(dose=dose, time_delta=time_delta, log=log, template=template)
            belief = predict(_belief_from_row(prior_row), ctx, params)

        db.add(
            EkfShadowLog(
                user_id=user_id,
                belief_at=_naive(belief.timestamp),
                model_version=belief.model_version,
                event_type="predict",
                mean_json=belief.mean_map(),
                variance_json=belief.variance_map(),
                covariance_json=belief.cov_list(),
                decision_impact="none_shadow_only",
            )
        )


async def record_ekf_update(
    db: AsyncSession,
    user_id: int,
    *,
    benchmark_code: str,
    mapping_specs: Sequence[MappingSpec],
    score01: float | None,
    observed_at: datetime,
) -> None:
    """Write one EKF update row for a benchmark observation. Never raises to the caller.

    ``mapping_specs`` must already be detached from the ORM (snapshotted by the caller
    *before* the production commit) so nothing is lazy-loaded across this transaction.
    """
    specs = list(mapping_specs)
    async with best_effort_write(db, f"ekf update for user {user_id}"):
        params = default_parameters()
        state = await load_current_state(db, user_id)
        if state is None:
            return

        prior_row = await _load_latest_belief(db, user_id)
        prior = _belief_from_row(prior_row) if prior_row is not None else EkfBelief.seed_from_unified(state, params)

        profile = get_validity_profile(benchmark_code)
        obs = build_observation(specs, profile, state, score01)
        if obs is None:
            return  # no score / no capacity mapping → nothing to assimilate

        db.add(_staged_update_row(user_id, prior, obs, params, observed_at))


async def record_ekf_wellness_observation(
    db: AsyncSession,
    user_id: int,
    shadow_input: WellnessShadowInput,
    *,
    observed_at: datetime,
) -> str:
    """Assimilate a wellness (soreness) reading into the belief's fatigue block, exactly once
    per (wellness observation, model version) (ADR-0041 + AUD-C8). Best-effort; never raises.

    Idempotency is DB-enforced by the partial unique index on
    ``(source_wellness_sample_id, model_version)``. An exact retry (same content) does not
    re-assimilate; a correction (same identity, changed content) does not sequentially
    re-assimilate either — it is marked ``correction_requires_replay`` for a later replay, since
    an EKF update is path-dependent and a corrected value cannot simply be applied after the old
    one. The claim row *is* the belief update, so the claim, the assimilation, and the shadow log
    commit together in this one best-effort transaction; a failure rolls the claim back so a
    retry can attempt it again, and never touches the live wellness write.

    Returns the outcome: ``assimilated`` | ``exact_retry`` | ``correction_requires_replay`` |
    ``skipped``.
    """
    outcome = "skipped"
    async with best_effort_write(db, f"ekf wellness update for user {user_id}"):
        params = default_parameters()
        obs = build_wellness_observation(shadow_input.measurement, params)
        if obs is None:
            return "skipped"  # no soreness → nothing to assimilate
        state = await load_current_state(db, user_id)
        if state is None:
            return "skipped"

        prior_row = await _load_latest_belief(db, user_id)
        prior = (
            _belief_from_row(prior_row)
            if prior_row is not None
            else EkfBelief.seed_from_unified(state, params)
        )

        values = _belief_update_values(user_id, prior, obs, params, observed_at)
        model_version = str(values["model_version"])
        values.update(
            source_wellness_sample_id=shadow_input.wellness_sample_id,
            assimilated_content_hash=shadow_input.content_hash,
            latest_seen_content_hash=shadow_input.content_hash,
            correction_requires_replay=False,
        )

        # Insert-first claim: the partial unique index is the concurrency authority. If the
        # INSERT wins, this row's belief update IS the one assimilation. If it conflicts, a
        # prior assimilation exists — classify the incoming content atomically (no
        # SELECT-before-INSERT), never re-assimilating.
        claim = (
            pg_insert(EkfShadowLog)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=["source_wellness_sample_id", "model_version"],
                index_where=text("source_wellness_sample_id IS NOT NULL"),
            )
            .returning(EkfShadowLog.id)
        )
        inserted_id = (await db.execute(claim)).scalar_one_or_none()
        outcome = (
            "assimilated"
            if inserted_id is not None
            else await _classify_wellness_conflict(db, shadow_input, model_version)
        )
    logger.info(
        "ekf_wellness_processing outcome=%s user=%s sample=%s model=%s",
        outcome, user_id, shadow_input.wellness_sample_id, shadow_input.content_hash[:8],
    )
    return outcome


async def _classify_wellness_conflict(
    db: AsyncSession, shadow_input: WellnessShadowInput, model_version: str
) -> str:
    """A prior assimilation exists for this (sample, model). Advance ``latest_seen`` and, if the
    incoming content differs from what was assimilated, mark a sticky correction — atomically, in
    one UPDATE. Never re-assimilates. Returns ``exact_retry`` or ``correction_requires_replay``.
    """
    changed = EkfShadowLog.assimilated_content_hash != shadow_input.content_hash
    stmt = (
        sa_update(EkfShadowLog)
        .where(
            EkfShadowLog.source_wellness_sample_id == shadow_input.wellness_sample_id,
            EkfShadowLog.model_version == model_version,
        )
        .values(
            latest_seen_content_hash=shadow_input.content_hash,
            # Sticky: an exact retry after a correction must not clear the flag.
            correction_requires_replay=or_(EkfShadowLog.correction_requires_replay, changed),
            # Stamp only the first correction.
            correction_detected_at=case(
                (and_(changed, EkfShadowLog.correction_detected_at.is_(None)), func.now()),
                else_=EkfShadowLog.correction_detected_at,
            ),
        )
        .returning(EkfShadowLog.correction_requires_replay)
    )
    new_flag = (await db.execute(stmt)).scalar_one_or_none()
    if new_flag is None:
        return "skipped"
    return "correction_requires_replay" if new_flag else "exact_retry"


def _belief_update_values(
    user_id: int,
    prior: EkfBelief,
    obs: Observation,
    params: EngineParameters,
    observed_at: datetime,
) -> dict[str, Any]:
    """Apply a measurement update and return the EkfShadowLog column values as a dict.

    Shared by the ORM path (``_staged_update_row``) and the wellness insert-first claim, which
    needs a values dict for the ``ON CONFLICT`` statement rather than an ORM instance.
    """
    res = update(prior, obs, params)
    belief = res.belief
    return {
        "user_id": user_id,
        "belief_at": _naive(observed_at),
        "model_version": belief.model_version,
        "event_type": "update",
        "mean_json": belief.mean_map(),
        "variance_json": belief.variance_map(),
        "covariance_json": belief.cov_list(),
        "benchmark_code": obs.benchmark_code,
        "innovation": float(np.mean(res.innovation)),
        "gain_norm": res.gain_norm,
        "trace_pre": res.trace_pre,
        "trace_post": res.trace_post,
        "nis": res.nis,
        "n_obs": len(obs.axis_keys),
        "decision_impact": "none_shadow_only",
    }


def _staged_update_row(
    user_id: int,
    prior: EkfBelief,
    obs: Observation,
    params: EngineParameters,
    observed_at: datetime,
) -> EkfShadowLog:
    """Apply a measurement update and build the (unsaved) EkfShadowLog update row."""
    return EkfShadowLog(**_belief_update_values(user_id, prior, obs, params, observed_at))


def _naive(ts: datetime) -> datetime:
    """The DB stores naive datetimes; drop tzinfo consistently."""
    return ts.replace(tzinfo=None) if ts.tzinfo else ts
