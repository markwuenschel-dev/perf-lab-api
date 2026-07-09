"""Conservative repair for ADR-0055 capacity corruption.

Pre-hotfix, workout-derived e1RM extraction could regress ``max_strength`` (a
submaximal set's low extrapolated e1RM pulled capacity down). Migration ``a025``
strips those observation rows of capacity authority and the runtime guard stops all
new damage — but athletes whose ``max_strength`` was already dragged down in the
append-only state series stay corrupted until repaired.

This job is **dry-run by default** and **conservative + monotonic**: for each affected
athlete (those with any ``workout_extraction`` observation), it restores ``max_strength``
to the athlete's own historical high-watermark by appending ONE correction state row —
it never lowers anything. Review the dry-run before ``--apply``.

    python -m app.scripts.repair_capacity_corruption            # dry run
    python -m app.scripts.repair_capacity_corruption --apply    # write corrections

Caveat: a genuine long-layoff detraining drop is also floored here; that is the
intended conservative direction (never silently leave capacity corrupted), and it only
touches athletes who have workout-extraction rows. Detraining is modelled elsewhere.
"""
import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.engine.state_bridge import (
    athlete_state_kwargs_from_unified,
    unified_from_athlete_row,
)
from app.models.athlete_state import AthleteState
from app.models.benchmark_observation import BenchmarkObservation

_EPS = 0.5  # kg — ignore trivial floating drift


async def _affected_user_ids(db: AsyncSession) -> list[int]:
    res = await db.execute(
        select(BenchmarkObservation.user_id)
        .where(BenchmarkObservation.source == "workout_extraction")
        .distinct()
    )
    return [r[0] for r in res.all() if r[0] is not None]


async def repair_with_db(db: AsyncSession, apply: bool) -> int:
    """Core repair against a given session — returns the number of athletes corrected."""
    corrected = 0
    user_ids = await _affected_user_ids(db)
    print(f"[repair] {len(user_ids)} athlete(s) with workout_extraction evidence")
    for uid in user_ids:
        rows = (
            await db.execute(
                select(AthleteState)
                .where(AthleteState.user_id == uid)
                .order_by(AthleteState.timestamp.asc())
            )
        ).scalars().all()
        if not rows:
            continue
        strengths = [unified_from_athlete_row(r).capacity_x.max_strength for r in rows]
        watermark = max(strengths)
        latest = unified_from_athlete_row(rows[-1])
        current = latest.capacity_x.max_strength
        if current >= watermark - _EPS:
            continue

        print(
            f"[repair] user {uid}: max_strength {current:.1f} < watermark "
            f"{watermark:.1f} → restore (+{watermark - current:.1f} kg)"
        )
        corrected += 1
        if apply:
            fixed = latest.model_copy(deep=True)
            fixed.capacity_x.max_strength = watermark
            fixed.timestamp = datetime.now(UTC).replace(tzinfo=None)
            kwargs = athlete_state_kwargs_from_unified(fixed)
            row = AthleteState(user_id=uid, **kwargs)
            row.engine_state = {
                **(row.engine_state or {}),
                "correction": {
                    "reason": "adr0055_capacity_corruption_repair",
                    "restored_max_strength": round(watermark, 2),
                    "from": round(current, 2),
                },
            }
            db.add(row)

    if apply and corrected:
        await db.commit()
    verb = "applied" if apply else "would correct (dry run)"
    tail = "Done." if apply else "Run with --apply to write."
    print(f"[repair] {verb} {corrected} athlete(s). {tail}")
    return corrected


async def repair(apply: bool) -> int:
    async with AsyncSessionLocal() as db:
        return await repair_with_db(db, apply)


if __name__ == "__main__":
    asyncio.run(repair(apply="--apply" in sys.argv))
