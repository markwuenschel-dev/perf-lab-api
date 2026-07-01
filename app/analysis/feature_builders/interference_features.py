"""Q9: Interference dataset (endurance/metabolic load before strength benchmarks).

Exports benchmark observations with definition metadata for interference analysis.
Verified: benchmark_observations has user_id, benchmark_definition_id, raw_value,
normalized_value, observed_at. benchmark_definitions has code, better_direction.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict]:
    """Benchmark observations with domain/direction metadata for Q9 interference modeling."""
    query = text("""
        SELECT
            bo.user_id                  AS athlete_id,
            bd.code                     AS benchmark_code,
            bd.domain,
            bd.better_direction,
            bo.raw_value,
            bo.normalized_value,
            bo.observed_at
        FROM benchmark_observations bo
        JOIN benchmark_definitions bd ON bd.id = bo.benchmark_definition_id
        ORDER BY bo.user_id, bo.observed_at
        LIMIT 50000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
