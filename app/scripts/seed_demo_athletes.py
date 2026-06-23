"""Seed multi-domain demo athletes from the local Kaggle datasets.

Builds a coherent demo population so the readiness engine and the benchmark
spine have real-ish data to chew on:

  * Athletes + profiles + daily wellness  <- google-fit-data
        users, athlete_profiles, wellness_samples (HRV / sleep / RHR / fatigue)
  * Endurance benchmark observations      <- run_ww_2020 (REAL 5k efforts)
        benchmark_observations(run_5k_time), source="kaggle:run_ww_2020"
  * Strength benchmark observations       <- physiological standards (SYNTHETIC)
        benchmark_observations(pl_e1rm_*), source="synthetic:strength_standards"

Why strength is synthetic: the gym-members-exercise dataset carries no load /
1RM columns (only HR, duration, workout type), so it cannot supply real strength
numbers. Rather than fabricate "measured" lifts, each athlete's squat/bench/
deadlift e1RM is derived from their bodyweight x experience level using standard
strength multipliers and clearly labelled in `source`. Treat as demo data only,
never as calibration ground truth (per the dataset memo / ADR throughline).

Idempotent: athletes are keyed by a `demo+gf{uid}@perflab.local` email; already
seeded athletes are skipped, so re-runs are safe.

Run (against a local DB, quietly):
    $env:DATABASE_URL = "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab"
    $env:DEBUG = "false"
    python -m app.scripts.seed_demo_athletes --users 25 --runs-per-athlete 3
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import itertools
import random
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.core.auth import hash_password
from app.core.db import AsyncSessionLocal
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.user import AthleteProfile, User
from app.models.wellness import WellnessSample
from app.scripts.load_google_fit_wellness import DEFAULT_CSV as GF_CSV
from app.scripts.load_google_fit_wellness import load_google_fit_wellness

RUN_CSV = Path("data/kaggle/long-distance-running/run_ww_2020_d.csv")
DEMO_PASSWORD = "demo-password"  # noqa: S105 — demo athletes only


def _demo_email(uid: int) -> str:
    return f"demo+gf{uid}@perflab.local"


# fitness_level (0/1/2) -> (experience_level, experience_years)
_EXPERIENCE = {"0": ("beginner", 0.5), "1": ("intermediate", 2.5), "2": ("advanced", 6.0)}
# fitness_level -> bodyweight-relative e1RM multipliers (standard strength levels)
_STRENGTH_MULT = {
    "0": {"pl_e1rm_squat": 1.00, "pl_e1rm_bench": 0.65, "pl_e1rm_deadlift": 1.25},
    "1": {"pl_e1rm_squat": 1.40, "pl_e1rm_bench": 1.00, "pl_e1rm_deadlift": 1.75},
    "2": {"pl_e1rm_squat": 1.80, "pl_e1rm_bench": 1.30, "pl_e1rm_deadlift": 2.15},
}
_STRENGTH_DATE = datetime(2024, 1, 15)  # within the google-fit wellness window


def _read_demographics(uids: set[int], csv_path: Path) -> dict[int, dict[str, str]]:
    """First row per target uid -> its (constant) demographic fields."""
    out: dict[int, dict[str, str]] = {}
    with Path(csv_path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            uid = int(row["user_id"])
            if uid in uids and uid not in out:
                out[uid] = row
                if len(out) == len(uids):
                    break
    return out


def _collect_5k_efforts(n: int, csv_path: Path) -> list[tuple[float, datetime]]:
    """Stream run_ww for real ~5k efforts -> [(seconds, observed_at), ...].

    distance is in km, duration in minutes; keep 4.7-5.3 km at a plausible
    3-8 min/km pace. Stops as soon as `n` efforts are collected.
    """
    efforts: list[tuple[float, datetime]] = []
    with Path(csv_path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                dist = float(row["distance"])
                dur_min = float(row["duration"])
            except (TypeError, ValueError):
                continue
            if not (4.7 <= dist <= 5.3) or dur_min <= 0:
                continue
            pace = dur_min / dist
            if not (3.0 <= pace <= 8.0):
                continue
            try:
                when = datetime.strptime(row["datetime"][:10], "%Y-%m-%d")
            except ValueError:
                when = _STRENGTH_DATE
            efforts.append((round(dur_min * 60.0, 1), when))
            if len(efforts) >= n:
                break
    return efforts


def _round_plate(kg: float) -> float:
    return round(kg / 2.5) * 2.5


async def seed(n_users: int = 25, runs_per_athlete: int = 3) -> None:
    # --- gather source data (no DB yet) ---
    samples = load_google_fit_wellness(GF_CSV, limit_users=n_users)
    by_uid: dict[int, list] = {}
    for s in samples:
        by_uid.setdefault(s.user_id, []).append(s)
    uids = sorted(by_uid)
    demographics = _read_demographics(set(uids), GF_CSV)
    efforts = _collect_5k_efforts(len(uids) * runs_per_athlete, RUN_CSV)
    print(f"loaded {len(samples)} wellness rows / {len(uids)} athletes / {len(efforts)} 5k efforts")

    async with AsyncSessionLocal() as db:
        # benchmark definition ids we write observations against
        codes = ["run_5k_time", "pl_e1rm_squat", "pl_e1rm_bench", "pl_e1rm_deadlift"]
        res = await db.execute(
            select(BenchmarkDefinition.code, BenchmarkDefinition.id).where(
                BenchmarkDefinition.code.in_(codes)
            )
        )
        def_id = dict(res.all())
        missing = [c for c in codes if c not in def_id]
        if missing:
            raise SystemExit(f"Missing benchmark definitions {missing}; run seed_benchmarks first.")

        # idempotency: skip athletes already seeded
        existing = set(
            (
                await db.execute(
                    select(User.email).where(User.email.like("demo+gf%@perflab.local"))
                )
            )
            .scalars()
            .all()
        )

        counts = {"users": 0, "profiles": 0, "wellness": 0, "endurance_obs": 0, "strength_obs": 0}
        effort_i = 0
        for uid in uids:
            email = _demo_email(uid)
            if email in existing:
                continue
            demo = demographics.get(uid, {})
            fitness = demo.get("fitness_level", "0")
            exp_level, exp_years = _EXPERIENCE.get(fitness, _EXPERIENCE["0"])
            bw = float(demo.get("weight_kg") or 75.0)
            height_cm = round(float(demo.get("height_m") or 1.75) * 100.0, 1)
            rng = random.Random(uid)  # stable per athlete

            user = User(email=email, hashed_password=hash_password(DEMO_PASSWORD), is_active=True)
            db.add(user)
            await db.flush()  # assign user.id
            counts["users"] += 1

            # synthesized strength (also stored on the profile for coherence)
            one_rms = {
                code: _round_plate(bw * mult * rng.uniform(0.95, 1.05))
                for code, mult in _STRENGTH_MULT.get(fitness, _STRENGTH_MULT["0"]).items()
            }

            db.add(
                AthleteProfile(
                    user_id=user.id,
                    experience_level=exp_level,
                    experience_years=exp_years,
                    bodyweight_kg=round(bw, 1),
                    height_cm=height_cm,
                    squat_1rm=one_rms["pl_e1rm_squat"],
                    bench_1rm=one_rms["pl_e1rm_bench"],
                    deadlift_1rm=one_rms["pl_e1rm_deadlift"],
                )
            )
            counts["profiles"] += 1

            # daily wellness (remap dataset uid -> real user id)
            for s in by_uid[uid]:
                rec = asdict(s)
                rec["user_id"] = user.id
                db.add(WellnessSample(**rec))
                counts["wellness"] += 1

            # strength observations (synthetic, labelled)
            for code, value in one_rms.items():
                db.add(
                    BenchmarkObservation(
                        user_id=user.id,
                        benchmark_definition_id=def_id[code],
                        observed_at=_STRENGTH_DATE,
                        raw_value=value,
                        bodyweight_kg=round(bw, 1),
                        source="synthetic:strength_standards",
                    )
                )
                counts["strength_obs"] += 1

            # endurance observations (real 5k efforts, round-robin)
            for _ in range(runs_per_athlete):
                if effort_i >= len(efforts):
                    break
                seconds, when = efforts[effort_i]
                effort_i += 1
                db.add(
                    BenchmarkObservation(
                        user_id=user.id,
                        benchmark_definition_id=def_id["run_5k_time"],
                        observed_at=when,
                        raw_value=seconds,
                        source="kaggle:run_ww_2020",
                    )
                )
                counts["endurance_obs"] += 1

        await db.commit()

    if counts["users"] == 0:
        print("Nothing to do — demo athletes already seeded (idempotent skip).")
    else:
        print("Seeded:")
        for k, v in counts.items():
            print(f"  {k:14s}: {v}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed multi-domain demo athletes from Kaggle data")
    ap.add_argument("--users", type=int, default=25)
    ap.add_argument("--runs-per-athlete", type=int, default=3)
    args = ap.parse_args()
    asyncio.run(seed(n_users=args.users, runs_per_athlete=args.runs_per_athlete))


if __name__ == "__main__":
    main()
