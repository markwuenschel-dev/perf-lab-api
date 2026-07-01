"""Q2: Fatigue recovery interval dataset.

Acute wellness signals at a given date; used to model fatigue axes and recovery.
Verified columns: wellness_samples.user_id, .date, .hrv_ms, .resting_hr,
.sleep_hours, .sleep_quality, .soreness — all present on the ORM model.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict]:
    """Fatigue axes at interval start vs observed readiness/performance at end."""
    query = text("""
        SELECT
            ws.user_id                          AS athlete_id,
            ws.date                             AS interval_date,
            ws.source,
            ws.hrv_ms,
            ws.resting_hr,
            ws.sleep_hours,
            ws.sleep_quality,
            ws.soreness,
            ws.mood
        FROM wellness_samples ws
        WHERE ws.hrv_ms IS NOT NULL
        ORDER BY ws.user_id, ws.date
        LIMIT 100000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
