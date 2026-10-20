"""The athlete calendar (B1): which day "today" is, across zones, UTC midnight and DST.

Pure: no database. The resolver, the readers and the routes are covered in
tests/test_athlete_timezone_readers.py.
"""
from datetime import UTC, date, datetime, timedelta

import pytest

from app.logic.athlete_calendar import (
    UNKNOWN,
    AthleteCalendar,
    InvalidTimezone,
    athlete_today,
    validate_timezone,
)

NY = AthleteCalendar("America/New_York")
LONDON = AthleteCalendar("Europe/London")
LA = AthleteCalendar("America/Los_Angeles")  # UTC-8 in winter, UTC-7 in summer
SYDNEY = AthleteCalendar("Australia/Sydney")  # UTC+10 in (austral) winter
BRISBANE = AthleteCalendar("Australia/Brisbane")  # UTC+10, no DST


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --- UTC midnight boundaries ----------------------------------------------------------------


def test_utc_minus_8_is_still_yesterday_after_utc_midnight() -> None:
    # 2026-01-15 03:00Z is 2026-01-14 19:00 in Los Angeles (PST, UTC-8).
    now = _utc(2026, 1, 15, 3, 0)
    assert LA.today(now) == date(2026, 1, 14)
    assert UNKNOWN.today(now) == date(2026, 1, 15)  # unknown falls back to the UTC day
    # One minute before LA midnight (07:59Z) it is still the 14th; at 08:00Z the 15th.
    assert LA.today(_utc(2026, 1, 15, 7, 59)) == date(2026, 1, 14)
    assert LA.today(_utc(2026, 1, 15, 8, 0)) == date(2026, 1, 15)


def test_utc_plus_10_is_already_tomorrow_before_utc_midnight() -> None:
    # 2026-07-15 15:00Z is 2026-07-16 01:00 in Brisbane (AEST, UTC+10).
    now = _utc(2026, 7, 15, 15, 0)
    assert BRISBANE.today(now) == date(2026, 7, 16)
    assert SYDNEY.today(now) == date(2026, 7, 16)
    assert UNKNOWN.today(now) == date(2026, 7, 15)
    assert BRISBANE.today(_utc(2026, 7, 15, 13, 59)) == date(2026, 7, 15)
    assert BRISBANE.today(_utc(2026, 7, 15, 14, 0)) == date(2026, 7, 16)


def test_explicit_utc_is_known_and_unknown_is_not() -> None:
    utc = AthleteCalendar("UTC")
    now = _utc(2026, 3, 1, 23, 30)
    assert utc.today(now) == UNKNOWN.today(now) == date(2026, 3, 1)
    assert utc.known and not UNKNOWN.known
    # Same date, different grace: an explicit UTC athlete's "today" is their own.
    assert (utc.missed_grace_days, UNKNOWN.missed_grace_days) == (0, 1)


def test_naive_instants_are_utc() -> None:
    assert LA.day_of(datetime(2026, 1, 15, 3, 0)) == date(2026, 1, 14)
    assert athlete_today("America/Los_Angeles", datetime(2026, 1, 15, 3, 0)) == date(2026, 1, 14)


# --- DST ------------------------------------------------------------------------------------


def test_new_york_spring_forward_day_is_23_hours() -> None:
    # 2026-03-08: clocks go 02:00 EST -> 03:00 EDT. Midnight is 05:00Z, the next 04:00Z.
    day = date(2026, 3, 8)
    assert NY.day_start_utc(day) == datetime(2026, 3, 8, 5, 0)
    assert NY.day_end_utc(day) == datetime(2026, 3, 9, 4, 0)
    assert NY.day_end_utc(day) - NY.day_start_utc(day) == timedelta(hours=23)
    # 03:59Z on the 9th is 23:59 EDT on the 8th; a naive "-5h" offset would say the 8th too, but
    # 04:30Z is 00:30 EDT on the 9th, where a fixed EST offset still says 23:30 on the 8th.
    assert NY.today(_utc(2026, 3, 9, 3, 59)) == day
    assert NY.today(_utc(2026, 3, 9, 4, 30)) == date(2026, 3, 9)


def test_new_york_fall_back_day_is_25_hours() -> None:
    # 2026-11-01: clocks go 02:00 EDT -> 01:00 EST. Midnight is 04:00Z, the next 05:00Z.
    day = date(2026, 11, 1)
    assert NY.day_start_utc(day) == datetime(2026, 11, 1, 4, 0)
    assert NY.day_end_utc(day) == datetime(2026, 11, 2, 5, 0)
    assert NY.day_end_utc(day) - NY.day_start_utc(day) == timedelta(hours=25)
    # 04:30Z on the 2nd is 23:30 EST on the 1st; a fixed EDT offset would say 00:30 on the 2nd.
    assert NY.today(_utc(2026, 11, 2, 4, 30)) == day
    # Both 01:30s of the repeated hour are the same calendar day.
    assert NY.today(_utc(2026, 11, 1, 5, 30)) == NY.today(_utc(2026, 11, 1, 6, 30)) == day


def test_london_bst_boundaries() -> None:
    # 2026-03-29 BST starts at 01:00Z; 2026-10-25 it ends at 01:00Z.
    assert LONDON.day_end_utc(date(2026, 3, 29)) - LONDON.day_start_utc(date(2026, 3, 29)) == (
        timedelta(hours=23))
    assert LONDON.day_end_utc(date(2026, 10, 25)) - LONDON.day_start_utc(date(2026, 10, 25)) == (
        timedelta(hours=25))
    # Summer: 23:30Z is already tomorrow in London; winter: it is not.
    assert LONDON.today(_utc(2026, 7, 1, 23, 30)) == date(2026, 7, 2)
    assert LONDON.today(_utc(2026, 12, 1, 23, 30)) == date(2026, 12, 1)
    assert LONDON.day_start_utc(date(2026, 7, 2)) == datetime(2026, 7, 1, 23, 0)
    assert LONDON.day_start_utc(date(2026, 12, 2)) == datetime(2026, 12, 2, 0, 0)


def test_days_partition_time_across_a_whole_year() -> None:
    """Every instant belongs to exactly the day whose [start, end) holds it, on every day of a
    year in a DST zone — the property offset arithmetic breaks twice a year."""
    for cal in (NY, LONDON, LA, SYDNEY):
        day = date(2026, 1, 1)
        while day.year == 2026:
            start, end = cal.day_start_utc(day), cal.day_end_utc(day)
            assert cal.day_of(start) == day
            assert cal.day_of(end - timedelta(microseconds=1)) == day
            assert cal.day_of(end) == day + timedelta(days=1)
            day += timedelta(days=1)


# --- validation ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["America/New_York", "Europe/London", "UTC", "Etc/UTC",
                                  "Australia/Sydney", " Asia/Tokyo "])
def test_iana_names_are_accepted(name: str) -> None:
    assert validate_timezone(name) == name.strip()


@pytest.mark.parametrize("name", ["", "   ", "EST5EDT-not", "Mars/Olympus", "localtime",
                                  "../../etc/passwd", "+05:30", "UTC+2", "a" * 65,
                                  "Factory"])
def test_non_zones_are_refused(name: str) -> None:
    with pytest.raises(InvalidTimezone):
        validate_timezone(name)
