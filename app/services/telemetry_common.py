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

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@asynccontextmanager
async def best_effort_write(db: AsyncSession, description: str) -> AsyncIterator[None]:
    """Commit the telemetry rows staged in the body; on ANY failure log and roll back.

    A telemetry failure must never propagate to the caller's request. The body stages
    rows (``db.add(...)``); this commits on exit. If the body or the commit raises, it is
    logged with traceback and rolled back; a rollback that itself fails is also swallowed.
    """
    try:
        yield
        await db.commit()
    except Exception:
        logger.warning("telemetry write failed (%s)", description, exc_info=True)
        try:
            await db.rollback()
        except Exception:
            logger.warning(
                "rollback after telemetry failure also failed (%s)", description, exc_info=True
            )
