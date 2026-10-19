"""Resolve and set an athlete's calendar (B1). The only reader and writer of
``athlete_profiles.timezone``.

* :func:`calendar_for` / :func:`calendars_for` — every "today" in the app resolves through here,
  request or not (the wearable sync CLI has no request and still has an athlete).
* :func:`capture_browser_zone` — the browser's zone, written only while the athlete's zone is
  unknown. A later login, another device or a trip never replaces a known zone.
* :func:`choose_zone` — the athlete's own choice in Settings; replaces whatever was there.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.athlete_calendar import (
    UNKNOWN,
    AthleteCalendar,
    InvalidTimezone,
    TimezoneSource,
    validate_timezone,
)
from app.models.user import AthleteProfile

logger = logging.getLogger(__name__)


def calendar_from_stored(user_id: int, stored: str | None) -> AthleteCalendar:
    """The calendar for a zone already read from ``athlete_profiles.timezone`` (a reader that
    must not open its own query, e.g. the wearable sync's snapshot)."""
    if stored is None:
        return UNKNOWN
    try:
        return AthleteCalendar(validate_timezone(stored))
    except InvalidTimezone:
        # Written validated, so this means zoneinfo lost the name (a tzdata change) or the row
        # was edited by hand. Unknown keeps the grace day: the safe reading of a bad zone.
        logger.warning("athlete %s has an unloadable timezone %r; using UTC", user_id, stored)
        return UNKNOWN


async def calendar_for(db: AsyncSession, user_id: int) -> AthleteCalendar:
    stored = await db.scalar(
        select(AthleteProfile.timezone).where(AthleteProfile.user_id == user_id)
    )
    return calendar_from_stored(user_id, stored)


async def calendars_for(db: AsyncSession, user_ids: Iterable[int]) -> dict[int, AthleteCalendar]:
    """One query for many athletes; an athlete with no profile row is unknown."""
    ids = sorted(set(user_ids))
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(AthleteProfile.user_id, AthleteProfile.timezone).where(
                AthleteProfile.user_id.in_(ids)
            )
        )
    ).all()
    found = {uid: calendar_from_stored(uid, tz) for uid, tz in rows}
    return {uid: found.get(uid, UNKNOWN) for uid in ids}


async def _ensure_profile(db: AsyncSession, user_id: int) -> None:
    """The profile row exists afterwards; a concurrent creator is not an error."""
    await db.execute(
        pg_insert(AthleteProfile)
        .values(user_id=user_id)
        .on_conflict_do_nothing(index_elements=[AthleteProfile.user_id])
    )


async def capture_browser_zone(db: AsyncSession, user_id: int, name: str) -> bool:
    """Store the browser's zone iff the athlete's is unknown. True when it was stored.

    One conditional UPDATE, so two tabs capturing at once cannot both win, and a zone chosen in
    Settings between the read and the write is never overwritten. Commits.
    """
    zone = validate_timezone(name)
    await _ensure_profile(db, user_id)
    result = await db.execute(
        update(AthleteProfile)
        .where(AthleteProfile.user_id == user_id, AthleteProfile.timezone.is_(None))
        .values(timezone=zone, timezone_source="browser")
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return bool(getattr(result, "rowcount", 0))


async def choose_zone(
    db: AsyncSession, user_id: int, name: str, source: TimezoneSource = "athlete"
) -> str:
    """Set the athlete's zone to ``name`` (validated). Commits; returns the stored name."""
    zone = validate_timezone(name)
    await _ensure_profile(db, user_id)
    await db.execute(
        update(AthleteProfile)
        .where(AthleteProfile.user_id == user_id)
        .values(timezone=zone, timezone_source=source)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return zone
