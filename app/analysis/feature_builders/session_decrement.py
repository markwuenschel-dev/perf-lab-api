"""Q1: Session-pair decrement dataset.

Target: observed_next_performance - expected_next_performance_given_plan.
Do NOT use raw next performance — that conflates plan difficulty with decrement.
Exports session-pair features only; labels must be derived offline.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict]:
    """Build session-pair rows for Q1 decrement modeling.

    Verified columns: workout_logs.session_timestamp (not .timestamp),
    workout_logs.session_rpe, workout_logs.modality, workout_logs.user_id.
    novelty dropped — no such column on the ORM model.
    """
    query = text("""
        SELECT
            wl1.id                              AS prev_session_id,
            wl1.user_id                         AS athlete_id,
            wl1.session_timestamp               AS prev_session_at,
            wl2.session_timestamp               AS next_session_at,
            EXTRACT(EPOCH FROM (wl2.session_timestamp - wl1.session_timestamp)) / 3600
                                                AS time_gap_hours,
            wl1.session_rpe                     AS prev_rpe,
            wl2.session_rpe                     AS next_rpe,
            wl1.modality                        AS prev_modality,
            wl2.modality                        AS next_modality,
            wl1.duration_minutes                AS prev_duration_minutes,
            wl1.total_volume_load               AS prev_volume_load
        FROM workout_logs wl1
        JOIN workout_logs wl2
            ON wl1.user_id = wl2.user_id
            AND wl2.session_timestamp > wl1.session_timestamp
            AND wl2.session_timestamp <= wl1.session_timestamp + INTERVAL '7 days'
        ORDER BY wl1.user_id, wl1.session_timestamp
        LIMIT 50000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
