"""Data-access boundary for athlete-profile records.

See ``athlete_context_repository.py``. The repository owns persistence
mechanics only — it reads and writes ORM rows and contains no domain logic
(no goal resolution, dose calculation, or column mapping). Callers apply any
create-if-missing or field-mapping behavior in the service/router layer.

The interface grows one migrated query at a time; methods are added only as
call sites are routed through them, so every method here has real callers.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import AthleteProfile


class AthleteProfileRepository:
    """Async persistence boundary over a provided :class:`AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_user(self, user_id: int) -> AthleteProfile | None:
        """The ``AthleteProfile`` for ``user_id``, or ``None`` if the athlete
        has no profile row yet."""
        result = await self.session.execute(
            select(AthleteProfile).where(AthleteProfile.user_id == user_id).limit(1)
        )
        return result.scalars().first()
