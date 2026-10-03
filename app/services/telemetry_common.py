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
    """The telemetry transaction's own session, and its outcome once the context exits.

    ``db`` is a FRESH session (F1), opened on the caller's engine and owned by this context:
    every telemetry read and write in the body goes through it. The caller's session is used
    only to find the engine — never read from, written to, committed or rolled back here.

    Callers whose own structured outcome means "durably persisted" can inspect ``committed``
    before emitting it, avoiding a false success log when the body completed but the commit
    failed.
    """

    db: AsyncSession
    committed: bool = False
    failed: bool = False


@asynccontextmanager
async def best_effort_write(
    db: AsyncSession, description: str
) -> AsyncIterator[BestEffortWriteStatus]:
    """Run telemetry in its OWN session and transaction; on ANY failure log, roll back, suppress.

    F1: this used to commit and roll back the CALLER's session — so a shadow write could
    commit the request's staged work, or roll it back and expire its ORM objects. Now:

    * the body gets ``status.db``, a new session on the caller's engine (the same database in
      production and under test — tests inject the engine through ``get_db``);
    * only that session is committed or rolled back, and it is closed on exit;
    * the caller's session is never touched, so it stays usable whatever happens here.

    Precondition (verified at every call site): the caller has COMMITTED the primary write the
    telemetry describes, so this separate transaction can see the rows it references.
    Inputs must be immutable snapshots, never ORM instances bound to the caller's session.
    """
    telemetry_db = AsyncSession(bind=db.bind, expire_on_commit=False, autoflush=False)
    status = BestEffortWriteStatus(db=telemetry_db)
    try:
        yield status
        await telemetry_db.commit()
        status.committed = True
    except Exception:
        status.failed = True
        logger.warning("telemetry write failed (%s)", description, exc_info=True)
        try:
            await telemetry_db.rollback()
        except Exception:
            logger.warning(
                "rollback after telemetry failure also failed (%s)", description, exc_info=True
            )
    finally:
        # Even closing must not reach the caller: it returns the connection to the pool.
        try:
            await telemetry_db.close()
        except Exception:
            logger.warning("closing telemetry session failed (%s)", description, exc_info=True)
