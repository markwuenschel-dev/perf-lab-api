"""Seed powerlifting demo athletes from the OpenPowerlifting database.

Creates demo+pl{i}@perflab.local athletes with REAL competition squat/bench/deadlift
numbers (not synthetic multipliers), replacing the bodyweight-ratio estimates used
for the google-fit athletes.

Sampling: up to 208 athletes spread evenly across 16 bodyweight×sex buckets so the
benchmark spine gets good coverage of the strength distribution.

Also adds 30 days of synthetic wellness (resting_hr / HRV / sleep) based on
fitness norms so the readiness engine has data for each PL athlete.

Prerequisite: run download_new_datasets.py first, then seed_benchmarks.

Run (local docker):
    $env:DATABASE_URL = "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab"
    $env:DEBUG = "false"
    python -m app.scripts.seed_openpl_strength
"""

from __future__ import annotations

import asyncio
import csv
import random
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.auth import hash_password
from app.core.db import AsyncSessionLocal
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.user import AthleteProfile, User
from app.models.wellness import WellnessSample

_DATA_DIR = Path("data/kaggle/powerlifting")
_DEMO_PASSWORD = "demo-password"  # noqa: S105
_STRENGTH_CODES = ["pl_e1rm_squat", "pl_e1rm_bench", "pl_e1rm_deadlift"]

_BW_BUCKETS = [(50, 60), (60, 70), (70, 80), (80, 90),
               (90, 100), (100, 110), (110, 120), (120, 130)]
_SEXES = ["M", "F"]
_PER_BUCKET = 13  # 16 buckets × 13 = 208 max athletes

_WELLNESS_START = date_cls(2024, 1, 1)
_WELLNESS_DAYS = 30
_FALLBACK_DATE = datetime(2023, 6, 1)


def _demo_email(i: int) -> str:
    return f"demo+pl{i}@perflab.local"


def _pick_csv() -> Path:
    if not _DATA_DIR.exists():
        raise SystemExit(
            f"Powerlifting data dir not found: {_DATA_DIR}\n"
            "Run `python -m app.scripts.download_new_datasets` first."
        )
    csvs = sorted(_DATA_DIR.rglob("*.csv"), key=lambda p: p.stat().st_size, reverse=True)
    if not csvs:
        raise SystemExit(f"No CSV files found in {_DATA_DIR}")
    return csvs[0]


def _col(row: dict[str, str], *names: str) -> str | None:
    for n in names:
        v = row.get(n, "").strip()
        if v:
            return v
    return None


def _positive_float(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f > 0 else None
    except ValueError:
        return None


def _bucket_key(bw: float, sex: str) -> tuple[tuple[int, int], str] | None:
    for lo, hi in _BW_BUCKETS:
        if lo <= bw < hi:
            return ((lo, hi), sex)
    return None


def _sample_from_csv(csv_path: Path) -> list[dict[str, Any]]:
    """Stream the (large) CSV and collect evenly distributed Raw-SBD entries."""
    buckets: dict[tuple[tuple[int, int], str], list[dict[str, Any]]] = {}

    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("Equipment", "").strip() != "Raw":
                continue
            if row.get("Event", "").strip() != "SBD":
                continue

            sex = row.get("Sex", "").strip()
            if sex not in _SEXES:
                continue

            bw_raw = _col(row, "BodyweightKg")
            bw = _positive_float(bw_raw)
            if bw is None or not (50.0 <= bw < 130.0):
                continue

            squat = _positive_float(_col(row, "Best3SquatKg", "BestSquatKg"))
            bench = _positive_float(_col(row, "Best3BenchKg", "BestBenchKg"))
            deadlift = _positive_float(_col(row, "Best3DeadliftKg", "BestDeadliftKg"))
            if squat is None or bench is None or deadlift is None:
                continue

            key = _bucket_key(bw, sex)
            if key is None:
                continue

            bucket = buckets.setdefault(key, [])
            if len(bucket) >= _PER_BUCKET:
                continue

            date_str = row.get("Date", "").strip()
            try:
                observed_at = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                observed_at = _FALLBACK_DATE

            bucket.append(
                {
                    "bw": bw,
                    "sex": sex,
                    "squat": squat,
                    "bench": bench,
                    "deadlift": deadlift,
                    "observed_at": observed_at,
                }
            )

            if all(len(b) >= _PER_BUCKET for b in buckets.values()) and len(buckets) == len(_BW_BUCKETS) * len(_SEXES):
                break

    return [entry for bucket in buckets.values() for entry in bucket]


def _derive_experience(total: float, bw: float) -> tuple[str, float]:
    ratio = total / bw if bw > 0 else 0.0
    if ratio < 3.5:
        return "beginner", 0.5
    if ratio < 5.0:
        return "intermediate", 2.5
    return "advanced", 6.0


def _synthetic_wellness(user_id: int, bw_kg: float, athlete_index: int) -> list[WellnessSample]:
    rng = random.Random(athlete_index * 7919)
    rhr = round(48.0 + (bw_kg / 130.0) * 22.0, 1)
    hrv = round(65.0 - (bw_kg / 130.0) * 20.0, 1)
    sleep_h = round(7.0 + rng.random() * 1.5, 2)
    sleep_q = round(70.0 + (bw_kg / 130.0) * 15.0, 1)

    samples = []
    for day in range(_WELLNESS_DAYS):
        from datetime import timedelta
        d = _WELLNESS_START + timedelta(days=day)
        samples.append(
            WellnessSample(
                user_id=user_id,
                date=d,
                source="synthetic:fitness_norms",
                resting_hr=rhr,
                hrv_ms=hrv,
                sleep_hours=sleep_h,
                sleep_quality=sleep_q,
                raw={"dataset": "synthetic:pl_wellness_norms"},
            )
        )
    return samples


async def seed() -> None:
    csv_path = _pick_csv()
    print(f"Sampling from {csv_path.name} ...")
    entries = _sample_from_csv(csv_path)
    print(f"Collected {len(entries)} Raw-SBD entries across bodyweight/sex buckets")

    async with AsyncSessionLocal() as db:
        def_res = await db.execute(
            select(BenchmarkDefinition.code, BenchmarkDefinition.id).where(
                BenchmarkDefinition.code.in_(_STRENGTH_CODES)
            )
        )
        def_id: dict[str, int] = dict(def_res.tuples().all())
        missing = [c for c in _STRENGTH_CODES if c not in def_id]
        if missing:
            raise SystemExit(
                f"Missing benchmark definitions {missing}; run seed_benchmarks first."
            )

        existing = set(
            (
                await db.execute(
                    select(User.email).where(User.email.like("demo+pl%@perflab.local"))
                )
            )
            .scalars()
            .all()
        )

        counts = {
            "users": 0, "profiles": 0, "strength_obs": 0, "wellness": 0, "skipped": 0
        }

        for i, entry in enumerate(entries, start=1):
            email = _demo_email(i)
            if email in existing:
                counts["skipped"] += 1
                continue

            bw = entry["bw"]
            squat = entry["squat"]
            bench = entry["bench"]
            deadlift = entry["deadlift"]
            total = squat + bench + deadlift

            exp_level, exp_years = _derive_experience(total, bw)

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
                    bodyweight_kg=round(bw, 1),
                    squat_1rm=squat,
                    bench_1rm=bench,
                    deadlift_1rm=deadlift,
                )
            )
            counts["profiles"] += 1

            for code, value in [
                ("pl_e1rm_squat", squat),
                ("pl_e1rm_bench", bench),
                ("pl_e1rm_deadlift", deadlift),
            ]:
                db.add(
                    BenchmarkObservation(
                        user_id=user.id,
                        benchmark_definition_id=def_id[code],
                        observed_at=entry["observed_at"],
                        raw_value=value,
                        bodyweight_kg=round(bw, 1),
                        source="kaggle:open-powerlifting",
                    )
                )
                counts["strength_obs"] += 1

            for sample in _synthetic_wellness(user.id, bw, i):
                db.add(sample)
                counts["wellness"] += 1

        await db.commit()

    if counts["users"] == 0:
        print(
            f"Nothing to do — all {counts['skipped']} PL demo athletes already seeded."
        )
    else:
        print("Seeded OpenPowerlifting athletes:")
        for k, v in counts.items():
            print(f"  {k:14s}: {v}")


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
