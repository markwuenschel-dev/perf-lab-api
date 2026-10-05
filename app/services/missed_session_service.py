"""Missed-session reconciliation (P2).

A past session nobody logged stayed ``pending`` forever: no reader could tell "not yet" from
"never". Reconciliation writes ``missed`` — the system's inference that training was not
recorded, distinct from ``skipped``, which only the athlete declares.

**One explicit ``as_of`` per reconciliation.** A session is missed when
``scheduled_date < as_of − 1 day``. The day of grace absorbs the skew between the server's
date and the athlete's until per-user timezones exist: reconciling as of Oct 5 marks Oct 3
missed and leaves Oct 4 pending.

**F3 row protocol.** Candidates are found unlocked, then each row is locked in ascending id
order (two reconciliations of one athlete cannot deadlock) and its status AND date are
re-checked under the lock: a concurrent log or move that committed first wins, and the row is
left alone. The status change and the supersession of any active feedback on that row are
staged together and commit in one transaction, which this function owns.

Callers are read endpoints, before their read query runs, so nothing of theirs is staged.
Behind ``RECONCILE_MISSED_SESSIONS`` (off by default): ``missed`` may be written only once
every reader understands it.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.mesocycle import PlannedSession, SessionStatus
from app.services.planned_session_protocol import lock_planned_session, supersede_feedback

#: Days after ``scheduled_date`` before an unlogged session counts as missed.
GRACE_DAYS = 1


def missed_cutoff(as_of: date) -> date:
    """Sessions dated strictly before this are missed when still pending."""
    return as_of - timedelta(days=GRACE_DAYS)


def is_missed(scheduled_date: date, as_of: date) -> bool:
    return scheduled_date < missed_cutoff(as_of)


async def reconcile_missed(db: AsyncSession, user_id: int, as_of: date) -> int:
    """Mark ``user_id``'s past PENDING sessions MISSED as of ``as_of``; returns how many.

    Commits its own transaction when it changed anything. A no-op (0) when the flag is off.
    """
    if not settings.RECONCILE_MISSED_SESSIONS:
        return 0
    cutoff = missed_cutoff(as_of)
    candidate_ids = (
        await db.scalars(
            select(PlannedSession.id)
            .where(
                PlannedSession.user_id == user_id,
                PlannedSession.status == SessionStatus.PENDING,
                PlannedSession.scheduled_date < cutoff,
            )
            .order_by(PlannedSession.id.asc())
        )
    ).all()
    if not candidate_ids:
        return 0

    changed = 0
    for session_id in sorted(candidate_ids):
        session = await lock_planned_session(db, session_id, user_id)
        # Re-check under the lock: a log or a move that committed while this waited wins.
        if (
            session is None
            or session.status != SessionStatus.PENDING
            or not is_missed(session.scheduled_date, as_of)
        ):
            continue
        await supersede_feedback(db, session, SessionStatus.MISSED)
        session.status = SessionStatus.MISSED
        changed += 1
    # Commit even when every candidate was taken by a concurrent writer: it releases the
    # row locks this transaction took.
    await db.commit()
    return changed
