"""Q8: Scoring weight dataset (all candidates + outcomes).

All considered candidates (not just chosen) joined to their feedback.
Verified: candidate_decision_logs has prescription_decision_id, branch_id,
candidate_type, score_components_json, final_score, chosen, hard_failed.
session_feedback has status, satisfaction_score.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def build_dataset(session: AsyncSession) -> list[dict[str, Any]]:
    """All candidate rows with outcomes for offline policy evaluation (Q8)."""
    query = text("""
        SELECT
            cdl.prescription_decision_id,
            cdl.branch_id,
            cdl.candidate_type,
            cdl.score_components_json,
            cdl.final_score,
            cdl.chosen,
            cdl.hard_failed,
            sf.status,
            sf.satisfaction_score
        FROM candidate_decision_logs cdl
        JOIN prescription_decisions pd ON pd.id = cdl.prescription_decision_id
        LEFT JOIN session_feedback sf ON sf.planned_session_id = pd.planned_session_id
        ORDER BY cdl.prescription_decision_id, cdl.final_score DESC
        LIMIT 500000
    """)
    result = await session.execute(query)
    return [dict(row) for row in result.mappings()]
