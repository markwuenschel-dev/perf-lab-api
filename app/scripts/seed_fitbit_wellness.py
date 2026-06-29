"""Seed WellnessSample rows from the Fitbit sleep CSVs (both export windows).

Maps each unique Fitbit user ID round-robin to existing demo+gf* athletes,
giving them additional sleep wellness records from a different wearable source.

Adds: sleep_hours, sleep_quality (resting_hr not available in this dataset).
source = "fitbit"

Run (local docker, after seed_demo_athletes):
    $env:DATABASE_URL = "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab"
    $env:DEBUG = "false"
    python -m app.scripts.seed_fitbit_wellness
"""

from __future__ import annotations

import asyncio
import csv
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.user import User
from app.models.wellness import WellnessSample

_SLEEP_CSVS = [
    Path(
        "data/kaggle/fitbit/mturkfitbit_export_3.12.16-4.11.16"
        "/Fitabase Data 3.12.16-4.11.16/sleepDay_merged.csv"
    ),
    Path(
        "data/kaggle/fitbit/mturkfitbit_export_4.12.16-5.12.16"
        "/Fitabase Data 4.12.16-5.12.16/sleepDay_merged.csv"
    ),
]
_SOURCE = "fitbit"


def _load_sleep_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[int, date_cls]] = set()
    for csv_path in _SLEEP_CSVS:
        if not csv_path.exists():
            continue
        with csv_path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    fitbit_id = int(row["Id"])
                    sleep_date = datetime.strptime(
                        row["SleepDay"].split()[0], "%m/%d/%Y"
                    ).date()
                    minutes_asleep = float(row["TotalMinutesAsleep"])
                    time_in_bed = float(row["TotalTimeInBed"])
                except (ValueError, KeyError):
                    continue
                key = (fitbit_id, sleep_date)
                if key in seen:
                    continue
                seen.add(key)
                sleep_quality = (
                    round(min(minutes_asleep / time_in_bed, 1.0) * 100, 1)
                    if time_in_bed > 0
                    else None
                )
                records.append(
                    {
                        "fitbit_id": fitbit_id,
                        "date": sleep_date,
                        "sleep_hours": round(minutes_asleep / 60.0, 2),
                        "sleep_quality": sleep_quality,
                    }
                )
    return records


async def seed() -> None:
    records = _load_sleep_records()
    if not records:
        raise SystemExit(
            "No Fitbit sleep records found. Check data/kaggle/fitbit/ paths."
        )
    print(f"Loaded {len(records)} Fitbit sleep records")

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

        # Bulk-load already-seeded (user_id, date) pairs for this source.
        existing_result = await db.execute(
            select(WellnessSample.user_id, WellnessSample.date).where(
                WellnessSample.source == _SOURCE,
                WellnessSample.user_id.in_(list(fitbit_to_athlete.values())),
            )
        )
        existing: set[tuple[int, date_cls]] = {
            (row.user_id, row.date) for row in existing_result
        }

        inserted = 0
        skipped = 0
        for rec in records:
            athlete_id = fitbit_to_athlete[rec["fitbit_id"]]
            if (athlete_id, rec["date"]) in existing:
                skipped += 1
                continue
            db.add(
                WellnessSample(
                    user_id=athlete_id,
                    date=rec["date"],
                    source=_SOURCE,
                    sleep_hours=rec["sleep_hours"],
                    sleep_quality=rec["sleep_quality"],
                    raw={
                        "fitbit_id": rec["fitbit_id"],
                        "dataset": "kaggle:mturkfitbit",
                    },
                )
            )
            existing.add((athlete_id, rec["date"]))
            inserted += 1

        await db.commit()

    print(
        f"Fitbit sleep wellness: inserted {inserted} rows, "
        f"skipped {skipped} (already seeded)."
    )


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
