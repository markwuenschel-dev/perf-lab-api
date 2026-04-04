"""
Seed benchmark_definitions, derived_metric_definitions, and observation_mappings.

Run from repo root after migrations:

    python -m app.scripts.seed_benchmarks

Idempotent: skips rows that already exist (by code). Seeds mappings only when
observation_mappings is empty.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import func, select

from app.core.db import AsyncSessionLocal
from app.models.benchmark_definition import BenchmarkDefinition
from app.models.derived_metric_definition import DerivedMetricDefinition
from app.models.observation_mapping import ObservationMapping

_BENCH_COLS = {c.key for c in BenchmarkDefinition.__table__.columns} - {"id"}
_DERIVED_COLS = {c.key for c in DerivedMetricDefinition.__table__.columns} - {"id"}


def _b(**kwargs: Any) -> dict[str, Any]:
    row = {k: v for k, v in kwargs.items() if k in _BENCH_COLS}
    return row


def _d(**kwargs: Any) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if k in _DERIVED_COLS}


BENCHMARKS: list[dict[str, Any]] = [
    # Running
    _b(code="run_400m_time", name="400 m time", domain="running", metric_type="time", unit="seconds",
       is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["aerobic", "power"], protocol_summary="Track or measured 400 m"),
    _b(code="run_1mile_time", name="1 mile time", domain="running", metric_type="time", unit="seconds",
       is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["aerobic"]),
    _b(code="run_5k_time", name="5 km time", domain="running", metric_type="time", unit="seconds",
       is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["aerobic", "work_capacity"]),
    _b(code="run_threshold_pace_30min_tt", name="30 min threshold time trial pace",
       domain="running", metric_type="pace", unit="pace_min_per_km",
       is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["aerobic"]),
    _b(code="run_long_run_decoupling", name="Long run HR decoupling / drift",
       domain="running", metric_type="ratio", unit="percent",
       is_primary_anchor=True, better_direction="lower", observation_weight=0.8,
       state_targets=["aerobic"], fatigue_targets=["metabolic"]),
    _b(code="run_threshold_talk_test", name="Threshold talk-test validator",
       domain="running", metric_type="grade", unit="score",
       is_primary_anchor=False, is_validator_only=True, better_direction="higher",
       observation_weight=0.5, state_targets=["aerobic"]),
    # Sprinting
    _b(code="sprint_0_30_split", name="0–30 m split", domain="sprinting", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["power", "skill"]),
    _b(code="sprint_flying_30", name="Flying 30 m", domain="sprinting", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["power"]),
    _b(code="sprint_60m_time", name="60 m time", domain="sprinting", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["power"]),
    _b(code="sprint_150m_time", name="150 m time", domain="sprinting", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["power", "glycolytic"]),
    _b(code="sprint_300m_time", name="300 m time", domain="sprinting", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["glycolytic", "power"]),
    # Powerlifting
    _b(code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting", metric_type="load",
       unit="kg", is_primary_anchor=True, better_direction="higher", observation_weight=1.0,
       state_targets=["max_strength"]),
    _b(code="pl_e1rm_bench", name="Bench press e1RM", domain="powerlifting", metric_type="load",
       unit="kg", is_primary_anchor=True, better_direction="higher", observation_weight=1.0,
       state_targets=["max_strength"]),
    _b(code="pl_e1rm_deadlift", name="Deadlift e1RM", domain="powerlifting", metric_type="load",
       unit="kg", is_primary_anchor=True, better_direction="higher", observation_weight=1.0,
       state_targets=["max_strength"]),
    _b(code="pl_top_set_rpe_delta", name="Top set RPE vs planned delta", domain="powerlifting",
       metric_type="score", unit="score", is_primary_anchor=True, better_direction="lower",
       observation_weight=0.7, fatigue_targets=["cns", "muscular"]),
    _b(code="pl_std_load_bar_speed", name="Standardized load bar speed metric",
       domain="powerlifting", metric_type="ratio", unit="ratio", is_primary_anchor=True,
       better_direction="higher", observation_weight=0.6, state_targets=["max_strength", "skill"]),
    # Olympic lifting
    _b(code="wl_snatch_1rm", name="Snatch 1RM", domain="olympic_lifting", metric_type="load",
       unit="kg", is_primary_anchor=True, better_direction="higher", observation_weight=1.0,
       state_targets=["power", "skill"]),
    _b(code="wl_clean_jerk_1rm", name="Clean & jerk 1RM", domain="olympic_lifting",
       metric_type="load", unit="kg", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["power", "max_strength"]),
    _b(code="wl_front_squat_1rm", name="Front squat 1RM", domain="olympic_lifting",
       metric_type="load", unit="kg", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.9, state_targets=["max_strength", "skill"]),
    _b(code="wl_technical_grade_85pct", name="Technical grade @ ~85%", domain="olympic_lifting",
       metric_type="grade", unit="score", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.8, state_targets=["skill"]),
    _b(code="wl_back_squat_1rm", name="Back squat 1RM (weightlifting)", domain="olympic_lifting",
       metric_type="load", unit="kg", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.9, state_targets=["max_strength"]),
    # Gymnastics
    _b(code="gym_strict_pullup_max", name="Strict pull-up max reps", domain="gymnastics",
       metric_type="reps", unit="reps", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["max_strength", "skill"]),
    _b(code="gym_ring_support_hold", name="Ring support hold", domain="gymnastics",
       metric_type="hold_time", unit="seconds", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["skill", "structural"], tissue_targets=["shoulder"]),
    _b(code="gym_handstand_hold", name="Handstand hold", domain="gymnastics",
       metric_type="hold_time", unit="seconds", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.9, state_targets=["skill"], tissue_targets=["wrist", "shoulder"]),
    _b(code="gym_strict_dip_max", name="Strict dip max reps", domain="gymnastics",
       metric_type="reps", unit="reps", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["max_strength", "skill"]),
    _b(code="gym_false_grip_hang", name="False grip hang", domain="gymnastics",
       metric_type="hold_time", unit="seconds", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.8, tissue_targets=["elbow", "finger"]),
    _b(code="gym_transition_quality", name="Ring/bar transition quality (rubric)",
       domain="gymnastics", metric_type="grade", unit="score", is_validator_only=True,
       is_primary_anchor=False, better_direction="higher", observation_weight=0.4,
       state_targets=["skill"]),
    # Grip
    _b(code="grip_plate_pinch_hold", name="Plate pinch hold", domain="grip",
       metric_type="hold_time", unit="seconds", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, fatigue_targets=["grip"], tissue_targets=["finger"]),
    _b(code="grip_thick_bar_hold", name="Thick bar hold", domain="grip",
       metric_type="hold_time", unit="seconds", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, fatigue_targets=["grip"]),
    _b(code="grip_rolling_handle_lift", name="Rolling handle lift", domain="grip",
       metric_type="load", unit="kg", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.9, fatigue_targets=["grip"]),
    _b(code="grip_crush_test", name="Crush dynamometer / standardized crush test", domain="grip",
       metric_type="score", unit="score", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.8, fatigue_targets=["grip"]),
    _b(code="grip_farmers_hold", name="Farmers carry hold time @ load", domain="grip",
       metric_type="hold_time", unit="seconds", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.9, fatigue_targets=["grip"], tissue_targets=["finger"]),
    # Mixed modal
    _b(code="mm_short_benchmark_wod", name="Short benchmark WOD", domain="mixed_modal",
       metric_type="time", unit="seconds", is_primary_anchor=True, better_direction="lower",
       observation_weight=0.9, state_targets=["glycolytic", "work_capacity"]),
    _b(code="mm_aerobic_skill_benchmark_wod", name="Aerobic + skill benchmark WOD",
       domain="mixed_modal", metric_type="time", unit="seconds", is_primary_anchor=True,
       better_direction="lower", observation_weight=0.9,
       state_targets=["aerobic", "skill"]),
    _b(code="mm_row_2k", name="2k row", domain="mixed_modal", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["aerobic", "work_capacity"]),
    _b(code="mm_bike_10min_output", name="10 min bike output", domain="mixed_modal",
       metric_type="calories", unit="calories", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["aerobic"]),
    _b(code="mm_repeatability_test", name="Repeatability / repeat WOD test", domain="mixed_modal",
       metric_type="score", unit="score", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.8, state_targets=["work_capacity", "glycolytic"]),
]

# Defaults for required bools
for row in BENCHMARKS:
    row.setdefault("is_derived_only", False)
    row.setdefault("is_validator_only", False)
    row.setdefault("is_primary_anchor", False)


DERIVED_METRICS: list[dict[str, Any]] = [
    _d(
        code="pl_projected_total",
        name="Projected Powerlifting Total",
        domain="powerlifting",
        metric_type="score",
        unit="kg",
        formula_type="sum",
        formula_config={
            "benchmark_codes": ["pl_e1rm_squat", "pl_e1rm_bench", "pl_e1rm_deadlift"],
        },
        display_priority=10,
        is_dashboard_kpi=True,
        can_affect_prescriber_rules=True,
    ),
    _d(
        code="pl_relative_total",
        name="Relative Total",
        domain="powerlifting",
        metric_type="ratio",
        unit="x_bodyweight",
        formula_type="custom_python_key",
        formula_config={
            "function": "relative_total",
            "inputs": ["pl_projected_total", "bodyweight_kg"],
        },
        display_priority=20,
        is_dashboard_kpi=True,
        can_affect_prescriber_rules=True,
    ),
    _d(
        code="run_fatigue_factor",
        name="400m-to-Mile Fatigue Factor",
        domain="running",
        metric_type="ratio",
        unit="percent",
        formula_type="custom_python_key",
        formula_config={
            "function": "hinshaw_fatigue_factor",
            "inputs": ["run_400m_time", "run_1mile_time"],
        },
        display_priority=15,
        is_dashboard_kpi=True,
        can_affect_prescriber_rules=True,
    ),
    _d(
        code="wl_snatch_cj_ratio",
        name="Snatch to Clean & Jerk Ratio",
        domain="olympic_lifting",
        metric_type="ratio",
        unit="percent",
        formula_type="ratio",
        formula_config={
            "numerator": "wl_snatch_1rm",
            "denominator": "wl_clean_jerk_1rm",
        },
        display_priority=25,
        is_dashboard_kpi=True,
        can_affect_prescriber_rules=True,
    ),
    _d(
        code="gym_pull_support_balance",
        name="Pull to Support Balance",
        domain="gymnastics",
        metric_type="ratio",
        unit="score",
        formula_type="custom_python_key",
        formula_config={
            "function": "pull_support_balance",
            "inputs": ["gym_strict_pullup_max", "gym_ring_support_hold"],
        },
        display_priority=30,
        is_dashboard_kpi=True,
        can_affect_prescriber_rules=False,
    ),
]


MAPPINGS: list[dict[str, Any]] = [
    {"benchmark_code": "pl_e1rm_squat", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "direct", "coefficient": 0.025, "intercept": 0.0,
     "config": {"scale": 120.0, "amp": 2.0}},
    {"benchmark_code": "pl_e1rm_bench", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "direct", "coefficient": 0.02, "intercept": 0.0,
     "config": {"scale": 100.0, "amp": 1.8}},
    {"benchmark_code": "pl_e1rm_deadlift", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "direct", "coefficient": 0.022, "intercept": 0.0,
     "config": {"scale": 120.0, "amp": 2.0}},
    {"benchmark_code": "pl_top_set_rpe_delta", "target_vector": "fatigue", "target_key": "cns",
     "mapping_type": "direct", "coefficient": 2.5, "intercept": 0.0, "config": {"scale": 3.0, "amp": 1.2}},
    {"benchmark_code": "pl_top_set_rpe_delta", "target_vector": "fatigue", "target_key": "muscular",
     "mapping_type": "direct", "coefficient": 2.0, "intercept": 0.0, "config": {"scale": 3.0, "amp": 1.0}},
    {"benchmark_code": "run_400m_time", "target_vector": "capacity", "target_key": "power",
     "mapping_type": "direct", "coefficient": 0.04, "intercept": 0.0,
     "config": {"scale": 0.002, "amp": 2.5}},
    {"benchmark_code": "run_400m_time", "target_vector": "capacity", "target_key": "glycolytic",
     "mapping_type": "direct", "coefficient": 0.03, "intercept": 0.0,
     "config": {"scale": 0.002, "amp": 2.0}},
    {"benchmark_code": "run_1mile_time", "target_vector": "capacity", "target_key": "aerobic",
     "mapping_type": "direct", "coefficient": 0.05, "intercept": 0.0,
     "config": {"scale": 0.0004, "amp": 3.0}},
    {"benchmark_code": "run_5k_time", "target_vector": "capacity", "target_key": "aerobic",
     "mapping_type": "direct", "coefficient": 0.06, "intercept": 0.0,
     "config": {"scale": 0.00025, "amp": 3.5}},
    {"benchmark_code": "wl_snatch_1rm", "target_vector": "capacity", "target_key": "power",
     "mapping_type": "direct", "coefficient": 0.03, "intercept": 0.0,
     "config": {"scale": 80.0, "amp": 2.2}},
    {"benchmark_code": "wl_clean_jerk_1rm", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "direct", "coefficient": 0.028, "intercept": 0.0,
     "config": {"scale": 100.0, "amp": 2.0}},
    {"benchmark_code": "gym_strict_pullup_max", "target_vector": "capacity", "target_key": "skill",
     "mapping_type": "direct", "coefficient": 0.08, "intercept": 0.0,
     "config": {"scale": 15.0, "amp": 2.0}},
    {"benchmark_code": "pl_std_load_bar_speed", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "direct", "coefficient": 0.018, "intercept": 0.0,
     "config": {"scale": 0.6, "amp": 1.6}},
]


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        b_inserted = 0
        for row in BENCHMARKS:
            res = await db.execute(
                select(BenchmarkDefinition).where(BenchmarkDefinition.code == row["code"])
            )
            if res.scalars().first():
                continue
            db.add(BenchmarkDefinition(**row))
            b_inserted += 1
        await db.commit()
        print(f"Benchmark definitions: inserted {b_inserted} new rows.")

        d_inserted = 0
        for row in DERIVED_METRICS:
            res = await db.execute(
                select(DerivedMetricDefinition).where(DerivedMetricDefinition.code == row["code"])
            )
            if res.scalars().first():
                continue
            db.add(DerivedMetricDefinition(**row))
            d_inserted += 1
        await db.commit()
        print(f"Derived metric definitions: inserted {d_inserted} new rows.")

        cnt = await db.scalar(select(func.count()).select_from(ObservationMapping))
        if (cnt or 0) > 0:
            print("Observation mappings: skipped (table non-empty).")
            return

        id_by_code: dict[str, int] = {}
        res = await db.execute(select(BenchmarkDefinition.id, BenchmarkDefinition.code))
        for bid, code in res.all():
            id_by_code[code] = bid

        m_inserted = 0
        for m in MAPPINGS:
            bid = id_by_code.get(m["benchmark_code"])
            if not bid:
                continue
            db.add(
                ObservationMapping(
                    benchmark_definition_id=bid,
                    target_vector=m["target_vector"],
                    target_key=m["target_key"],
                    mapping_type=m["mapping_type"],
                    coefficient=m["coefficient"],
                    intercept=m.get("intercept", 0.0),
                    min_value=m.get("min_value"),
                    max_value=m.get("max_value"),
                    config=m.get("config"),
                )
            )
            m_inserted += 1
        await db.commit()
        print(f"Observation mappings: inserted {m_inserted} rows.")


if __name__ == "__main__":
    asyncio.run(seed())
