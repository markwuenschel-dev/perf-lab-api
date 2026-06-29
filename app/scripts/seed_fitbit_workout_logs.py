"""Seed WorkoutLog rows from the Fitbit daily activity CSVs (both export windows).

Extracts actual training days (VeryActiveMinutes + FairlyActiveMinutes >= 20)
and maps them round-robin to existing demo+gf* athletes, providing real
training history for the training load model.

Run (local docker, after seed_demo_athletes):
    $env:DATABASE_URL = "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab"
    $env:DEBUG = "false"
    python -m app.scripts.seed_fitbit_workout_logs
"""

from __future__ import annotations

import asyncio
import csv
from datetime import date as date_cls
from datetime import datetime
from datetime import time as time_cls
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.core.db import AsyncSessionLocal
from app.models.user import User
from app.models.workout_log import WorkoutLog

_ACTIVITY_CSVS = [
    Path(
        "data/kaggle/fitbit/mturkfitbit_export_3.12.16-4.11.16"
        "/Fitabase Data 3.12.16-4.11.16/dailyActivity_merged.csv"
    ),
    Path(
        "data/kaggle/fitbit/mturkfitbit_export_4.12.16-5.12.16"
        "/Fitabase Data 4.12.16-5.12.16/dailyActivity_merged.csv"
    ),
]
_MIN_ACTIVE_MINUTES = 20


def _classify_session(
    very_min: float, fairly_min: float, very_dist_km: float
) -> tuple[str, float]:
    if very_dist_km >= 1.0:
        return "running", (6.5 if very_min > 30 else 6.0)
    if very_min > fairly_min:
        return "conditioning", 6.0
    return "conditioning", 5.0


def _load_activity_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[int, date_cls]] = set()
    for csv_path in _ACTIVITY_CSVS:
        if not csv_path.exists():
            continue
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    fitbit_id = int(row["Id"])
                    act_date = datetime.strptime(
                        row["ActivityDate"], "%m/%d/%Y"
                    ).date()
                    very_min = float(row["VeryActiveMinutes"])
                    fairly_min = float(row["FairlyActiveMinutes"])
                    very_dist_km = float(row["VeryActiveDistance"])
                    total_dist_km = float(row["TotalDistance"])
                except (ValueError, KeyError):
                    continue
                if very_min + fairly_min < _MIN_ACTIVE_MINUTES:
                    continue
                key = (fitbit_id, act_date)
                if key in seen:
                    continue
                seen.add(key)
                modality, rpe = _classify_session(very_min, fairly_min, very_dist_km)
                records.append(
                    {
                        "fitbit_id": fitbit_id,
                        "date": act_date,
                        "modality": modality,
                        "duration_minutes": very_min + fairly_min,
                        "session_rpe": rpe,
                        "distance_meters": round(total_dist_km * 1000.0, 1),
                    }
                )
    return records


async def seed() -> None:
    records = _load_activity_records()
    if not records:
        raise SystemExit(
            "No Fitbit activity records found. Check data/kaggle/fitbit/ paths."
        )
    print(f"Loaded {len(records)} active Fitbit session records")

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User.id)
            .where(User.email.like("demo+gf%@perflab.local"))
            .order_by(User.id)
        )
        athlete_ids = result.scalars().all()
        if not athlete_ids:
            raise SystemExit(
                "No demo athletes found. Run seed_demo_athletes first."
            )

        fitbit_ids_ordered = sorted({r["fitbit_id"] for r in records})
        fitbit_to_athlete = {
            fid: athlete_ids[i % len(athlete_ids)]
            for i, fid in enumerate(fitbit_ids_ordered)
        }

        # Athletes that already have workout logs — skip all their records.
        log_counts = await db.execute(
            select(WorkoutLog.user_id)
            .where(WorkoutLog.user_id.in_(list(set(fitbit_to_athlete.values()))))
            .distinct()
        )
        already_seeded: set[int] = set(log_counts.scalars().all())
        skipped_athletes = len(already_seeded)

        inserted = 0
        for rec in records:
            athlete_id = fitbit_to_athlete[rec["fitbit_id"]]
            if athlete_id in already_seeded:
                continue
            session_ts = datetime.combine(rec["date"], time_cls(7, 0))
            db.add(
                WorkoutLog(
                    user_id=athlete_id,
                    session_timestamp=session_ts,
                    modality=rec["modality"],
                    duration_minutes=rec["duration_minutes"],
                    session_rpe=rec["session_rpe"],
                    distance_meters=rec["distance_meters"],
                    is_benchmark=False,
                )
            )
            inserted += 1

        await db.commit()

    print(
        f"Fitbit workout logs: inserted {inserted} WorkoutLog rows "
        f"(skipped {skipped_athletes} athletes already seeded)."
    )


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
