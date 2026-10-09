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
* two named rules share one definition of current provenance: the public ``best_currently_validated_e1rm``
  (demonstrated strength only) and the decline machine's prior (that, plus the legacy migration's
  record); migrated history never reads as validated strength;
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
    decline_protection_clause,
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


def test_a_legacy_unknown_row_is_decline_protection_only_with_the_whole_migration_record():
    assert oa.is_decline_protection_evidence(**_MIGRATED)
    # …and it is never demonstrated strength, whatever it carries.
    assert not oa.is_demonstrated_strength(
        **{k: _MIGRATED[k] for k in ("source_type", "evidence_type", "value_semantics", "protocol_validity")}
    )
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
        ("protocol_validity", oa.PV_INVALID),  # a measurement the protocol rejected, migrated or not
    ]:
        assert not oa.is_decline_protection_evidence(**{**_MIGRATED, field: forged}), (field, forged)
    # And the bare source type, which is what a live write of an unrecognised source gets:
    assert not oa.is_decline_protection_evidence(
        source_type=oa.ST_LEGACY_UNKNOWN, evidence_type=se.EV_DIRECT_MEASUREMENT,
        value_semantics=se.VS_MEASURED, protocol_validity=oa.PV_VALID,
    )
    # Current provenance is decline-protection evidence too.
    assert oa.is_decline_protection_evidence(
        source_type=oa.ST_ATHLETE_ENTRY, evidence_type=se.EV_DIRECT_MEASUREMENT,
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


async def test_the_sql_clauses_admit_a_migrated_legacy_row_for_decline_only_and_none_of_its_forgeries(async_db):
    """The migration branch, field by field, in SQL: the whole record is decline-protection evidence
    and nothing else; changing any one field of it removes it; and it is never in the public set."""
    user = await _user(async_db, "dem-migrated@test.com")
    d = await _seed(async_db)
    variants: list[tuple[str, dict[str, str | None]]] = [("whole record", {})]
    for field, forged in [
        ("provenance_operation", "live_write"), ("provenance_operation", None),
        ("migration_version", "a099"), ("migration_version", None),
        ("authority_resolution_reason", "anything_else"), ("authority_resolution_reason", None),
        ("observation_model", "workout_e1rm_extraction_v1"), ("observation_model", None),
        ("evidence_type", "estimated_from_training_set"), ("value_semantics", "estimated"),
        ("source_type", "athlete_entry_typo"), ("protocol_validity", "invalid"),
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

    decline = set((await async_db.execute(
        select(BenchmarkObservation.id).where(decline_protection_clause())
    )).scalars())
    public = set((await async_db.execute(
        select(BenchmarkObservation.id).where(demonstrated_strength_clause())
    )).scalars())

    assert {r.raw_value for r in rows if r.id in decline} == {100.0}  # only the whole record
    assert public == set()  # and it is never demonstrated strength


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


# ----- two watermarks over one definition of current provenance -------------------------------------------------------- #

async def test_the_decline_prior_and_the_public_figure_agree_on_current_provenance_and_differ_only_by_legacy(async_db):
    user = await _user(async_db, "dem-same@test.com")
    d = await _seed(async_db)
    base = datetime.now(UTC).replace(tzinfo=None)
    for raw, st, ev, vs in [
        (150.0, "athlete_entry", "direct_measurement", "measured"),
        (175.0, "workout_extraction", "estimated_from_training_set", "estimated"),
        (160.0, "athlete_entry", "reported_estimate", "estimated"),
        (140.0, "athlete_entry", "protocol_grade_estimate", "measured"),
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

    # A migrated legacy test can raise the decline prior, never the public figure.
    async_db.add(BenchmarkObservation(
        user_id=user.id, benchmark_definition_id=d.id, raw_value=165.0, observed_at=base,
        validity_status="valid", source="benchmark_test", **_MIGRATED,
    ))
    await async_db.commit()
    assert await state_service.best_currently_validated_e1rm(async_db, user.id, CODE) == 150.0
    assert await sds._prior_watermark(async_db, user.id, CODE, exclude_observation_id=-1) == 165.0


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


async def test_legacy_history_protects_against_a_decline_without_becoming_validated_strength(async_db):
    """A tested max written before provenance existed carries what migrations a025/a028 stamped on
    it. It anchors the decline machine, so one low test after the migration is a candidate and strength
    is held, not a silent 'first measurement'. But the migration cannot prove a max, so the public
    best-validated figure stays empty."""
    user = await _user(async_db, "dem-legacy@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, t0)  # applied to the athlete's state then
    tested = (await async_db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.raw_value == 150.0)
    )).scalar_one()
    for field, value in {  # …and relabelled by the legacy migration, as every pre-existing test was
        **_MIGRATED, "collection_mode": "legacy_unknown", "capacity_effect": "none",
        "actor_type": "unknown",
    }.items():
        setattr(tested, field, value)
    await async_db.commit()
    assert await state_service.best_currently_validated_e1rm(async_db, user.id, CODE) is None  # not validated
    assert await demonstrated_watermark(async_db, user.id, CODE) is None
    before = await _max_strength(async_db, user.id)

    await _bench(async_db, user.id, 132.0, t0 + timedelta(days=10))  # the first lower measured test

    (candidate,) = await _candidates(async_db, user.id)
    assert candidate.prior_mean == 150.0 and candidate.status == "active"
    last = await _last_observation(async_db, user.id)
    assert (last.decline_transition_status, last.applied_capacity_effect) == ("decline_candidate", "none")
    assert await _max_strength(async_db, user.id) == pytest.approx(before)  # canonical strength preserved


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


# ----- a higher maximum today does not rehabilitate a candidate opened on the wrong evidence ----------- #

async def test_a_later_higher_maximum_does_not_rehabilitate_an_obsolete_candidate(async_db):
    """Review repro: an old candidate (prior 100, trigger 90); a workout advances the head; a backdated
    measured 200 arrives record-only; then an on-time measured 180. The old candidate's prior was
    never the evidence for that moment (a test of 200 was dated before the trigger), so it is retired
    and the fresh assessment is 180 against 200, with its own ceiling."""
    from app.schemas.workouts import WorkoutLog

    user = await _user(async_db, "dem-rehab@test.com")
    d = await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 90.0, t0)  # the trigger (a first measurement, applied)
    trigger = (await async_db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.raw_value == 90.0)
    )).scalar_one()
    obsolete = await _old_code_candidate(async_db, user, d, prior=100.0, trigger=trigger)
    await state_service.process_new_workout(
        async_db, user.id,
        WorkoutLog(timestamp=t0 + timedelta(days=1), modality="Strength", duration_minutes=45.0, session_rpe=6.0),
        received_at=datetime.now(UTC),
    )  # the head advances
    await _bench(async_db, user.id, 200.0, t0 - timedelta(days=2))  # backdated: recorded, not applied
    assert (await _last_observation(async_db, user.id)).state_disposition == "record_only"

    await _bench(async_db, user.id, 180.0, t0 + timedelta(days=2))  # on time

    await async_db.refresh(obsolete)
    assert (obsolete.status, obsolete.resolution_reason) == ("dismissed", "prior_not_demonstrated")
    fresh = [c for c in await _candidates(async_db, user.id) if c.id != obsolete.id]
    assert len(fresh) == 1 and fresh[0].prior_mean == 200.0 and fresh[0].observed_value == 180.0
    last = await _last_observation(async_db, user.id)
    assert (last.decline_transition_status, last.applied_capacity_effect) == ("decline_candidate", "none")
    decision = await sds.resolve_prescription_basis(
        async_db, user.id, code=CODE, latest_raw=180.0, current_axis=60.0,
        rules=d.standardization_rules, mode=sds.BASIS_MODE_ON,
    )
    assert decision.candidate_id == fresh[0].id
    assert decision.ceiling == pytest.approx(187.56, abs=0.01)  # 180 + 4.2% (not the obsolete 93.78)


async def _real_candidate(db, tag: str):
    user = await _user(db, f"dem-sup-{tag}@test.com")
    d = await _seed(db)
    await initialize_athlete_state(db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(db, user.id, 150.0, t0)
    await _bench(db, user.id, 132.0, t0 + timedelta(days=10))
    (candidate,) = await _candidates(db, user.id)
    trigger = (await db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.raw_value == 132.0)
    )).scalar_one()
    return user, d, candidate, trigger


async def test_a_candidate_on_current_evidence_is_supported(async_db):
    _, _, candidate, _ = await _real_candidate(async_db, "ok")
    assert await sds._candidate_is_supported(async_db, candidate) is True


@pytest.mark.parametrize(
    "change",
    ["trigger_corrected_up", "trigger_quarantined", "trigger_lost_authority",
     "trigger_semantics_now_estimated", "trigger_moved_in_time", "other_policy"],
)
async def test_a_candidate_whose_original_evidence_or_policy_changed_is_not_supported(async_db, change):
    user, d, candidate, trigger = await _real_candidate(async_db, change)
    if change == "trigger_corrected_up":
        trigger.raw_value = 160.0  # a data correction raised the trigger above the recorded value
    elif change == "trigger_quarantined":
        trigger.validity_status = "quarantined"
        trigger.quarantined_at = datetime.now(UTC).replace(tzinfo=None)
    elif change == "trigger_lost_authority":
        trigger.capacity_effect = "none"  # no longer a measurement the decline machine may act on
    elif change == "trigger_semantics_now_estimated":
        # The stored effect still says bidirectional; the provenance now says estimate.
        trigger.value_semantics = "estimated"
    elif change == "trigger_moved_in_time":
        trigger.observed_at = trigger.observed_at + timedelta(days=9)
    else:
        candidate.decline_policy_version = "strength_decline_policy_v0"
    await async_db.commit()

    assert await sds._candidate_is_supported(async_db, candidate) is False
    decision = await sds.resolve_prescription_basis(
        async_db, user.id, code=CODE, latest_raw=150.0, current_axis=60.0,
        rules=d.standardization_rules, mode=sds.BASIS_MODE_ON,
    )
    assert (decision.candidate_id, decision.ceiling) == (None, None)  # no ceiling from unsupported evidence


async def test_strength_tested_after_the_trigger_retires_a_candidate_unless_it_is_the_observation_in_flight(async_db):
    """The as-of date matters in both directions: a higher test dated BEFORE the trigger voids the
    candidate (above); one dated AFTER it is a re-demonstration. If the machine has not seen it (it
    arrived record-only) the candidate must not stand on its ceiling; if it is the observation being
    processed, the machine handles it itself, with its own reason."""
    user, _, candidate, trigger = await _real_candidate(async_db, "after")
    later = trigger.observed_at + timedelta(days=10)
    async_db.add(BenchmarkObservation(
        user_id=user.id, benchmark_definition_id=candidate.benchmark_definition_id, raw_value=160.0,
        observed_at=later, validity_status="valid", source="benchmark_test",
        source_type="athlete_entry", evidence_type="direct_measurement", value_semantics="measured",
        protocol_validity="valid",
    ))
    await async_db.commit()
    unseen = (await async_db.execute(
        select(BenchmarkObservation).where(BenchmarkObservation.raw_value == 160.0)
    )).scalar_one()
    assert await sds._unsupported_reason(async_db, candidate) == "re_demonstrated_by_a_later_test"
    assert await sds._candidate_is_supported(async_db, candidate, current_observation_id=unseen.id) is True

    await _bench(async_db, user.id, 165.0, later + timedelta(days=1))  # arrives: at or above the watermark

    await async_db.refresh(candidate)
    assert candidate.status == "dismissed"
    assert candidate.resolution_reason == "re_demonstrated_by_a_later_test"  # the 160 was already there
    assert candidate.confirmation_observation_id is None


# ----- the sequences the review found ------------------------------------------------------------------- #

async def test_corrected_provenance_cannot_let_the_next_low_test_confirm_a_regression(async_db):
    """Review repro: the trigger's semantics are corrected to `estimated` (its stored effect still
    says bidirectional). The next genuine low test must not confirm a downward update; it is the first
    qualifying low test."""
    user, _, candidate, trigger = await _real_candidate(async_db, "prov")
    trigger.value_semantics = "estimated"
    await async_db.commit()
    before = await _max_strength(async_db, user.id)

    await _bench(async_db, user.id, 135.0, trigger.observed_at + timedelta(days=10))

    await async_db.refresh(candidate)
    assert (candidate.status, candidate.resolution_reason) == ("dismissed", "trigger_not_a_current_measurement")
    assert [c for c in await _candidates(async_db, user.id) if c.status == "confirmed"] == []
    last = await _last_observation(async_db, user.id)
    assert (last.decline_transition_status, last.applied_capacity_effect) == ("decline_candidate", "none")
    assert await _max_strength(async_db, user.id) == pytest.approx(before)  # held, not regressed
    (fresh,) = [c for c in await _candidates(async_db, user.id) if c.id != candidate.id]
    assert fresh.prior_mean == 150.0 and fresh.observed_value == 135.0  # the first qualifying low test


async def test_a_late_re_demonstration_does_not_leave_an_obsolete_ceiling(async_db):
    """Review repro: measured 150, candidate at 132, a workout advances the head, a measured 160
    performed after the trigger arrives record-only (the machine never sees it), then an on-time 150.
    The 160 is a re-demonstration: the 137.54 ceiling must go, and the fresh assessment is 150 against 160."""
    from app.schemas.workouts import WorkoutLog

    user = await _user(async_db, "dem-late@test.com")
    d = await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, t0)
    await _bench(async_db, user.id, 132.0, t0 + timedelta(days=10))
    (old,) = await _candidates(async_db, user.id)
    assert old.prior_mean == 150.0
    await state_service.process_new_workout(
        async_db, user.id,
        WorkoutLog(timestamp=t0 + timedelta(days=12), modality="Strength", duration_minutes=45.0, session_rpe=6.0),
        received_at=datetime.now(UTC),
    )
    await _bench(async_db, user.id, 160.0, t0 + timedelta(days=11))  # after the trigger, before the head
    assert (await _last_observation(async_db, user.id)).state_disposition == "record_only"

    await _bench(async_db, user.id, 150.0, t0 + timedelta(days=20))  # on time

    await async_db.refresh(old)
    assert (old.status, old.resolution_reason) == ("dismissed", "re_demonstrated_by_a_later_test")
    (fresh,) = [c for c in await _candidates(async_db, user.id) if c.id != old.id]
    assert fresh.prior_mean == 160.0 and fresh.observed_value == 150.0
    decision = await sds.resolve_prescription_basis(
        async_db, user.id, code=CODE, latest_raw=150.0, current_axis=60.0,
        rules=d.standardization_rules, mode=sds.BASIS_MODE_ON,
    )
    assert decision.candidate_id == fresh.id
    assert decision.ceiling == pytest.approx(156.3, abs=0.01)  # 150 + 4.2%, not the obsolete 137.54


async def test_a_trigger_moved_in_time_cannot_shorten_the_retest_interval(async_db):
    """Review repro: the trigger's date is corrected later, so the recorded clock (created_at) and the
    row disagree. A test one day after the corrected date must not count as a confirmation separated by
    the interval measured from the old date."""
    user, _, candidate, trigger = await _real_candidate(async_db, "clock")
    trigger.observed_at = trigger.observed_at + timedelta(days=9)  # corrected from day 10 to day 19
    await async_db.commit()

    await _bench(async_db, user.id, 128.0, trigger.observed_at + timedelta(days=1))  # day 20

    await async_db.refresh(candidate)
    assert candidate.status == "dismissed" and candidate.confirmation_observation_id is None
    assert [c for c in await _candidates(async_db, user.id) if c.status == "confirmed"] == []


# ----- tied timestamps: (observed_at, id) is the order ------------------------------------------------- #

async def test_a_tied_test_that_arrived_later_retires_the_candidate_and_the_next_low_test_starts_fresh(async_db):
    """Review repro: a measured 150 with the trigger's exact timestamp but a larger id, recorded after
    the head moved on, is a re-demonstration. The 137.54 ceiling must not survive it, and a later 135
    must not confirm the obsolete candidate."""
    from app.schemas.workouts import WorkoutLog

    user, d, candidate, trigger = await _real_candidate(async_db, "tie-later")
    await state_service.process_new_workout(
        async_db, user.id,
        WorkoutLog(timestamp=trigger.observed_at + timedelta(days=2), modality="Strength", duration_minutes=45.0, session_rpe=6.0),
        received_at=datetime.now(UTC),
    )  # the head advances
    await _bench(async_db, user.id, 150.0, trigger.observed_at.replace(tzinfo=UTC))  # same instant, larger id
    tied = await _last_observation(async_db, user.id)
    assert tied.observed_at == trigger.observed_at and tied.id > trigger.id
    assert tied.state_disposition == "record_only"
    assert await sds._unsupported_reason(async_db, candidate) == "re_demonstrated_by_a_later_test"
    decision = await sds.resolve_prescription_basis(
        async_db, user.id, code=CODE, latest_raw=150.0, current_axis=60.0,
        rules=d.standardization_rules, mode=sds.BASIS_MODE_ON,
    )
    assert (decision.candidate_id, decision.ceiling) == (None, None)  # no obsolete ceiling
    before = await _max_strength(async_db, user.id)

    await _bench(async_db, user.id, 135.0, trigger.observed_at + timedelta(days=20))

    await async_db.refresh(candidate)
    assert (candidate.status, candidate.resolution_reason) == ("dismissed", "re_demonstrated_by_a_later_test")
    assert [c for c in await _candidates(async_db, user.id) if c.status == "confirmed"] == []
    (fresh,) = [c for c in await _candidates(async_db, user.id) if c.id != candidate.id]
    assert fresh.prior_mean == 150.0 and fresh.observed_value == 135.0
    assert await _max_strength(async_db, user.id) == pytest.approx(before)  # held


async def test_a_tied_test_that_arrived_earlier_is_part_of_the_prior_not_a_re_demonstration(async_db):
    """The other side of the same boundary: a 150 recorded for the trigger's own instant BEFORE the
    trigger is what the trigger fell from. The candidate stays supported (a plain `>=` would void it)."""
    user = await _user(async_db, "dem-tie-earlier@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t1 = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, t1)
    await _bench(async_db, user.id, 132.0, t1)  # same instant, larger id: not late, so it is judged
    (candidate,) = await _candidates(async_db, user.id)
    assert candidate.prior_mean == 150.0

    assert await sds._unsupported_reason(async_db, candidate) is None


async def test_a_higher_tied_test_that_arrived_later_is_recovery_not_part_of_the_prior(async_db):
    """The prior and the recovery boundary use the same order: a tied 160 recorded after the trigger
    is what came AFTER it (a re-demonstration), so it must not be folded into the prior the
    candidate was opened against (which would blame its prior instead)."""
    from app.schemas.workouts import WorkoutLog

    user, _, candidate, trigger = await _real_candidate(async_db, "tie-higher")
    await state_service.process_new_workout(
        async_db, user.id,
        WorkoutLog(timestamp=trigger.observed_at + timedelta(days=2), modality="Strength", duration_minutes=45.0, session_rpe=6.0),
        received_at=datetime.now(UTC),
    )
    await _bench(async_db, user.id, 160.0, trigger.observed_at.replace(tzinfo=UTC))

    assert await sds._unsupported_reason(async_db, candidate) == "re_demonstrated_by_a_later_test"
