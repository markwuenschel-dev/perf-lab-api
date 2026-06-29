"""Seed demo athletes from the gym-members-exercise dataset with resting-HR wellness.

Creates up to 50 demo+gym{i}@perflab.local athletes with:
  - AthleteProfile from Age/Gender/Weight/Height/Experience
  - WellnessSample (7 days, resting_hr from Resting_BPM)
  - WorkoutLog (1 representative session per athlete)

Run (local docker):
    $env:DATABASE_URL = "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab"
    $env:DEBUG = "false"
    python -m app.scripts.seed_gym_members_wellness
"""

from __future__ import annotations

import asyncio
import csv
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.auth import hash_password
from app.core.db import AsyncSessionLocal
from app.models.user import AthleteProfile, User
from app.models.wellness import WellnessSample
from app.models.workout_log import WorkoutLog

_CSV = Path("data/kaggle/gym-members-exercise/gym_members_exercise_tracking.csv")
_DEMO_PASSWORD = "demo-password"  # noqa: S105
_MAX_ATHLETES = 50

_EXPERIENCE_MAP = {
    "1": ("beginner", 0.5),
    "2": ("intermediate", 2.5),
    "3": ("advanced", 6.0),
}
_MODALITY_MAP = {
    "Yoga": "mobility",
    "HIIT": "conditioning",
    "Cardio": "conditioning",
    "Strength": "strength",
}
_RPE_MAP = {
    "HIIT": 7.0,
    "Strength": 6.5,
    "Cardio": 5.5,
    "Yoga": 4.0,
}
_WELLNESS_DATES = [date_cls(2024, 6, d) for d in range(1, 8)]


def _demo_email(i: int) -> str:
    return f"demo+gym{i}@perflab.local"


def _load_rows() -> list[dict[str, str]]:
    if not _CSV.exists():
        raise SystemExit(f"Gym members CSV not found: {_CSV}")
    with _CSV.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


async def seed(max_athletes: int = _MAX_ATHLETES) -> None:
    rows = _load_rows()
    rows = rows[:max_athletes]
    print(f"Gym members: processing {len(rows)} rows -> demo+gym athletes")

    async with AsyncSessionLocal() as db:
        existing = set(
            (
                await db.execute(
                    select(User.email).where(User.email.like("demo+gym%@perflab.local"))
                )
            )
            .scalars()
            .all()
        )

        counts: dict[str, int] = {
            "users": 0, "profiles": 0, "wellness": 0, "logs": 0, "skipped": 0
        }

        for i, row in enumerate(rows, start=1):
            email = _demo_email(i)
            if email in existing:
                counts["skipped"] += 1
                continue

            try:
                bw_kg = float(row["Weight (kg)"])
                height_m = float(row["Height (m)"])
                resting_bpm = float(row["Resting_BPM"])
                avg_bpm = float(row["Avg_BPM"])
                max_bpm = float(row["Max_BPM"])
                duration_h = float(row["Session_Duration (hours)"])
                calories = float(row["Calories_Burned"])
            except (ValueError, KeyError):
                continue

            exp_raw = row.get("Experience_Level", "1").strip()
            exp_level, exp_years = _EXPERIENCE_MAP.get(exp_raw, ("beginner", 0.5))
            workout_type = row.get("Workout_Type", "").strip()

            user = User(
                email=email,
                hashed_password=hash_password(_DEMO_PASSWORD),
                is_active=True,
            )
            db.add(user)
            await db.flush()
            counts["users"] += 1

            db.add(
                AthleteProfile(
                    user_id=user.id,
                    experience_level=exp_level,
                    experience_years=exp_years,
                    bodyweight_kg=round(bw_kg, 1),
                    height_cm=round(height_m * 100.0, 1),
                )
            )
            counts["profiles"] += 1

            raw_payload: dict[str, Any] = {
                "avg_bpm": avg_bpm,
                "max_bpm": max_bpm,
                "workout_type": workout_type,
                "dataset": "kaggle:gym-members-exercise",
            }
            for wellness_date in _WELLNESS_DATES:
                db.add(
                    WellnessSample(
                        user_id=user.id,
                        date=wellness_date,
                        source="gym_members",
                        resting_hr=resting_bpm,
                        raw=raw_payload,
                    )
                )
                counts["wellness"] += 1

            modality = _MODALITY_MAP.get(workout_type, "conditioning")
            rpe = _RPE_MAP.get(workout_type, 6.0)
            db.add(
                WorkoutLog(
                    user_id=user.id,
                    session_timestamp=datetime(2024, 6, 1, 8, 0),
                    modality=modality,
                    duration_minutes=round(duration_h * 60.0, 1),
                    session_rpe=rpe,
                    is_benchmark=False,
                    dose_snapshot={"calories_burned": calories},
                )
            )
            counts["logs"] += 1

        await db.commit()

    if counts["users"] == 0:
        print(f"Nothing to do — all {counts['skipped']} gym demo athletes already seeded.")
    else:
        print("Seeded gym-members athletes:")
        for k, v in counts.items():
            print(f"  {k:12s}: {v}")


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
