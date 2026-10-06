"""Roll back missed-session reconciliation (P2): every ``missed`` session back to ``pending``.

The rollback, in order:

1. Set ``RECONCILE_MISSED_SESSIONS=false`` and restart the API, so nothing writes ``missed``.
2. Dry-run this script and review the counts::

       python -m app.scripts.revert_missed_sessions            # report only
       python -m app.scripts.revert_missed_sessions --apply    # write

3. Only then deploy code that predates ``missed``: it cannot load a ``missed`` row.

``--apply`` refuses while the flag is on, because the next read would mark the sessions missed
again. Each session is locked (F3, ascending id) and re-checked under the lock. Feedback the
athlete gave about a miss describes an outcome the session no longer has, so it is superseded
in the same transaction — kept, not deleted. All changes commit once, at the end.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.models.mesocycle import PlannedSession, SessionStatus
from app.services.planned_session_protocol import lock_planned_session, supersede_feedback


@dataclass
class RevertReport:
    sessions: int
    users: int
    feedback_superseded: int
    applied: bool
    refused: str | None = None

    @property
    def exit_code(self) -> int:
        return 1 if self.refused else 0


async def revert_with_db(db: AsyncSession, apply: bool) -> RevertReport:
    rows = (
        await db.execute(
            select(PlannedSession.id, PlannedSession.user_id)
            .where(PlannedSession.status == SessionStatus.MISSED)
            .order_by(PlannedSession.id.asc())
        )
    ).all()
    users = len({user_id for _, user_id in rows})
    if not apply:
        return RevertReport(len(rows), users, 0, applied=False)
    if settings.RECONCILE_MISSED_SESSIONS:
        return RevertReport(
            len(rows),
            users,
            0,
            applied=False,
            refused="RECONCILE_MISSED_SESSIONS is on; the next read would mark them again",
        )

    reverted = superseded = 0
    for session_id, user_id in rows:
        session = await lock_planned_session(db, session_id, user_id)
        if session is None or session.status != SessionStatus.MISSED:
            continue  # a late log or a move got there first
        superseded += await supersede_feedback(db, session, SessionStatus.PENDING)
        session.status = SessionStatus.PENDING
        reverted += 1
    await db.commit()
    return RevertReport(reverted, users, superseded, applied=True)


async def revert(apply: bool) -> RevertReport:
    async with AsyncSessionLocal() as db:
        return await revert_with_db(db, apply)


def _print(report: RevertReport) -> None:
    if report.refused:
        print(f"[revert-missed] REFUSED: {report.refused}. Nothing was written.")
        return
    verb = "Reverted" if report.applied else "Would revert"
    print(f"[revert-missed] {verb} {report.sessions} missed session(s) across {report.users} user(s).")
    if report.applied:
        print(f"[revert-missed] Superseded {report.feedback_superseded} feedback row(s) about a miss.")
    else:
        print("[revert-missed] Run with --apply to write.")


if __name__ == "__main__":
    result = asyncio.run(revert(apply="--apply" in sys.argv))
    _print(result)
    sys.exit(result.exit_code)
