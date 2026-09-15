"""Replay simulated athlete history THROUGH THE SERVICE LAYER so the shadow and
telemetry tables actually fill.

Every other seeder in this package inserts ORM rows directly — `db.add(WellnessSample(...))`,
`db.add(WorkoutLog(...))` — and none of them imports `app.services`. That is why a database
can hold 208 seeded athletes with months of wellness and workout rows and still have an empty
`ekf_shadow_log`: nothing ever *assimilated* those readings. The shadow writers only run on
the request path.

This script closes that gap. It drives the same functions the API routes call, in the same
order, so the estimators run and record what they believed.

Writes (all via services, none by direct insert):
    athlete_states              one baseline S0 per athlete, then one row per workout
    wellness_samples            one per simulated day (idempotent per user/date/source)
    ekf_shadow_log              `predict` per workout, `update` per wellness day
    recovery_shadow_log         one per wellness day
    personalization_shadow_log  one per wellness day, once enough paired history exists
    dose_routing_shadow_log     one per workout
    workout_logs                one per training day
    planned_sessions            a mesocycle block, then marked COMPLETED as workouts land
    prescription_decisions      one per prescribed session
    mpc_shadow_log              one per prescribed session
    candidate_decision_logs     N per prescribed session

WHY THE ORDER MATTERS. `ekf_shadow_service` returns silently when the athlete has no current
state (`app/services/ekf_shadow_service.py:146`, `:191`, `:341`), so S0 must exist before the
first wellness day or the whole replay produces nothing. `initialize_athlete_state` is called
explicitly with the profile's real 1RMs rather than letting `process_new_workout` self-seed a
defaults-only baseline — the seeded numbers become the athlete's capacity provenance.

A wellness day with `soreness=None` produces NO EKF update row: soreness is the only
EKF-consumed signal (`app/logic/ekf/wellness_input.py:59`). Every day this script writes
carries a soreness value, which is what makes the 40-update calibration threshold
(`app/ml/q10_confidence/ekf_calibration.py:31`) reachable at all.

THREE SOURCES, AND THE DIFFERENCE IS NOT COSMETIC.

`--source synthetic` (default) invents every value. It exercises the pipeline end to end
and lets the calibration and forecast-scoring harnesses run against realistic row shapes,
but it is NOT calibration ground truth and must never authorize a promotion — the same
caution `app/services/ekf_calibration_gate_service.py:8-11` records about the repo's other
synthetic figures. A NIS ratio computed over invented soreness measures the generator, not
the estimator.

`--source pmdata` replays real participants from the PMData corpus: their own soreness,
stress, mood, sleep and session RPE, with the real day-to-day spacing preserved. This is
the only mode in which the calibration gate's verdict says anything about the filter.
See `app/scripts/load_pmdata.py` — note PMData is CC BY-NC 4.0, non-commercial.

`--source hit-strength` replays one real lifter with real per-set weight, reps and RPE
(`app/scripts/load_hit_strength.py`, CC BY 4.0). It is the only source that gives
`total_volume_load`, `estimated_sets` and `avg_rir` genuine values and lets top sets emit
real e1RM observations instead of the bodyweight-multiplier estimates elsewhere in this
package. It writes NO wellness days — that lifter logged no check-ins — so it produces
`predict` and benchmark-driven `update` rows but no wellness-driven ones.

The two real corpora are complementary and deliberately NOT merged: PMData has the
subjective daily signal and no set detail; hit-strength has the set detail and no
check-ins. Splicing one person's sets onto another's check-ins would recreate exactly the
round-robin contamination that makes the existing seeders' pairings meaningless.

Idempotent at the athlete level: an athlete who already has state rows is skipped whole,
because `process_new_workout` appends rather than upserts and a second pass would duplicate
their timeline. Requires PostgreSQL (`readiness_service.upsert_wellness_sample` uses a
Postgres-specific ON CONFLICT against a named constraint).

Run (against a local DB):
    $env:DATABASE_URL = "postgresql+asyncpg://perfuser:perfpass123@localhost:5432/perflab"
    $env:DEBUG = "false"
    python -m app.scripts.seed_shadow_history --users 5 --days 60
    python -m app.scripts.seed_shadow_history --users 8 --days 150 --source pmdata
    python -m app.scripts.seed_shadow_history --source hit-strength
"""

from __future__ import annotations

import argparse
import asyncio
import random
import traceback
from dataclasses import dataclass, field
from datetime import UTC, timedelta

from sqlalchemy import func, select

from app.core.db import AsyncSessionLocal
from app.logic.ekf.wellness_input import build_wellness_shadow_input
from app.logic.wellness_shadow_snapshot import WellnessTelemetrySnapshot
from app.models.athlete_state import AthleteState
from app.models.mesocycle import BlockGoal, PlannedSession, SessionStatus
from app.models.user import AthleteProfile, User
from app.schemas.planning import BlockCreateRequest
from app.schemas.wellness import WellnessSampleIn
from app.schemas.workouts import WorkoutLog, WorkoutSetEntry
from app.scripts import load_hit_strength, load_pmdata, load_scopesense
from app.services import (
    ekf_shadow_service,
    personalization_shadow_service,
    planning_service,
    prescription_service,
    readiness_service,
    recovery_shadow_service,
)
from app.services.state_service import initialize_athlete_state, process_new_workout

# Training days as ISO weekday numbers (Mon=1). Three a week leaves recovery days between
# sessions, which is what `personalization_shadow_service` needs to find paired
# consecutive-day fatigue observations.
TRAINING_WEEKDAYS = (1, 3, 5)

# Modality per training day, cycled. Mixed modalities exercise more of the dose engine than
# a pure-strength block would.
MODALITY_CYCLE = ("Strength", "Running", "Hypertrophy")


SOURCE_SYNTHETIC = "synthetic"
SOURCE_PMDATA = "pmdata"
SOURCE_HIT_STRENGTH = "hit-strength"
SOURCE_SCOPESENSE = "scopesense"
SOURCES = (SOURCE_SYNTHETIC, SOURCE_PMDATA, SOURCE_HIT_STRENGTH, SOURCE_SCOPESENSE)

# The `source` recorded on replayed wellness rows. A real corpus is labelled by its own name
# rather than "manual" so provenance is not misstated — `is_device_source` treats anything but
# the literal "manual" as a device.
#
# KNOWN LIMITATION for ScopeSense: one row carries a self-reported PMSys check-in AND
# watch-measured HRV/RHR, but `source` is per row, so its subjective signals are ranked as
# device evidence rather than athlete evidence. Modelling it faithfully would mean two rows
# per day (one per origin), which the (user, date, source) key supports and per-signal
# resolution would then merge. Not done here: it doubles the shadow writes per day for a
# replay corpus whose purpose is realistic estimator input, not provenance fidelity.
WELLNESS_SOURCE_BY_REPLAY = {
    SOURCE_SYNTHETIC: "manual",
    SOURCE_PMDATA: "pmdata",
    SOURCE_SCOPESENSE: "scopesense",
    SOURCE_HIT_STRENGTH: "manual",  # writes no wellness rows at all
}


@dataclass
class _PlannedSession:
    session_rpe: float
    duration_minutes: float
    modality: str
    #: Per-set detail, when the source has it. Present only for hit-strength: it is what
    #: makes the dose engine compute from real external load and lets top sets emit e1RM
    #: observations, instead of both being invented.
    sets: list[WorkoutSetEntry] = field(default_factory=list)
    total_volume_load: float | None = None
    avg_rir: float | None = None


@dataclass
class _PlannedDay:
    """One day of a replay, expressed as an offset from the athlete's S0.

    Offsets rather than dates, because the calendar anchor is only known once
    ``initialize_athlete_state`` has produced S0.
    """

    offset: int
    #: ``None`` when the source logged no check-in that day. A day with sessions but no
    #: wellness is a real shape (hit-strength has no check-ins at all) and must not be
    #: filled in — an invented check-in is exactly the defect the rest of this file avoids.
    wellness: dict[str, float | str | None] | None
    sessions: list[_PlannedSession] = field(default_factory=list)


def _synthetic_plan(rng: random.Random, days: int) -> list[_PlannedDay]:
    """Invent a plausible history. Every value here is a random draw — see module docstring."""
    plan: list[_PlannedDay] = []
    trained_yesterday = False
    for offset in range(days):
        is_training = ((offset % 7) + 1) in TRAINING_WEEKDAYS
        soreness = rng.uniform(3.5, 6.5) if trained_yesterday else rng.uniform(1.0, 3.5)
        day = _PlannedDay(
            offset=offset,
            wellness={
                "hrv_ms": round(rng.uniform(45.0, 95.0), 1),
                "sleep_hours": round(rng.uniform(5.5, 8.5), 1),
                "sleep_quality": round(rng.uniform(40.0, 95.0), 1),
                "resting_hr": round(rng.uniform(48.0, 66.0), 1),
                "soreness": round(soreness, 1),
                "mood": round(rng.uniform(4.0, 9.0), 1),
                "stress": round(rng.uniform(1.0, 7.0), 1),
            },
        )
        if is_training:
            day.sessions.append(
                _PlannedSession(
                    session_rpe=round(rng.uniform(5.0, 8.5), 1),
                    duration_minutes=round(rng.uniform(45.0, 80.0), 1),
                    modality=MODALITY_CYCLE[offset % len(MODALITY_CYCLE)],
                )
            )
        plan.append(day)
        trained_yesterday = is_training
    return plan


def _pmdata_plan(participant: str, days: int | None = None) -> list[_PlannedDay]:
    """Replay one real PMData participant.

    Values — soreness, stress, mood, sleep, session RPE, duration, modality — are the
    participant's own answers, not draws. Only the calendar epoch shifts: offsets are
    measured from the participant's FIRST check-in, so the real day-to-day spacing (and
    therefore every rest day and training gap) is preserved.

    Days the participant did not answer are simply absent from the plan rather than being
    filled in, so a missed check-in stays missed.
    """
    wellness = load_pmdata.load_wellness(participant)
    if not wellness:
        return []

    sessions_by_day: dict[object, list[_PlannedSession]] = {}
    for session in load_pmdata.load_sessions(participant):
        sessions_by_day.setdefault(session.day, []).append(
            _PlannedSession(
                session_rpe=session.session_rpe,
                duration_minutes=session.duration_minutes,
                modality=session.modality,
            )
        )

    anchor = min(w.day for w in wellness)
    plan: list[_PlannedDay] = []
    for reading in sorted(wellness, key=lambda w: w.day):
        offset = (reading.day - anchor).days
        if days is not None and offset >= days:
            break
        plan.append(
            _PlannedDay(
                offset=offset,
                wellness={
                    "hrv_ms": reading.hrv_ms,
                    "sleep_hours": reading.sleep_hours,
                    "sleep_quality": reading.sleep_quality,
                    "resting_hr": reading.resting_hr,
                    "soreness": reading.soreness,
                    "mood": reading.mood,
                    "stress": reading.stress,
                },
                sessions=sessions_by_day.get(reading.day, []),
            )
        )
    return plan


def _seed_kwargs(profile: AthleteProfile | None) -> dict[str, object]:
    """Baseline inputs for S0, taken from the athlete's real profile where present.

    Passing the measured numbers rather than relying on defaults is what gives the athlete a
    non-``no_data`` seed tier — `_stage_seed_snapshot` records per-axis provenance from
    exactly these arguments.
    """
    if profile is None:
        return {}
    return {
        "experience_level": profile.experience_level or "intermediate",
        "experience_years": profile.experience_years or 0.0,
        "squat_1rm_kg": profile.squat_1rm,
        "deadlift_1rm_kg": profile.deadlift_1rm,
        "bench_1rm_kg": profile.bench_1rm,
        "bodyweight_kg": profile.bodyweight_kg,
        "run_5k_seconds": profile.run_5k_seconds,
        "goal": profile.primary_goal,
    }


def _hit_strength_plan(days: int | None = None) -> list[_PlannedDay]:
    """Replay the single real lifter from the hit-strength corpus.

    Every set carries the lifter's own weight, reps and RPE, so this is the only source
    that gives ``total_volume_load``, ``estimated_sets`` and ``avg_rir`` real values and
    lets top sets emit genuine e1RM observations.

    There are NO wellness days: this lifter logged no check-ins. Those days are emitted
    with ``wellness=None`` rather than an invented check-in, which means this source
    produces EKF ``predict`` rows and benchmark-driven ``update`` rows but no
    wellness-driven ones. That is the corpus telling the truth about itself.
    """
    sessions = load_hit_strength.load_sessions()
    if not sessions:
        return []

    anchor = min(s.day for s in sessions)
    by_offset: dict[int, _PlannedDay] = {}
    for session in sessions:
        offset = (session.day - anchor).days
        if days is not None and offset >= days:
            continue
        entry = by_offset.setdefault(offset, _PlannedDay(offset=offset, wellness=None))
        entry.sessions.append(
            _PlannedSession(
                session_rpe=session.session_rpe,
                duration_minutes=session.duration_minutes,
                modality="Strength",
                sets=[
                    WorkoutSetEntry(
                        exercise_name=s.catalog_name,
                        free_text_name=s.free_text_name,
                        load_kg=s.load_kg,
                        reps=s.reps,
                        rpe=s.rpe,
                        rir=s.rir,
                    )
                    for s in session.sets
                ],
                total_volume_load=session.total_volume_load,
                avg_rir=session.avg_rir,
            )
        )
    return [by_offset[k] for k in sorted(by_offset)]


def _scopesense_plan(participant: str, days: int | None = None) -> list[_PlannedDay]:
    """Replay one real ScopeSense participant — the only source with measured HRV.

    Carries ``hrv_metric="sdnn"`` on every reading. HealthKit exposes only SDNN, and the
    baseline must never average it against rMSSD history (migration a040), so the metric
    travels with the value rather than being inferred downstream.
    """
    wellness = load_scopesense.load_wellness(participant)
    if not wellness:
        return []

    sessions_by_day: dict[object, list[_PlannedSession]] = {}
    for session in load_scopesense.load_sessions(participant):
        sessions_by_day.setdefault(session.day, []).append(
            _PlannedSession(
                session_rpe=session.session_rpe,
                duration_minutes=session.duration_minutes,
                modality="Mixed",  # ScopeSense records daily load, not a per-session modality
            )
        )

    anchor_day = min(w.day for w in wellness)
    plan: list[_PlannedDay] = []
    for reading in sorted(wellness, key=lambda w: w.day):
        offset = (reading.day - anchor_day).days
        if days is not None and offset >= days:
            break
        plan.append(
            _PlannedDay(
                offset=offset,
                wellness={
                    "hrv_ms": reading.hrv_ms,
                    "hrv_metric": reading.hrv_metric,
                    "sleep_hours": reading.sleep_hours,
                    "sleep_quality": reading.sleep_quality,
                    "resting_hr": reading.resting_hr,
                    "soreness": reading.soreness,
                    "mood": reading.mood,
                    "stress": reading.stress,
                },
                sessions=sessions_by_day.get(reading.day, []),
            )
        )
    return plan


async def _already_replayed(db, user_id: int) -> bool:
    """True when this athlete already has a state timeline.

    The replay appends, so re-running over an athlete would duplicate their history rather
    than reconcile it. Skipping whole athletes keeps the script safe to re-run.
    """
    count = await db.scalar(
        select(func.count()).select_from(AthleteState).where(AthleteState.user_id == user_id)
    )
    return bool(count)


async def _pending_session_on(db, user_id: int, on_date) -> PlannedSession | None:
    """The athlete's PENDING session for a specific date.

    `planning_service.get_today_session` is hardwired to wall-clock today, so a historical
    replay has to resolve the session itself and hand it to the prescriber explicitly —
    otherwise `target_session` is None and the forecast is never persisted
    (`app/services/prescription_service.py:488`).
    """
    stmt = (
        select(PlannedSession)
        .where(
            PlannedSession.user_id == user_id,
            PlannedSession.scheduled_date == on_date,
            PlannedSession.status == SessionStatus.PENDING,
        )
        .order_by(PlannedSession.id.asc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def _replay_athlete(
    db,
    user: User,
    profile: AthleteProfile | None,
    *,
    plan: list[_PlannedDay],
    with_forecasts: bool,
    wellness_source: str,
    counts: dict[str, int],
) -> None:
    """Drive one athlete through a planned history via the service layer."""
    # 1) S0 first. Without it every shadow writer returns silently.
    base = await initialize_athlete_state(db, user.id, **_seed_kwargs(profile))  # type: ignore[arg-type]
    counts["athletes"] += 1

    start = base.timestamp
    if start.tzinfo is not None:
        start = start.replace(tzinfo=None)

    span = (plan[-1].offset + 1) if plan else 0

    # 2) A block of PENDING sessions, so prescriptions have somewhere to persist a forecast.
    if with_forecasts and span:
        weeks = max(1, (span // 7) + 1)
        await planning_service.create_block_with_sessions(
            db,
            user.id,
            BlockCreateRequest(
                goal=BlockGoal.STRENGTH,
                start_date=start.date(),
                duration_weeks=min(weeks, 24),
                sessions_per_week=len(TRAINING_WEEKDAYS),
            ),
        )
        counts["blocks"] += 1

    for entry in plan:
        day = start + timedelta(days=entry.offset)

        # --- morning check-in -------------------------------------------------------
        # Mirrors app/api/v1/wellness.py: create the row, snapshot every shadow input from
        # the still-valid sample, THEN run the shadow writers. Each shadow rolls the shared
        # session back on failure, which would expire a live ORM instance handed to a later
        # one.
        #
        # Skipped entirely when the source logged no check-in. Writing a placeholder row
        # would manufacture a wellness reading nobody gave.
        if entry.wellness is not None:
            sample = await readiness_service.upsert_wellness_sample(
                db,
                user.id,
                WellnessSampleIn(date=day.date(), source=wellness_source, **entry.wellness),  # type: ignore[arg-type]
            )
            telemetry_snapshot = WellnessTelemetrySnapshot.from_sample(sample)
            ekf_input = build_wellness_shadow_input(user.id, sample.id, sample.soreness)

            await recovery_shadow_service.record_recovery_shadow(db, user.id, telemetry_snapshot)
            await personalization_shadow_service.record_personalization_shadow(
                db, user.id, telemetry_snapshot
            )
            outcome = await ekf_shadow_service.record_ekf_wellness_observation(
                db, user.id, ekf_input, observed_at=day.replace(tzinfo=UTC)
            )
            counts["wellness_days"] += 1
            if outcome == "assimilated":
                counts["ekf_updates"] += 1

        if not entry.sessions:
            continue

        # --- prescribe, then train ---------------------------------------------------
        planned = await _pending_session_on(db, user.id, day.date()) if with_forecasts else None
        if planned is not None:
            rx = await prescription_service.prescribe_for_athlete(
                db, user.id, None, planned_session=planned
            )
            counts["prescriptions"] += 1
            if rx.why is not None and rx.why.expected_outcomes:
                counts["forecasts_recorded"] += 1

        # Only the first session of a day can be linked to the planned session; a second
        # one that day is logged on its own rather than double-completing the same slot.
        for index, session in enumerate(entry.sessions):
            ts = day.replace(hour=10 + min(index, 8), minute=0, second=0, microsecond=0)
            await process_new_workout(
                db,
                user.id,
                WorkoutLog(
                    timestamp=ts,
                    modality=session.modality,  # type: ignore[arg-type]
                    duration_minutes=session.duration_minutes,
                    session_rpe=session.session_rpe,
                    # Real per-set detail when the source has it. Passing `sets` is what
                    # makes the dose engine read genuine external load and lets top sets
                    # emit e1RM observations (ADR-0045); the aggregate fields stay None
                    # rather than 0.0 when a source cannot supply them.
                    sets=session.sets,
                    total_volume_load=session.total_volume_load,
                    avg_rir=session.avg_rir,
                    estimated_sets=float(len(session.sets)) if session.sets else None,
                    planned_session_id=planned.id if (planned and index == 0) else None,
                ),
            )
            counts["workouts"] += 1
            counts["sets_logged"] += len(session.sets)


async def seed(
    n_users: int = 5,
    days: int = 60,
    with_forecasts: bool = True,
    source: str = SOURCE_SYNTHETIC,
) -> None:
    """Replay history for up to `n_users` athletes who do not yet have a state timeline.

    ``source="pmdata"`` replays real PMData participants — real soreness, stress, mood and
    session RPE. ``source="synthetic"`` invents them. The difference matters: the
    calibration gate's verdict is only meaningful on the former.
    """
    if source not in SOURCES:
        raise SystemExit(f"Unknown --source {source!r}; expected one of {list(SOURCES)}.")

    pmdata_participants: list[str] = []
    if source == SOURCE_PMDATA:
        pmdata_participants = load_pmdata.participants()
        if not pmdata_participants:
            raise SystemExit(
                "PMData is not on disk. Run: "
                "python -m app.scripts.download_new_datasets --only pmdata"
            )
    scopesense_participants: list[str] = []
    if source == SOURCE_SCOPESENSE:
        scopesense_participants = load_scopesense.participants()
        if not scopesense_participants:
            raise SystemExit(
                "ScopeSense is not on disk. It is OSF-hosted, not Kaggle: download "
                "https://osf.io/v5acr/ and unpack under data/scopesense/."
            )
    if source == SOURCE_HIT_STRENGTH and n_users > 1:
        # The corpus is one lifter. Replaying them onto several athletes would be the
        # round-robin contamination this whole exercise exists to remove.
        print("hit-strength is a single-lifter corpus; replaying onto 1 athlete.")
        n_users = 1

    counts = {
        "athletes": 0,
        "blocks": 0,
        "wellness_days": 0,
        "ekf_updates": 0,
        "workouts": 0,
        "prescriptions": 0,
        "forecasts_recorded": 0,
        "sets_logged": 0,
    }
    failed: list[tuple[str, str]] = []

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(User, AthleteProfile)
                .outerjoin(AthleteProfile, AthleteProfile.user_id == User.id)
                .order_by(User.id.asc())
            )
        ).all()
        if not rows:
            raise SystemExit(
                "No users found; run seed_demo_athletes (or register an athlete) first."
            )

        candidates: list[tuple[User, AthleteProfile | None]] = []
        for user, profile in rows:
            if len(candidates) >= n_users:
                break
            if await _already_replayed(db, user.id):
                continue
            candidates.append((user, profile))

        if not candidates:
            print(
                "Nothing to do — every athlete already has a state timeline "
                "(idempotent skip). Seed more athletes, or raise --users."
            )
            return

        label = {
            SOURCE_PMDATA: "real PMData",
            SOURCE_HIT_STRENGTH: "real hit-strength",
            SOURCE_SCOPESENSE: "real ScopeSense",
        }.get(source, "synthetic")
        print(f"replaying {label} history for {len(candidates)} athlete(s)...")
        for index, (user, profile) in enumerate(candidates):
            try:
                if source == SOURCE_PMDATA:
                    participant = pmdata_participants[index % len(pmdata_participants)]
                    plan = _pmdata_plan(participant, days)
                    tag = f" <- {participant}"
                elif source == SOURCE_SCOPESENSE:
                    participant = scopesense_participants[index % len(scopesense_participants)]
                    plan = _scopesense_plan(participant, days)
                    tag = f" <- {participant}"
                elif source == SOURCE_HIT_STRENGTH:
                    plan = _hit_strength_plan(days)
                    tag = " <- hit-strength lifter"
                else:
                    plan = _synthetic_plan(random.Random(user.id), days)
                    tag = ""

                if not plan:
                    print(f"  [skip] {user.email}{tag}: no usable history")
                    continue

                await _replay_athlete(
                    db,
                    user,
                    profile,
                    plan=plan,
                    with_forecasts=with_forecasts,
                    wellness_source=WELLNESS_SOURCE_BY_REPLAY[source],
                    counts=counts,
                )
                print(f"  [ok]   {user.email}{tag}")
            except Exception as exc:  # one bad athlete must not abort the run
                await db.rollback()
                failed.append((user.email, f"{type(exc).__name__}: {exc}"))
                print(f"  [FAIL] {user.email}: {type(exc).__name__}: {exc}")
                traceback.print_exc()

    print("Seeded:")
    for k, v in counts.items():
        print(f"  {k:20s}: {v}")

    if counts["ekf_updates"] < 40:
        print(
            f"\nNote: {counts['ekf_updates']} EKF update rows written fleet-wide; the "
            "calibration gate needs 40. Raise --days or --users."
        )

    if failed:
        print(f"\n{len(failed)} athlete(s) failed:")
        for email, err in failed:
            print(f"  {email}: {err}")
        raise SystemExit(1)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Replay athlete history through the service layer to populate shadow tables"
    )
    ap.add_argument("--users", type=int, default=5)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument(
        "--source",
        choices=SOURCES,
        default=SOURCE_SYNTHETIC,
        help=(
            "'pmdata' replays real participants (real soreness / stress / mood / session "
            "RPE). 'hit-strength' replays one real lifter with real per-set weight / reps / "
            "RPE, giving true volume load, set counts, RIR and e1RM observations — but no "
            "check-ins, because that lifter logged none. 'synthetic' invents everything."
        ),
    )
    ap.add_argument(
        "--no-forecasts",
        action="store_true",
        help="Skip the block/prescription leg; wellness and workouts only",
    )
    args = ap.parse_args()
    asyncio.run(
        seed(
            n_users=args.users,
            days=args.days,
            with_forecasts=not args.no_forecasts,
            source=args.source,
        )
    )


if __name__ == "__main__":
    main()
