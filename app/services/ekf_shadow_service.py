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
from app.logic.ekf.events import (
    EKF_EVENT_PREDICT,
    EKF_EVENT_REPLAY,
    EKF_EVENT_UPDATE,
    original_wellness_assimilation_clause,
)
from app.logic.ekf.observation import (
    MappingSpec,
    Observation,
    build_observation,
    build_wellness_observation,
    update,
)
from app.logic.ekf.transition import TransitionContext, predict
from app.logic.ekf.wellness_input import (
    WellnessMeasurement,
    WellnessShadowInput,
    wellness_content_hash,
)
from app.models.ekf_shadow import EkfShadowLog
from app.models.wellness import WellnessSample
from app.schemas.workouts import StressDose, WorkoutLog
from app.services.state_service import load_current_state
from app.services.telemetry_common import best_effort_write

logger = logging.getLogger(__name__)

# Namespaced, transaction-scoped advisory lock: every EKF appender (predict, update, wellness,
# replay) serializes per athlete on the ONE per-user belief chain. The unique indexes guard
# duplicate *identities*; this guards chain *order* — a replay must not append from a stale head
# while a concurrent workout advances it. Both args are int4 (the namespace salt is a constant;
# users.id is a 32-bit Integer PK — asserted in tests/test_ekf_chain_lock.py), so the two-int
# advisory form is exact with no BIGINT truncation.
_EKF_CHAIN_LOCK_NAMESPACE = 0x454B4631  # "EKF1"; 1_163_481_137 — within signed int4


async def _acquire_ekf_chain_lock(db: AsyncSession, user_id: int) -> None:
    """Serialize this transaction against every other EKF appender for this athlete.

    MUST be the first chain-related DB operation in the transaction: acquired before any
    head/prior read or append, so the head cannot move under a replay. Released automatically at
    commit/rollback (xact-scoped)."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :uid)"),
        {"ns": _EKF_CHAIN_LOCK_NAMESPACE, "uid": user_id},
    )


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
        await _acquire_ekf_chain_lock(db, user_id)  # serialize the per-user belief chain
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
                event_type=EKF_EVENT_PREDICT,
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
        await _acquire_ekf_chain_lock(db, user_id)  # serialize the per-user belief chain
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

    Orchestrates three things under a per-user chain lock (head-correction replay, a038):

    1. **Pre-ingest replay** — repair any pre-existing pending HEAD correction *first*, in this
       same transaction, so the current event assimilates on the corrected trajectory. An
       *unexpected* failure here aborts the whole shadow transaction (the current event must not
       append and turn a still-eligible correction into a mid-history one); handled/blocked cases
       return a typed outcome and let assimilation proceed.
    2. **Assimilate / classify** the current input. Idempotency is DB-enforced by the partial
       unique index on ``(source_wellness_sample_id, model_version) WHERE event_type='update'``.
       An exact retry does not re-assimilate; a correction advances ``correction_revision`` and
       marks ``correction_requires_replay`` (path-dependent — a corrected value cannot simply be
       applied after the old one).
    3. **Post-classification replay** — if *this* event created/advanced a correction, attempt its
       head replay in a SEPARATE best-effort transaction, so a replay failure cannot un-commit the
       durable pending correction.

    Returns the assimilation outcome: ``assimilated`` | ``exact_retry`` |
    ``correction_requires_replay`` | ``skipped``.
    """
    outcome = "skipped"
    async with best_effort_write(db, f"ekf wellness update for user {user_id}"):
        await _acquire_ekf_chain_lock(db, user_id)  # serialize the per-user belief chain
        # (1) Pre-existing pending head correction, in THIS transaction (see docstring). Blocked/
        # no_pending return normally; an unexpected error propagates and aborts the shadow txn.
        await _replay_pending_head_correction(db, user_id, phase="pre_ingest")
        # (2) Assimilate or classify the current input.
        outcome = await _assimilate_or_classify(db, user_id, shadow_input, observed_at)
    # (3) A newly detected correction: repair it in its own best-effort transaction so a replay
    # failure leaves the (already committed) pending correction and the live wellness write intact.
    if outcome == "correction_requires_replay":
        async with best_effort_write(db, f"ekf head replay for user {user_id}"):
            await _acquire_ekf_chain_lock(db, user_id)
            await _replay_pending_head_correction(db, user_id, phase="post_classification")
    logger.info(
        "ekf_wellness_processing outcome=%s user=%s sample=%s model=%s",
        outcome, user_id, shadow_input.wellness_sample_id, shadow_input.content_hash[:8],
    )
    return outcome


async def _assimilate_or_classify(
    db: AsyncSession, user_id: int, shadow_input: WellnessShadowInput, observed_at: datetime
) -> str:
    """Assimilate a new wellness observation once, or classify a retry/correction. Runs inside the
    caller's shadow transaction + advisory lock; never opens its own. No predict step — a wellness
    assimilation is a pure measurement update on the current head belief."""
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
        correction_revision=0,
        replayed_revision=0,
    )

    # Insert-first claim: the partial unique index is the concurrency authority. If the INSERT
    # wins, this row's belief update IS the one assimilation. If it conflicts, an original
    # assimilation exists — classify the incoming content atomically, never re-assimilating.
    claim = (
        pg_insert(EkfShadowLog)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=["source_wellness_sample_id", "model_version"],
            index_where=text("source_wellness_sample_id IS NOT NULL AND event_type = 'update'"),
        )
        .returning(EkfShadowLog.id)
    )
    inserted_id = (await db.execute(claim)).scalar_one_or_none()
    if inserted_id is not None:
        return "assimilated"
    return await _classify_wellness_conflict(db, shadow_input, model_version)


async def _classify_wellness_conflict(
    db: AsyncSession, shadow_input: WellnessShadowInput, model_version: str
) -> str:
    """An original assimilation exists for this (sample, model). Advance ``latest_seen`` and, when
    the incoming content differs from the LAST SEEN content, register a new correction generation:
    bump ``correction_revision``, set the sticky flag, stamp first detection — atomically, in one
    UPDATE scoped to the original assimilation row (``event_type='update'``), never a replay row.
    Comparing against ``latest_seen`` (not ``assimilated``) makes A→B→A two generations. Never
    re-assimilates. Returns ``exact_retry`` or ``correction_requires_replay``.
    """
    changed = EkfShadowLog.latest_seen_content_hash != shadow_input.content_hash
    stmt = (
        sa_update(EkfShadowLog)
        .where(
            EkfShadowLog.source_wellness_sample_id == shadow_input.wellness_sample_id,
            EkfShadowLog.model_version == model_version,
            EkfShadowLog.event_type == EKF_EVENT_UPDATE,  # the original assimilation row only
        )
        .values(
            latest_seen_content_hash=shadow_input.content_hash,
            # A new generation only on genuinely changed content.
            correction_revision=EkfShadowLog.correction_revision + case((changed, 1), else_=0),
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


# ── Head-correction replay (a038) ───────────────────────────────────────────────────────────
# Repairs a corrected wellness observation that is still the effective EKF head by replaying the
# trusted update kernel from the ORIGINAL predecessor belief with the corrected content, appending
# an ``event_type='replay'`` row that supersedes the current head. Mid-history corrections (a later
# transition exists) stay pending — owned by a separate deterministic replay-engine mission.

_REPLAY_NONEVENT = frozenset({"no_pending"})  # not worth a log line


async def _replay_pending_head_correction(
    db: AsyncSession, user_id: int, *, phase: str
) -> str:
    """Replay the pending head correction if one is eligible; otherwise return why not.

    Runs inside the caller's transaction + advisory lock (never opens its own). Raises only on an
    *unexpected* error, so the caller's shadow transaction aborts. Outcomes: ``no_pending`` |
    ``completed`` | ``blocked_mid_history`` | ``blocked_missing_predecessor`` |
    ``source_revision_changed`` | ``claim_lost``.
    """
    head = await _load_latest_belief(db, user_id)  # the canonical per-user effective head
    original = await _pending_correction_on_head(db, user_id, head)
    if original is None:
        outcome = "blocked_mid_history" if await _any_pending_correction(db, user_id) else "no_pending"
        _log_replay(outcome, user_id=user_id, phase=phase)
        return outcome

    assert head is not None  # _pending_correction_on_head returns None when head is None
    assert original.source_wellness_sample_id is not None  # original is a wellness-lineage row
    claimed_revision = original.correction_revision
    source_id = original.source_wellness_sample_id

    # Eligibility: the source sample still exists and its CURRENT normalized content still equals
    # what we intend to replay (latest_seen). If it changed again, a newer generation is coming —
    # leave pending for that one.
    sample = (
        await db.execute(
            select(WellnessSample.id, WellnessSample.soreness).where(WellnessSample.id == source_id)
        )
    ).first()
    if sample is None:
        # Unreachable under the source FK CASCADE (deleting the sample removes its lineage), but
        # the inputs to reproduce the update are then unavailable.
        _log_replay("blocked_missing_predecessor", user_id=user_id, phase=phase, source_id=source_id)
        return "blocked_missing_predecessor"
    measurement = WellnessMeasurement(soreness=sample.soreness)
    if wellness_content_hash(measurement) != original.latest_seen_content_hash:
        _log_replay("source_revision_changed", user_id=user_id, phase=phase, source_id=source_id)
        return "source_revision_changed"

    # Predecessor belief = the row immediately before the ORIGINAL assimilation (what its prior
    # was). Rebuild from there — NOT from the current head/prior replay.
    base_row = await _belief_before(db, user_id, original.id)
    if base_row is None:
        _log_replay("blocked_missing_predecessor", user_id=user_id, phase=phase, source_id=source_id)
        return "blocked_missing_predecessor"

    base = _belief_from_row(base_row)
    obs = build_wellness_observation(measurement, default_parameters())
    posterior = base if obs is None else update(base, obs, default_parameters()).belief

    replay_values: dict[str, Any] = {
        "user_id": user_id,
        "belief_at": _naive(original.belief_at),
        "model_version": original.model_version,
        "event_type": EKF_EVENT_REPLAY,
        "mean_json": posterior.mean_map(),
        "variance_json": posterior.variance_map(),
        "covariance_json": posterior.cov_list(),
        "decision_impact": "none_shadow_only",
        "source_wellness_sample_id": source_id,
        "assimilated_content_hash": original.latest_seen_content_hash,
        "latest_seen_content_hash": original.latest_seen_content_hash,
        "correction_requires_replay": False,
        "correction_revision": claimed_revision,
        "supersedes_log_id": head.id,
        "replay_base_log_id": base_row.id,
    }
    # Insert-first claim against the replay idempotency index (source, model, revision): one
    # replay per correction generation, robust to a shifting head under retries.
    claim = (
        pg_insert(EkfShadowLog)
        .values(**replay_values)
        .on_conflict_do_nothing(
            index_elements=["source_wellness_sample_id", "model_version", "correction_revision"],
            index_where=text("source_wellness_sample_id IS NOT NULL AND event_type = 'replay'"),
        )
        .returning(EkfShadowLog.id)
    )
    replay_id = (await db.execute(claim)).scalar_one_or_none()
    if replay_id is None:
        # This generation is already replayed (belt-and-suspenders under the lock).
        _log_replay("claim_lost", user_id=user_id, phase=phase, source_id=source_id, revision=claimed_revision)
        return "claim_lost"

    await _reconcile_after_replay(db, original.id, claimed_revision, replay_id)
    _log_replay(
        "completed", user_id=user_id, phase=phase, source_id=source_id,
        model_version=original.model_version, revision=claimed_revision,
        superseded=head.id, replay_id=replay_id,
    )
    return "completed"


async def _reconcile_after_replay(
    db: AsyncSession, original_id: int, claimed_revision: int, replay_id: int
) -> None:
    """Advance ``replayed_revision`` to the replayed generation on the original row and clear the
    flag ONLY if no newer correction arrived (``correction_revision`` still == claimed). Guarded by
    ``replayed_revision < claimed`` so it is idempotent and never regresses. A newer generation
    (``correction_revision > claimed``) keeps the flag true for its own future replay."""
    stmt = (
        sa_update(EkfShadowLog)
        .where(
            EkfShadowLog.id == original_id,
            EkfShadowLog.event_type == EKF_EVENT_UPDATE,
            EkfShadowLog.replayed_revision < claimed_revision,
        )
        .values(
            replayed_revision=claimed_revision,
            replayed_at=func.now(),
            replayed_by_log_id=replay_id,
            correction_requires_replay=EkfShadowLog.correction_revision > claimed_revision,
        )
    )
    await db.execute(stmt)


async def _pending_correction_on_head(
    db: AsyncSession, user_id: int, head: EkfShadowLog | None
) -> EkfShadowLog | None:
    """The original wellness-assimilation row with an outstanding correction, IFF its lineage is
    the current effective head (lineage-aware: after a replay, the head is the replay row of the
    same source/model). None when the head is not a wellness lineage or has nothing outstanding."""
    if head is None or head.source_wellness_sample_id is None:
        return None
    if head.event_type == EKF_EVENT_UPDATE:
        original: EkfShadowLog | None = head
    elif head.event_type == EKF_EVENT_REPLAY:
        original = await _original_assimilation(
            db, head.source_wellness_sample_id, head.model_version
        )
    else:
        return None
    if original is None or not original.correction_requires_replay:
        return None
    if original.replayed_revision >= original.correction_revision:
        return None
    return original


async def _original_assimilation(
    db: AsyncSession, source_id: int, model_version: str
) -> EkfShadowLog | None:
    res = await db.execute(
        select(EkfShadowLog)
        .where(
            EkfShadowLog.source_wellness_sample_id == source_id,
            EkfShadowLog.model_version == model_version,
            EkfShadowLog.event_type == EKF_EVENT_UPDATE,
        )
        .limit(1)
    )
    return res.scalars().first()


async def _any_pending_correction(db: AsyncSession, user_id: int) -> bool:
    """Any original assimilation for this athlete still awaiting replay (possibly mid-history)."""
    count = await db.scalar(
        select(func.count())
        .select_from(EkfShadowLog)
        .where(
            EkfShadowLog.user_id == user_id,
            original_wellness_assimilation_clause(),
            EkfShadowLog.correction_requires_replay.is_(True),
            EkfShadowLog.replayed_revision < EkfShadowLog.correction_revision,
        )
    )
    return bool(count)


async def _belief_before(
    db: AsyncSession, user_id: int, before_id: int
) -> EkfShadowLog | None:
    """The belief row immediately preceding ``before_id`` on the athlete's chain — what the
    original assimilation used as its prior (per-user, model-agnostic, id-ordered)."""
    res = await db.execute(
        select(EkfShadowLog)
        .where(EkfShadowLog.user_id == user_id, EkfShadowLog.id < before_id)
        .order_by(EkfShadowLog.id.desc())
        .limit(1)
    )
    return res.scalars().first()


def _log_replay(
    outcome: str,
    *,
    user_id: int,
    phase: str,
    source_id: int | None = None,
    model_version: str | None = None,
    revision: int | None = None,
    superseded: int | None = None,
    replay_id: int | None = None,
) -> None:
    """Structured replay telemetry. No metrics runtime exists yet; the outcome taxonomy maps 1:1
    to future counters. Deliberately logs NO soreness/HRV values, content hashes, or belief
    payloads (privacy)."""
    if outcome in _REPLAY_NONEVENT:
        return
    logger.info(
        "ekf_wellness_replay phase=%s outcome=%s user_id=%s source_wellness_sample_id=%s "
        "model_version=%s correction_revision=%s superseded_log_id=%s replay_log_id=%s",
        phase, outcome, user_id, source_id, model_version, revision, superseded, replay_id,
    )


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
        "event_type": EKF_EVENT_UPDATE,
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
