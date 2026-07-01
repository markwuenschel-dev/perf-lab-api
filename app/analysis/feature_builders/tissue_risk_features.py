"""Q3: Tissue risk dataset (athlete-day-tissue-axis rows).

Labels come from outcome_events (tissue_skip, pain_event, etc.).
Joined to wellness_samples for daily context signals.
Verified: outcome_events has athlete_id (not user_id); wellness_samples has user_id.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict]:
    """Rows for tissue risk model training. Labels from outcome_events."""
    query = text("""
        SELECT
            oe.athlete_id,
            oe.occurred_at,
            oe.event_type,
            oe.tissue_axis,
            oe.confidence,
            ws.sleep_quality,
            ws.soreness,
            ws.hrv_ms
        FROM outcome_events oe
        LEFT JOIN wellness_samples ws
            ON ws.user_id = oe.athlete_id
            AND ws.date = DATE(oe.occurred_at)
        WHERE oe.event_type IN (
            'tissue_skip', 'tissue_modified', 'pain_event',
            'non_tissue_skip', 'unknown_skip'
        )
        ORDER BY oe.athlete_id, oe.occurred_at
        LIMIT 100000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
