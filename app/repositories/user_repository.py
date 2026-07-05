"""Data-access boundary for user records.

See ``athlete_context_repository.py``. The repository owns persistence
mechanics only — it reads ORM rows and contains no domain logic (no password
hashing, verification, or email normalization). Callers pass the exact email
string to match, so any case-folding stays a caller decision and behavior is
identical across call sites.

The interface grows one migrated query at a time; methods are added only as
call sites are routed through them, so every method here has real callers.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    """Async persistence boundary over a provided :class:`AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_email(self, email: str) -> User | None:
        """The ``User`` whose ``email`` equals the given string exactly, or
        ``None`` if none matches. The caller is responsible for any
        normalization (e.g. lower-casing) before calling."""
        result = await self.session.execute(
            select(User).where(User.email == email)
        )
        return result.scalars().first()
