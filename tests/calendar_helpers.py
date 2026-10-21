"""The day the backend calls "today" for a test athlete (B1).

Test athletes have no stored timezone, so the backend counts their days in UTC (the unknown-zone
fallback), not in this machine's local calendar. A fixture built on ``date.today()`` disagrees
with the backend whenever the local date and the UTC date differ (every evening west of UTC);
building it on this day keeps the two in step on any machine.
"""
from datetime import date

from app.logic.athlete_calendar import UNKNOWN


def backend_today() -> date:
    """The UTC day, read from the clock every backend "today" reads."""
    return UNKNOWN.today()
