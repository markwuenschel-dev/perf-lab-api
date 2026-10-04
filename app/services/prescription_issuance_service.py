"""Prescription issuance (P1): what the athlete is shown is an immutable revision.

Every read of today's session re-scores it (``prescribe_for_athlete``). Before P1 the result
overwrote ``planned_sessions.prescribed_content`` each time, so the workout an athlete had been
shown — and logged against — could change under them. Now the first evaluation ISSUES revision 1,
and later evaluations either serve that revision unchanged or issue a replacement, by one rule.

**Replacement rule (fork 4, decided 2026-10-04).** Ordinary changes — readiness, ranking,
exercises, loads — never replace an issued revision; they inform the next unissued session.
Only safety can:

=================================================  ==========================================
situation                                          outcome
=================================================  ==========================================
no current revision (incl. preserved legacy)       issue — ``first_issue`` / ``legacy_reissue``
current revision is a "safety could not run" rest  replace — ``safety_check_rerun``
the ISSUED workout fails current safety validation  replace — ``issued_no_longer_safe``
the safety signature changed toward a restriction,  replace — ``safety_outcome_changed``
or to a different restriction
a restriction cleared (fresh signature is "none")  keep — relaxing needs ``recheck`` (athlete)
same safety signature                              keep — same revision, identical content
=================================================  ==========================================

The **safety signature** is structured, not a boolean: its kind (``none`` / ``readiness_redirect``
/ ``safety_override`` / ``hard_violation`` / ``unevaluated``) plus the branch that fired and the
exact hard-violation and unevaluated codes. A different override, a different redirect, or a
different binding restriction is a different signature even when both are "restricted".

Guardrail: a freshly scored alternative passing validation does not prove the STORED workout is
still safe, so the stored content is itself re-validated against the current state.

Called under the planned-session row lock (F3); the caller commits.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logic.coaching_template_registry import get_structured_template_for_goal
from app.logic.constraint_engine import (
    SessionValidator,
    build_constraint_context,
    encode_session_candidate,
)
from app.models.mesocycle import PlannedSession
from app.models.prescription_revision import (
    ORIGIN_ISSUED,
    ORIGIN_LEGACY_UNKNOWN,
    PrescriptionRevision,
)
from app.schemas.prescription import PRESCRIPTION_ENGINE_VERSION, WorkoutPrescription
from app.schemas.state import UnifiedStateVector
from app.schemas.training_goals import TrainingGoal
from app.services.planned_session_protocol import lock_planned_session

KIND_NONE = "none"
KIND_REDIRECT = "readiness_redirect"
KIND_OVERRIDE = "safety_override"
KIND_HARD = "hard_violation"
KIND_UNEVALUATED = "unevaluated"

REASON_FIRST = "first_issue"
REASON_LEGACY = "legacy_reissue"
REASON_RERUN = "safety_check_rerun"
REASON_UNSAFE = "issued_no_longer_safe"
REASON_SAFETY_CHANGED = "safety_outcome_changed"
REASON_RECHECK = "athlete_recheck"


def safety_signature(rx: WorkoutPrescription) -> dict[str, Any]:
    """The structured safety outcome of a prescription (see module docstring)."""
    why = rx.why
    validation = why.validation if why is not None else None
    unevaluated = sorted(validation.unevaluated_hard) if validation is not None else []
    hard = sorted(validation.hard_violations) if validation is not None else []
    branch = why.prescription_branch if why is not None else None
    if unevaluated:
        kind = KIND_UNEVALUATED
    elif hard:
        kind = KIND_HARD
    elif branch is not None and branch.startswith("safety_"):
        kind = KIND_OVERRIDE
    elif branch is not None and branch.startswith("readiness_"):
        kind = KIND_REDIRECT
    else:
        kind = KIND_NONE
    return {
        "kind": kind,
        # The branch identifies WHICH override/redirect fired; for an unrestricted session it
        # is ordinary scoring and deliberately not part of the signature (fork 4).
        "branch": branch if kind in (KIND_OVERRIDE, KIND_REDIRECT) else None,
        "hard_violations": hard,
        "unevaluated": unevaluated,
    }


def revalidate_issued(
    stored: WorkoutPrescription,
    state: UnifiedStateVector,
    recent: list[dict[str, Any]],
    goal: TrainingGoal,
) -> list[str]:
    """Hard-safety problems the STORED workout has under the CURRENT state (empty = still safe).

    The same validator finalize runs, applied to what was issued rather than to a new candidate.
    """
    branch = (stored.why.prescription_branch if stored.why is not None else None) or "issued"
    report = SessionValidator(get_structured_template_for_goal(goal)).validate(
        encode_session_candidate(stored, goal, branch),
        build_constraint_context(state, recent, goal),
    )
    return list(report.hard_failed) + [f"unevaluated:{c}" for c in report.unevaluated_hard]


@dataclass(frozen=True)
class Decision:
    issue: bool
    reason: str | None


def decide(
    current: PrescriptionRevision | None,
    stored: WorkoutPrescription | None,
    fresh: WorkoutPrescription,
    *,
    stored_problems: list[str],
    allow_relax: bool,
) -> Decision:
    """Pure: keep the current revision, or issue ``fresh`` and why (the module's table)."""
    if current is None:
        return Decision(True, REASON_FIRST)
    if current.origin == ORIGIN_LEGACY_UNKNOWN:
        return Decision(True, REASON_LEGACY)
    if stored is None:
        return Decision(True, REASON_FIRST)
    old = current.safety_signature or {}
    new = safety_signature(fresh)
    if old.get("kind") == KIND_UNEVALUATED:
        return Decision(True, REASON_RERUN)
    if stored_problems:
        return Decision(True, REASON_UNSAFE)
    if new == old:
        return Decision(False, None)
    if new["kind"] == KIND_NONE:
        # A restriction cleared: relaxing an issued restriction raises the athlete's load on
        # a workout they may have started — only when they ask (recheck).
        return Decision(True, REASON_RECHECK) if allow_relax else Decision(False, None)
    return Decision(True, REASON_SAFETY_CHANGED)


def _content_hash(content: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


@dataclass(frozen=True)
class IssuedPrescription:
    """What is served for the session: the current revision's content."""

    prescription: WorkoutPrescription
    revision: PrescriptionRevision
    issued_now: bool


async def current_revision(
    db: AsyncSession, session: PlannedSession
) -> PrescriptionRevision | None:
    if session.current_revision_id is None:
        # The preserved legacy content, if any — so it is re-issued, never served.
        return (
            await db.execute(
                select(PrescriptionRevision)
                .where(
                    PrescriptionRevision.planned_session_id == session.id,
                    PrescriptionRevision.origin == ORIGIN_LEGACY_UNKNOWN,
                )
                .order_by(PrescriptionRevision.revision_no.desc())
                .limit(1)
            )
        ).scalars().first()
    return await db.get(PrescriptionRevision, session.current_revision_id)


async def issue_or_serve(
    db: AsyncSession,
    session: PlannedSession,
    fresh: WorkoutPrescription,
    *,
    state: UnifiedStateVector,
    recent: list[dict[str, Any]],
    goal: TrainingGoal,
    evaluation_date: date,
    allow_relax: bool,
) -> IssuedPrescription:
    """Keep or replace the session's revision, under the F3 row lock; the caller commits."""
    locked = await lock_planned_session(db, session.id, session.user_id)  # re-entrant
    assert locked is not None
    session = locked
    current = await current_revision(db, session)
    stored = (
        WorkoutPrescription.model_validate(current.content)
        if current is not None and current.origin == ORIGIN_ISSUED
        else None
    )
    problems = revalidate_issued(stored, state, recent, goal) if stored is not None else []
    decision = decide(current, stored, fresh, stored_problems=problems, allow_relax=allow_relax)
    if not decision.issue:
        assert current is not None and stored is not None
        return IssuedPrescription(stored, current, issued_now=False)

    last_no = await db.scalar(
        select(func.max(PrescriptionRevision.revision_no)).where(
            PrescriptionRevision.planned_session_id == session.id
        )
    )
    content = fresh.to_prescribed_content()
    signature = safety_signature(fresh)
    revision = PrescriptionRevision(
        planned_session_id=session.id,
        user_id=session.user_id,
        revision_no=(last_no or 0) + 1,  # increasing for the session's whole life
        content=content,
        content_hash=_content_hash(content),
        safety_kind=signature["kind"],
        safety_signature=signature,
        versions={"prescription_engine": PRESCRIPTION_ENGINE_VERSION},
        reason=decision.reason or REASON_FIRST,
        origin=ORIGIN_ISSUED,
        evaluation_date=evaluation_date,
    )
    db.add(revision)
    await db.flush()
    session.current_revision_id = revision.id
    session.prescribed_content = content  # the mirror existing readers use
    return IssuedPrescription(fresh, revision, issued_now=True)
