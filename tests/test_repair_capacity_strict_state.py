"""INT-15 W1-A slice S4 — the ADR-0055 capacity repair job decodes strictly.

The job appends an ``AthleteState`` row that becomes the athlete's newest state, so it is
a canonical mutation path. Before this slice it read through
``state_bridge.unified_from_athlete_row``, which reconstructs from the lossy legacy scalar
mirror when ``engine_state`` is missing or damaged — and then
``athlete_state_kwargs_from_unified`` restamped that reconstruction ``version: 2`` and
committed it as canonical. The repair "succeeded" and laundered an inference into the
decision path.

Two strict reads are pinned here, because closing only the first leaves the defect open:

* the **mutation base** (the latest row we copy forward), and
* every **watermark source** (the historical rows the high-watermark is computed from).

``test_malformed_history_row_cannot_set_the_watermark`` is the load-bearing one: with only
the first check, a malformed historical row is silently skipped and "the athlete's
historical high-watermark" quietly becomes "the highest value among rows that still parse".
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.engine.state_bridge import default_engine_state_dict
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.user import User
from app.scripts import repair_capacity_corruption as job

pytestmark = pytest.mark.asyncio

BASE_TS = datetime(2026, 7, 1, 12, 0)


def _payload(max_strength: float) -> dict:
    """A valid current-version payload carrying a specific max_strength."""
    eng = default_engine_state_dict()
    eng["x"]["max_strength"] = max_strength
    return eng


@pytest.fixture
async def definition(async_db) -> BenchmarkDefinition:
    """One definition for the whole file — observations need a real FK target."""
    d = BenchmarkDefinition(
        code="pl_e1rm_squat",
        name="Squat e1RM",
        domain="powerlifting",
        metric_type="load",
        unit="kg",
        better_direction="higher",
        observation_weight=1.0,
        standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    async_db.add(d)
    await async_db.flush()
    return d


async def _athlete(db, definition: BenchmarkDefinition, email: str) -> User:
    user = User(email=email, hashed_password="x", is_active=True)
    db.add(user)
    await db.flush()
    # The job only considers athletes carrying workout-extraction evidence.
    db.add(
        BenchmarkObservation(
            user_id=user.id,
            benchmark_definition_id=definition.id,
            source="workout_extraction",
            raw_value=100.0,
            observed_at=BASE_TS,
        )
    )
    await db.flush()
    return user


async def _add_state(db, user_id: int, offset_days: int, engine_state: object) -> AthleteState:
    """A state row with healthy legacy scalars — so any reconstruction WOULD succeed.

    That is the point: every refusal test below has a usable legacy mirror sitting right
    there. If the job ever falls back, these tests go green on laundered data, so each one
    also asserts on what was persisted.
    """
    row = AthleteState(
        user_id=user_id,
        timestamp=BASE_TS + timedelta(days=offset_days),
        c_met_aerobic=50.0,
        c_nm_force=1200.0,
        c_struct=60.0,
        b_met_anaerobic=9000.0,
        f_met_systemic=10.0,
        f_nm_peripheral=12.0,
        f_nm_central=8.0,
        f_struct_damage=15.0,
        s_struct_signal=0.0,
        habit_strength=0.5,
        skill_state={},
        engine_state=engine_state,
    )
    db.add(row)
    await db.flush()
    return row


async def _state_rows(db, user_id: int) -> list[AthleteState]:
    res = await db.execute(
        select(AthleteState)
        .where(AthleteState.user_id == user_id)
        .order_by(AthleteState.timestamp.asc())
    )
    return list(res.scalars().all())


async def _state_count(db, user_id: int) -> int:
    res = await db.execute(
        select(func.count()).select_from(AthleteState).where(AthleteState.user_id == user_id)
    )
    return int(res.scalar_one())


@pytest.fixture
def no_permissive_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the permissive path explode if anything reaches it.

    Stronger than asserting on outputs: it proves the fallback is unreachable rather than
    merely unused on these fixtures. `state_loading` imports the reconstruction helpers by
    name, so patch them at their point of use as well as at the source.
    """

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("permissive legacy reconstruction was invoked by a strict path")

    monkeypatch.setattr("app.engine.state_bridge.unified_from_athlete_row", _boom)
    monkeypatch.setattr("app.engine.state_bridge.capacity_from_legacy", _boom)
    monkeypatch.setattr("app.engine.state_loading.capacity_from_legacy", _boom)


# ── refusals: the mutation base ───────────────────────────────────────────────


async def test_malformed_latest_state_refuses_and_writes_nothing(
    async_db, definition, no_permissive_fallback
) -> None:
    """Malformed payload + a perfectly usable legacy mirror ⇒ still refuse.

    The mirror being usable is exactly what makes this the dangerous case: the old code
    reconstructed from it and committed the result as canonical.
    """
    user = await _athlete(async_db, definition, "malformed@t.io")
    await _add_state(async_db, user.id, 0, _payload(140.0))
    latest = await _add_state(async_db, user.id, 1, {"version": 2, "x": {}, "f": {}, "t": {}})
    await async_db.commit()
    before = await _state_count(async_db, user.id)

    report = await job.repair_with_db(async_db, apply=True)

    assert report.corrected == 0
    assert report.partial_failure
    assert report.exit_code == 1
    (refusal,) = report.refused
    assert refusal.user_id == user.id
    assert refusal.reason == "latest_state_incomplete"
    assert refusal.latest_state_row_id == latest.id
    assert refusal.declared_version == 2

    # No replacement row, and the damaged row is untouched — not restamped, not repaired.
    assert await _state_count(async_db, user.id) == before
    await async_db.refresh(latest)
    assert latest.engine_state == {"version": 2, "x": {}, "f": {}, "t": {}}


async def test_null_latest_state_refuses(async_db, definition, no_permissive_fallback) -> None:
    """A legacy row bootstraps from scalars today; under a mutation path it must not."""
    user = await _athlete(async_db, definition, "null@t.io")
    await _add_state(async_db, user.id, 0, _payload(140.0))
    latest = await _add_state(async_db, user.id, 1, None)
    await async_db.commit()
    before = await _state_count(async_db, user.id)

    report = await job.repair_with_db(async_db, apply=True)

    assert report.corrected == 0
    (refusal,) = report.refused
    assert refusal.reason == "latest_state_null"
    assert refusal.declared_version is None
    assert await _state_count(async_db, user.id) == before
    await async_db.refresh(latest)
    assert latest.engine_state is None


async def test_future_version_latest_state_refuses_without_restamping(
    async_db, definition, no_permissive_fallback
) -> None:
    """The data is fine; this reader is too old. Never downgrade it to v2."""
    user = await _athlete(async_db, definition, "future@t.io")
    await _add_state(async_db, user.id, 0, _payload(140.0))
    future = {"version": 99, "x": {"max_strength": 200.0}, "f": {}, "t": {}, "unknown": 1}
    latest = await _add_state(async_db, user.id, 1, future)
    await async_db.commit()
    before = await _state_count(async_db, user.id)

    report = await job.repair_with_db(async_db, apply=True)

    assert report.corrected == 0
    (refusal,) = report.refused
    assert refusal.reason == "latest_state_future_version"
    assert refusal.declared_version == 99
    assert await _state_count(async_db, user.id) == before

    await async_db.refresh(latest)
    assert latest.engine_state == future  # byte-for-byte: no restamp, no field loss


# ── refusals: the watermark sources ───────────────────────────────────────────


async def test_malformed_history_row_cannot_set_the_watermark(
    async_db, definition, no_permissive_fallback
) -> None:
    """A decodable latest row is not enough — the watermark's own sources must decode.

    Here the malformed row is the ONLY one holding the real high-watermark (180). Skipping
    it would silently "repair" the athlete to 150, the best surviving canonical value, and
    report success. That is a different claim than the one this job makes.
    """
    user = await _athlete(async_db, definition, "history@t.io")
    await _add_state(async_db, user.id, 0, _payload(150.0))
    damaged = await _add_state(async_db, user.id, 1, {"version": 2, "x": {}, "f": {}, "t": {}})
    await _add_state(async_db, user.id, 2, _payload(120.0))  # decodable, regressed, latest
    await async_db.commit()
    before = await _state_count(async_db, user.id)

    report = await job.repair_with_db(async_db, apply=True)

    assert report.corrected == 0
    assert report.partial_failure
    (refusal,) = report.refused
    assert refusal.reason == "watermark_source_invalid"
    assert str(damaged.id) in refusal.detail
    assert await _state_count(async_db, user.id) == before


# ── the happy path still works ────────────────────────────────────────────────


async def test_valid_canonical_series_is_repaired(async_db, definition, no_permissive_fallback) -> None:
    user = await _athlete(async_db, definition, "valid@t.io")
    await _add_state(async_db, user.id, 0, _payload(100.0))
    await _add_state(async_db, user.id, 1, _payload(180.0))  # the high-watermark
    await _add_state(async_db, user.id, 2, _payload(120.0))  # regressed — the damage
    await async_db.commit()
    before = await _state_count(async_db, user.id)

    report = await job.repair_with_db(async_db, apply=True)

    assert report.corrected == 1
    assert report.refused == []
    assert report.exit_code == 0
    assert await _state_count(async_db, user.id) == before + 1  # exactly one row

    rows = await _state_rows(async_db, user.id)
    newest = rows[-1]
    assert newest.engine_state["x"]["max_strength"] == 180.0
    assert newest.engine_state["version"] == 2
    assert newest.engine_state["correction"]["reason"] == "adr0055_capacity_corruption_repair"
    assert newest.engine_state["correction"]["from"] == 120.0
    # Unrelated vectors are carried across untouched, not defaulted.
    assert newest.engine_state["f"] == _payload(120.0)["f"]
    assert newest.engine_state["t"] == _payload(120.0)["t"]


async def test_uncorrupted_athlete_is_left_alone(async_db, definition, no_permissive_fallback) -> None:
    """Monotonic: never lower anything, and never write when there is nothing to restore."""
    user = await _athlete(async_db, definition, "healthy@t.io")
    await _add_state(async_db, user.id, 0, _payload(100.0))
    await _add_state(async_db, user.id, 1, _payload(180.0))
    await async_db.commit()
    before = await _state_count(async_db, user.id)

    report = await job.repair_with_db(async_db, apply=True)

    assert report.corrected == 0
    assert report.refused == []
    assert report.exit_code == 0
    assert await _state_count(async_db, user.id) == before


async def test_dry_run_writes_nothing(async_db, definition, no_permissive_fallback) -> None:
    user = await _athlete(async_db, definition, "dryrun@t.io")
    await _add_state(async_db, user.id, 0, _payload(180.0))
    await _add_state(async_db, user.id, 1, _payload(120.0))
    await async_db.commit()
    before = await _state_count(async_db, user.id)

    report = await job.repair_with_db(async_db, apply=False)

    assert report.corrected == 1  # would correct
    assert await _state_count(async_db, user.id) == before  # but did not


# ── partial failure stays visible ─────────────────────────────────────────────


async def test_mixed_batch_repairs_the_valid_and_names_the_refused(
    async_db, definition, no_permissive_fallback
) -> None:
    """One athlete's damage must not stop the others — but it must not vanish either."""
    good = await _athlete(async_db, definition, "good@t.io")
    await _add_state(async_db, good.id, 0, _payload(180.0))
    await _add_state(async_db, good.id, 1, _payload(120.0))

    bad = await _athlete(async_db, definition, "bad@t.io")
    await _add_state(async_db, bad.id, 0, _payload(140.0))
    await _add_state(async_db, bad.id, 1, None)

    await async_db.commit()
    good_before = await _state_count(async_db, good.id)
    bad_before = await _state_count(async_db, bad.id)

    report = await job.repair_with_db(async_db, apply=True)

    assert report.corrected == 1
    assert report.partial_failure
    assert report.exit_code == 1  # success is not reported while an athlete is unrepaired

    (refusal,) = report.refused
    assert refusal.user_id == bad.id
    assert refusal.reason == "latest_state_null"

    assert await _state_count(async_db, good.id) == good_before + 1
    assert await _state_count(async_db, bad.id) == bad_before


async def test_refusal_output_never_prints_raw_payloads(
    async_db, definition, no_permissive_fallback, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refusals name rows, not athlete data."""
    user = await _athlete(async_db, definition, "quiet@t.io")
    await _add_state(async_db, user.id, 0, _payload(140.0))
    secret = {"version": 2, "x": {}, "f": {}, "t": {}, "private_marker": "SHOULD_NOT_APPEAR"}
    await _add_state(async_db, user.id, 1, secret)
    await async_db.commit()

    await job.repair_with_db(async_db, apply=True)

    out = capsys.readouterr().out
    assert "SHOULD_NOT_APPEAR" not in out
    assert "latest_state_incomplete" in out
    assert f"user {user.id}" in out
