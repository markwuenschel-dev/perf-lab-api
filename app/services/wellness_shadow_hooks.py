"""The shadows that run after a wellness sample commits, for every source (P7).

One entry point for the manual check-in, the Oura sync and the phone ingest, so no ingest path
can quietly skip the shadow loops again (the wiring audit of 2026-10-03 found both wearable paths
did). Per source:

| shadow          | manual                      | Oura / phone ingest                   |
|-----------------|-----------------------------|---------------------------------------|
| recovery        | as-of UTC day only, else an explicit skip row                       |
| personalization | every day                   | every day                             |
| EKF wellness    | assimilates soreness        | explicit skip: no soreness is supplied |

Each shadow runs in its own best-effort session (F1) and never affects the caller. The caller
must have COMMITTED the sample, and calls this while the sample row is still loaded: everything
is snapshotted here, before any shadow runs (AUD-C24).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.ekf.wellness_input import build_wellness_shadow_input
from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot
from app.models.wellness import WellnessSample
from app.services import (
    ekf_shadow_service,
    personalization_shadow_service,
    recovery_shadow_service,
)
from app.services.wellness_shadow_chain import WellnessSampleRef

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class WellnessShadowOutcomes:
    recovery: str
    personalization: str
    ekf: str


def sample_ref(sample: WellnessSample) -> WellnessSampleRef:
    """The plain-data identity of a committed sample (this module is the ORM boundary)."""
    return WellnessSampleRef(sample_id=sample.id, user_id=sample.user_id, sample_date=sample.date)


async def run_wellness_shadows(
    db: AsyncSession,
    sample: WellnessSample,
    *,
    as_of: datetime | None = None,
    assimilate_soreness: bool,
) -> WellnessShadowOutcomes:
    """Run every wellness shadow for one committed sample. Never raises to the caller.

    ``as_of`` is the instant the shadows are evaluated at (now, unless a seeder replays history);
    the recovery shadow records only for a sample of its UTC day. ``assimilate_soreness`` is
    False for wearable sources, which supply no soreness: the EKF records an explicit skip.
    """
    when = (as_of or _now()).astimezone(UTC)
    ref = sample_ref(sample)
    source = sample.source
    snapshot = WellnessTelemetrySnapshot.from_sample(sample)
    ekf_input = (
        build_wellness_shadow_input(ref.user_id, ref.sample_id, sample.soreness)
        if assimilate_soreness else None
    )

    recovery = await recovery_shadow_service.record_recovery_shadow(db, ref, snapshot, as_of=when)
    personalization = await personalization_shadow_service.record_personalization_shadow(
        db, ref, snapshot
    )
    if ekf_input is None:
        logger.info("ekf wellness skipped for sample %s (source %s supplies no soreness)",
                    ref.sample_id, source)
        ekf = "skipped_no_soreness_source"
    else:
        ekf = await ekf_shadow_service.record_ekf_wellness_observation(
            db, ref.user_id, ekf_input, observed_at=when
        )
    return WellnessShadowOutcomes(recovery=recovery, personalization=personalization, ekf=ekf)
