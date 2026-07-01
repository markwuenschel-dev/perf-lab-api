"""Q7: Adaptive vs static experiment dataset.

Cross joins experiment assignments with subsequent prescription decisions and
session feedback to compare arm outcomes.
Verified: experiment_assignments has user_id, arm, experiment_name, assigned_at.
prescription_decisions has athlete_id, decision_mode, chosen_score, created_at.
session_feedback has status, satisfaction_score, followed_as_prescribed,
modified_volume, modified_intensity.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict]:
    """Experiment arm assignment rows with prescription and feedback outcomes for Q7."""
    query = text("""
        SELECT
            ea.user_id                  AS athlete_id,
            ea.arm,
            ea.experiment_name,
            ea.assigned_at,
            pd.decision_mode,
            pd.chosen_score,
            pd.created_at               AS decision_at,
            sf.status,
            sf.satisfaction_score,
            sf.followed_as_prescribed,
            sf.modified_volume,
            sf.modified_intensity
        FROM experiment_assignments ea
        LEFT JOIN prescription_decisions pd
            ON pd.athlete_id = ea.user_id
            AND pd.created_at >= ea.assigned_at
        LEFT JOIN session_feedback sf
            ON sf.planned_session_id = pd.planned_session_id
        ORDER BY ea.user_id, pd.created_at
        LIMIT 200000
    """)
    result = await session.execute(query)
    return [dict(row._mapping) for row in result]
