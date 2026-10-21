"""The athlete's calendar: which day "today" is for one athlete (B1).

Instants are stored in UTC and stay that way. A *day* is a calendar date in the athlete's own
calendar: a planned session's ``scheduled_date``, the day a workout instant falls on, "today".
This module is the one place that turns an instant into such a day.

* A **known** zone is an IANA name the athlete (or their browser, once) gave. ``UTC`` is a
  known zone like any other.
* An **unknown** zone (NULL in ``athlete_profiles.timezone``) falls back to UTC, and keeps the
  missed-session grace day: the UTC day can run up to a day ahead of the athlete's own, so a
  session must not be called missed on a guess.

DST is zoneinfo's job, never offset arithmetic: a day is ``instant.astimezone(zone).date()``,
and the start of a day is local midnight resolved by zoneinfo, which is 23 or 25 hours from the
next one on a transition day.

Provider-supplied daily dates (an Oura ``day``) and client-supplied dates (a check-in ``date``)
are calendar dates already; nothing here converts them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

TimezoneSource = Literal["browser", "athlete"]

#: The missed-session grace while the zone is unknown (missed_session_service.GRACE_DAYS).
UNKNOWN_ZONE_GRACE_DAYS = 1
#: A known zone needs no grace: its "today" is the athlete's own.
KNOWN_ZONE_GRACE_DAYS = 0

_MAX_NAME = 64
#: Can be listed by zoneinfo but are not places anyone lives: "Factory" is tzdata's placeholder
#: zone, and on a system tz database (Linux, the production image) ``available_timezones()`` also
#: lists ``localtime`` (the host's own zone, a symlink) and ``posixrules``. Refused by name, so the
#: answer does not depend on which tz database the platform has.
_NOT_A_PLACE = frozenset({"Factory", "localtime", "posixrules"})


class InvalidTimezone(ValueError):
    """Not an IANA zone name this server's zoneinfo knows."""


@lru_cache(maxsize=1)
def _known_names() -> frozenset[str]:
    return frozenset(available_timezones())


def validate_timezone(name: str) -> str:
    """The name, if it is an IANA zone zoneinfo knows; else ``InvalidTimezone``.

    Membership in ``available_timezones()`` rather than "ZoneInfo() did not raise": ZoneInfo
    also opens file paths and names like ``localtime`` that are not zones a browser reports.
    """
    candidate = name.strip()
    if (not candidate or len(candidate) > _MAX_NAME or candidate in _NOT_A_PLACE
            or candidate not in _known_names()):
        raise InvalidTimezone(f"unknown IANA timezone {name!r}")
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError) as exc:  # pragma: no cover - listed but unloadable
        raise InvalidTimezone(f"unknown IANA timezone {name!r}") from exc
    return candidate


def _as_utc(instant: datetime) -> datetime:
    """Naive datetimes are UTC in this codebase (stored columns are naive UTC)."""
    return instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant.astimezone(UTC)


def _zone(name: str | None) -> ZoneInfo:
    return ZoneInfo(name) if name else ZoneInfo("UTC")


def utc_now() -> datetime:
    """The clock every default "today" reads (tests replace it; nothing else should)."""
    return datetime.now(UTC)


def athlete_today(zone: str | None, now: datetime) -> date:
    """The athlete's calendar date at the instant ``now``. ``zone`` None = unknown → UTC."""
    return _as_utc(now).astimezone(_zone(zone)).date()


@dataclass(frozen=True, slots=True)
class AthleteCalendar:
    """One athlete's calendar. ``zone_name`` None means unknown (UTC fallback)."""

    zone_name: str | None = None

    @property
    def known(self) -> bool:
        return self.zone_name is not None

    @property
    def zone(self) -> ZoneInfo:
        return _zone(self.zone_name)

    @property
    def missed_grace_days(self) -> int:
        return KNOWN_ZONE_GRACE_DAYS if self.known else UNKNOWN_ZONE_GRACE_DAYS

    def today(self, now: datetime | None = None) -> date:
        return athlete_today(self.zone_name, now if now is not None else utc_now())

    def day_of(self, instant: datetime) -> date:
        """The athlete's calendar day an instant falls on (naive = UTC)."""
        return _as_utc(instant).astimezone(self.zone).date()

    def day_start_utc(self, day: date) -> datetime:
        """The NAIVE UTC instant at which ``day`` begins in this calendar.

        Local midnight, resolved by zoneinfo (fold=0). Where a transition skips midnight the
        wall-clock time does not exist and zoneinfo maps it forward, which is still the first
        instant of that day.
        """
        local = datetime.combine(day, time.min, tzinfo=self.zone)
        return local.astimezone(UTC).replace(tzinfo=None)

    def at_utc(self, day: date, wall: time) -> datetime:
        """The NAIVE UTC instant of wall-clock ``wall`` on ``day`` in this calendar (zoneinfo,
        fold=0; a wall time a transition skips maps forward)."""
        return datetime.combine(day, wall, tzinfo=self.zone).astimezone(UTC).replace(tzinfo=None)

    def day_end_utc(self, day: date) -> datetime:
        """The NAIVE UTC instant at which ``day`` ends (exclusive): the next day's start."""
        return self.day_start_utc(day + timedelta(days=1))


UNKNOWN = AthleteCalendar(None)
