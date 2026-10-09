"""Fold late workouts and benchmarks that were recorded but never applied (P3c).

Events dated before an athlete's state head are recorded record-only. ``APPLY_LATE_EVENTS`` folds
new ones in as they arrive; this folds in the ones already waiting, through the same function
(exact tail replay: the stored history is proven first, and a refusal leaves the event as it was)::

    python -m app.scripts.fold_late_events                 # report only: does every fold, keeps none
    python -m app.scripts.fold_late_events --apply         # write
    python -m app.scripts.fold_late_events --user-id 7     # one athlete

The report per athlete: how many events were found waiting, how many folded, the refusals by
code (``app.services.tail_replay_service`` lists them), events with no captured inputs (written
before ``a055``; they cannot be repaired), events skipped because another event shares their exact
timestamp and the arrival order cannot be recovered, and events that were folded by someone else
before their turn (``gone``).

Each event is decided under the athlete's chain lock, from fresh data, so the list is a hint and a
live writer cannot slip a tied event in behind it. An apply run holds the lock for one fold at a
time. A dry run is one transaction per athlete and holds that athlete's lock from its first
candidate until it rolls back, so that athlete's writers wait for it; keep it short. Its report is
what ``--apply`` would do only if the athlete's history does not change in between. Events that
are refused, not capturable or ambiguous are reported again on every run.

It does not need ``APPLY_LATE_EVENTS``: that flag is for events as they arrive. Applying is a
deliberate operator act; there is no automated revert (see docs/DEPLOY.md).
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.db import AsyncSessionLocal
from app.services.late_event_repair_service import RepairReport, repair_all


def _print(report: RepairReport) -> None:
    tag = "[fold-late-events]"
    if not report.athletes:
        print(f"{tag} Nothing to do: no record-only events waiting.")
        return
    for a in report.athletes:
        refused = ", ".join(f"{code}={n}" for code, n in sorted(a.refused.items())) or "none"
        print(
            f"{tag} user {a.user_id}: considered {a.considered}, folded {a.folded}, "
            f"refused [{refused}], not capturable {a.not_capturable}, "
            f"ambiguous tie {a.ambiguous_tie}, gone {a.gone}"
        )
    verb = "Folded" if report.applied else "Would fold"
    print(f"{tag} {verb} {report.folded} event(s) across {len(report.athletes)} athlete(s).")
    if not report.applied:
        print(f"{tag} Nothing was written. Run with --apply to write.")


async def _run(apply: bool, user_id: int | None) -> RepairReport:
    return await repair_all(AsyncSessionLocal, apply=apply, user_id=user_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the folds (default: report only)")
    parser.add_argument("--user-id", type=int, default=None, help="only this athlete")
    args = parser.parse_args(argv)
    _print(asyncio.run(_run(args.apply, args.user_id)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
