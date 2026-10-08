"""P3b-1: capture what each state transition was computed from. No behaviour change.

Exact tail replay (P3b-2) can only be exact if every transition's inputs, its predecessor and the
identity of the code that ran it were recorded when it happened. Pinned here:

* the identity: deterministic; names the operators' whole transitive import closure (recomputed
  from source, so a new dependency cannot be missed); changes with a module's source or a
  parameter; ignores line endings; keeps its components beside the digest;
* every ``AthleteState`` writer states its ``event_kind``;
* workouts: ``replay_input`` holds the final log fields and the dose actually used; the state
  row records its kind, predecessor and identity; the S0 re-anchor keeps the timestamp it had;
* benchmarks: ``replay_input`` holds the definition's weight (not the request's), the mappings in
  applied order, the score, the resolved effect, the decline decision and what was written;
* **sufficiency**: rebuilding the operator call from only the persisted capture reproduces the
  stored state row exactly, for workouts and benchmarks, and omitting a captured field makes it
  differ (so the capture is both enough and needed);
* ``replay_input`` cannot be changed once written (DB trigger); a workout/benchmark row cannot be
  stored without an identity (DB check).
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.engine import transition_identity as ti
from app.engine.parameters import EngineParameters
from app.engine.state_bridge import unified_from_athlete_row
from app.logic.replay_inputs import (
    MappingSnapshot,
    benchmark_replay_input,
    workout_replay_input,
)
from app.logic.state_transitions import BenchmarkOperatorInput
from app.logic.state_update_v0 import apply_benchmark_observation, update_athlete_state
from app.models.athlete_state import AthleteState
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.benchmark_observation import BenchmarkObservation
from app.models.engine_transition_identity import EngineTransitionIdentity
from app.models.observation_mapping import ObservationMapping
from app.models.user import User
from app.models.workout_log import WorkoutLog as WorkoutLogORM
from app.schemas.benchmarks import BenchmarkObservationCreate
from app.schemas.workouts import StressDose, WorkoutLog
from app.services import benchmark_service, state_service

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Identity (pure)
# --------------------------------------------------------------------------- #

def _module_file(name: str) -> Path | None:
    base = ROOT / name.replace(".", "/")
    for cand in (base.with_suffix(".py"), base / "__init__.py"):
        if cand.exists():
            return cand
    return None


def _app_imports(name: str) -> set[str]:
    path = _module_file(name)
    assert path is not None
    pkg = name if path.name == "__init__.py" else name.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = pkg.split(".")
                base = parts[: len(parts) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            found.add(mod)
            found.update(f"{mod}.{a.name}" for a in node.names)
    return {m for m in found if m == "app" or m.startswith("app.")}


def _closure(roots: tuple[str, ...]) -> set[str]:
    """Static transitive ``app`` import closure, lazy (function-level) imports included, with
    each module's parent packages (importing ``a.b.c`` executes ``a`` and ``a.b``)."""
    seen: set[str] = set()
    todo = list(roots)
    while todo:
        name = todo.pop()
        if name in seen or _module_file(name) is None:  # `from m import f` names a function
            continue
        seen.add(name)
        parts = name.split(".")
        todo.extend(".".join(parts[:i]) for i in range(1, len(parts)))
        todo.extend(_app_imports(name))
    return seen


def test_declared_modules_are_the_operators_whole_import_closure():
    """A module added to (or dropped from) what the operators import must change the identity,
    so the declared tuple has to follow the source."""
    assert set(ti.TRANSITION_MODULES) == _closure(ti.TRANSITION_ROOTS)
    assert list(ti.TRANSITION_MODULES) == sorted(ti.TRANSITION_MODULES)


def test_the_tissue_routing_table_is_part_of_the_identity():
    assert "app.engine.phi_table" in ti.TRANSITION_MODULES  # tissue_impulse_from_dose reads it
    assert "app.engine.state_bridge" in ti.TRANSITION_MODULES  # how a row is decoded and encoded


def test_the_closure_walker_sees_a_lazy_import(tmp_path, monkeypatch):
    """The walker must follow imports inside functions (state_bridge imports the operator
    lazily); a walker that skipped them would under-declare."""
    assert "app.logic.state_update_v0" in _app_imports("app.engine.state_bridge")


def test_identity_is_deterministic_and_keeps_its_components():
    a, b = ti.compute_identity(), ti.compute_identity()
    assert a == b and len(a.digest) == 64
    assert set(a.components["modules"]) == set(ti.TRANSITION_MODULES)
    assert a.components["parameters"] == ti.parameters_digest()
    assert a.components["state_update_model"] and a.components["schema"] == 1


def test_a_changed_module_changes_the_digest_and_names_which(monkeypatch):
    base = ti.compute_identity()
    real = ti.source_digest

    def tampered(name: str) -> str:
        return "0" * 64 if name == "app.engine.phi_table" else real(name)

    monkeypatch.setattr(ti, "source_digest", tampered)
    changed = ti.compute_identity()
    assert changed.digest != base.digest
    differing = {
        n for n in base.components["modules"]
        if base.components["modules"][n] != changed.components["modules"][n]
    }
    assert differing == {"app.engine.phi_table"}
    assert changed.components["parameters"] == base.components["parameters"]


def test_a_changed_parameter_changes_the_digest(monkeypatch):
    base = ti.compute_identity()
    p = EngineParameters()
    p.recovery_zscore_scale = p.recovery_zscore_scale + 0.25
    monkeypatch.setattr(ti, "default_parameters", lambda: p)
    changed = ti.compute_identity()
    assert changed.digest != base.digest
    assert changed.components["parameters"] != base.components["parameters"]
    assert changed.components["modules"] == base.components["modules"]


def test_a_nested_parameter_changes_the_digest(monkeypatch):
    """Dict-valued parameters (the routing/decay tables) are part of the serialization."""
    base = ti.parameters_digest()
    p = EngineParameters()
    key = sorted(p.tau_fatigue_hours)[0]
    p.tau_fatigue_hours = {**p.tau_fatigue_hours, key: p.tau_fatigue_hours[key] * 1.01}
    monkeypatch.setattr(ti, "default_parameters", lambda: p)
    assert ti.parameters_digest() != base


def test_a_non_finite_parameter_is_an_error_not_a_digest(monkeypatch):
    p = EngineParameters()
    p.recovery_zscore_scale = float("nan")
    monkeypatch.setattr(ti, "default_parameters", lambda: p)
    with pytest.raises(ValueError):
        ti.parameters_digest()


def test_source_normalization_ignores_line_endings_and_bom():
    assert ti.normalize_source(b"a = 1\r\nb = 2\r\n") == ti.normalize_source(b"a = 1\nb = 2\n")
    assert ti.normalize_source(b"\xef\xbb\xbfa = 1\n") == ti.normalize_source(b"a = 1\n")
    assert ti.normalize_source(b"a = 1\n") != ti.normalize_source(b"a = 2\n")


def test_current_identity_is_the_computed_one():
    assert ti.current_identity() == ti.compute_identity()


# --------------------------------------------------------------------------- #
# Snapshot builders (pure)
# --------------------------------------------------------------------------- #

def test_workout_snapshot_is_json_and_refuses_a_non_finite_input():
    dose = {"d": 0.1 + 0.2, "nested": {"x": 1e-9}}
    snap = workout_replay_input(
        modality="Strength", dominant_movement_pattern="hinge",
        sleep_quality=None, life_stress_inverse=7.5, dose=dose,
    )
    assert json.loads(json.dumps(snap, allow_nan=False)) == snap
    assert snap["sleep_quality"] is None and snap["dose"]["d"] == 0.30000000000000004
    with pytest.raises(ValueError):
        workout_replay_input(
            modality="Strength", dominant_movement_pattern=None,
            sleep_quality=float("nan"), life_stress_inverse=None, dose={},
        )


def _mapping(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "id": 1, "target_vector": "capacity", "target_key": "max_strength",
        "mapping_type": "residual", "coefficient": 1.0, "intercept": 0.0,
        "min_value": None, "max_value": None, "config": None,
    }
    base.update(kw)
    return type("M", (), base)()


def test_benchmark_snapshot_keeps_mapping_order_and_round_trips_through_a_reader():
    maps = [_mapping(id=9, target_key="b"), _mapping(id=2, target_key="a", config={"k": 0.1 + 0.2})]
    snap = benchmark_replay_input(
        BenchmarkOperatorInput(
            observed_at=datetime(2026, 10, 8, 12, 30, 15, 123456), raw_value=150.0,
            normalized_value=52.5, score01=None, better_direction="higher",
            observation_weight_used=0.5, mappings=maps,
        ),
        effect="bidirectional_update",
        authority_policy_version="authority_policy_v1", evaluation="applied",
        decline=None, state_row_written=True, predecessor_state_id=7,
    )
    assert [m["id"] for m in snap["mappings"]] == [9, 2]
    back = json.loads(json.dumps(snap, allow_nan=False))
    assert back == snap
    assert datetime.fromisoformat(back["observed_at"]) == datetime(2026, 10, 8, 12, 30, 15, 123456)
    rebuilt = [MappingSnapshot.from_dict(m) for m in back["mappings"]]
    assert rebuilt[1].config == {"k": 0.30000000000000004} and rebuilt[0].target_key == "b"
    with pytest.raises(ValueError):
        benchmark_replay_input(
            BenchmarkOperatorInput(
                observed_at=datetime(2026, 1, 1), raw_value=float("inf"), normalized_value=None,
                score01=None, better_direction="higher", observation_weight_used=1.0, mappings=[],
            ),
            effect="none", authority_policy_version=None, evaluation="not_evaluated",
            decline=None, state_row_written=False, predecessor_state_id=None,
        )


def test_every_athlete_state_writer_states_its_event_kind():
    """A writer that omits ``event_kind`` would leave a row that no replay can classify."""
    missing: list[str] = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if name == "AthleteState" and "event_kind" not in {k.arg for k in node.keywords}:
                missing.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert missing == []


# --------------------------------------------------------------------------- #
# Persisted capture (DB)
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _user(db, email: str) -> User:
    u = User(email=email, hashed_password="x", is_active=True)
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


async def _seed_head(db, user_id: int, head_ts: datetime) -> int:
    """Two baseline rows (so no first-event re-anchor applies); returns the head's id."""
    for ts in (head_ts - timedelta(days=2), head_ts):
        _, row = state_service._build_baseline_vector(user_id)
        row.timestamp = ts
        db.add(row)
        await db.commit()
    return await _head_id(db, user_id)


async def _head_id(db, user_id: int) -> int:
    return (await db.execute(
        select(AthleteState.id).where(AthleteState.user_id == user_id)
        .order_by(AthleteState.timestamp.desc(), AthleteState.id.desc()).limit(1)
    )).scalar_one()


async def _seed_definition(
    db, code: str = "pl_e1rm_squat", weight: float = 1.0, better: str = "higher"
) -> None:
    definition = BenchmarkDefinition(
        code=code, name=code, domain="powerlifting", metric_type="load", unit="kg",
        better_direction=better, observation_weight=weight,
        standardization_rules={"floor": 40.0, "cap": 250.0},
    )
    db.add(definition)
    await db.flush()
    db.add(ObservationMapping(
        benchmark_definition_id=definition.id, target_vector="capacity", target_key="max_strength",
        mapping_type="residual", coefficient=1.0, intercept=0.0,
    ))
    await db.commit()


def _measure(observed_at: datetime | None, raw: float = 150.0, **kw: Any) -> BenchmarkObservationCreate:
    return BenchmarkObservationCreate(
        benchmark_code=kw.pop("code", "pl_e1rm_squat"), raw_value=raw, source="benchmark_test",
        observed_at=observed_at, **kw,
    )


async def _states(db, user_id: int) -> list[AthleteState]:
    return list((await db.execute(
        select(AthleteState).where(AthleteState.user_id == user_id)
        .order_by(AthleteState.timestamp, AthleteState.id)
    )).scalars())


def _vec(u) -> dict[str, Any]:
    return {
        "x": u.capacity_x.model_dump(), "f": u.fatigue_f.model_dump(), "t": u.tissue_t.model_dump(),
        "c": u.capacity_confidence.model_dump(), "signal": u.s_struct_signal,
    }


async def _log_workout(db, user: User, ts: datetime, **extra: Any):
    log = WorkoutLog(
        timestamp=ts.replace(tzinfo=UTC), modality="Strength", duration_minutes=60.0,
        session_rpe=8.0, **extra,
    )
    return await state_service.process_new_workout(db, user.id, log, received_at=datetime.now(UTC))


async def _workout_row(db, user_id: int) -> WorkoutLogORM:
    return (await db.execute(
        select(WorkoutLogORM).where(WorkoutLogORM.user_id == user_id)
        .order_by(WorkoutLogORM.id.desc()).limit(1)
    )).scalar_one()


def _rebuild_workout(pred: AthleteState, state: AthleteState, w: WorkoutLogORM, **drop: Any):
    """The workout operator call, built from only what was persisted."""
    ri = dict(w.replay_input)
    ri.update(drop)
    log = WorkoutLog(
        timestamp=state.timestamp.replace(tzinfo=UTC), modality=ri["modality"],
        duration_minutes=w.duration_minutes, session_rpe=w.session_rpe,
        dominant_movement_pattern=ri["dominant_movement_pattern"],
        sleep_quality=ri["sleep_quality"], life_stress_inverse=ri["life_stress_inverse"],
    )
    return update_athlete_state(
        unified_from_athlete_row(pred), StressDose(**ri["dose"]),
        state.timestamp - pred.timestamp, log,
    )


async def test_a_workout_records_its_inputs_kind_predecessor_and_identity(async_db):
    user = await _user(async_db, "cap-w1@test.com")
    head_id = await _seed_head(async_db, user.id, _now() - timedelta(hours=30))

    await _log_workout(
        async_db, user, _now() - timedelta(hours=2),
        dominant_movement_pattern="hinge", sleep_quality=4.0, life_stress_inverse=7.5,
    )

    w = await _workout_row(async_db, user.id)
    state = (await _states(async_db, user.id))[-1]
    assert (state.event_kind, state.source_workout_log_id) == ("workout", w.id)
    assert state.predecessor_state_id == head_id
    assert state.transition_identity == ti.current_identity().digest
    ri = w.replay_input
    assert ri["v"] == 1 and ri["kind"] == "workout"
    assert (ri["modality"], ri["dominant_movement_pattern"]) == (w.modality, "hinge")
    assert (ri["sleep_quality"], ri["life_stress_inverse"]) == (4.0, 7.5)
    assert ri["dose"] == w.dose_snapshot
    registered = await async_db.get(EngineTransitionIdentity, state.transition_identity)
    assert registered is not None and registered.components == ti.current_identity().components


async def test_rebuilding_the_workout_call_from_the_capture_reproduces_the_stored_row(async_db):
    user = await _user(async_db, "cap-w2@test.com")
    # A real earlier workout, so the head carries fatigue for the recovery multiplier to act on.
    await _log_workout(async_db, user, _now() - timedelta(hours=30), dominant_movement_pattern="squat")
    await _log_workout(
        async_db, user, _now() - timedelta(hours=6),
        dominant_movement_pattern="hinge", sleep_quality=4.0, life_stress_inverse=2.0,
    )
    w = await _workout_row(async_db, user.id)
    *_, pred, state = await _states(async_db, user.id)
    assert state.predecessor_state_id == pred.id and pred.event_kind == "workout"

    rebuilt = _vec(_rebuild_workout(pred, state, w))

    assert rebuilt == _vec(unified_from_athlete_row(state))
    # …and each captured field is needed: dropping it changes the result.
    assert _vec(_rebuild_workout(pred, state, w, dominant_movement_pattern=None)) != rebuilt
    assert _vec(_rebuild_workout(pred, state, w, sleep_quality=None)) != rebuilt
    assert _vec(_rebuild_workout(pred, state, w, life_stress_inverse=None)) != rebuilt


async def test_a_first_workout_baseline_is_a_baseline_and_keeps_its_original_timestamp(async_db):
    user = await _user(async_db, "cap-w3@test.com")
    when = _now() - timedelta(hours=5)

    await _log_workout(async_db, user, when)

    baseline, workout_state = await _states(async_db, user.id)
    assert baseline.event_kind == "baseline" and baseline.transition_identity is None
    assert baseline.timestamp == when - timedelta(seconds=1)
    assert baseline.anchored_from is not None and baseline.anchored_from != baseline.timestamp
    assert workout_state.event_kind == "workout"
    assert workout_state.predecessor_state_id == baseline.id


async def test_a_record_only_workout_still_captures_its_inputs_and_writes_no_state(async_db):
    user = await _user(async_db, "cap-w4@test.com")
    await _seed_head(async_db, user.id, _now() - timedelta(hours=1))
    before = len(await _states(async_db, user.id))

    await _log_workout(async_db, user, _now() - timedelta(days=1), dominant_movement_pattern="squat")

    w = await _workout_row(async_db, user.id)
    assert w.state_disposition == "record_only"
    assert w.replay_input["dominant_movement_pattern"] == "squat" and w.replay_input["dose"]
    assert len(await _states(async_db, user.id)) == before


async def test_an_applied_benchmark_records_inputs_and_is_reproducible_from_them(async_db):
    user = await _user(async_db, "cap-b1@test.com")
    await _seed_definition(async_db, weight=0.5)
    head_id = await _seed_head(async_db, user.id, _now() - timedelta(hours=3))

    await benchmark_service.create_observation(
        async_db, user.id, _measure(_now() - timedelta(hours=1), observation_weight=0.1)
    )

    obs = (await async_db.execute(select(BenchmarkObservation))).scalar_one()
    state = (await _states(async_db, user.id))[-1]
    assert (state.event_kind, state.source_observation_id) == ("benchmark", obs.id)
    assert state.predecessor_state_id == head_id
    assert state.transition_identity == ti.current_identity().digest
    ri = obs.replay_input
    assert ri["v"] == 1 and ri["kind"] == "benchmark" and ri["evaluation"] == "applied"
    assert ri["effect"] == "bidirectional_update" and ri["state_row_written"] is True
    assert ri["predecessor_state_id"] == head_id
    # The definition's weight is what the operator read; the request's is a different number.
    assert (ri["observation_weight_used"], obs.observation_weight) == (0.5, 0.1)
    assert ri["decline"] == {
        "intercepted": False, "hold_axis": False, "apply_posterior": None,
        "applied_capacity_effect": "bidirectional_update", "decline_transition_status": None,
    }

    pred = await async_db.get(AthleteState, head_id)

    def rebuild(weight: float):
        return apply_benchmark_observation(
            unified_from_athlete_row(pred), raw_value=ri["raw_value"],
            normalized_value=ri["normalized_value"], better_direction=ri["better_direction"],
            observation_weight=weight,
            mappings=[MappingSnapshot.from_dict(m) for m in ri["mappings"]],
            observed_at=datetime.fromisoformat(ri["observed_at"]), score01=ri["score01"],
        )

    stored = _vec(unified_from_athlete_row(state))
    assert _vec(rebuild(ri["observation_weight_used"])) == stored
    assert _vec(rebuild(obs.observation_weight)) != stored  # the wrong weight does not


async def test_a_lower_is_better_benchmark_is_reproducible_too(async_db):
    user = await _user(async_db, "cap-b2@test.com")
    await _seed_definition(async_db, code="run_time", better="lower")
    head_id = await _seed_head(async_db, user.id, _now() - timedelta(hours=3))

    await benchmark_service.create_observation(
        async_db, user.id, _measure(_now() - timedelta(hours=1), raw=120.0, code="run_time")
    )

    obs = (await async_db.execute(select(BenchmarkObservation))).scalar_one()
    ri = obs.replay_input
    assert ri["better_direction"] == "lower"
    pred = await async_db.get(AthleteState, head_id)
    rebuilt = apply_benchmark_observation(
        unified_from_athlete_row(pred), raw_value=ri["raw_value"],
        normalized_value=ri["normalized_value"], better_direction=ri["better_direction"],
        observation_weight=ri["observation_weight_used"],
        mappings=[MappingSnapshot.from_dict(m) for m in ri["mappings"]],
        observed_at=datetime.fromisoformat(ri["observed_at"]), score01=ri["score01"],
    )
    assert _vec(rebuilt) == _vec(unified_from_athlete_row((await _states(async_db, user.id))[-1]))


async def test_a_late_benchmark_is_captured_as_record_only_with_the_head_it_was_judged_against(async_db):
    user = await _user(async_db, "cap-b3@test.com")
    await _seed_definition(async_db)
    head_id = await _seed_head(async_db, user.id, _now() - timedelta(hours=1))
    before = len(await _states(async_db, user.id))

    await benchmark_service.create_observation(async_db, user.id, _measure(_now() - timedelta(days=1)))

    ri = (await async_db.execute(select(BenchmarkObservation))).scalar_one().replay_input
    assert (ri["evaluation"], ri["state_row_written"], ri["predecessor_state_id"]) == (
        "record_only", False, head_id,
    )
    assert ri["decline"] is None
    assert len(await _states(async_db, user.id)) == before


async def test_a_fresh_athletes_benchmark_chains_from_the_staged_baseline(async_db):
    user = await _user(async_db, "cap-b4@test.com")
    await _seed_definition(async_db)
    observed = _now() - timedelta(hours=2)

    await benchmark_service.create_observation(async_db, user.id, _measure(observed))

    baseline, bench = await _states(async_db, user.id)
    assert (baseline.event_kind, baseline.anchored_from is not None) == ("baseline", True)
    assert baseline.timestamp == observed - timedelta(seconds=1)
    assert (bench.event_kind, bench.predecessor_state_id) == ("benchmark", baseline.id)


async def test_the_decline_decision_is_captured_and_agrees_with_the_observation(async_db):
    user = await _user(async_db, "cap-b5@test.com")
    await _seed_definition(async_db)
    await _seed_head(async_db, user.id, _now() - timedelta(hours=6))
    await benchmark_service.create_observation(
        async_db, user.id, _measure(_now() - timedelta(hours=5), raw=150.0)
    )

    await benchmark_service.create_observation(
        async_db, user.id, _measure(_now() - timedelta(hours=1), raw=45.0)
    )

    obs = (await async_db.execute(
        select(BenchmarkObservation).order_by(BenchmarkObservation.id.desc()).limit(1)
    )).scalar_one()
    d = obs.replay_input["decline"]
    assert d["intercepted"] is True and d["hold_axis"] is True
    assert d["decline_transition_status"] == obs.decline_transition_status is not None
    assert d["applied_capacity_effect"] == obs.applied_capacity_effect
    wrote = (await async_db.execute(
        select(AthleteState.id).where(AthleteState.source_observation_id == obs.id)
    )).first() is not None
    assert obs.replay_input["state_row_written"] is wrote


async def test_an_observation_without_state_authority_is_captured_as_not_evaluated(async_db):
    user = await _user(async_db, "cap-b6@test.com")
    await _seed_definition(async_db)
    await _seed_head(async_db, user.id, _now() - timedelta(hours=3))
    before = len(await _states(async_db, user.id))

    await benchmark_service.create_observation(
        async_db, user.id,
        _measure(_now() - timedelta(hours=1), validity_status="invalid"),
    )

    ri = (await async_db.execute(select(BenchmarkObservation))).scalar_one().replay_input
    assert (ri["evaluation"], ri["state_row_written"], ri["predecessor_state_id"]) == (
        "not_evaluated", False, None,
    )
    assert len(await _states(async_db, user.id)) == before


# --------------------------------------------------------------------------- #
# DB guarantees
# --------------------------------------------------------------------------- #

async def test_replay_input_cannot_be_changed_once_written(async_db):
    user = await _user(async_db, "cap-i1@test.com")
    await _seed_definition(async_db)
    await _seed_head(async_db, user.id, _now() - timedelta(hours=3))
    await benchmark_service.create_observation(async_db, user.id, _measure(_now() - timedelta(hours=1)))
    await _log_workout(async_db, user, _now() - timedelta(minutes=10))
    obs_id = (await async_db.execute(select(BenchmarkObservation.id))).scalar_one()
    w_id = (await _workout_row(async_db, user.id)).id

    for table, row_id in (("benchmark_observations", obs_id), ("workout_logs", w_id)):
        with pytest.raises(DBAPIError, match="replay_input is immutable"):
            await async_db.execute(
                text(f"UPDATE {table} SET replay_input = '{{}}'::jsonb WHERE id = :i"), {"i": row_id}
            )
        await async_db.rollback()
        with pytest.raises(DBAPIError, match="replay_input is immutable"):
            await async_db.execute(
                text(f"UPDATE {table} SET replay_input = NULL WHERE id = :i"), {"i": row_id}
            )
        await async_db.rollback()
        # An unrelated column, and re-writing the identical value, are fine.
        await async_db.execute(
            text(f"UPDATE {table} SET replay_input = replay_input WHERE id = :i"), {"i": row_id}
        )
    await async_db.execute(text("UPDATE benchmark_observations SET notes = 'x' WHERE id = :i"), {"i": obs_id})
    await async_db.commit()


async def test_a_row_without_capture_can_gain_it_once_and_then_it_is_fixed(async_db):
    """NULL → value is allowed (the writer sets it in the transaction that inserts the row)."""
    user = await _user(async_db, "cap-i2@test.com")
    await _seed_definition(async_db)
    definition_id = (await async_db.execute(select(BenchmarkDefinition.id))).scalar_one()
    obs = BenchmarkObservation(
        user_id=user.id, benchmark_definition_id=definition_id, observed_at=_now(),
        raw_value=100.0, validity_status="valid", source="benchmark_test",
    )
    async_db.add(obs)
    await async_db.commit()
    assert obs.replay_input is None

    obs.replay_input = {"v": 1, "kind": "benchmark"}
    await async_db.commit()
    obs.replay_input = {"v": 1, "kind": "tampered"}
    with pytest.raises(DBAPIError, match="replay_input is immutable"):
        await async_db.commit()
    await async_db.rollback()


async def test_a_workout_or_benchmark_row_cannot_be_stored_without_an_identity(async_db):
    uid = (await _user(async_db, "cap-i3@test.com")).id  # a rollback expires the ORM object
    for kind in ("workout", "benchmark"):
        _, row = state_service._build_baseline_vector(uid)
        row.event_kind = kind
        async_db.add(row)
        with pytest.raises(IntegrityError, match="ck_athlete_states_transition_has_identity"):
            await async_db.commit()
        await async_db.rollback()


async def test_an_unknown_event_kind_is_refused(async_db):
    uid = (await _user(async_db, "cap-i4@test.com")).id
    _, row = state_service._build_baseline_vector(uid)
    row.event_kind = "mystery"
    async_db.add(row)
    with pytest.raises(IntegrityError, match="ck_athlete_states_event_kind"):
        await async_db.commit()
    await async_db.rollback()
