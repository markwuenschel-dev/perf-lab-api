"""P4-2a: one definition of "demonstrated strength", used by every watermark that means it.

ADR-0066 judges a strength decline against the best currently valid *demonstrated* e1RM. The code
used to take the max over every valid row, workout-derived estimates included. An estimate is a
model's extrapolation, not a lift: Epley could already overshoot what an athlete can do, and the
chart estimate (P4-2b) will too. Judging a test against it makes an honest test look like a decline.
So demonstrated strength is provenance, not a label: a measured max test, and nothing an estimate
produced.

Pinned here:

* the Python predicate and the SQL clause are the same rule, and both match an independently
  written table of what qualifies (a grid over every provenance combination, against the database);
* the decline machine's prior and the public ``best_currently_validated_e1rm`` are one function;
* both directions, through the real writers: a high training estimate does NOT make an honest test
  look like a decline (prior 150, passthrough, strength unchanged), and genuinely declining
  measured tests still open a candidate against 150 and hold strength;
* history is preserved only through an explicit, migration-record rule: a row the legacy migration
  relabelled counts; a live write of an unrecognised source does not; pending and unknown
  validity statuses do not;
* a candidate opened against a prior that is no longer demonstrated is retired before it can
  confirm a regression or cap a prescription (the upgrade regression);
* estimated-PR tracking is separate, formula-specific, and never a demonstrated watermark.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.logic import observation_authority as oa
from app.logic import strength_evidence as se
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.exercise import Exercise
from app.models.observation_mapping import ObservationMapping
from app.models.strength_decline_candidate import StrengthDeclineCandidate
from app.models.user import User
from app.repositories.benchmark_observation_repository import (
    demonstrated_strength_clause,
    demonstrated_watermark,
    estimated_pr_baseline,
)
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.services import benchmark_service, state_service
from app.services import strength_decline_service as sds
from app.services.state_service import initialize_athlete_state

CODE = "pl_e1rm_squat"


async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed(db) -> BenchmarkDefinition:
    db.add(Exercise(name="Back Squat", modality="Strength", movement_pattern="squat",
                    load_type="barbell", is_benchmark=True, e1rm_benchmark_code=CODE))
    d = BenchmarkDefinition(
        code=CODE, name="Squat e1RM", domain="powerlifting", metric_type="load", unit="kg",
        better_direction="higher", observation_weight=1.0,
        standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    db.add(d)
    await db.flush()
    db.add(ObservationMapping(benchmark_definition_id=d.id, target_vector="capacity",
                              target_key="max_strength", mapping_type="residual",
                              coefficient=1.0, intercept=0.0))
    await db.commit()
    await db.refresh(d)
    return d


async def _bench(db, uid: int, raw: float, when: datetime, *, source: str = "benchmark_test") -> None:
    await benchmark_service.create_observation(
        db, uid, BenchmarkObservationCreate(
            benchmark_code=CODE, raw_value=raw, source=source, observed_at=when.replace(tzinfo=None),
        ),
    )


async def _candidates(db, uid: int) -> list[StrengthDeclineCandidate]:
    return list((await db.execute(
        select(StrengthDeclineCandidate).where(StrengthDeclineCandidate.user_id == uid)
    )).scalars())


# ----- the rule, in Python and in SQL ------------------------------------------------------------ #

_SOURCE_TYPES = [oa.ST_ATHLETE_ENTRY, oa.ST_WORKOUT_EXTRACTION, oa.ST_LEGACY_UNKNOWN, None]
_EVIDENCE = [se.EV_DIRECT_MEASUREMENT, se.EV_PROTOCOL_GRADE_ESTIMATE, se.EV_ESTIMATED_FROM_TRAINING_SET,
             se.EV_LOWER_BOUND, se.EV_REPORTED_ESTIMATE, None]
_SEMANTICS = [se.VS_MEASURED, se.VS_ESTIMATED, se.VS_LOWER_BOUND, None]
_PROTOCOL = [oa.PV_VALID, oa.PV_INVALID, oa.PV_NOT_EVALUATED, None]


_MIGRATED = {
    "source_type": oa.ST_LEGACY_UNKNOWN, "evidence_type": se.EV_DIRECT_MEASUREMENT,
    "value_semantics": se.VS_MEASURED, "protocol_validity": oa.PV_NOT_EVALUATED,
    "provenance_operation": oa.OP_SCHEMA_BACKFILL, "migration_version": "a028",
    "authority_resolution_reason": "schema_backfill_conservative_legacy",
    "observation_model": "benchmark_protocol",
}


def test_the_predicate_on_the_cases_that_matter():
    ok: dict[str, str | None] = {
        "source_type": oa.ST_ATHLETE_ENTRY, "evidence_type": se.EV_DIRECT_MEASUREMENT,
        "value_semantics": se.VS_MEASURED, "protocol_validity": oa.PV_VALID,
    }
    assert oa.is_demonstrated_strength(**ok)
    # A protocol-grade measured test counts, as the decline machine itself can act on one.
    assert oa.is_demonstrated_strength(**{**ok, "evidence_type": se.EV_PROTOCOL_GRADE_ESTIMATE})
    assert oa.is_demonstrated_strength(**{**ok, "protocol_validity": oa.PV_NOT_EVALUATED})  # unproven ≠ invalid
    assert not oa.is_demonstrated_strength(**{**ok, "protocol_validity": oa.PV_INVALID})
    assert not oa.is_demonstrated_strength(**{**ok, "source_type": oa.ST_WORKOUT_EXTRACTION})
    assert not oa.is_demonstrated_strength(**{**ok, "evidence_type": se.EV_ESTIMATED_FROM_TRAINING_SET})
    assert not oa.is_demonstrated_strength(**{**ok, "evidence_type": se.EV_REPORTED_ESTIMATE})
    assert not oa.is_demonstrated_strength(**{**ok, "value_semantics": se.VS_ESTIMATED})
    # Every unstated field fails closed.
    for field in ("source_type", "evidence_type", "value_semantics"):
        assert not oa.is_demonstrated_strength(**{**ok, field: None})


def test_a_legacy_unknown_row_counts_only_with_the_whole_migration_record():
    assert oa.is_demonstrated_strength(**_MIGRATED)
    for field, forged in [
        ("provenance_operation", oa.OP_LIVE_WRITE),
        ("provenance_operation", None),
        ("migration_version", None),
        ("migration_version", "a099"),
        ("authority_resolution_reason", "anything_else"),
        ("authority_resolution_reason", None),
        ("observation_model", "workout_e1rm_extraction_v1"),
        ("evidence_type", se.EV_ESTIMATED_FROM_TRAINING_SET),
        ("value_semantics", se.VS_ESTIMATED),
    ]:
        assert not oa.is_demonstrated_strength(**{**_MIGRATED, field: forged}), (field, forged)
    # And the bare source type, which is what a live write of an unrecognised source gets:
    assert not oa.is_demonstrated_strength(
        source_type=oa.ST_LEGACY_UNKNOWN, evidence_type=se.EV_DIRECT_MEASUREMENT,
        value_semantics=se.VS_MEASURED, protocol_validity=oa.PV_VALID,
    )


def _expected_demonstrated(st, ev, vs, pv) -> bool:
    """What qualifies, written out independently of the code under test (current provenance only;
    the migration rule has its own tests)."""
    return (
        st == "athlete_entry"
        and ev in ("direct_measurement", "protocol_grade_estimate")
        and vs == "measured"
        and pv != "invalid"
    )


async def test_the_sql_clause_and_the_predicate_both_match_the_independent_table_over_every_combination(async_db):
    user = await _user(async_db, "dem-grid@test.com")
    d = await _seed(async_db)
    combos = list(itertools.product(_SOURCE_TYPES, _EVIDENCE, _SEMANTICS, _PROTOCOL))
    assert len(combos) == 4 * 6 * 4 * 4 == 384
    rows = []
    for i, (st, ev, vs, pv) in enumerate(combos):
        rows.append(BenchmarkObservation(
            user_id=user.id, benchmark_definition_id=d.id, raw_value=100.0 + i,
            observed_at=datetime.now(UTC).replace(tzinfo=None), validity_status="valid",
            source="benchmark_test", source_type=st, evidence_type=ev, value_semantics=vs,
            protocol_validity=pv,
        ))
    async_db.add_all(rows)
    await async_db.commit()
    by_id = {r.id: c for r, c in zip(rows, combos, strict=True)}

    in_sql = set((await async_db.execute(
        select(BenchmarkObservation.id).where(demonstrated_strength_clause())
    )).scalars())

    expected = {rid for rid, c in by_id.items() if _expected_demonstrated(*c)}
    assert in_sql == expected
    assert len(expected) == 2 * 1 * 1 * 3 and any(  # athlete_entry × {direct, protocol_grade} × measured × 3 non-invalid
        c == ("athlete_entry", "protocol_grade_estimate", "measured", "valid") for c in by_id.values()
        if _expected_demonstrated(*c)
    )
    for st, ev, vs, pv in by_id.values():
        assert oa.is_demonstrated_strength(
            source_type=st, evidence_type=ev, value_semantics=vs, protocol_validity=pv
        ) is _expected_demonstrated(st, ev, vs, pv)


async def test_the_sql_clause_admits_a_migrated_legacy_row_and_none_of_its_forgeries(async_db):
    """The migration branch, field by field, in SQL: the whole record qualifies; changing any one
    field of it does not (so a live write cannot claim it)."""
    user = await _user(async_db, "dem-migrated@test.com")
    d = await _seed(async_db)
    variants: list[tuple[str, dict[str, str | None]]] = [("whole record", {})]
    for field, forged in [
        ("provenance_operation", "live_write"), ("provenance_operation", None),
        ("migration_version", "a099"), ("migration_version", None),
        ("authority_resolution_reason", "anything_else"), ("authority_resolution_reason", None),
        ("observation_model", "workout_e1rm_extraction_v1"), ("observation_model", None),
        ("evidence_type", "estimated_from_training_set"), ("value_semantics", "estimated"),
        ("source_type", "athlete_entry_typo"),
    ]:
        variants.append((f"{field}={forged}", {field: forged}))
    rows = []
    for i, (_, change) in enumerate(variants):
        fields = {**_MIGRATED, **change}
        rows.append(BenchmarkObservation(
            user_id=user.id, benchmark_definition_id=d.id, raw_value=100.0 + i,
            observed_at=datetime.now(UTC).replace(tzinfo=None), validity_status="valid",
            source="benchmark_test", **fields,
        ))
    async_db.add_all(rows)
    await async_db.commit()

    in_sql = set((await async_db.execute(
        select(BenchmarkObservation.id).where(demonstrated_strength_clause())
    )).scalars())

    assert {r.raw_value for r in rows if r.id in in_sql} == {100.0}  # only the whole record


async def test_only_a_positively_valid_row_is_ever_demonstrated(async_db):
    """Ingestion requires ``valid``, and the API accepts any string: a pending (or unknown) row has
    received no state application and must not become validated strength."""
    user = await _user(async_db, "dem-status@test.com")
    d = await _seed(async_db)
    for i, status in enumerate(["valid", "pending", "unknown_status", "", "VALID", "quarantined", "invalid"]):
        async_db.add(BenchmarkObservation(
            user_id=user.id, benchmark_definition_id=d.id, raw_value=100.0 + 10 * i,
            observed_at=datetime.now(UTC).replace(tzinfo=None), validity_status=status,
            source="benchmark_test", source_type="athlete_entry", evidence_type="direct_measurement",
            value_semantics="measured", protocol_validity="valid",
        ))
    await async_db.commit()

    assert await demonstrated_watermark(async_db, user.id, CODE) == 100.0  # only the "valid" one


async def test_quarantined_and_invalid_rows_are_never_demonstrated(async_db):
    user = await _user(async_db, "dem-q@test.com")
    d = await _seed(async_db)
    base = {
        "user_id": user.id, "benchmark_definition_id": d.id,
        "observed_at": datetime.now(UTC).replace(tzinfo=None), "source": "benchmark_test",
        "source_type": "athlete_entry", "evidence_type": "direct_measurement",
        "value_semantics": "measured", "protocol_validity": "valid",
    }
    async_db.add_all([
        BenchmarkObservation(raw_value=150.0, validity_status="valid", **base),
        BenchmarkObservation(raw_value=200.0, validity_status="quarantined", **base),
        BenchmarkObservation(raw_value=210.0, validity_status="invalid", **base),
        BenchmarkObservation(raw_value=220.0, validity_status="valid", quarantined_at=datetime.now(UTC).replace(tzinfo=None), **base),
    ])
    await async_db.commit()
    assert await demonstrated_watermark(async_db, user.id, CODE) == 150.0


# ----- one function behind both watermarks -------------------------------------------------------- #

async def test_the_decline_prior_and_the_public_best_validated_figure_are_the_same_function(async_db):
    user = await _user(async_db, "dem-same@test.com")
    d = await _seed(async_db)
    base = datetime.now(UTC).replace(tzinfo=None)
    for raw, st, ev, vs in [
        (150.0, "athlete_entry", "direct_measurement", "measured"),
        (175.0, "workout_extraction", "estimated_from_training_set", "estimated"),
        (160.0, "athlete_entry", "reported_estimate", "estimated"),
        (140.0, "legacy_unknown", "direct_measurement", "measured"),
    ]:
        async_db.add(BenchmarkObservation(
            user_id=user.id, benchmark_definition_id=d.id, raw_value=raw, observed_at=base,
            validity_status="valid", source="benchmark_test", source_type=st, evidence_type=ev,
            value_semantics=vs, protocol_validity="valid",
        ))
    await async_db.commit()

    public = await state_service.best_currently_validated_e1rm(async_db, user.id, CODE)
    prior = await sds._prior_watermark(async_db, user.id, CODE, exclude_observation_id=-1)

    assert public == prior == 150.0  # the 175 estimate and the 160 reported estimate do not count


async def test_the_decline_prior_excludes_the_observation_being_judged(async_db):
    """The prior is what the athlete had demonstrated BEFORE this test; if the test is itself a
    new max it must not be compared against itself."""
    user = await _user(async_db, "dem-excl@test.com")
    d = await _seed(async_db)
    base = datetime.now(UTC).replace(tzinfo=None)
    rows = [
        BenchmarkObservation(
            user_id=user.id, benchmark_definition_id=d.id, raw_value=raw, observed_at=base,
            validity_status="valid", source="benchmark_test", source_type="athlete_entry",
            evidence_type="direct_measurement", value_semantics="measured", protocol_validity="valid",
        )
        for raw in (150.0, 160.0)
    ]
    async_db.add_all(rows)
    await async_db.commit()

    assert await sds._prior_watermark(async_db, user.id, CODE, rows[1].id) == 150.0
    assert await sds._prior_watermark(async_db, user.id, CODE, rows[0].id) == 160.0


# ----- both directions through the real decline machine ------------------------------------------ #

async def _max_strength(db, uid: int) -> float:
    from app.engine.state_bridge import unified_from_athlete_row
    from app.repositories.athlete_context_repository import AthleteContextRepository

    row = await AthleteContextRepository(db).get_latest_state(uid)
    assert row is not None
    return float(unified_from_athlete_row(row).capacity_x.max_strength)


async def _last_observation(db, uid: int) -> BenchmarkObservation:
    row = (await db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.user_id == uid)
        .order_by(BenchmarkObservation.id.desc()).limit(1)
    )).scalar_one()
    await db.refresh(row)
    return row


async def test_a_high_training_estimate_does_not_make_an_honest_test_look_like_a_decline(async_db):
    user = await _user(async_db, "dem-fp@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, t0)  # a tested max
    # Training says the athlete is at least this strong (an estimate that overshoots the test).
    await _bench(async_db, user.id, 175.0, t0 + timedelta(days=5), source="workout_extraction")
    before = await _max_strength(async_db, user.id)

    await _bench(async_db, user.id, 150.0, t0 + timedelta(days=10))  # an honest repeat of the test

    assert await _candidates(async_db, user.id) == []  # 150 vs the 175 estimate would have been -14%
    last = await _last_observation(async_db, user.id)
    assert (last.decline_transition_status, last.applied_capacity_effect) == (None, "bidirectional_update")  # passthrough
    assert await _max_strength(async_db, user.id) >= before - 0.01  # and strength was not held back or lowered
    assert await state_service.best_currently_validated_e1rm(async_db, user.id, CODE) == 150.0


async def test_genuinely_declining_measured_tests_still_open_a_candidate_against_the_tested_max(async_db):
    user = await _user(async_db, "dem-tp@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, t0)
    await _bench(async_db, user.id, 175.0, t0 + timedelta(days=5), source="workout_extraction")  # noise
    before = await _max_strength(async_db, user.id)

    await _bench(async_db, user.id, 132.0, t0 + timedelta(days=10))  # a real drop from the tested 150

    (candidate,) = await _candidates(async_db, user.id)
    assert candidate.prior_mean == 150.0  # judged against the demonstrated watermark, not the 175 estimate
    assert candidate.observed_value == 132.0 and candidate.status == "active"
    last = await _last_observation(async_db, user.id)
    assert (last.decline_transition_status, last.applied_capacity_effect) == ("decline_candidate", "none")  # pending
    assert await _max_strength(async_db, user.id) == pytest.approx(before)  # held: one test never regresses it


async def test_legacy_provenance_keeps_its_protection_through_the_migration_record(async_db):
    """A tested max written before provenance existed carries what migrations a025/a028 stamped on
    it. It still anchors the decline machine, so one low test after the migration is a candidate,
    not a silent 'first measurement'."""
    user = await _user(async_db, "dem-legacy@test.com")
    d = await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    async_db.add(BenchmarkObservation(
        user_id=user.id, benchmark_definition_id=d.id, raw_value=150.0,
        observed_at=t0.replace(tzinfo=None) - timedelta(days=60), validity_status="valid",
        source="benchmark_test", source_type="legacy_unknown", evidence_type="direct_measurement",
        value_semantics="measured", observation_model="benchmark_protocol",
        collection_mode="legacy_unknown", capacity_effect="none", protocol_validity="not_evaluated",
        provenance_operation="schema_backfill", migration_version="a028",
        authority_resolution_reason="schema_backfill_conservative_legacy",
    ))
    await async_db.commit()
    assert await state_service.best_currently_validated_e1rm(async_db, user.id, CODE) == 150.0

    await _bench(async_db, user.id, 132.0, t0)

    (candidate,) = await _candidates(async_db, user.id)
    assert candidate.prior_mean == 150.0


async def test_a_new_write_of_an_unrecognised_source_is_not_demonstrated(async_db):
    """Review repro: source "something_weird", raw 500 resolves to legacy_unknown / measured / direct /
    valid with no capacity authority. It must not become the public validated watermark."""
    user = await _user(async_db, "dem-weird@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    await _bench(async_db, user.id, 500.0, datetime.now(UTC) + timedelta(minutes=1), source="something_weird")
    row = await _last_observation(async_db, user.id)
    assert row.source_type == "legacy_unknown" and row.provenance_operation == "live_write"  # the premise

    assert await state_service.best_currently_validated_e1rm(async_db, user.id, CODE) is None
    assert await demonstrated_watermark(async_db, user.id, CODE) is None


# ----- candidates opened against a prior that is no longer demonstrated -------------------------------- #

async def _old_code_candidate(db, user: User, d: BenchmarkDefinition, *, prior: float, trigger: BenchmarkObservation):
    """The candidate the previous watermark would have opened: judged against ``prior``."""
    assessment = sds.assess_decline(
        prior_mean=prior, observed_value=trigger.raw_value, error=None, mean_fatigue=0.0
    )
    candidate = sds._build_candidate(
        user_id=user.id, observation=trigger, definition=d, assessment=assessment, severe=False
    )
    db.add(candidate)
    await db.commit()
    await db.refresh(candidate)
    return candidate


async def test_upgrade_regression_a_candidate_opened_against_an_estimate_cannot_confirm_a_regression(async_db):
    """Review repro. Under the old watermark a 165 kg training estimate was the prior, so a measured
    150 opened a candidate against 165. Seven days later a measured 145 confirmed it: material
    against 165, though against the real 150 it is a 5-point drop, inside the 6.3 error band."""
    user = await _user(async_db, "dem-upgrade@test.com")
    d = await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, t0)  # the tested max, applied
    await _bench(async_db, user.id, 165.0, t0 + timedelta(days=1), source="workout_extraction")  # the estimate
    trigger = (await async_db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.raw_value == 150.0)
    )).scalar_one()
    stale = await _old_code_candidate(async_db, user, d, prior=165.0, trigger=trigger)
    assert stale.status == "active" and stale.prior_mean == 165.0  # the state the upgrade meets
    assert sds.assess_decline(prior_mean=165.0, observed_value=145.0, error=None, mean_fatigue=0.0).is_material
    assert not sds.assess_decline(prior_mean=150.0, observed_value=145.0, error=None, mean_fatigue=0.0).is_material
    before = await _max_strength(async_db, user.id)

    await _bench(async_db, user.id, 145.0, t0 + timedelta(days=9))  # past the retest interval

    await async_db.refresh(stale)
    assert (stale.status, stale.resolution_reason) == ("dismissed", "prior_not_demonstrated")
    assert stale.confirmation_observation_id is None
    assert [c for c in await _candidates(async_db, user.id) if c.status == "confirmed"] == []
    last = await _last_observation(async_db, user.id)
    assert (last.decline_transition_status, last.applied_capacity_effect) == ("no_material_decline", "none")
    assert await _max_strength(async_db, user.id) == pytest.approx(before)  # nothing regressed


async def test_a_stale_candidate_no_longer_caps_a_prescription(async_db):
    user = await _user(async_db, "dem-ceiling@test.com")
    d = await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, t0)
    await _bench(async_db, user.id, 165.0, t0 + timedelta(days=1), source="workout_extraction")
    trigger = (await async_db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.raw_value == 150.0)
    )).scalar_one()
    stale = await _old_code_candidate(async_db, user, d, prior=165.0, trigger=trigger)

    decision = await sds.resolve_prescription_basis(
        async_db, user.id, code=CODE, latest_raw=150.0, current_axis=60.0,
        rules=d.standardization_rules, mode=sds.BASIS_MODE_ON,
    )

    assert (decision.ceiling, decision.candidate_id) == (None, None)  # ignored…
    await async_db.refresh(stale)
    assert stale.status == "active"  # …but not rewritten: the resolver stays side-effect free


async def test_a_current_candidate_still_caps_a_prescription(async_db):
    user = await _user(async_db, "dem-ceiling2@test.com")
    d = await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, t0)
    await _bench(async_db, user.id, 132.0, t0 + timedelta(days=10))
    (candidate,) = await _candidates(async_db, user.id)

    decision = await sds.resolve_prescription_basis(
        async_db, user.id, code=CODE, latest_raw=132.0, current_axis=60.0,
        rules=d.standardization_rules, mode=sds.BASIS_MODE_ON,
    )

    assert decision.candidate_id == candidate.id and decision.ceiling is not None


async def test_a_candidate_whose_prior_row_was_quarantined_is_retired_not_confirmed(async_db):
    """A data correction is not a decline: if the test the candidate was measured against is
    quarantined, the candidate's prior is gone."""
    user = await _user(async_db, "dem-quar@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, t0)
    await _bench(async_db, user.id, 132.0, t0 + timedelta(days=10))
    (candidate,) = await _candidates(async_db, user.id)
    assert candidate.prior_mean == 150.0
    prior_row = (await async_db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.raw_value == 150.0)
    )).scalar_one()
    prior_row.validity_status = "quarantined"
    prior_row.quarantined_at = datetime.now(UTC).replace(tzinfo=None)
    await async_db.commit()

    await _bench(async_db, user.id, 130.0, t0 + timedelta(days=20))

    await async_db.refresh(candidate)
    assert (candidate.status, candidate.resolution_reason) == ("dismissed", "prior_not_demonstrated")


# ----- estimated-PR tracking is separate ----------------------------------------------------------- #

async def test_the_estimated_pr_bar_is_per_formula_and_never_a_demonstrated_watermark(async_db):
    user = await _user(async_db, "dem-pr@test.com")
    d = await _seed(async_db)
    base = datetime.now(UTC).replace(tzinfo=None)

    def row(raw, formula, st="workout_extraction", ev="estimated_from_training_set", vs="estimated"):
        return BenchmarkObservation(
            user_id=user.id, benchmark_definition_id=d.id, raw_value=raw, observed_at=base,
            validity_status="valid", source="benchmark_test" if st == "athlete_entry" else "workout_extraction",
            source_type=st, evidence_type=ev,
            value_semantics=vs, protocol_validity="valid", formula=formula,
        )

    async_db.add_all([row(160.0, "epley"), row(172.0, "rpe_rir_chart"), row(140.0, None, "athlete_entry", "direct_measurement", "measured")])
    await async_db.commit()

    assert await estimated_pr_baseline(async_db, user.id, CODE, formula="epley") == 160.0  # not the chart's 172
    assert await estimated_pr_baseline(async_db, user.id, CODE, formula="rpe_rir_chart") == 172.0
    assert await estimated_pr_baseline(async_db, user.id, CODE, formula="other") == 140.0  # the tested max still counts
    assert await demonstrated_watermark(async_db, user.id, CODE) == 140.0  # estimates never do
