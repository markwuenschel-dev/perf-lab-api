"""Recent workout log summaries for constraint context (best-effort tags)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workout_log import WorkoutLog


def _derive_tags(row: WorkoutLog) -> list[str]:
    tags: list[str] = []
    modality = (row.modality or "").lower()
    rpe = float(row.session_rpe or 0)
    dur = float(row.duration_minutes or 0)

    if "conditioning" in modality or "metcon" in modality or "hiit" in modality:
        tags.append("metcon")
        if rpe >= 7.5 and dur >= 25:
            tags.append("metcon_high_density")
    if "run" in modality or "running" in modality or modality == "aerobic":
        tags.append("running")
        if rpe >= 8.0:
            tags.append("threshold_or_vo2")
            tags.append("high_intensity_run")
        if rpe <= 5.0 and dur >= 50:
            tags.append("long_easy")
            tags.append("long_run")
    if "barbell" in modality or "olympic" in modality or "weightlifting" in modality:
        tags.append("barbell_strength")
    if "deadlift" in modality:
        tags.append("deadlift_session")
    if "gymnastics" in modality or "rings" in modality or "calisthenics" in modality:
        tags.append("tendon_heavy")
    return list(dict.fromkeys(tags))


def workout_log_to_summary(row: WorkoutLog) -> dict[str, Any]:
    ts = row.session_timestamp
    if isinstance(ts, datetime) and ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    iso = ts.isoformat() if isinstance(ts, datetime) else str(ts)
    return {
        "modality": row.modality,
        "duration_minutes": float(row.duration_minutes or 0),
        "session_rpe": float(row.session_rpe or 0),
        "session_timestamp": iso,
        "tags": _derive_tags(row),
        "intensity_bucket": "high"
        if float(row.session_rpe or 0) >= 8.0
        else ("low" if float(row.session_rpe or 0) <= 4.0 else "moderate"),
    }


async def recent_workout_summaries(
    db: AsyncSession,
    user_id: int,
    *,
    days: int = 14,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Most recent sessions first — used by prescribe + constraint context."""
    # Naive UTC to match WorkoutLog.session_timestamp (a naive DateTime column);
    # an aware value would make asyncpg reject the comparison bind (INT-19).
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    result = await db.execute(
        select(WorkoutLog)
        .where(WorkoutLog.user_id == user_id)
        .where(WorkoutLog.session_timestamp >= cutoff)
        .order_by(WorkoutLog.session_timestamp.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    return [workout_log_to_summary(r) for r in rows]
