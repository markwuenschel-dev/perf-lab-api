"""Q6: Deload risk dataset.

Prescription decisions joined to session feedback to capture athlete response.
Verified: prescription_decisions has athlete_id, created_at, decision_mode,
chosen_score, planned_session_id. session_feedback has planned_session_id,
status, satisfaction_score, pain_flag.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict]:
    """Prescription decisions with feedback outcomes for deload risk modeling."""
    query = text("""
        SELECT
            pd.athlete_id,
            pd.created_at,
            pd.decision_mode,
            pd.chosen_score,
            pd.goal,
            sf.status,
            sf.satisfaction_score,
            sf.pain_flag,
            sf.soreness_flag,
            sf.followed_as_prescribed
        FROM prescription_decisions pd
        LEFT JOIN session_feedback sf ON sf.planned_session_id = pd.planned_session_id
        ORDER BY pd.athlete_id, pd.created_at
        LIMIT 100000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
