"""Q10: Confidence calibration dataset (predicted variance vs observed residual).

Remapped: brief referenced bo.observation_weight — that column lives on
benchmark_definitions (bd.observation_weight), not benchmark_observations.
Fixed to bd.observation_weight.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict[str, Any]]:
    """Benchmark observation sequences with window context for calibration modeling."""
    query = text("""
        SELECT
            bo.user_id                  AS athlete_id,
            bd.code                     AS benchmark_code,
            bo.raw_value,
            bo.normalized_value,
            bd.observation_weight,
            bo.observed_at,
            LAG(bo.observed_at) OVER (
                PARTITION BY bo.user_id, bo.benchmark_definition_id
                ORDER BY bo.observed_at
            ) AS prev_observed_at
        FROM benchmark_observations bo
        JOIN benchmark_definitions bd ON bd.id = bo.benchmark_definition_id
        ORDER BY bo.user_id, bd.code, bo.observed_at
        LIMIT 50000
    """)
    result = await session.execute(query)
    return [dict(row) for row in result.mappings()]
