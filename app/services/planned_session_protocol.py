"""Planned-session row protocol (F3): one lock per row, one table of allowed transitions.

Every mutation of a ``PlannedSession`` — a status/date PATCH, linking a logged workout,
feedback, persisting a prescription — used to read the row unlocked, decide, and write. Two
of them could interleave: a workout linking a session while a PATCH moved it, two logs both
"completing" one session, a prescription overwriting the content of a session a workout had
just completed. Now each takes ``SELECT … FOR UPDATE`` on the row first (with
``populate_existing``, so an object already in the session is refreshed to the row it locked,
not judged on stale values), validates against the row's CURRENT status and date, and commits
in the same transaction. The service function owns that commit.

Lock order with the athlete-state chain lock (F2): chain lock first, then the session row.
Only the workout path takes both, in that order; no path takes them the other way round.

Allowed status changes (``SessionStatus``):

==========================================  ==================  ==========================
from → to                                   ``PATCH`` status    a logged workout (link)
==========================================  ==================  ==========================
same → same                                 allowed (no-op)     —
PENDING → SKIPPED / RESCHEDULED             allowed             —
PENDING → COMPLETED                         409                 allowed
PENDING → MISSED                            409 (reconciliation only, ``missed_session_service``)
SKIPPED / RESCHEDULED / MISSED → PENDING    allowed iff the     —
                                            session's date is
                                            today or later
SKIPPED ↔ RESCHEDULED; MISSED → either      allowed             —
MISSED, date moved, status unchanged        409 — send ``pending`` with the new date
SKIPPED / RESCHEDULED / MISSED → COMPLETED  409                 allowed — a late log
                                                                (MISSED also by the
                                                                same-day match)
anything → MISSED                           409                 —
COMPLETED → anything; moving it             409                 explicit link → 409
==========================================  ==================  ==========================

``RESCHEDULED`` stays writable by an explicit PATCH for compatibility (ADR-0069 point 1).
Completion comes only from a logged workout, never from a PATCH. ``MISSED`` is the system's
inference that nothing was recorded; the athlete can overrule it by logging the session,
declaring it skipped, or moving it.

**Feedback follows the outcome (P2).** Feedback describes the outcome a session had when it
was given. Every status change supersedes the session's active feedback in the same
transaction, under the same row lock (:func:`supersede_feedback`): the row is kept for audit,
excluded from every reader, and new feedback may follow. A date move keeps the status, so it
keeps the feedback.
"""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mesocycle import PlannedSession, SessionStatus
from app.models.telemetry import SessionFeedback

#: Statuses a logged workout may complete by an EXPLICIT link (a late log of a session the
#: athlete skipped or moved). The implicit same-day match only ever links PENDING.
LINKABLE_BY_EXPLICIT_LOG = frozenset(
    {
        SessionStatus.PENDING,
        SessionStatus.SKIPPED,
        SessionStatus.RESCHEDULED,
        SessionStatus.MISSED,
    }
)

#: Statuses the implicit same-day match may complete. A MISSED session is the system's guess
#: that nothing was recorded; a log on that day disproves it.
LINKABLE_BY_SAME_DAY_MATCH = frozenset({SessionStatus.PENDING, SessionStatus.MISSED})

_PATCHABLE_TARGETS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.PENDING: frozenset({SessionStatus.SKIPPED, SessionStatus.RESCHEDULED}),
    SessionStatus.SKIPPED: frozenset({SessionStatus.PENDING, SessionStatus.RESCHEDULED}),
    SessionStatus.RESCHEDULED: frozenset({SessionStatus.PENDING, SessionStatus.SKIPPED}),
    SessionStatus.MISSED: frozenset(
        {SessionStatus.PENDING, SessionStatus.SKIPPED, SessionStatus.RESCHEDULED}
    ),
    SessionStatus.COMPLETED: frozenset(),
}


def conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


async def lock_planned_session(
    db: AsyncSession, session_id: int, user_id: int
) -> PlannedSession | None:
    """The owned session row, locked FOR UPDATE and refreshed from the locked row.

    ``None`` when it does not exist or is not ``user_id``'s. The lock lasts until this
    transaction commits or rolls back.
    """
    result = await db.execute(
        select(PlannedSession)
        .where(PlannedSession.id == session_id, PlannedSession.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalars().first()


async def supersede_feedback(
    db: AsyncSession, session: PlannedSession, new_status: SessionStatus | None
) -> int:
    """Mark the LOCKED session's active feedback superseded when its status is changing.

    Staged in the caller's transaction, so the outcome change and the supersession commit or
    roll back together. Returns how many rows were superseded (0 or 1 by the partial unique
    index).
    """
    if new_status is None or new_status == session.status:
        return 0
    result = await db.execute(
        update(SessionFeedback)
        .where(
            SessionFeedback.planned_session_id == session.id,
            SessionFeedback.superseded_at.is_(None),
        )
        .values(superseded_at=func.timezone("utc", func.now()))
        .execution_options(synchronize_session=False)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def check_patch(
    session: PlannedSession,
    *,
    new_status: SessionStatus | None,
    new_date: date | None,
    today: date,
) -> None:
    """Raise 409 unless the PATCH is an allowed change of the LOCKED row's current state."""
    current = SessionStatus(session.status)
    moves = new_date is not None and new_date != session.scheduled_date
    changes = new_status is not None and new_status != current
    if current is SessionStatus.COMPLETED and (moves or changes):
        raise conflict("A completed session cannot be moved or have its status changed")
    if current is SessionStatus.MISSED and moves and not changes:
        # MISSED is inferred from the date; a missed session on a later date would be
        # nonsense. Moving it reopens it, and says so.
        raise conflict(
            "A missed session is moved by reopening it: send status pending with the new date"
        )
    if not changes:
        return
    assert new_status is not None
    if new_status is SessionStatus.COMPLETED:
        raise conflict("A session is completed by logging a workout, not by setting its status")
    if new_status is SessionStatus.MISSED:
        raise conflict("A session becomes missed only by reconciliation, not by setting its status")
    if new_status not in _PATCHABLE_TARGETS[current]:
        raise conflict(f"A {current.value} session cannot become {new_status.value}")
    effective_date = new_date if new_date is not None else session.scheduled_date
    if new_status is SessionStatus.PENDING and effective_date < today:
        raise conflict(
            "A session can return to pending only on today or a later date; "
            "move it as part of the same change"
        )
