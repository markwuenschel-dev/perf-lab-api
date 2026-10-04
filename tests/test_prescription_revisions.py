"""P1 — what the athlete is shown is an immutable, revisioned prescription.

Pinned (``app/services/prescription_issuance_service.py``):

* **Fork 4 (decisive):** changed inputs that produce different exercises or loads, with an
  equivalent safety decision, serve the SAME revision and IDENTICAL stored content.
* Safety restrictions appearing / changing replace the revision (visible reason, previous
  revision preserved); clearing one never relaxes on GET, only on the athlete's recheck.
* The stored workout is itself re-validated against current safety (a passing fresh
  alternative proves nothing about it).
* Legacy content is preserved and re-issued, never served; revisions are immutable in the DB;
  a log points at exactly the revision it answered; /next-session issues nothing; a decision is
  recorded only when a revision is issued.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth import get_current_user
from app.core.db import get_db
from app.main import app
from app.models.mesocycle import BlockGoal, BlockStatus, MesocycleBlock, PlannedSession
from app.models.prescription_revision import (
    ORIGIN_ISSUED,
    ORIGIN_LEGACY_UNKNOWN,
    PrescriptionRevision,
)
from app.models.telemetry import PrescriptionDecision
from app.models.user import AthleteProfile, User
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.schemas.prescription import ValidationSummary, WorkoutPrescription
from app.schemas.workouts import WorkoutLog
from app.services import prescription_issuance_service as pis
from app.services import prescription_service, state_service

# ── pure: the signature and the decision table ────────────────────────────────────────


def _rx(branch: str = "strength_max", *, hard: list[str] | None = None,
        unevaluated: list[str] | None = None, minutes: int = 60) -> WorkoutPrescription:
    from app.schemas.prescription import PrescriptionExplanation

    return WorkoutPrescription(
        type="Strength", focus="x", rationale="y", duration_min=minutes,
        why=PrescriptionExplanation(
            state_drivers=[], goal_alignment="Strength", constraints_applied=[],
            source_alignment=[], prescription_branch=branch,
            validation=ValidationSummary(
                passed=not (hard or unevaluated), hard_violations=hard or [],
                unevaluated_hard=unevaluated or [],
            ),
            warnings=[],
        ),
    )


def _rev(rx: WorkoutPrescription, origin: str = ORIGIN_ISSUED) -> PrescriptionRevision:
    return PrescriptionRevision(
        revision_no=1, content=rx.to_prescribed_content(), content_hash="h", reason="first_issue",
        origin=origin, safety_signature=pis.safety_signature(rx),
    )


@pytest.mark.parametrize(
    ("rx", "kind"),
    [
        (_rx("strength_max"), "none"),
        (_rx("readiness_deload_day"), "readiness_redirect"),
        (_rx("safety_systemic_metabolic"), "safety_override"),
        (_rx("strength_max", hard=["wrist"]), "hard_violation"),
        (_rx("strength_max", unevaluated=["universal_fatigue_ok"]), "unevaluated"),
    ],
)
def test_safety_signature_kinds(rx, kind):
    assert pis.safety_signature(rx)["kind"] == kind


def test_the_signature_is_structured_not_boolean():
    a = pis.safety_signature(_rx("safety_systemic_metabolic"))
    b = pis.safety_signature(_rx("safety_regional_tissue"))
    assert a["kind"] == b["kind"] == "safety_override" and a != b
    # Ordinary scoring differences are NOT part of the signature (fork 4).
    assert pis.safety_signature(_rx("strength_max")) == pis.safety_signature(_rx("hyp_upper_split"))


_NONE, _OVR_A, _OVR_B = _rx("strength_max"), _rx("safety_systemic_metabolic"), _rx("safety_regional_tissue")


@pytest.mark.parametrize(
    ("current", "fresh", "problems", "relax", "expected"),
    [
        (None, _NONE, [], False, (True, "first_issue")),
        ("legacy", _NONE, [], False, (True, "legacy_reissue")),
        (_rx(unevaluated=["c"], minutes=0), _NONE, [], False, (True, "safety_check_rerun")),
        (_NONE, _NONE, ["wrist"], False, (True, "issued_no_longer_safe")),
        (_NONE, _rx("hyp_upper_split", minutes=45), [], False, (False, None)),  # fork 4
        (_NONE, _OVR_A, [], False, (True, "safety_outcome_changed")),           # appears
        (_OVR_A, _OVR_B, [], False, (True, "safety_outcome_changed")),          # changes
        (_OVR_A, _OVR_A, [], False, (False, None)),
        (_OVR_A, _NONE, [], False, (False, None)),                              # clears: keep
        (_OVR_A, _NONE, [], True, (True, "athlete_recheck")),                   # recheck relaxes
    ],
    ids=["first", "legacy", "rerun", "unsafe", "fork4-keep", "appears", "changes",
         "same-restriction", "clears-keep", "clears-recheck"],
)
def test_decision_table(current, fresh, problems, relax, expected):
    if current is None:
        rev, stored = None, None
    elif current == "legacy":
        rev, stored = _rev(_NONE, ORIGIN_LEGACY_UNKNOWN), None
    else:
        rev, stored = _rev(current), current
    d = pis.decide(rev, stored, fresh, stored_problems=problems, allow_relax=relax)
    assert (d.issue, d.reason) == expected


def test_the_stored_workout_is_revalidated_against_current_state():
    """Guardrail 1, with the real validator: a gymnastics skill session that was fine is a hard
    violation once wrist stress rises — whatever a fresh alternative would look like."""
    from datetime import UTC as _UTC

    from app.schemas.state import UnifiedStateVector

    state = UnifiedStateVector(
        timestamp=datetime.now(_UTC), c_met_aerobic=50.0, c_nm_force=50.0, c_struct=50.0,
        b_met_anaerobic=50.0, f_met_systemic=20.0, f_nm_peripheral=15.0, f_nm_central=20.0,
        f_struct_damage=10.0, s_struct_signal=20.0, habit_strength=0.6, skill_state={},
    )
    stored = _rx("gym_skill")
    assert pis.revalidate_issued(stored, state, [], "Gymnastics") == []
    state.tissue_t.wrist = 80.0
    assert pis.revalidate_issued(stored, state, [], "Gymnastics")


# ── through the real /planning/today ──────────────────────────────────────────────────


@pytest.fixture
def factory(async_db: AsyncSession) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(async_db.bind, expire_on_commit=False, autoflush=False)


async def _athlete(factory, email: str, equipment: list[str] | None = None) -> tuple[int, int]:
    today = date.today()
    async with factory() as db:
        user = User(email=email, hashed_password="h", is_active=True)
        db.add(user)
        await db.commit()
        db.add(AthleteProfile(user_id=user.id, equipment=equipment or ["barbell", "dumbbell"]))
        block = MesocycleBlock(
            user_id=user.id, goal=BlockGoal.STRENGTH, duration_weeks=4, sessions_per_week=3,
            start_date=today, deload_every_n_weeks=4, status=BlockStatus.ACTIVE,
        )
        db.add(block)
        await db.commit()
        session = PlannedSession(
            block_id=block.id, user_id=user.id, scheduled_date=today, week_number=1,
            day_of_week=today.isoweekday(), category="Heavy Lower", modality="Strength",
            domain="strength",
        )
        db.add(session)
        await db.commit()
        await state_service.initialize_athlete_state(db, user.id)
        return user.id, session.id


async def _call(factory, uid: int, method: str = "GET", path: str = "/v1/planning/today",
                **kw: Any):
    async def _db():
        async with factory() as db:
            yield db

    async with factory() as db:
        user = await db.get(User, uid)

    async def _user():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.request(method, path, params={"goal": "Strength"}, **kw)
    finally:
        app.dependency_overrides.clear()


async def _revisions(factory, sid: int) -> list[PrescriptionRevision]:
    async with factory() as db:
        return list((await db.execute(
            select(PrescriptionRevision).where(PrescriptionRevision.planned_session_id == sid)
            .order_by(PrescriptionRevision.revision_no)
        )).scalars().all())


async def _decisions(factory, uid: int) -> int:
    async with factory() as db:
        return int((await db.execute(
            select(func.count()).select_from(PrescriptionDecision)
            .where(PrescriptionDecision.athlete_id == uid)
        )).scalar_one())


@pytest.mark.asyncio
async def test_fork4_changed_inputs_same_safety_serve_the_same_revision(factory, monkeypatch):
    """THE decisive test. Between two reads the athlete's equipment changes, so fresh scoring
    selects different exercises — but the safety decision is the same. The second read must
    serve the same revision with identical content, and issue nothing new."""
    uid, sid = await _athlete(factory, "p1-fork4@test.com", ["barbell", "dumbbell"])
    fresh: list[WorkoutPrescription] = []
    real = prescription_service._score_prescription

    def recording(state, ctx):
        rx = real(state, ctx)
        fresh.append(rx.model_copy(deep=True))
        return rx

    monkeypatch.setattr(prescription_service, "_score_prescription", recording)

    first = await _call(factory, uid)
    assert first.status_code == 200, first.text
    async with factory() as db:
        profile = (await db.execute(
            select(AthleteProfile).where(AthleteProfile.user_id == uid)
        )).scalars().one()
        profile.equipment = ["bodyweight"]
        await db.commit()
    second = await _call(factory, uid)
    assert second.status_code == 200, second.text

    # The inputs really changed what scoring produces...
    assert [e.name for e in fresh[0].exercises] != [e.name for e in fresh[1].exercises]
    assert pis.safety_signature(fresh[0]) == pis.safety_signature(fresh[1])
    # ...yet the athlete keeps the workout already shown.
    a, b = first.json(), second.json()
    assert b["revision"]["id"] == a["revision"]["id"]
    assert b["revision"]["issued_now"] is False and a["revision"]["issued_now"] is True
    assert b["prescription"] == a["prescription"]
    assert len(await _revisions(factory, sid)) == 1
    assert await _decisions(factory, uid) == 1  # serving an issued revision decides nothing


@pytest.mark.asyncio
async def test_a_safety_restriction_appearing_replaces_and_preserves_the_previous(factory):
    """Real state change: very high systemic fatigue. The fresh prescription is restricted (a
    safety override that also trips a hard rule — hard violations take precedence in the
    signature), and the stored workout fails the fatigue rule too."""
    from app.engine.state_bridge import athlete_state_kwargs_from_unified
    from app.models.athlete_state import AthleteState

    uid, sid = await _athlete(factory, "p1-appear@test.com")
    first = (await _call(factory, uid)).json()
    async with factory() as db:
        state = await state_service.load_current_state(db, uid)
        assert state is not None
        state.f_met_systemic = 95.0
        state.fatigue_f.metabolic = 95.0
        state.timestamp = datetime.now(UTC).replace(tzinfo=None)
        db.add(AthleteState(user_id=uid, **athlete_state_kwargs_from_unified(state)))
        await db.commit()
    second = (await _call(factory, uid)).json()

    assert second["revision"]["id"] != first["revision"]["id"]
    assert second["revision"]["revision_no"] == 2
    # Real fatigue this high also makes the STORED strength workout fail the fatigue rule, so
    # the first-checked (and more specific) reason is the stored-workout recheck (guardrail 1).
    assert second["revision"]["reason"] == "issued_no_longer_safe"
    assert second["revision"]["safety_kind"] == "hard_violation"
    revs = await _revisions(factory, sid)
    assert [r.revision_no for r in revs] == [1, 2]
    assert revs[0].content == first["prescription"]  # the previous revision is preserved intact


async def _issue_with(factory, monkeypatch, uid: int, rx: WorkoutPrescription, method="GET",
                      path="/v1/planning/today"):
    monkeypatch.setattr(prescription_service, "_score_prescription", lambda state, ctx: rx.model_copy(deep=True))
    return (await _call(factory, uid, method, path)).json()


@pytest.mark.asyncio
async def test_a_different_restriction_replaces(factory, monkeypatch):
    uid, _ = await _athlete(factory, "p1-change@test.com")
    a = await _issue_with(factory, monkeypatch, uid, _OVR_A)
    b = await _issue_with(factory, monkeypatch, uid, _OVR_B)
    assert b["revision"]["id"] != a["revision"]["id"]
    assert b["revision"]["reason"] == "safety_outcome_changed"


@pytest.mark.asyncio
async def test_a_cleared_restriction_is_kept_until_the_athlete_rechecks(factory, monkeypatch):
    uid, _ = await _athlete(factory, "p1-clear@test.com")
    restricted = await _issue_with(factory, monkeypatch, uid, _OVR_A)
    kept = await _issue_with(factory, monkeypatch, uid, _NONE)
    assert kept["revision"]["id"] == restricted["revision"]["id"]
    assert kept["prescription"] == restricted["prescription"]
    relaxed = await _issue_with(factory, monkeypatch, uid, _NONE, "POST", "/v1/planning/today/recheck")
    assert relaxed["revision"]["id"] != restricted["revision"]["id"]
    assert relaxed["revision"]["reason"] == "athlete_recheck"


@pytest.mark.asyncio
async def test_an_unevaluated_rest_is_rechecked_on_the_next_read(factory, monkeypatch):
    uid, _ = await _athlete(factory, "p1-rerun@test.com")
    rest = await _issue_with(factory, monkeypatch, uid, _rx(unevaluated=["universal_fatigue_ok"], minutes=0))
    after = await _issue_with(factory, monkeypatch, uid, _NONE)
    assert after["revision"]["id"] != rest["revision"]["id"]
    assert after["revision"]["reason"] == "safety_check_rerun"


@pytest.mark.asyncio
async def test_a_stored_workout_that_is_no_longer_safe_is_replaced(factory, monkeypatch):
    uid, _ = await _athlete(factory, "p1-unsafe@test.com")
    first = await _issue_with(factory, monkeypatch, uid, _NONE)
    monkeypatch.setattr(pis, "revalidate_issued", lambda *a, **k: ["now unsafe"])
    second = await _issue_with(factory, monkeypatch, uid, _rx("hyp_upper_split"))
    assert second["revision"]["id"] != first["revision"]["id"]
    assert second["revision"]["reason"] == "issued_no_longer_safe"


@pytest.mark.asyncio
async def test_legacy_content_is_preserved_and_reissued_never_served(factory):
    uid, sid = await _athlete(factory, "p1-legacy@test.com")
    legacy = {"type": "Old", "focus": "legacy", "rationale": "pre-a051", "duration_min": 30}
    async with factory() as db:
        db.add(PrescriptionRevision(
            planned_session_id=sid, user_id=uid, revision_no=0, content=legacy,
            content_hash="md5", reason="legacy_preserved", origin=ORIGIN_LEGACY_UNKNOWN,
        ))
        row = await db.get(PlannedSession, sid)
        assert row is not None
        row.prescribed_content = legacy
        await db.commit()
    body = (await _call(factory, uid)).json()
    assert body["prescription"]["type"] != "Old"
    assert body["revision"]["revision_no"] == 1 and body["revision"]["reason"] == "legacy_reissue"
    revs = await _revisions(factory, sid)
    assert [(r.revision_no, r.origin) for r in revs] == [(0, ORIGIN_LEGACY_UNKNOWN), (1, ORIGIN_ISSUED)]
    assert revs[0].content == legacy


@pytest.mark.asyncio
async def test_revisions_are_immutable_in_the_database(factory):
    uid, sid = await _athlete(factory, "p1-immutable@test.com")
    await _call(factory, uid)
    async with factory() as db:
        with pytest.raises(Exception, match="immutable"):
            await db.execute(text(
                "UPDATE prescription_revisions SET content = '{}'::jsonb WHERE planned_session_id = :s"
            ), {"s": sid})


def _log(planned_session_id: int, revision_id: int | None = None) -> WorkoutLog:
    return WorkoutLog(
        timestamp=datetime.combine(date.today(), time(9, 0), tzinfo=UTC), modality="Running",
        duration_minutes=30.0, session_rpe=6.0, planned_session_id=planned_session_id,
        prescription_revision_id=revision_id,
    )


@pytest.mark.asyncio
async def test_a_log_keeps_the_revision_it_answered(factory, monkeypatch):
    uid, sid = await _athlete(factory, "p1-log@test.com")
    shown = await _issue_with(factory, monkeypatch, uid, _NONE)
    rev1 = shown["revision"]["id"]
    async with factory() as db:
        await state_service.process_new_workout(db, uid, _log(sid, rev1))
    async with factory() as db:
        logged = (await db.execute(
            select(WorkoutLogORM).where(WorkoutLogORM.user_id == uid)
        )).scalars().one()
    assert logged.prescription_revision_id == rev1


@pytest.mark.asyncio
async def test_a_log_cannot_claim_another_sessions_revision(factory, monkeypatch):
    from fastapi import HTTPException

    uid_a, sid_a = await _athlete(factory, "p1-claim-a@test.com")
    uid_b, _ = await _athlete(factory, "p1-claim-b@test.com")
    other = await _issue_with(factory, monkeypatch, uid_b, _NONE)
    async with factory() as db:
        with pytest.raises(HTTPException) as exc:
            await state_service.process_new_workout(db, uid_a, _log(sid_a, other["revision"]["id"]))
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_next_session_never_issues(factory):
    uid, sid = await _athlete(factory, "p1-next@test.com")
    resp = await _call(factory, uid, "GET", "/v1/next-session")
    assert resp.status_code == 200, resp.text
    assert await _revisions(factory, sid) == []
    async with factory() as db:
        row = await db.get(PlannedSession, sid)
        assert row is not None
    assert row.current_revision_id is None and row.prescribed_content is None


@pytest.mark.asyncio
async def test_a_preview_of_a_planned_session_records_no_decision(factory):
    """/next-session with a planned session today decides nothing (it issues nothing); the
    unplanned case keeps its decision row (tests/test_decision_telemetry_routes.py)."""
    uid, _ = await _athlete(factory, "p1-preview-telemetry@test.com")
    assert (await _call(factory, uid, "GET", "/v1/next-session")).status_code == 200
    assert await _decisions(factory, uid) == 0
