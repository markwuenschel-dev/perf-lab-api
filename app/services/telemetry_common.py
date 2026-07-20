"""Shared best-effort persistence for research/shadow telemetry writers.

The recovery-clearance shadow log and the prescription-decision telemetry are both
side-channel writes that must NEVER break the request that triggered them. This wraps
the common "commit; on any failure log-and-rollback" dance so each writer stays
declarative and the swallow behavior is defined in exactly one place.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class BestEffortWriteStatus:
    """Observable result of a best-effort transaction after its context exits.

    Existing callers may ignore the yielded object. Callers whose own structured outcome means
    "durably persisted" can inspect ``committed`` before emitting it, avoiding a false success log
    when the body completed but the commit or a later operation in the same transaction failed.
    """

    committed: bool = False
    failed: bool = False


@asynccontextmanager
async def best_effort_write(
    db: AsyncSession, description: str
) -> AsyncIterator[BestEffortWriteStatus]:
    """Commit telemetry staged in the body; on ANY failure log, roll back, and suppress it.

    A telemetry failure must never propagate to the caller's request. The yielded status is
    finalized only after commit/rollback, so durability-sensitive callers can distinguish a
    committed write from a body that merely reached its end.
    """
    status = BestEffortWriteStatus()
    try:
        yield status
        await db.commit()
        status.committed = True
    except Exception:
        status.failed = True
        logger.warning("telemetry write failed (%s)", description, exc_info=True)
        try:
            await db.rollback()
        except Exception:
            logger.warning(
                "rollback after telemetry failure also failed (%s)", description, exc_info=True
            )
