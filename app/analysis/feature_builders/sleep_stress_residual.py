"""Q4: Sleep/stress residual dataset (benchmark performance moderated by recovery).

Joins benchmark observations with same-day wellness to capture fatigue context
at test time. Verified: benchmark_observations has user_id, benchmark_definition_id,
raw_value, normalized_value, observed_at. wellness_samples has user_id, date.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict]:
    """Benchmark performance rows with same-day wellness context for Q4 modeling."""
    query = text("""
        SELECT
            bo.user_id                              AS athlete_id,
            bo.id                                   AS observation_id,
            bo.benchmark_definition_id,
            bo.raw_value,
            bo.normalized_value,
            bo.observed_at,
            ws.sleep_quality,
            ws.soreness,
            ws.hrv_ms
        FROM benchmark_observations bo
        LEFT JOIN wellness_samples ws
            ON ws.user_id = bo.user_id
            AND ws.date = DATE(bo.observed_at)
        ORDER BY bo.user_id, bo.observed_at
        LIMIT 50000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
