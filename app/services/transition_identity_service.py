"""Register the running process's transition identity (P3b-1).

A state row's ``transition_identity`` is a foreign key to ``engine_transition_identities``,
so the component digests that explain it always exist beside it. The registry row is inserted
in the caller's transaction, before the state row that references it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.transition_identity import current_identity
from app.models.engine_transition_identity import EngineTransitionIdentity


async def ensure_current_identity(db: AsyncSession) -> str:
    """Make sure this process's identity is registered; return its digest.

    The common case is a read. The insert tolerates a concurrent registration of the same
    digest from another transaction.
    """
    identity = current_identity()
    exists = (
        await db.execute(
            select(EngineTransitionIdentity.digest).where(
                EngineTransitionIdentity.digest == identity.digest
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        await db.execute(
            pg_insert(EngineTransitionIdentity)
            .values(digest=identity.digest, components=identity.components)
            .on_conflict_do_nothing(index_elements=["digest"])
        )
    return identity.digest
