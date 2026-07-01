"""Data-access boundary for athlete-context records.

See ``CONTEXT.md``. The repository owns persistence mechanics only — it reads
and writes ORM rows and contains no domain logic (no dose calculation, state
updates, or benchmark mapping). Callers convert ORM rows to domain vectors via
``app.engine.state_bridge`` in the service/engine layer.

The interface grows one migrated query at a time; methods are added only as
call sites are routed through them, so every method here has real callers.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.athlete_state import AthleteState


class AthleteContextRepository:
    """Async persistence boundary over a provided :class:`AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ----- State -----
    async def get_latest_state(self, user_id: int) -> AthleteState | None:
        """Most recent ``AthleteState`` for ``user_id``, or ``None`` if the
        athlete has no recorded state yet."""
        result = await self.session.execute(
            select(AthleteState)
            .where(AthleteState.user_id == user_id)
            .order_by(AthleteState.timestamp.desc())
            .limit(1)
        )
        return result.scalars().first()
