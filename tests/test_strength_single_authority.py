"""One strength fact, one write authority (S2 decision 3, O1).

Onboarding and Settings used to write squat/bench/deadlift as bare profile numbers, while
Assess records characterized strength evidence. These tests pin the single-authority model:

* every canonical-lift strength fact — onboarding, Assess, or a Settings edit — goes through
  the E1 characterization service, which writes the evidence and DERIVES the profile columns;
* the profile columns are a projection and seed field, never an independently writable truth;
* onboarding gets no weaker rules than Assess (and additionally needs a date for a tested max
  or a set);
* legacy profile-only numbers stay seeds and never become a load basis by themselves.

Driven through the real app, route to row to selection.
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.logic import strength_calibration as sc
from app.logic.prescription_evidence import REASON_INSUFFICIENT_CHARACTERIZATION
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.user import AthleteProfile, User
from app.repositories.benchmark_observation_repository import select_prescription_basis

pytestmark = pytest.mark.asyncio

SQUAT, BENCH, DEADLIFT = "pl_e1rm_squat", "pl_e1rm_bench", "pl_e1rm_deadlift"
_LIFTS = {
    SQUAT: ("Back Squat", "squat"),
    BENCH: ("Bench Press", "push_horizontal"),
    DEADLIFT: ("Conventional Deadlift", "hinge"),
}
_NOW = datetime.now(UTC).replace(microsecond=0)


def _ago(days: float) -> str:
    return (_NOW - timedelta(days=days)).isoformat()


async def _catalog(db) -> None:
    for code, (name, pattern) in _LIFTS.items():
        db.add(Exercise(
            name=name, modality="Strength", movement_pattern=pattern, load_type="barbell",
            is_benchmark=True, e1rm_benchmark_code=code,
        ))
        db.add(BenchmarkDefinition(
            code=code, name=f"{name} e1RM", domain="powerlifting", metric_type="load", unit="kg",
            better_direction="higher", observation_weight=1.0,
            standardization_rules={"floor": 20.0, "cap": 320.0},
        ))
    await db.commit()


async def _athlete(client, db, email: str) -> tuple[dict[str, str], int]:
    reg = await client.post("/auth/register", json={"email": email, "password": "securepass1"})
    assert reg.status_code == 201, reg.text
    tok = await client.post(
        "/auth/token",
        data={"username": email, "password": "securepass1"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    user_id = (await db.execute(select(User.id).where(User.email == email))).scalar_one()
    return {"Authorization": f"Bearer {tok.json()['access_token']}"}, user_id


async def _onboard(client, headers, strength: list[dict], **extra):
    body = {"experience_level": "intermediate", "goal": "Powerlifting", "strength": strength, **extra}
    return await client.post("/v1/onboard", json=body, headers=headers)


async def _profile(db, user_id: int) -> AthleteProfile:
    return (await db.execute(
        select(AthleteProfile)
        .where(AthleteProfile.user_id == user_id)
        .execution_options(populate_existing=True)
    )).scalar_one()


async def _evidence(db, user_id: int, code: str) -> list[BenchmarkObservation]:
    return list((await db.execute(
        select(BenchmarkObservation)
        .join(BenchmarkDefinition, BenchmarkObservation.benchmark_definition_id == BenchmarkDefinition.id)
        .where(BenchmarkObservation.user_id == user_id, BenchmarkDefinition.code == code)
        .order_by(BenchmarkObservation.id)
    )).scalars().all())


async def _basis(db, user_id: int, code: str):
    return (await select_prescription_basis(db, user_id, {code}, as_of=_NOW))[code]


async def _state_rows(db, user_id: int) -> int:
    return (await db.execute(
        select(func.count()).select_from(AthleteState).where(AthleteState.user_id == user_id)
    )).scalar_one()


# ── onboarding goes through E1 ───────────────────────────────────────────────────

async def test_an_onboarding_tested_max_is_evidence_projection_seed_and_basis(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "o1-tested@test.com")

    resp = await _onboard(http_client, headers, [
        {"benchmark_code": SQUAT, "method": "tested_max", "value_kg": 140.0, "performed_at": _ago(2)},
    ])
    assert resp.status_code == 200, resp.text

    (row,) = await _evidence(async_db, user_id, SQUAT)
    assert (row.source_type, row.collection_mode, row.evidence_type, row.value_semantics) == (
        "athlete_entry", "onboarding_onramp", "direct_measurement", "measured"
    )
    assert (await _profile(async_db, user_id)).squat_1rm == 140.0
    # The state was seeded once, from the same fact — the onramp evidence did not seed a second time.
    assert await _state_rows(async_db, user_id) == 1
    assert (await _basis(async_db, user_id, SQUAT)).selected is not None


async def test_onboarding_sets_are_computed_by_the_server_and_qualify_only_through_the_gate(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "o1-sets@test.com")

    resp = await _onboard(http_client, headers, [
        {"benchmark_code": SQUAT, "method": "rep_set", "load_kg": 120.0, "reps": 3, "rpe": 9.0, "performed_at": _ago(1)},
        {"benchmark_code": BENCH, "method": "rep_set", "load_kg": 80.0, "reps": 8, "rpe": 9.0, "performed_at": _ago(1)},
    ])
    assert resp.status_code == 200, resp.text

    profile = await _profile(async_db, user_id)
    assert profile.squat_1rm == sc.e1rm_from_set(120.0, 3)
    assert profile.bench_1rm == sc.e1rm_from_set(80.0, 8)
    assert (await _basis(async_db, user_id, SQUAT)).selected is not None
    bench = await _basis(async_db, user_id, BENCH)
    assert bench.selected is None
    assert bench.reason == REASON_INSUFFICIENT_CHARACTERIZATION


async def test_an_onboarding_estimate_is_retained_and_seeds_but_is_never_a_basis(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "o1-estimate@test.com")

    resp = await _onboard(http_client, headers, [
        {"benchmark_code": DEADLIFT, "method": "estimate", "value_kg": 180.0},
    ])
    assert resp.status_code == 200, resp.text

    (row,) = await _evidence(async_db, user_id, DEADLIFT)
    assert (row.evidence_type, row.affects_prescription) == ("reported_estimate", False)
    assert (await _profile(async_db, user_id)).deadlift_1rm == 180.0
    assert await _state_rows(async_db, user_id) == 1
    assert (await _basis(async_db, user_id, DEADLIFT)).selected is None


# ── onboarding gets no weaker rules ──────────────────────────────────────────────

async def test_onboarding_refuses_bare_lift_numbers(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "o1-bare@test.com")

    resp = await http_client.post(
        "/v1/onboard", json={"goal": "Powerlifting", "squat_1rm_kg": 140.0}, headers=headers
    )
    assert resp.status_code == 422
    assert "squat_1rm_kg" in resp.text
    assert (await _profile(async_db, user_id)).squat_1rm is None


@pytest.mark.parametrize("method_fields", [
    {"method": "tested_max", "value_kg": 140.0},
    {"method": "rep_set", "load_kg": 120.0, "reps": 3, "rpe": 9.0},
])
async def test_onboarding_needs_a_date_for_a_tested_max_or_a_set(http_client, async_db, method_fields):
    await _catalog(async_db)
    headers, _ = await _athlete(http_client, async_db, "o1-date@test.com")
    resp = await _onboard(http_client, headers, [{"benchmark_code": SQUAT, **method_fields}])
    assert resp.status_code == 422


async def test_onboarding_refuses_two_reports_for_one_lift(http_client, async_db):
    await _catalog(async_db)
    headers, _ = await _athlete(http_client, async_db, "o1-dupe@test.com")
    resp = await _onboard(http_client, headers, [
        {"benchmark_code": SQUAT, "method": "tested_max", "value_kg": 140.0, "performed_at": _ago(2)},
        {"benchmark_code": SQUAT, "method": "estimate", "value_kg": 150.0},
    ])
    assert resp.status_code == 422


@pytest.mark.parametrize("bad_report", [
    {"benchmark_code": SQUAT, "method": "tested_max", "value_kg": 140.0,
     "performed_at": (_NOW + timedelta(days=1)).isoformat()},
    {"benchmark_code": "sprint_300m_time", "method": "estimate", "value_kg": 50.0},
])
async def test_an_invalid_report_is_refused_before_anything_is_written(http_client, async_db, bad_report):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "o1-invalid@test.com")

    resp = await _onboard(http_client, headers, [
        {"benchmark_code": BENCH, "method": "estimate", "value_kg": 90.0},
        bad_report,
    ])
    assert resp.status_code == 400
    assert await _evidence(async_db, user_id, BENCH) == []
    assert await _state_rows(async_db, user_id) == 0
    assert (await _profile(async_db, user_id)).bench_1rm is None


# ── legacy numbers stay seeds ────────────────────────────────────────────────────

async def test_a_legacy_profile_1rm_stays_a_seed_until_new_e1_information_arrives(http_client, async_db):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "o1-legacy@test.com")
    profile = await _profile(async_db, user_id)
    profile.squat_1rm = 150.0  # written before O1, with no method or date
    await async_db.commit()

    assert (await _basis(async_db, user_id, SQUAT)).selected is None
    assert (await http_client.get("/v1/profile", headers=headers)).json()["squat_1rm_kg"] == 150.0

    resp = await http_client.post("/v1/benchmarks/strength-evidence", headers=headers, json={
        "benchmark_code": SQUAT, "method": "tested_max", "value_kg": 140.0, "performed_at": _ago(3),
    })
    assert resp.status_code == 200, resp.text
    assert (await _profile(async_db, user_id)).squat_1rm == 140.0
    assert (await _basis(async_db, user_id, SQUAT)).selected is not None


# ── Settings cannot write a bare canonical-lift number ───────────────────────────

@pytest.mark.parametrize("field", ["squat_1rm_kg", "bench_1rm_kg", "deadlift_1rm_kg"])
async def test_settings_cannot_write_a_bare_canonical_lift_number(http_client, async_db, field):
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "o1-settings@test.com")

    refused = await http_client.patch("/v1/profile", json={field: 200.0}, headers=headers)
    assert refused.status_code == 422
    assert "strength-evidence" in refused.text

    profile = await _profile(async_db, user_id)
    assert (profile.squat_1rm, profile.bench_1rm, profile.deadlift_1rm) == (None, None, None)
    # The rest of the profile stays editable.
    assert (await http_client.patch("/v1/profile", json={"bodyweight_kg": 80.0}, headers=headers)).status_code == 200


# ── the projection has exactly one rule ──────────────────────────────────────────

async def test_the_profile_value_follows_the_newest_performed_report(http_client, async_db):
    """Not the latest submission: an older performance entered later does not overwrite a
    newer one, and an undated report ranks below every dated one."""
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "o1-projection@test.com")

    async def report(**body) -> None:
        resp = await http_client.post(
            "/v1/benchmarks/strength-evidence", headers=headers, json={"benchmark_code": SQUAT, **body}
        )
        assert resp.status_code == 200, resp.text

    await report(method="tested_max", value_kg=150.0, performed_at=_ago(2))
    assert (await _profile(async_db, user_id)).squat_1rm == 150.0
    await report(method="tested_max", value_kg=130.0, performed_at=_ago(20))
    assert (await _profile(async_db, user_id)).squat_1rm == 150.0
    await report(method="estimate", value_kg=170.0)
    assert (await _profile(async_db, user_id)).squat_1rm == 150.0
    await report(method="rep_set", load_kg=140.0, reps=2, rpe=9.5, performed_at=_ago(1))
    assert (await _profile(async_db, user_id)).squat_1rm == sc.e1rm_from_set(140.0, 2)


async def test_only_the_strength_report_writer_can_move_the_profile_value(http_client, async_db):
    """The generic observations API accepts `observation_model` from clients, so a client-chosen
    marker must never make a generic observation a projection candidate. The generic API cannot
    set a performance date today; the row is dated directly so that provenance alone — not an
    accident of ordering — is what keeps it out."""
    await _catalog(async_db)
    headers, user_id = await _athlete(http_client, async_db, "o1-marker@test.com")

    generic = await http_client.post("/v1/benchmarks/observations", headers=headers, json={
        "benchmark_code": SQUAT, "raw_value": 300.0, "source": "manual",
        "collection_mode": "retest", "observation_model": "strength_report_v1",
    })
    assert generic.status_code == 200, generic.text
    (forged,) = await _evidence(async_db, user_id, SQUAT)
    forged.performed_at = (_NOW - timedelta(days=1)).replace(tzinfo=None)
    await async_db.commit()

    report = await http_client.post("/v1/benchmarks/strength-evidence", headers=headers, json={
        "benchmark_code": SQUAT, "method": "tested_max", "value_kg": 140.0, "performed_at": _ago(5),
    })
    assert report.status_code == 200, report.text

    assert (await _profile(async_db, user_id)).squat_1rm == 140.0


def test_every_canonical_lift_projects_onto_a_real_profile_column() -> None:
    import sqlalchemy as sa

    from app.data.exercise_bulk import bulk_exercises
    from app.scripts.seed_exercises import EXERCISES
    from app.services.strength_evidence_service import CANONICAL_LIFT_PROFILE_COLUMNS

    catalog_codes = {
        row["e1rm_benchmark_code"] for row in [*EXERCISES, *bulk_exercises()] if row.get("e1rm_benchmark_code")
    }
    assert set(CANONICAL_LIFT_PROFILE_COLUMNS) == catalog_codes
    columns = set(sa.inspect(AthleteProfile).columns.keys())
    assert set(CANONICAL_LIFT_PROFILE_COLUMNS.values()) <= columns
