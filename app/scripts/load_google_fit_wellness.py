"""Load the Kaggle *google-fit-data* CSV into ``WellnessSample``-shaped records.

Maps the synthetic Google Fit daily rows onto the daily-wellness schema the
readiness engine expects (roadmap P5 / PDR-0005):

    WellnessSample(user_id, date, source, hrv_ms, sleep_hours, sleep_quality,
                   resting_hr, soreness, mood, raw)

The P5 ORM model (``app/models/wellness.py`` + migration ``a004_wellness``) does
not exist yet, so this module is intentionally **DB-agnostic** and depends only
on the stdlib: it produces validated records you can inspect / write to JSONL
now, and drop straight into the real table the moment P5 lands (see ``seed`` at
the bottom).

Column mapping (Google Fit -> WellnessSample)
---------------------------------------------
    hrv               -> hrv_ms            (rMSSD-style, already ms; 10-118)
    resting_hr        -> resting_hr        (bpm; 45-100)
    sleep_hours       -> sleep_hours       (None when sleep_data_available is False)
    sleep_efficiency  -> sleep_quality     (0-1 ratio rescaled to 0-100)
    (n/a)             -> soreness, mood    (not in dataset -> None)
    fatigue_score,    -> raw{...}          (dataset-only signals kept for provenance)
    spo2, avg_heart_rate, activity_type

Usage
-----
    # 25 demo athletes (25 users x 30 days = 750 rows) -> JSONL + summary
    python -m app.scripts.load_google_fit_wellness --users 25 \
        --out data/seeds/wellness_google_fit.jsonl

    # everything (3000 users), summary only
    python -m app.scripts.load_google_fit_wellness --users 0 --no-out
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_CSV = Path("data/kaggle/google-fit-data/hamon_googlefit_medical_realistic.csv")
DEFAULT_OUT = Path("data/seeds/wellness_google_fit.jsonl")
SOURCE = "google_fit"

# Extra Google Fit columns with no WellnessSample home -> preserved under `raw`.
_RAW_INT_KEYS = ("fatigue_score", "spo2", "avg_heart_rate")
_RAW_STR_KEYS = ("activity_type",)
_MISSING = {"", "nan", "na", "null", "none"}


@dataclass
class WellnessSample:
    """Mirror of the planned ``app/models/wellness.py`` row (roadmap P5).

    Replace this with ``from app.models.wellness import WellnessSample`` once the
    ORM model exists; field names are kept identical so ``seed_record()`` output
    feeds ``WellnessSample(**row)`` unchanged.
    """

    user_id: int
    date: date_cls
    source: str
    hrv_ms: float | None = None
    sleep_hours: float | None = None
    sleep_quality: float | None = None  # 0-100
    resting_hr: float | None = None
    soreness: float | None = None
    mood: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _opt_float(v: str | None, ndigits: int | None = None) -> float | None:
    if v is None or v.strip().lower() in _MISSING:
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    return round(f, ndigits) if ndigits is not None else f


def _opt_int(v: str | None) -> int | None:
    f = _opt_float(v)
    return None if f is None else int(f)


def _row_to_sample(row: dict[str, str], *, user_id_offset: int) -> WellnessSample:
    eff = _opt_float(row.get("sleep_efficiency"))
    raw: dict[str, Any] = {k: _opt_int(row.get(k)) for k in _RAW_INT_KEYS if k in row}
    for k in _RAW_STR_KEYS:
        if k in row:
            raw[k] = (row[k] or None)
    raw["dataset"] = "kaggle:aridoge13/google-fit-data"
    return WellnessSample(
        user_id=int(row["user_id"]) + user_id_offset,
        date=datetime.strptime(row["date"], "%Y-%m-%d").date(),
        source=SOURCE,
        hrv_ms=_opt_float(row.get("hrv")),
        sleep_hours=_opt_float(row.get("sleep_hours"), 2),
        sleep_quality=None if eff is None else round(eff * 100.0, 1),
        resting_hr=_opt_float(row.get("resting_hr")),
        soreness=None,  # not captured by Google Fit
        mood=None,      # not captured by Google Fit
        raw=raw,
    )


def load_google_fit_wellness(
    csv_path: Path | str = DEFAULT_CSV,
    *,
    limit_users: int | None = None,
    user_id_offset: int = 0,
) -> list[WellnessSample]:
    """Read the Google Fit CSV and return ``WellnessSample`` records.

    ``limit_users`` keeps only the first N distinct ``user_id`` values (None/0 =
    all). ``user_id_offset`` shifts dataset user ids so demo rows don't collide
    with real users when seeded.
    """
    samples: list[WellnessSample] = []
    seen: set[str] = set()
    with Path(csv_path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            uid = row["user_id"]
            if limit_users and uid not in seen and len(seen) >= limit_users:
                continue
            seen.add(uid)
            samples.append(_row_to_sample(row, user_id_offset=user_id_offset))
    return samples


def seed_record(sample: WellnessSample) -> dict[str, Any]:
    """A ``WellnessSample(**record)``-ready dict (date as ISO string)."""
    rec = asdict(sample)
    rec["date"] = sample.date.isoformat()
    return rec


def _summarize(samples: list[WellnessSample]) -> None:
    n = len(samples)
    users = len({s.user_id for s in samples})
    with_sleep = sum(1 for s in samples if s.sleep_hours is not None)
    dates = [s.date for s in samples]
    print(f"WellnessSample records : {n}")
    print(f"distinct demo athletes : {users}")
    print(f"date span              : {min(dates)} -> {max(dates)}")
    print(f"rows with sleep data   : {with_sleep} ({with_sleep / n:.0%})")
    print("example record         :")
    print("  " + json.dumps(seed_record(samples[0]), indent=2).replace("\n", "\n  "))


def main() -> None:
    ap = argparse.ArgumentParser(description="Load Google Fit CSV -> WellnessSample records")
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    ap.add_argument("--users", type=int, default=25, help="distinct athletes to keep (0 = all)")
    ap.add_argument("--user-id-offset", type=int, default=0)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--no-out", action="store_true", help="summary only, do not write JSONL")
    args = ap.parse_args()

    samples = load_google_fit_wellness(
        args.csv, limit_users=(args.users or None), user_id_offset=args.user_id_offset
    )
    if not samples:
        raise SystemExit(f"No rows produced from {args.csv}")

    if not args.no_out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as fh:
            for s in samples:
                fh.write(json.dumps(seed_record(s)) + "\n")
        print(f"wrote {len(samples)} records -> {args.out}")
    _summarize(samples)


# --- DB seeding (enable once app/models/wellness.py + a004_wellness exist) -------
#
# async def seed(limit_users: int = 25, user_id_offset: int = 100_000) -> None:
#     from app.core.db import AsyncSessionLocal
#     from app.models.wellness import WellnessSample as WellnessSampleORM
#     samples = load_google_fit_wellness(limit_users=limit_users, user_id_offset=user_id_offset)
#     async with AsyncSessionLocal() as db:
#         for s in samples:
#             db.add(WellnessSampleORM(**asdict(s)))
#         await db.commit()
#     print(f"seeded {len(samples)} WellnessSample rows")


if __name__ == "__main__":
    main()
