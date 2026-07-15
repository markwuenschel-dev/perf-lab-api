"""Nightly wearable pull — the scheduled cron entrypoint (Phase 2).

Pulls each stored wearable connection's recent daily data and upserts it into
``WellnessSample`` via the canonical readiness sink. A one-shot command that exits,
so it maps cleanly onto a scheduled cron job (ADR-0027 — no celery/redis). Does NOT
run migrations; the API service already does that on deploy.

Idempotent: the wellness sink upserts on (user_id, date, source), so re-runs never
duplicate rows.

Run (against a local DB):
    $env:DATABASE_URL = "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab"
    $env:APP_ENCRYPTION_KEY = "<fernet-key>"
    python -m app.scripts.sync_wearables --days 7

Cron job command:
    python -m app.scripts.sync_wearables
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.db import AsyncSessionLocal
from app.services import wearable_service


async def _run(*, days: int, user_id: int | None) -> None:
    async with AsyncSessionLocal() as db:
        if user_id is not None:
            written = await wearable_service.sync_user(db, user_id, days=days)
            print(f"[sync_wearables] user={user_id}: wrote {written} wellness rows")
            return
        results = await wearable_service.sync_all(db, days=days)
        total = results.pop("_total", 0)
        for key, n in sorted(results.items()):
            status = "FAILED" if n < 0 else f"{n} rows"
            print(f"[sync_wearables] {key}: {status}")
        print(f"[sync_wearables] done — {total} rows across {len(results)} connections")


def main() -> None:
    ap = argparse.ArgumentParser(description="Nightly wearable sync (Oura).")
    ap.add_argument(
        "--days",
        type=int,
        default=wearable_service.DEFAULT_SYNC_DAYS,
        help="Trailing days to pull on a first sync (no watermark).",
    )
    ap.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="Sync only this user's connection (default: all).",
    )
    args = ap.parse_args()
    asyncio.run(_run(days=args.days, user_id=args.user_id))


if __name__ == "__main__":
    main()
