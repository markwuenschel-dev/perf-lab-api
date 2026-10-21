"""Q9: Interference dataset (endurance/metabolic load before strength benchmarks).

Exports benchmark observations with definition metadata for interference analysis.
Verified: benchmark_observations has user_id, benchmark_definition_id, raw_value,
normalized_value, observed_at. benchmark_definitions has code, better_direction.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.feature_builders.display_only_exclusion import (
    DISPLAY_ONLY_FILTER,
    bind_display_only_codes,
)


async def build_dataset(session: AsyncSession) -> list[dict[str, Any]]:
    """Benchmark observations with domain/direction metadata for Q9 interference modeling."""
    query = bind_display_only_codes(text(f"""
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
        WHERE {DISPLAY_ONLY_FILTER}
        ORDER BY bo.user_id, bo.observed_at
        LIMIT 50000
    """))
    result = await session.execute(query)
    return [dict(row) for row in result.mappings()]
