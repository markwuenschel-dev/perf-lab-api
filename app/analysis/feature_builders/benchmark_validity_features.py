"""Q5: Benchmark validity dataset (observed vs expected, fatigue context).

Remapped: brief referenced bo.observation_weight — that column lives on
benchmark_definitions (bd.observation_weight), not benchmark_observations.
Fixed to bd.observation_weight.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict]:
    """Benchmark observations with validity status and same-day wellness for Q5."""
    query = text("""
        SELECT
            bo.id                       AS observation_id,
            bo.user_id                  AS athlete_id,
            bd.code                     AS benchmark_code,
            bo.raw_value,
            bo.normalized_value,
            bo.validity_status,
            bd.observation_weight,
            bo.observed_at,
            ws.sleep_quality,
            ws.soreness,
            ws.hrv_ms
        FROM benchmark_observations bo
        JOIN benchmark_definitions bd ON bd.id = bo.benchmark_definition_id
        LEFT JOIN wellness_samples ws
            ON ws.user_id = bo.user_id
            AND ws.date = DATE(bo.observed_at)
        ORDER BY bo.user_id, bd.code, bo.observed_at
        LIMIT 50000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
