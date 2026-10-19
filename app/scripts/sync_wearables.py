"""Manual wearable catch-up: sync every stored connection (or one user's) now.

Not scheduled anywhere. Wearable data arrives when the athlete shows up: the web syncs a stale
connection on load and when the check-in opens (ADR-0027, superseded: the production box sleeps
overnight, so a nightly job cannot be relied on). This command is for an operator who wants every
connection caught up by hand.

It is **catch-up, not backfill**: a connection that has synced before restarts from the day
before its ``last_sync_at``, whatever ``--days`` says. ``--days`` only sizes a connection's first
sync.

Each connection is synced under its sync lock (``app.services.wearable_sync_lock``), so a run
beside a user's own sync waits for it rather than refreshing the same credentials twice. This
process has its own bound on lock connections, separate from each API worker's.

Exit status is 1 if any connection failed. A failed connection may still have saved some days
first; the report says how many, and names a day whose write was in flight (it may or may not
have committed). Idempotent: the wellness sink upserts on (user_id, date, source).

Run (against a local DB):
    $env:DATABASE_URL = "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab"
    $env:APP_ENCRYPTION_KEY = "<fernet-key>"
    python -m app.scripts.sync_wearables --days 7
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.db import session_scope
from app.services import wearable_service


def _describe(report: wearable_service.ConnectionSyncReport) -> str:
    if report.ok:
        return f"{report.rows_confirmed} rows"
    text = f"FAILED after {report.rows_confirmed} saved rows ({report.error})"
    if report.in_doubt_day is not None:
        text += f"; the write for {report.in_doubt_day} may or may not have been saved"
    return text


async def _run(*, days: int, user_id: int | None) -> int:
    # session_scope: a session quarantined by a stuck cleanup is not closed under it, so the
    # reports below are still printed.
    async with session_scope() as db:
        targets = await wearable_service.sync_targets(db, user_id=user_id)
        if user_id is not None and not targets:
            print(f"[sync_wearables] user={user_id}: no connection")
            return 1
        reports = [await wearable_service.sync_one(db, cid, key, days=days) for cid, key in targets]
    for report in reports:
        print(f"[sync_wearables] {report.key}: {_describe(report)}")
    failed = sum(not r.ok for r in reports)
    total = sum(r.rows_confirmed for r in reports)
    print(f"[sync_wearables] done: {total} rows saved across {len(reports)} connections, "
          f"{failed} failed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Manual wearable catch-up (Oura).")
    ap.add_argument(
        "--days",
        type=int,
        default=wearable_service.DEFAULT_SYNC_DAYS,
        help="Trailing days for a connection's FIRST sync only (no watermark yet).",
    )
    ap.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="Sync only this user's connection (default: all).",
    )
    args = ap.parse_args(argv)
    return asyncio.run(_run(days=args.days, user_id=args.user_id))


if __name__ == "__main__":
    sys.exit(main())
