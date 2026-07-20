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
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.parameters import EngineParameters, default_parameters
from app.logic.benchmark_validity import get_validity_profile
from app.logic.ekf.belief import EKF_MODEL_VERSION, EkfBelief
from app.logic.ekf.events import (
    EKF_EVENT_PREDICT,
    EKF_EVENT_REPLAY,
    EKF_EVENT_UPDATE,
    original_wellness_assimilation_clause,
    wellness_replay_clause,
)
from app.logic.ekf.observation import (
    MappingSpec,
    Observation,
    UpdateResult,
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
_EKF_CHAIN_LOCK_NAMESPACE = 0x454B4631  # "EKF1"; 1_162_561_073 — within signed int4
_SUPPORTED_HEAD_REPLAY_MODEL_VERSIONS = frozenset({EKF_MODEL_VERSION})


class _AbortPreIngestReplay(RuntimeError):
    """Abort the shared pre-ingest shadow transaction without affecting the live wellness write."""


@dataclass(frozen=True)
class _ReplayLogRecord:
    """A structured replay outcome whose emission may need to wait for transaction commit."""

    outcome: str
    user_id: int
    phase: str
    source_id: int | None = None
    model_version: str | None = None
    revision: int | None = None
    superseded: int | None = None
    replay_id: int | None = None

    def emit(self, *, outcome: str | None = None) -> None:
        _log_replay(
            outcome or self.outcome,
            user_id=self.user_id,
            phase=self.phase,
            source_id=self.source_id,
            model_version=self.model_version,
            revision=self.revision,
            superseded=self.superseded,
            replay_id=self.replay_id,
        )


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


def _validate_wellness_shadow_input(
    user_id: int, shadow_input: WellnessShadowInput
) -> None:
    """Reject malformed immutable hand-offs before they can classify or replay chain state."""
    if shadow_input.user_id != user_id:
        raise ValueError("wellness shadow input user does not match EKF chain user")
    expected_hash = wellness_content_hash(shadow_input.measurement)
    if shadow_input.content_hash != expected_hash:
        raise ValueError("wellness shadow input content hash does not match its measurement")


async def _validate_wellness_source_tenant(
    db: AsyncSession, user_id: int, source_id: int
) -> None:
    source_user_id = await db.scalar(
        select(WellnessSample.user_id).where(WellnessSample.id == source_id)
    )
    if source_user_id != user_id:
        raise ValueError("wellness source does not belong to EKF chain user")


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
    pre_replay_logs: list[_ReplayLogRecord] = []
    async with best_effort_write(
        db, f"ekf wellness update for user {user_id}"
    ) as main_write:
        await _acquire_ekf_chain_lock(db, user_id)  # serialize the per-user belief chain
        _validate_wellness_shadow_input(user_id, shadow_input)
        await _validate_wellness_source_tenant(
            db, user_id, shadow_input.wellness_sample_id
        )
        # (1) Pre-existing pending head correction, in THIS transaction (see docstring). Blocked/
        # no_pending return normally; an unexpected error propagates and aborts the shadow txn.
        await _replay_pending_head_correction(
            db,
            user_id,
            phase="pre_ingest",
            current_input=shadow_input,
            deferred_logs=pre_replay_logs,
        )
        # (2) Assimilate or classify the current input.
        outcome = await _assimilate_or_classify(db, user_id, shadow_input, observed_at)
    _flush_deferred_replay_logs(pre_replay_logs, committed=main_write.committed)

    # (3) A newly detected correction: repair it in its own best-effort transaction so a replay
    # failure leaves the (already committed) pending correction and the live wellness write intact.
    if outcome == "correction_requires_replay":
        post_replay_logs: list[_ReplayLogRecord] = []
        async with best_effort_write(
            db, f"ekf head replay for user {user_id}"
        ) as replay_write:
            await _acquire_ekf_chain_lock(db, user_id)
            await _replay_pending_head_correction(
                db,
                user_id,
                phase="post_classification",
                deferred_logs=post_replay_logs,
            )
        _flush_deferred_replay_logs(post_replay_logs, committed=replay_write.committed)
        if replay_write.failed and not post_replay_logs:
            _log_replay("failed", user_id=user_id, phase="post_classification")
    logger.info(
        "ekf_wellness_processing outcome=%s user_id=%s source_wellness_sample_id=%s",
        outcome,
        user_id,
        shadow_input.wellness_sample_id,
    )
    return outcome


async def _assimilate_or_classify(
    db: AsyncSession, user_id: int, shadow_input: WellnessShadowInput, observed_at: datetime
) -> str:
    """Assimilate a new wellness observation once, or classify a retry/correction.

    Runs inside the caller's shadow transaction + advisory lock and never opens its own. A
    wellness assimilation is a pure measurement update on the current head belief: no predict
    step. Existing identities are classified before numerical work, which also lets a correction
    that removes the last EKF-consumed value (for example soreness -> NULL) retract the original
    update through head replay instead of disappearing as an unobserved no-op.
    """
    _validate_wellness_shadow_input(user_id, shadow_input)

    params = default_parameters()
    prior_row = await _load_latest_belief(db, user_id)
    model_version = prior_row.model_version if prior_row is not None else EKF_MODEL_VERSION

    original = await _original_assimilation(
        db,
        shadow_input.wellness_sample_id,
        model_version,
        user_id=user_id,
    )
    if original is not None:
        return await _classify_wellness_conflict(
            db,
            user_id,
            shadow_input,
            model_version,
            original_id=original.id,
        )

    obs = build_wellness_observation(shadow_input.measurement, params)
    if obs is None:
        return "skipped"  # no EKF-consumed value and no prior assimilation to retract

    state = await load_current_state(db, user_id)
    if state is None:
        return "skipped"
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

    # Insert-first claim remains the final duplicate-identity authority even though the shared
    # advisory lock makes the normal path conflict-free. If a non-participating legacy writer races
    # us, classify its winning original row rather than assimilating twice.
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
    return await _classify_wellness_conflict(
        db,
        user_id,
        shadow_input,
        model_version,
    )


async def _classify_wellness_conflict(
    db: AsyncSession,
    user_id: int,
    shadow_input: WellnessShadowInput,
    model_version: str,
    *,
    original_id: int | None = None,
) -> str:
    """Classify an existing original assimilation as an exact retry or a new correction.

    The update is scoped to the centralized ORIGINAL-assimilation predicate and, when available,
    its known primary key. Every genuinely changed latest-seen hash creates one monotonic
    correction generation and stamps that generation's detection time. Exact retries preserve the
    sticky pending state and never touch replay rows.
    """
    changed = EkfShadowLog.latest_seen_content_hash != shadow_input.content_hash
    identity_clause = (
        EkfShadowLog.id == original_id
        if original_id is not None
        else and_(
            EkfShadowLog.user_id == user_id,
            EkfShadowLog.source_wellness_sample_id == shadow_input.wellness_sample_id,
            EkfShadowLog.model_version == model_version,
        )
    )
    stmt = (
        sa_update(EkfShadowLog)
        .where(
            identity_clause,
            original_wellness_assimilation_clause(),
        )
        .values(
            latest_seen_content_hash=shadow_input.content_hash,
            correction_revision=EkfShadowLog.correction_revision + case((changed, 1), else_=0),
            correction_requires_replay=or_(EkfShadowLog.correction_requires_replay, changed),
            correction_detected_at=case(
                (changed, func.now()),
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

async def _replay_pending_head_correction(
    db: AsyncSession,
    user_id: int,
    *,
    phase: str,
    current_input: WellnessShadowInput | None = None,
    deferred_logs: list[_ReplayLogRecord] | None = None,
) -> str:
    """Replay one eligible pending head correction and return a stable outcome.

    The caller owns the transaction and shared advisory lock. Expected blocked/retry states return
    normally; unexpected failures are logged with the replay taxonomy and re-raised so
    ``best_effort_write`` rolls back the whole shadow transaction.
    """
    try:
        return await _replay_pending_head_correction_impl(
            db,
            user_id,
            phase=phase,
            current_input=current_input,
            deferred_logs=deferred_logs,
        )
    except _AbortPreIngestReplay:
        raise
    except Exception:
        record = _ReplayLogRecord(outcome="failed", user_id=user_id, phase=phase)
        if deferred_logs is None:
            record.emit()
        else:
            deferred_logs.append(record)
        raise


async def _replay_pending_head_correction_impl(
    db: AsyncSession,
    user_id: int,
    *,
    phase: str,
    current_input: WellnessShadowInput | None,
    deferred_logs: list[_ReplayLogRecord] | None,
) -> str:
    head = await _load_latest_belief(db, user_id)  # canonical per-user effective head
    original = await _pending_correction_on_head(db, user_id, head)
    if original is None:
        outcome = (
            "blocked_mid_history"
            if await _any_pending_correction(db, user_id)
            else "no_pending"
        )
        _log_replay(outcome, user_id=user_id, phase=phase)
        return outcome

    assert head is not None
    assert original.source_wellness_sample_id is not None
    assert original.latest_seen_content_hash is not None
    claimed_revision = original.correction_revision
    claimed_hash = original.latest_seen_content_hash
    source_id = original.source_wellness_sample_id

    if original.model_version not in _SUPPORTED_HEAD_REPLAY_MODEL_VERSIONS:
        _log_replay(
            "blocked_unsupported_version",
            user_id=user_id,
            phase=phase,
            source_id=source_id,
            model_version=original.model_version,
            revision=claimed_revision,
        )
        return "blocked_unsupported_version"

    # Re-read the durable source under the chain lock and prove that it still represents the
    # classified generation. The tenant predicate prevents a malformed cross-user FK from becoming
    # a replay input.
    sample = (
        await db.execute(
            select(WellnessSample.id, WellnessSample.soreness).where(
                WellnessSample.id == source_id,
                WellnessSample.user_id == user_id,
            )
        )
    ).first()
    if sample is None:
        _log_replay(
            "blocked_missing_predecessor",
            user_id=user_id,
            phase=phase,
            source_id=source_id,
            model_version=original.model_version,
            revision=claimed_revision,
        )
        return "blocked_missing_predecessor"

    measurement = WellnessMeasurement(soreness=sample.soreness)
    source_hash = wellness_content_hash(measurement)
    if source_hash != claimed_hash:
        _log_replay(
            "source_revision_changed",
            user_id=user_id,
            phase=phase,
            source_id=source_id,
            model_version=original.model_version,
            revision=claimed_revision,
        )
        current_input_explains_change = (
            current_input is not None
            and current_input.wellness_sample_id == source_id
            and current_input.content_hash == source_hash
        )
        if phase == "pre_ingest" and not current_input_explains_change:
            # Continuing with an unrelated current append would convert an eligible correction
            # whose source changed behind its classification into an unsupported mid-history case.
            raise _AbortPreIngestReplay("pending source revision changed outside current input")
        return "source_revision_changed"

    # Exact predecessor = the row immediately before the ORIGINAL assimilation. Later revisions
    # always rebuild from this row, never from the prior replay head.
    base_row = await _belief_before(db, user_id, original.id)
    if base_row is None:
        _log_replay(
            "blocked_missing_predecessor",
            user_id=user_id,
            phase=phase,
            source_id=source_id,
            model_version=original.model_version,
            revision=claimed_revision,
        )
        return "blocked_missing_predecessor"
    if base_row.model_version != original.model_version:
        _log_replay(
            "blocked_unsupported_version",
            user_id=user_id,
            phase=phase,
            source_id=source_id,
            model_version=original.model_version,
            revision=claimed_revision,
        )
        return "blocked_unsupported_version"

    params = default_parameters()
    base = _belief_from_row(base_row)
    obs = build_wellness_observation(measurement, params)
    if obs is None:
        # Retraction correction: the corrected source no longer contains an EKF-consumed value, so
        # the exact posterior is the predecessor belief itself.
        posterior = base
        diagnostics: dict[str, Any] = {}
    else:
        update_result = update(base, obs, params)
        posterior = update_result.belief
        diagnostics = _update_diagnostic_values(update_result, obs)

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
        "assimilated_content_hash": claimed_hash,
        "latest_seen_content_hash": claimed_hash,
        "correction_requires_replay": False,
        "correction_revision": claimed_revision,
        "supersedes_log_id": head.id,
        "replay_base_log_id": base_row.id,
        **diagnostics,
    }
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
        # The unique index is the final race/retry authority. Reconcile an already-durable,
        # canonical replay only when it is now the effective head and exactly matches this claim;
        # otherwise preserve the pending original and surface claim_lost.
        existing = await _replay_for_revision(
            db,
            source_id=source_id,
            model_version=original.model_version,
            revision=claimed_revision,
        )
        refreshed_head = await _load_latest_belief(db, user_id)
        if (
            existing is not None
            and refreshed_head is not None
            and refreshed_head.id == existing.id
            and existing.replay_base_log_id == base_row.id
            and existing.assimilated_content_hash == claimed_hash
        ):
            reconciled = await _reconcile_after_replay(
                db,
                original.id,
                claimed_revision,
                claimed_hash,
                existing.id,
            )
            outcome = "exact_retry" if reconciled else "source_revision_changed"
            record = _ReplayLogRecord(
                outcome=outcome,
                user_id=user_id,
                phase=phase,
                source_id=source_id,
                model_version=original.model_version,
                revision=claimed_revision,
                superseded=existing.supersedes_log_id,
                replay_id=existing.id,
            )
            if reconciled and deferred_logs is not None:
                deferred_logs.append(record)
            else:
                record.emit()
            if not reconciled and phase == "pre_ingest":
                raise _AbortPreIngestReplay(
                    "eligible pre-ingest replay could not reconcile its claimed revision"
                )
            return outcome

        _log_replay(
            "claim_lost",
            user_id=user_id,
            phase=phase,
            source_id=source_id,
            model_version=original.model_version,
            revision=claimed_revision,
        )
        if phase == "pre_ingest":
            raise _AbortPreIngestReplay("eligible pre-ingest replay claim was lost")
        return "claim_lost"

    reconciled = await _reconcile_after_replay(
        db,
        original.id,
        claimed_revision,
        claimed_hash,
        replay_id,
    )
    if not reconciled:
        record = _ReplayLogRecord(
            outcome="source_revision_changed",
            user_id=user_id,
            phase=phase,
            source_id=source_id,
            model_version=original.model_version,
            revision=claimed_revision,
            superseded=head.id,
            replay_id=replay_id,
        )
        if phase == "pre_ingest":
            record.emit()
            raise _AbortPreIngestReplay(
                "eligible pre-ingest replay could not reconcile its claimed revision"
            )
        if deferred_logs is None:
            record.emit()
        else:
            deferred_logs.append(record)
        return "source_revision_changed"

    record = _ReplayLogRecord(
        outcome="completed",
        user_id=user_id,
        phase=phase,
        source_id=source_id,
        model_version=original.model_version,
        revision=claimed_revision,
        superseded=head.id,
        replay_id=replay_id,
    )
    if deferred_logs is None:
        record.emit()
    else:
        deferred_logs.append(record)
    return "completed"


async def _reconcile_after_replay(
    db: AsyncSession,
    original_id: int,
    claimed_revision: int,
    claimed_hash: str,
    replay_id: int,
) -> bool:
    """Revision-guarded reconciliation of mutable metadata on the ORIGINAL row.

    The compare condition proves that the claimed generation/hash is still current. If a newer
    correction arrived, no metadata is advanced and its pending flag remains untouched. Numerical
    history is never updated.
    """
    stmt = (
        sa_update(EkfShadowLog)
        .where(
            EkfShadowLog.id == original_id,
            original_wellness_assimilation_clause(),
            EkfShadowLog.correction_revision == claimed_revision,
            EkfShadowLog.latest_seen_content_hash == claimed_hash,
            EkfShadowLog.replayed_revision < claimed_revision,
        )
        .values(
            assimilated_content_hash=claimed_hash,
            replayed_revision=claimed_revision,
            replayed_at=func.now(),
            replayed_by_log_id=replay_id,
            correction_requires_replay=False,
        )
        .returning(EkfShadowLog.id)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def _replay_for_revision(
    db: AsyncSession,
    *,
    source_id: int,
    model_version: str,
    revision: int,
) -> EkfShadowLog | None:
    result = await db.execute(
        select(EkfShadowLog)
        .where(
            wellness_replay_clause(),
            EkfShadowLog.source_wellness_sample_id == source_id,
            EkfShadowLog.model_version == model_version,
            EkfShadowLog.correction_revision == revision,
        )
        .limit(1)
    )
    return result.scalars().first()


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
            db,
            head.source_wellness_sample_id,
            head.model_version,
            user_id=user_id,
        )
    else:
        return None
    if original is None or not original.correction_requires_replay:
        return None
    if original.replayed_revision >= original.correction_revision:
        return None
    return original


async def _original_assimilation(
    db: AsyncSession,
    source_id: int,
    model_version: str,
    *,
    user_id: int | None = None,
) -> EkfShadowLog | None:
    stmt = select(EkfShadowLog).where(
        original_wellness_assimilation_clause(),
        EkfShadowLog.source_wellness_sample_id == source_id,
        EkfShadowLog.model_version == model_version,
    )
    if user_id is not None:
        stmt = stmt.where(EkfShadowLog.user_id == user_id)
    result = await db.execute(stmt.limit(1))
    return result.scalars().first()


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


def _flush_deferred_replay_logs(
    records: list[_ReplayLogRecord], *, committed: bool
) -> None:
    """Emit durability-sensitive outcomes only after the owning transaction exits.

    A replay that was numerically computed and staged but rolled back is a ``failed`` outcome,
    never ``completed``. This also covers a later current-event failure in the shared pre-ingest
    transaction and a commit failure after the replay body returned normally.
    """
    for record in records:
        record.emit(outcome=record.outcome if committed else "failed")


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
        "decision_impact": "none_shadow_only",
        **_update_diagnostic_values(res, obs),
    }


def _update_diagnostic_values(result: UpdateResult, obs: Observation) -> dict[str, Any]:
    """Persist the same numerical diagnostics for original and replayed measurement updates."""
    return {
        "benchmark_code": obs.benchmark_code,
        "innovation": float(np.mean(result.innovation)),
        "gain_norm": result.gain_norm,
        "trace_pre": result.trace_pre,
        "trace_post": result.trace_post,
        "nis": result.nis,
        "n_obs": len(obs.axis_keys),
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
