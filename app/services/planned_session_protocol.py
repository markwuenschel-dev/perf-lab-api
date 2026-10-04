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

=================================  ==================  =================================
from → to                          ``PATCH`` status    a logged workout (link)
=================================  ==================  =================================
same → same                        allowed (no-op)     —
PENDING → SKIPPED / RESCHEDULED    allowed             —
PENDING → COMPLETED                409                 allowed
SKIPPED / RESCHEDULED → PENDING    allowed iff the     —
                                   session's date is
                                   today or later
SKIPPED ↔ RESCHEDULED              allowed             —
SKIPPED / RESCHEDULED → COMPLETED  409                 allowed — explicit link only
                                                       (a late log)
COMPLETED → anything; moving it    409                 explicit link → 409
=================================  ==================  =================================

``RESCHEDULED`` stays writable by an explicit PATCH for compatibility (ADR-0069 point 1).
Completion comes only from a logged workout, never from a PATCH.

**Feedback pins the outcome.** Feedback describes the outcome a session had when it was
given, and is one-per-session (ADR-0070). A status change that would leave existing feedback
describing an outcome the session no longer has — reopening or rescheduling a skipped session,
or completing it with a late log — is a 409 while that feedback exists, checked under the
same row lock (:func:`ensure_feedback_allows`). A date move keeps the status, so it stays
allowed. Superseding the earlier feedback instead is the P2 design (it needs a migration).
"""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mesocycle import PlannedSession, SessionStatus
from app.models.telemetry import SessionFeedback

#: Statuses a logged workout may complete by an EXPLICIT link (a late log of a session the
#: athlete skipped or moved). The implicit same-day match only ever links PENDING.
LINKABLE_BY_EXPLICIT_LOG = frozenset(
    {SessionStatus.PENDING, SessionStatus.SKIPPED, SessionStatus.RESCHEDULED}
)

_PATCHABLE_TARGETS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.PENDING: frozenset({SessionStatus.SKIPPED, SessionStatus.RESCHEDULED}),
    SessionStatus.SKIPPED: frozenset({SessionStatus.PENDING, SessionStatus.RESCHEDULED}),
    SessionStatus.RESCHEDULED: frozenset({SessionStatus.PENDING, SessionStatus.SKIPPED}),
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


async def ensure_feedback_allows(
    db: AsyncSession, session: PlannedSession, new_status: SessionStatus | None
) -> None:
    """409 when changing the LOCKED session to ``new_status`` would strand its feedback."""
    if new_status is None or new_status == session.status:
        return
    feedback_id = await db.scalar(
        select(SessionFeedback.id).where(SessionFeedback.planned_session_id == session.id)
    )
    if feedback_id is not None:
        raise conflict(
            f"This session already has feedback for its {SessionStatus(session.status).value} "
            "outcome; changing the outcome would contradict it"
        )


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
    if not changes:
        return
    assert new_status is not None
    if new_status is SessionStatus.COMPLETED:
        raise conflict("A session is completed by logging a workout, not by setting its status")
    if new_status not in _PATCHABLE_TARGETS[current]:
        raise conflict(f"A {current.value} session cannot become {new_status.value}")
    effective_date = new_date if new_date is not None else session.scheduled_date
    if new_status is SessionStatus.PENDING and effective_date < today:
        raise conflict(
            "A session can return to pending only on today or a later date; "
            "move it as part of the same change"
        )
