"""The fit policy is consulted, not documented (phase 8.4).

``app/logic/dose_model.py`` promises that every consumer of the dose shadow log under
``app/ml`` consults the density fit policy. Since 8.4 a consumer exists
(``app/ml/dose_calibration/build_training_frame.py``), so the promise is checked two ways:

* structurally — a module that reads the shadow log must call ``fit_tier`` (which consults
  ``density_fit_eligible``) or ``density_fit_eligible`` directly;
* behaviourally — the real frame drops every row the policy excludes, refuses a model
  version it cannot recompute, keeps seeded athletes out, and labels causally.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.logic import dose_engine_v1, dose_fit_policy
from app.logic.dose_fit_policy import FIT_POLICY_VERSION, LoggedSession
from app.ml.dose_calibration.build_training_frame import (
    DATA_SOURCE_SEEDED,
    DATA_SOURCE_SHADOW,
    FEATURE_SCHEMA_VERSION,
    GROUP_COLUMN,
    LABEL_COLUMN,
    ShadowFrameError,
    build_shadow_frame,
    grouped_time_split,
)

ROOT = Path(__file__).resolve().parents[1]
LIVE = dose_engine_v1.WORK_PER_TIME_DENSITY.version
T0 = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
POLICY_CALLS = {"density_fit_eligible", "fit_tier"}


# --- structural ------------------------------------------------------------------------


def test_calibration_code_reading_the_shadow_log_consults_the_fit_policy() -> None:
    """A policy nothing consults is documentation. Any module under app/ml that reads the
    dose shadow log must call ``fit_tier`` or ``density_fit_eligible``."""
    readers, offenders = [], []
    for path in (ROOT / "app" / "ml").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {
            n.id if isinstance(n, ast.Name) else n.attr
            for n in ast.walk(tree)
            if isinstance(n, ast.Name | ast.Attribute)
        }
        called = {
            (c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", ""))
            for c in ast.walk(tree)
            if isinstance(c, ast.Call)
        }
        if "DoseModelShadowLog" in names:
            readers.append(path.name)
            if not called & POLICY_CALLS:
                offenders.append(str(path.relative_to(ROOT)))

    assert "build_training_frame.py" in readers, "the guard must not be vacuous"
    assert not offenders, offenders


def test_fit_tier_consults_density_fit_eligible(monkeypatch) -> None:
    """If the density policy said "eligible" for everything, fit_tier would follow it."""
    assert dose_fit_policy.fit_tier(
        v0_volume_used_fabricated_sets=False, v1_density_basis="not_applicable"
    ) == "density_not_modelled"
    monkeypatch.setattr(dose_fit_policy, "density_fit_eligible", lambda _basis: True)
    assert dose_fit_policy.fit_tier(
        v0_volume_used_fabricated_sets=False, v1_density_basis="not_applicable"
    ) == "eligible"


# --- behavioural (pure) ----------------------------------------------------------------


def _athlete(
    user: int, n: int, *, first_wl: int, version: str = LIVE, **over: Any
) -> tuple[list[dict[str, Any]], list[LoggedSession]]:
    rows, sessions = [], []
    for i in range(n):
        wl = first_wl + i
        at = T0 + timedelta(days=2 * i)
        rpe = 5.0 + (i % 3)
        sessions.append(LoggedSession(workout_log_id=wl, user_id=user, at=at, session_rpe=rpe))
        row: dict[str, Any] = {
            "user_id": user, "workout_log_id": wl, "session_at": at,
            "v1_model_version": version, "v1_density_basis": "sets_per_elapsed_minute",
            "v0_volume_used_fabricated_sets": False, "modality": "Strength",
            "duration_minutes": 50.0 + i, "session_rpe": rpe, "total_volume_load": 6000.0,
            "reported_sets": 16.0 + i, "distance_meters": 0.0, "v1_total": 1.0,
            "avg_rir": 2.0, "sleep_quality": 7.0, "life_stress_inverse": 7.0,
        }
        row.update(over)
        rows.append(row)
    return rows, sessions


def test_only_labelled_eligible_rows_of_one_version_enter_the_frame() -> None:
    rows, sessions = _athlete(1, 8, first_wl=1)
    rows[4]["v1_density_basis"] = "prescribed_timed_work_over_elapsed"
    rows[5]["v0_volume_used_fabricated_sets"] = True
    rows[6]["v1_density_basis"] = "not_applicable"
    other, other_s = _athlete(1, 1, first_wl=50, version="v1.1")
    later = T0 + timedelta(days=30)
    other[0]["session_at"] = later
    other_s = [LoggedSession(workout_log_id=50, user_id=1, at=later, session_rpe=6.0)]

    out = build_shadow_frame([*rows, *other], [*sessions, *other_s], {1: "a@x.com"},
                             model_version=LIVE)

    # 8 sessions: wl1-3 lack history, wl4 is labelled, wl5-7 are excluded by tier, wl8 is
    # the last session before the other-version one on day 30 (gap 16 days: too long).
    assert list(out.frame["workout_log_id"]) == [4]
    ex = out.manifest["excluded"]
    assert ex["tier:prescribed_proxy"] == 1
    assert ex["tier:fabricated_sets"] == 1
    assert ex["tier:density_not_modelled"] == 1
    assert ex["other_v1_model_version"] == 1
    assert ex["pair:insufficient_prior_history"] == 3
    assert ex["pair:gap_too_long"] == 1


def test_the_label_is_causal() -> None:
    rows, sessions = _athlete(1, 6, first_wl=1)
    frame = build_shadow_frame(rows, sessions, {}, model_version=LIVE).frame
    first = frame.iloc[0]  # wl 4: earlier RPEs 5, 6, 7 -> baseline 6; next (wl 5) RPE 6
    assert first["workout_log_id"] == 4
    assert first["causal_baseline_rpe"] == pytest.approx(6.0)
    assert first[LABEL_COLUMN] == pytest.approx(6.0 - 6.0)


def test_a_version_this_code_does_not_compute_is_refused() -> None:
    with pytest.raises(ShadowFrameError, match="never pooled"):
        build_shadow_frame([], [], {}, model_version="v1.1")


def test_seeded_athletes_are_excluded_unless_asked_and_then_tag_the_frame() -> None:
    real, real_s = _athlete(1, 6, first_wl=1)
    seed, seed_s = _athlete(2, 6, first_wl=100)
    emails = {1: "athlete@example.com", 2: "demo+gf1@perflab.local"}

    default = build_shadow_frame([*real, *seed], [*real_s, *seed_s], emails, model_version=LIVE)
    assert set(default.frame[GROUP_COLUMN]) == {1}
    assert default.manifest["data_source"] == DATA_SOURCE_SHADOW
    assert default.manifest["excluded"]["seeded_account"] == 6

    opted = build_shadow_frame([*real, *seed], [*real_s, *seed_s], emails, model_version=LIVE,
                               allow_seeded=True)
    assert set(opted.frame[GROUP_COLUMN]) == {1, 2}
    assert opted.manifest["data_source"] == DATA_SOURCE_SEEDED


def test_the_manifest_names_every_version_and_fingerprints_the_rows() -> None:
    rows, sessions = _athlete(1, 7, first_wl=1)
    m = build_shadow_frame(rows, sessions, {}, model_version=LIVE).manifest
    assert m["model_version"] == LIVE
    assert m["fit_policy_version"] == FIT_POLICY_VERSION
    assert m["feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert m["pairing_rule"]["baseline"] == "mean_rpe_of_strictly_earlier_sessions"
    assert m["split_unit"] == GROUP_COLUMN
    assert (m["n_rows"], m["n_athletes"]) == (3, 1)
    assert m["recompute_fidelity"]["n"] == 3

    again = build_shadow_frame(rows, sessions, {}, model_version=LIVE).manifest
    assert again["frame_fingerprint"] == m["frame_fingerprint"]
    rows[5]["total_volume_load"] = 6001.0
    changed = build_shadow_frame(rows, sessions, {}, model_version=LIVE).manifest
    assert changed["frame_fingerprint"] != m["frame_fingerprint"]


def test_an_empty_log_is_an_empty_frame_not_an_error() -> None:
    out = build_shadow_frame([], [], {}, model_version=LIVE)
    assert out.frame.empty
    assert out.manifest["n_rows"] == 0
    assert out.manifest["n_athletes"] == 0


def test_no_athlete_straddles_the_split_on_a_shadow_frame() -> None:
    rows, sessions = [], []
    for user in range(1, 9):
        r, s = _athlete(user, 8, first_wl=user * 100)
        rows += r
        sessions += s
    frame = build_shadow_frame(rows, sessions, {}, model_version=LIVE).frame
    train_df, test_df = grouped_time_split(frame)
    assert set(train_df[GROUP_COLUMN]).isdisjoint(set(test_df[GROUP_COLUMN]))
    assert len(train_df) + len(test_df) == len(frame)


# --- behavioural (database, through the real ingest path) ------------------------------


@pytest.mark.asyncio
async def test_the_shadow_frame_is_built_from_real_ingested_workouts(async_db) -> None:
    from app.ml.dose_calibration.build_training_frame import load_shadow_frame
    from app.models.user import User
    from app.schemas.workouts import WorkoutLog
    from app.services.state_service import process_new_workout

    athletes = []
    for email in ("frame-real@test.com", "demo+gf99@perflab.local"):
        u = User(email=email, hashed_password="x", is_active=True)
        async_db.add(u)
        await async_db.commit()
        await async_db.refresh(u)
        athletes.append(u)

    start = datetime(2026, 9, 1, 8, 0)
    for u in athletes:
        for i in range(6):
            await process_new_workout(
                async_db, u.id,
                WorkoutLog(timestamp=start + timedelta(days=2 * i), modality="Strength",
                           duration_minutes=50.0, session_rpe=6.0 + (i % 2),
                           estimated_sets=18.0, total_volume_load=7000.0),
            )

    out = await load_shadow_frame(async_db, model_version=LIVE)
    real, seeded = athletes
    assert set(out.frame[GROUP_COLUMN]) == {real.id}
    # 6 sessions: 3 without history, 2 labelled, the last has no next session.
    assert out.manifest["n_rows"] == 2
    assert out.manifest["data_source"] == DATA_SOURCE_SHADOW
    assert out.manifest["excluded"]["seeded_account"] == 6
    assert out.manifest["recompute_fidelity"]["n"] == 2

    with pytest.raises(ShadowFrameError):
        await load_shadow_frame(async_db, model_version="v1.1")
