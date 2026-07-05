"""Data-access boundary for weak-point records.

See ``athlete_context_repository.py``. The repository owns persistence
mechanics only — it reads ORM rows and contains no domain logic (no
confidence aggregation, resolution rules, or bias-signal shaping). Callers
apply filtering intent and mutations in the service/router layer.

The interface grows one migrated query at a time; methods are added only as
call sites are routed through them, so every method here has real callers.
"""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.weak_point import WeakPoint


class WeakPointRepository:
    """Async persistence boundary over a provided :class:`AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(
        self, user_id: int, *, active_only: bool = True
    ) -> Sequence[WeakPoint]:
        """All weak-point rows owned by ``user_id``. When ``active_only`` is
        true (the default), only unresolved rows (``resolved_at IS NULL``) are
        returned."""
        stmt = select(WeakPoint).where(WeakPoint.user_id == user_id)
        if active_only:
            stmt = stmt.where(WeakPoint.resolved_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_for_user(
        self, weak_point_id: int, user_id: int
    ) -> WeakPoint | None:
        """The weak-point row with ``weak_point_id`` owned by ``user_id``, or
        ``None`` if no such row exists (missing or owned by another user)."""
        result = await self.session.execute(
            select(WeakPoint).where(
                WeakPoint.id == weak_point_id,
                WeakPoint.user_id == user_id,
            )
        )
        return result.scalars().first()
