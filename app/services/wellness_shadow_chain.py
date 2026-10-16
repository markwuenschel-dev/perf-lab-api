"""Correction chains for the recovery and personalization shadow logs (P7, a062).

Every wellness write (manual check-in, Oura sync, phone ingest) runs both shadows after it
commits. A day's rows form a chain per (sample, model[, parameter]): the root, then one row per
change, each superseding the last. The schema enforces the chain's shape; this module decides
what to append. Which rows count as current output is the reader's business
(``shadow_evidence``), and it does not trust the head blindly: a row counts only while it is
its sample's latest row and its inputs are still the sample's committed values.

* **Unchanged adds nothing.** A head with this inputs hash satisfies a skip request, and
  satisfies a computed request only if the head is itself computed. So a re-sync on a later day
  never appends merely because the clock moved, while a sample that was skipped before its day
  gets its computation once it becomes eligible. Exactly-once is judged against the head, not
  against every hash the chain has held, so a value corrected and then corrected back appends.
* **Publication is checked against the source at the moment it lands.** Each hook takes a
  transaction-scoped advisory lock on its sample (hooks for one sample run one at a time), reads
  the committed values early to skip stale work cheaply, and re-reads them ``FOR SHARE``
  immediately before inserting. That share lock is held to commit, so no update to the sample
  can commit between the check and the publication. A hook whose snapshot no longer matches
  writes nothing: a newer write committed since, and its own hook owns the chain.
* **A key conflict is re-read, not lost.** Writers that hold the lock cannot collide, but an
  insert that does is rolled back to its savepoint, the head re-read, and the decision retried.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot
from app.models.personalization_shadow import PersonalizationShadowLog
from app.models.recovery_shadow import RecoveryShadowLog
from app.repositories.athlete_context_repository import AthleteContextRepository, WellnessInputs
from app.services.shadow_evidence import RECORDED, inputs_hash, snapshot_of

logger = logging.getLogger(__name__)

# "WSH1"; within signed int4. Distinct from the EKF (0x454B4631) and state-chain (0x53544131)
# namespaces, so these locks never queue behind theirs.
SHADOW_CHAIN_LOCK_NAMESPACE = 0x57534831
_MAX_ATTEMPTS = 3

ShadowRow = RecoveryShadowLog | PersonalizationShadowLog


@dataclass(frozen=True, slots=True)
class WellnessSampleRef:
    """Which committed sample a shadow describes: plain data, taken with the snapshot."""

    sample_id: int
    user_id: int
    sample_date: date


@dataclass(frozen=True, slots=True)
class _Head:
    id: int
    content_hash: str
    decision_impact: str


def is_as_of_day(ref: WellnessSampleRef, as_of: datetime) -> bool:
    """True when the sample is of the as-of instant's UTC calendar day."""
    return ref.sample_date == as_of.astimezone(UTC).date()


def _matches(inputs: WellnessInputs | None, ref: WellnessSampleRef,
             snapshot: WellnessTelemetrySnapshot) -> bool:
    return inputs is not None and inputs.user_id == ref.user_id and snapshot_of(inputs) == snapshot


async def lock_current_inputs(
    tdb: AsyncSession, ref: WellnessSampleRef, snapshot: WellnessTelemetrySnapshot
) -> str | None:
    """Lock the sample's chains; return the inputs hash if ``snapshot`` is still committed.

    MUST be the first chain operation in the telemetry transaction. ``None`` means write
    nothing: the sample is gone, belongs to someone else, or has newer committed values."""
    await tdb.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :sid)"),
        {"ns": SHADOW_CHAIN_LOCK_NAMESPACE, "sid": ref.sample_id},
    )
    current = (await AthleteContextRepository(tdb).wellness_inputs([ref.sample_id])).get(ref.sample_id)
    if not _matches(current, ref, snapshot):
        logger.info("shadow chain: sample %s is gone, foreign, or changed since this snapshot; "
                    "nothing written", ref.sample_id)
        return None
    return inputs_hash(snapshot)


async def _head(
    tdb: AsyncSession, model: type[ShadowRow], ref: WellnessSampleRef, model_version: str,
    parameter: str | None,
) -> _Head | None:
    successor = aliased(model)
    query = (
        select(model.id, model.content_hash, model.decision_impact)
        .where(model.source_wellness_sample_id == ref.sample_id,
               model.model_version == model_version,
               ~select(successor.id).where(successor.supersedes_id == model.id).exists())
    )
    if parameter is not None and model is PersonalizationShadowLog:
        query = query.where(PersonalizationShadowLog.parameter == parameter)
    found = (await tdb.execute(query)).one_or_none()
    if found is None:
        return None
    return _Head(id=found.id, content_hash=str(found.content_hash),
                 decision_impact=found.decision_impact)


def _satisfies(head: _Head | None, digest: str, kind: str) -> bool:
    """A head with this hash satisfies any request, except that a computed request needs a
    computed head (a skip whose day has come gets its computation)."""
    if head is None or head.content_hash != digest:
        return False
    return kind != RECORDED or head.decision_impact == RECORDED


async def head_holds(
    tdb: AsyncSession, model: type[ShadowRow], ref: WellnessSampleRef, model_version: str,
    digest: str, kind: str, parameter: str | None = None,
) -> bool:
    """True when the chain already holds what this request would write: nothing to compute."""
    return _satisfies(await _head(tdb, model, ref, model_version, parameter), digest, kind)


async def append_to_chain(
    tdb: AsyncSession,
    ref: WellnessSampleRef,
    snapshot: WellnessTelemetrySnapshot,
    build: Callable[[], ShadowRow],
    *,
    model: type[ShadowRow],
    model_version: str,
    kind: str,
    parameter: str | None = None,
) -> str:
    """Append ``build()``'s row (of ``kind``) to its chain unless the head already satisfies it.

    Returns ``"appended"``, ``"unchanged"`` or ``"stale"``; a third key conflict in a row
    raises, and the caller's best-effort write logs it.
    """
    digest = inputs_hash(snapshot)
    repo = AthleteContextRepository(tdb)
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        head = await _head(tdb, model, ref, model_version, parameter)
        if _satisfies(head, digest, kind):
            return "unchanged"
        # The source check that publication depends on, held FOR SHARE until this transaction
        # commits: an update to the sample now waits for this row to land (or roll back).
        if not _matches(await repo.lock_wellness_inputs(ref.sample_id), ref, snapshot):
            logger.info("shadow chain: sample %s changed before publication; nothing written",
                        ref.sample_id)
            return "stale"
        row: ShadowRow = build()
        row.source_wellness_sample_id = ref.sample_id
        row.content_hash = digest
        row.supersedes_id = None if head is None else head.id
        try:
            async with tdb.begin_nested():
                tdb.add(row)
                await tdb.flush()
            return "appended"
        except IntegrityError:
            # The savepoint's rollback expunged the row; re-read the head and decide again.
            logger.info("shadow chain: key conflict on sample %s (attempt %d); re-reading head",
                        ref.sample_id, attempt)
    raise RuntimeError(f"shadow chain for sample {ref.sample_id}: {_MAX_ATTEMPTS} key conflicts")
