"""Which recovery and personalization shadow rows count as current evidence (P7).

P7b writes these logs as correction chains (a062): rows that compute a shadow output, and rows
that record the recovery shadow declining to (``SKIPPED_NOT_AS_OF_DAY``). This reader ships in
P7a, ahead of those writers, so that a rollback from P7b to P7a never shows a skip, a corrected
row or a stale row as current output.

A row counts only when all of these hold:

* **It is computed evidence**, recognized positively: ``decision_impact == RECORDED``. A skip,
  or any marker a later release adds, is not.
* **It is its sample's latest row**, across every model version (and, for personalization, of
  its parameter). A later row for the same sample, whatever its chain or kind, retires it, so a
  skip written under one model version also retires another version's computation.
* **Its inputs are still the sample's committed values**: its ``content_hash`` equals the hash
  of the sample as it stands now. This holds even when a newer write's own shadow failed.
* **No damaged-state skip of the athlete is newer** (recovery only, INT-15 S3). A
  ``STATE_SKIPS`` row says the athlete's current state could not be read; until a computed row
  follows it, an earlier day's computation is not presented as current either.
* **An unlinked row counts only while no linked row of the athlete is newer** in that log. An
  unlinked row names no sample, so nothing can retire it individually: one written before P7b
  stops counting once P7 evidence follows it, while one P7a writes after a rollback from P7b is
  newer than every linked row and counts until P7b writes again.

All of this is judged in one statement, so a row's place in its chain and its sample's values
come from the same database snapshot, and candidates are examined newest first until one counts
or none are left: no page size decides what is evidence.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot
from app.models.personalization_shadow import PersonalizationShadowLog
from app.models.recovery_shadow import RecoveryShadowLog
from app.repositories.athlete_context_repository import AthleteContextRepository, WellnessInputs

# ``decision_impact`` of a row that computed a shadow output, and of a recovery row that
# recorded declining to (the sample was not of the as-of UTC day).
RECORDED = "none_shadow_only"
SKIPPED_NOT_AS_OF_DAY = "skipped_not_as_of_day"
# INT-15 S3: the recovery shadow declined because the athlete's CURRENT state could not be decoded
# (never reconstructed). Unlike a not-as-of-day skip, this says something about the athlete now,
# so while one is the athlete's newest recovery row, no earlier computation counts as current.
STATE_SKIPS: frozenset[str] = frozenset(
    {"skipped_missing", "skipped_incomplete", "skipped_malformed", "skipped_future_version"}
)

_HASH_POLICY = "wellness_shadow_inputs_v1"

ShadowRow = RecoveryShadowLog | PersonalizationShadowLog


def inputs_hash(snapshot: WellnessTelemetrySnapshot) -> str:
    """SHA-256 of the five values both shadows consume, canonically encoded (None kept)."""
    body = json.dumps({"policy": _HASH_POLICY, **asdict(snapshot)}, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def snapshot_of(inputs: WellnessInputs) -> WellnessTelemetrySnapshot:
    return WellnessTelemetrySnapshot(
        sleep_hours=inputs.sleep_hours, hrv_ms=inputs.hrv_ms, resting_hr=inputs.resting_hr,
        soreness=inputs.soreness, mood=inputs.mood,
    )


def _candidates(model: type[ShadowRow], user_id: int) -> Any:
    later = aliased(model)
    same_sample: Any = later.source_wellness_sample_id == model.source_wellness_sample_id
    if model is PersonalizationShadowLog:  # personalization rows are scoped by parameter too
        parameter: Any = getattr(later, "parameter")  # noqa: B009
        same_sample = same_sample & (parameter == PersonalizationShadowLog.parameter)
    linked = aliased(model)
    newer_state_skip: Any = True
    if model is RecoveryShadowLog:
        skip = aliased(model)
        newer_state_skip = ~select(skip.id).where(
            skip.user_id == user_id, skip.decision_impact.in_(STATE_SKIPS), skip.id > model.id
        ).exists()
    return (
        select(model)
        .where(
            model.user_id == user_id,
            model.decision_impact == RECORDED,
            newer_state_skip,
            (
                model.source_wellness_sample_id.is_not(None)
                & ~select(later.id).where(same_sample, later.id > model.id).exists()
            )
            | (
                model.source_wellness_sample_id.is_(None)
                & ~select(linked.id).where(linked.user_id == user_id,
                                           linked.source_wellness_sample_id.is_not(None),
                                           linked.id > model.id).exists()
            ),
        )
        .order_by(model.id.desc())
    )


async def latest_current_evidence(
    db: AsyncSession, model: type[ShadowRow], user_id: int
) -> ShadowRow | None:
    """The athlete's newest row of ``model`` that counts as current evidence, or None."""

    def counts(row: ShadowRow, inputs: WellnessInputs | None) -> bool:
        if row.source_wellness_sample_id is None:
            return True
        return (inputs is not None and inputs.user_id == user_id
                and inputs_hash(snapshot_of(inputs)) == row.content_hash)

    return await AthleteContextRepository(db).first_with_wellness_inputs(
        _candidates(model, user_id), model.source_wellness_sample_id, counts)
