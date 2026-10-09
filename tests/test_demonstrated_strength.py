"""P4-2a: one definition of "demonstrated strength", used by every watermark that means it.

ADR-0066 judges a strength decline against the best currently valid *demonstrated* e1RM. The code
used to take the max over every valid row, workout-derived estimates included. That is safe only
while an estimate cannot exceed what the athlete can do; once estimates can (P4-2b), a high
training estimate would make an honest test look like a decline. So demonstrated strength is
provenance, not a label: a measured max test, and nothing an estimate produced.

Pinned here:

* the Python predicate and the SQL clause are the same rule (a grid over every provenance
  combination, against the database);
* the decline machine's prior and the public ``best_currently_validated_e1rm`` are one function;
* both directions: a high training estimate does NOT make an honest test look like a decline, and
  genuinely declining measured tests still open a candidate;
* legacy provenance is preserved (an athlete's pre-migration tests keep their protection);
* estimated-PR tracking is separate, formula-specific, and never a demonstrated watermark.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

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


def test_the_predicate_on_the_cases_that_matter():
    ok: dict[str, str | None] = {
        "source_type": oa.ST_ATHLETE_ENTRY, "evidence_type": se.EV_DIRECT_MEASUREMENT,
        "value_semantics": se.VS_MEASURED, "protocol_validity": oa.PV_VALID,
    }
    assert oa.is_demonstrated_strength(**ok)
    assert oa.is_demonstrated_strength(**{**ok, "protocol_validity": oa.PV_NOT_EVALUATED})  # unproven ≠ invalid
    assert oa.is_demonstrated_strength(**{**ok, "source_type": oa.ST_LEGACY_UNKNOWN})  # history kept
    assert not oa.is_demonstrated_strength(**{**ok, "protocol_validity": oa.PV_INVALID})
    assert not oa.is_demonstrated_strength(**{**ok, "source_type": oa.ST_WORKOUT_EXTRACTION})
    assert not oa.is_demonstrated_strength(**{**ok, "evidence_type": se.EV_ESTIMATED_FROM_TRAINING_SET})
    assert not oa.is_demonstrated_strength(**{**ok, "evidence_type": se.EV_REPORTED_ESTIMATE})
    assert not oa.is_demonstrated_strength(**{**ok, "value_semantics": se.VS_ESTIMATED})
    # Every unstated field fails closed.
    for field in ok:
        assert not oa.is_demonstrated_strength(**{**ok, field: None}) or field == "protocol_validity"


async def test_the_sql_clause_is_the_same_rule_as_the_predicate_over_every_combination(async_db):
    user = await _user(async_db, "dem-grid@test.com")
    d = await _seed(async_db)
    combos = list(itertools.product(_SOURCE_TYPES, _EVIDENCE, _SEMANTICS, _PROTOCOL))
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

    expected = {
        rid for rid, (st, ev, vs, pv) in by_id.items()
        if oa.is_demonstrated_strength(source_type=st, evidence_type=ev, value_semantics=vs, protocol_validity=pv)
    }
    assert in_sql == expected and 0 < len(expected) < len(combos)


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

async def test_a_high_training_estimate_does_not_make_an_honest_test_look_like_a_decline(async_db):
    user = await _user(async_db, "dem-fp@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, t0)  # a tested max
    # Training says the athlete is at least this strong (an estimate that overshoots the test).
    await _bench(async_db, user.id, 175.0, t0 + timedelta(days=5), source="workout_extraction")

    await _bench(async_db, user.id, 150.0, t0 + timedelta(days=10))  # an honest repeat of the test

    assert await _candidates(async_db, user.id) == []  # 150 vs the 175 estimate would have been -14%
    assert await state_service.best_currently_validated_e1rm(async_db, user.id, CODE) == 150.0


async def test_genuinely_declining_measured_tests_still_open_a_candidate(async_db):
    user = await _user(async_db, "dem-tp@test.com")
    await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    await _bench(async_db, user.id, 150.0, t0)
    await _bench(async_db, user.id, 175.0, t0 + timedelta(days=5), source="workout_extraction")  # noise

    await _bench(async_db, user.id, 132.0, t0 + timedelta(days=10))  # a real drop from the tested 150

    candidates = await _candidates(async_db, user.id)
    assert len(candidates) == 1  # judged against 150, the demonstrated watermark, not 175


async def test_legacy_provenance_keeps_its_protection(async_db):
    """A tested max written before provenance existed (migration gave it legacy_unknown /
    direct_measurement / measured) still anchors the decline machine, so one low test after the
    migration is a candidate, not a silent 'first measurement'."""
    user = await _user(async_db, "dem-legacy@test.com")
    d = await _seed(async_db)
    await initialize_athlete_state(async_db, user.id)
    t0 = datetime.now(UTC) + timedelta(minutes=1)
    async_db.add(BenchmarkObservation(
        user_id=user.id, benchmark_definition_id=d.id, raw_value=150.0, observed_at=t0.replace(tzinfo=None) - timedelta(days=60),
        validity_status="valid", source="benchmark_test", source_type="legacy_unknown",
        evidence_type="direct_measurement", value_semantics="measured",
        collection_mode="legacy_unknown", capacity_effect="none",
    ))
    await async_db.commit()
    assert await state_service.best_currently_validated_e1rm(async_db, user.id, CODE) == 150.0

    await _bench(async_db, user.id, 132.0, t0)

    assert len(await _candidates(async_db, user.id)) == 1


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
