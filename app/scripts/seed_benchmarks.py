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
       state_targets=["aerobic", "power"], protocol_summary="Track or measured 400 m",
       standardization_rules={"floor": 120.0, "cap": 50.0}),
    _b(code="run_1mile_time", name="1 mile time", domain="running", metric_type="time", unit="seconds",
       is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["aerobic"], standardization_rules={"floor": 720.0, "cap": 240.0}),
    _b(code="run_5k_time", name="5 km time", domain="running", metric_type="time", unit="seconds",
       is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["aerobic", "work_capacity"],
       standardization_rules={"floor": 2100.0, "cap": 900.0}),
    _b(code="run_threshold_pace_30min_tt", name="30 min threshold time trial pace",
       domain="running", metric_type="pace", unit="pace_min_per_km",
       is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["aerobic"], standardization_rules={"floor": 7.5, "cap": 3.0}),
    _b(code="run_long_run_decoupling", name="Long run HR decoupling / drift",
       domain="running", metric_type="ratio", unit="percent",
       is_primary_anchor=True, better_direction="lower", observation_weight=0.8,
       state_targets=["aerobic"], fatigue_targets=["metabolic"],
       standardization_rules={"floor": 15.0, "cap": 2.0}),
    _b(code="run_threshold_talk_test", name="Threshold talk-test validator",
       domain="running", metric_type="grade", unit="score",
       is_primary_anchor=False, is_validator_only=True, better_direction="higher",
       observation_weight=0.5, state_targets=["aerobic"]),
    # Legacy 300 m + 1.5 mi Field Test, now a benchmark definition (ADR-0047).
    # /compute-metrics is demoted to the internal calculator behind this def.
    _b(code="run_vo2_field_test_300m_1p5mi", name="VO₂ field test (300 m + 1.5 mi)",
       domain="running", metric_type="score", unit="ml_kg_min",
       is_primary_anchor=True, better_direction="higher", observation_weight=0.9,
       state_targets=["aerobic"],
       protocol_summary="300 m all-out + 1.5 mi time trial; estimates VO₂max / "
                        "aerobic capacity. The onramp aerobic benchmark.",
       standardization_rules={"floor": 25.0, "cap": 70.0},
       domain_lenses=["running"],
       assessable_skill_tags=["aerobic_capacity"],
       measurement_protocol={
           "summary": "Two-part run test: a 300 m all-out and a 1.5 mi time trial.",
           "inputs": ["run_300m_seconds", "run_1p5mi_seconds"],
           "output": "estimated VO₂max (ml/kg/min)",
       }),
    # Sprinting
    _b(code="sprint_0_30_split", name="0–30 m split", domain="sprinting", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["power", "skill"], standardization_rules={"floor": 5.5, "cap": 3.7}),
    _b(code="sprint_flying_30", name="Flying 30 m", domain="sprinting", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["power"], standardization_rules={"floor": 4.0, "cap": 2.5}),
    _b(code="sprint_60m_time", name="60 m time", domain="sprinting", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["power"], standardization_rules={"floor": 9.5, "cap": 6.4}),
    _b(code="sprint_150m_time", name="150 m time", domain="sprinting", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["power", "glycolytic"], standardization_rules={"floor": 24.0, "cap": 14.5}),
    _b(code="sprint_300m_time", name="300 m time", domain="sprinting", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["glycolytic", "power"], standardization_rules={"floor": 55.0, "cap": 32.0}),
    # Powerlifting
    _b(code="pl_e1rm_squat", name="Squat e1RM", domain="powerlifting", metric_type="load",
       unit="kg", is_primary_anchor=True, better_direction="higher", observation_weight=1.0,
       state_targets=["max_strength"], standardization_rules={"floor": 40.0, "cap": 250.0}),
    _b(code="pl_e1rm_bench", name="Bench press e1RM", domain="powerlifting", metric_type="load",
       unit="kg", is_primary_anchor=True, better_direction="higher", observation_weight=1.0,
       state_targets=["max_strength"], standardization_rules={"floor": 20.0, "cap": 180.0}),
    _b(code="pl_e1rm_deadlift", name="Deadlift e1RM", domain="powerlifting", metric_type="load",
       unit="kg", is_primary_anchor=True, better_direction="higher", observation_weight=1.0,
       state_targets=["max_strength"], standardization_rules={"floor": 60.0, "cap": 320.0}),
    _b(code="pl_top_set_rpe_delta", name="Top set RPE vs planned delta", domain="powerlifting",
       metric_type="score", unit="score", is_primary_anchor=True, better_direction="lower",
       observation_weight=0.7, fatigue_targets=["cns", "muscular"]),
    _b(code="pl_std_load_bar_speed", name="Standardized load bar speed metric",
       domain="powerlifting", metric_type="ratio", unit="ratio", is_primary_anchor=True,
       better_direction="higher", observation_weight=0.6, state_targets=["max_strength", "skill"],
       standardization_rules={"floor": 0.1, "cap": 1.0}),
    # Olympic lifting
    _b(code="wl_snatch_1rm", name="Snatch 1RM", domain="olympic_lifting", metric_type="load",
       unit="kg", is_primary_anchor=True, better_direction="higher", observation_weight=1.0,
       state_targets=["power", "skill"], standardization_rules={"floor": 20.0, "cap": 150.0}),
    _b(code="wl_clean_jerk_1rm", name="Clean & jerk 1RM", domain="olympic_lifting",
       metric_type="load", unit="kg", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["power", "max_strength"],
       standardization_rules={"floor": 30.0, "cap": 200.0}),
    _b(code="wl_front_squat_1rm", name="Front squat 1RM", domain="olympic_lifting",
       metric_type="load", unit="kg", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.9, state_targets=["max_strength", "skill"],
       standardization_rules={"floor": 30.0, "cap": 220.0}),
    _b(code="wl_technical_grade_85pct", name="Technical grade @ ~85%", domain="olympic_lifting",
       metric_type="grade", unit="score", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.8, state_targets=["skill"],
       standardization_rules={"floor": 0.0, "cap": 100.0}),
    _b(code="wl_back_squat_1rm", name="Back squat 1RM (weightlifting)", domain="olympic_lifting",
       metric_type="load", unit="kg", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.9, state_targets=["max_strength"],
       standardization_rules={"floor": 40.0, "cap": 260.0}),
    # Gymnastics
    _b(code="gym_strict_pullup_max", name="Strict pull-up max reps", domain="gymnastics",
       metric_type="reps", unit="reps", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["max_strength", "skill"],
       standardization_rules={"floor": 0.0, "cap": 30.0}),
    _b(code="gym_ring_support_hold", name="Ring support hold", domain="gymnastics",
       metric_type="hold_time", unit="seconds", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["skill", "structural"], tissue_targets=["shoulder"],
       standardization_rules={"floor": 0.0, "cap": 60.0}),
    _b(code="gym_handstand_hold", name="Handstand hold", domain="gymnastics",
       metric_type="hold_time", unit="seconds", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.9, state_targets=["skill"], tissue_targets=["wrist", "shoulder"],
       standardization_rules={"floor": 0.0, "cap": 120.0}),
    _b(code="gym_strict_dip_max", name="Strict dip max reps", domain="gymnastics",
       metric_type="reps", unit="reps", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["max_strength", "skill"],
       standardization_rules={"floor": 0.0, "cap": 40.0}),
    _b(code="gym_false_grip_hang", name="False grip hang", domain="gymnastics",
       metric_type="hold_time", unit="seconds", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.8, state_targets=["max_strength"], tissue_targets=["elbow", "finger"],
       standardization_rules={"floor": 0.0, "cap": 60.0}),
    _b(code="gym_transition_quality", name="Ring/bar transition quality (rubric)",
       domain="gymnastics", metric_type="grade", unit="score", is_validator_only=True,
       is_primary_anchor=False, better_direction="higher", observation_weight=0.4,
       state_targets=["skill"]),
    # Grip. No grip CAPACITY axis exists (grip is a fatigue axis); grip strength is a
    # strength expression, so grip benchmarks map WEAKLY into capacity.max_strength as
    # partial evidence of general strength (ADR-0034 amendment 2026-07-06). fatigue.grip
    # stays dose-driven; tissue_targets remain metadata (not observation mappings).
    _b(code="grip_plate_pinch_hold", name="Plate pinch hold", domain="grip",
       metric_type="hold_time", unit="seconds", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["max_strength"], fatigue_targets=["grip"],
       tissue_targets=["finger"], standardization_rules={"floor": 0.0, "cap": 60.0}),
    _b(code="grip_thick_bar_hold", name="Thick bar hold", domain="grip",
       metric_type="hold_time", unit="seconds", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["max_strength"], fatigue_targets=["grip"],
       standardization_rules={"floor": 0.0, "cap": 60.0}),
    _b(code="grip_rolling_handle_lift", name="Rolling handle lift", domain="grip",
       metric_type="load", unit="kg", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.9, state_targets=["max_strength"], fatigue_targets=["grip"],
       standardization_rules={"floor": 20.0, "cap": 120.0}),
    _b(code="grip_crush_test", name="Crush dynamometer / standardized crush test", domain="grip",
       metric_type="score", unit="score", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.8, state_targets=["max_strength"], fatigue_targets=["grip"],
       standardization_rules={"floor": 0.0, "cap": 100.0}),
    _b(code="grip_farmers_hold", name="Farmers carry hold time @ load", domain="grip",
       metric_type="hold_time", unit="seconds", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.9, state_targets=["max_strength"], fatigue_targets=["grip"],
       tissue_targets=["finger"], standardization_rules={"floor": 0.0, "cap": 90.0}),
    # Mixed modal
    _b(code="mm_short_benchmark_wod", name="Short benchmark WOD", domain="mixed_modal",
       metric_type="time", unit="seconds", is_primary_anchor=True, better_direction="lower",
       observation_weight=0.9, state_targets=["glycolytic", "work_capacity"],
       standardization_rules={"floor": 600.0, "cap": 180.0}),
    _b(code="mm_aerobic_skill_benchmark_wod", name="Aerobic + skill benchmark WOD",
       domain="mixed_modal", metric_type="time", unit="seconds", is_primary_anchor=True,
       better_direction="lower", observation_weight=0.9,
       state_targets=["aerobic", "skill"],
       standardization_rules={"floor": 1500.0, "cap": 480.0}),
    _b(code="mm_row_2k", name="2k row", domain="mixed_modal", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["aerobic", "work_capacity"],
       standardization_rules={"floor": 600.0, "cap": 360.0}),
    _b(code="mm_bike_10min_output", name="10 min bike output", domain="mixed_modal",
       metric_type="calories", unit="calories", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["aerobic"],
       standardization_rules={"floor": 80.0, "cap": 350.0}),
    _b(code="mm_repeatability_test", name="Repeatability / repeat WOD test", domain="mixed_modal",
       metric_type="score", unit="score", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.8, state_targets=["work_capacity", "glycolytic"],
       standardization_rules={"floor": 0.0, "cap": 100.0}),
]

# Defaults for required bools
for row in BENCHMARKS:
    row.setdefault("is_derived_only", False)
    row.setdefault("is_validator_only", False)
    row.setdefault("is_primary_anchor", False)


# Skill-state view metadata for benchmarks that already exist (ADR-0046/0047).
# Applied as an idempotent enrichment pass so already-seeded DBs pick it up too —
# the insert loop skips existing codes, so inline kwargs alone would never reach
# them. Keyed by code; each value sets the four view-metadata columns.
SKILL_VIEW_METADATA: dict[str, dict[str, Any]] = {
    "wl_technical_grade_85pct": {
        "domain_lenses": ["olympic_lifting", "strength"],
        "movement_skill_mappings": {"clean": "value", "snatch": "value"},
        "assessable_skill_tags": ["olympic_lifting_technique"],
        "measurement_protocol": {
            "summary": "Coach/self technical-grade rubric on lifts at ~85% 1RM.",
            "scale": "0-100",
        },
    },
    "gym_transition_quality": {
        "domain_lenses": ["gymnastics"],
        "movement_skill_mappings": {"ring_muscle_up": "value"},
        "assessable_skill_tags": ["ring_transition"],
        "measurement_protocol": {
            "summary": "Rubric-scored quality of ring/bar transitions.",
            "scale": "0-100",
        },
    },
    "run_long_run_decoupling": {
        "domain_lenses": ["running"],
        "movement_skill_mappings": None,
        "assessable_skill_tags": ["aerobic_durability"],
        "measurement_protocol": {
            "summary": "HR decoupling / cardiac drift across a steady long run.",
            "output": "drift percent (lower is better)",
        },
    },
    "run_threshold_talk_test": {
        "domain_lenses": ["running"],
        "movement_skill_mappings": None,
        "assessable_skill_tags": ["threshold_awareness"],
        "measurement_protocol": {
            "summary": "Talk-test validator of threshold-pace effort perception.",
            "scale": "0-100",
        },
    },
}


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


# For capacity targets the coefficient is the mapping's *informativeness weight*
# (~[0,1]) in the residual anchor (ADR-0034) — normalization comes from each
# definition's standardization_rules. Fatigue targets keep the legacy additive
# nudge (coefficient + config scale/amp).
MAPPINGS: list[dict[str, Any]] = [
    {"benchmark_code": "pl_e1rm_squat", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 1.0, "intercept": 0.0, "config": {}},
    {"benchmark_code": "pl_e1rm_bench", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 0.7, "intercept": 0.0, "config": {}},
    {"benchmark_code": "pl_e1rm_deadlift", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 0.9, "intercept": 0.0, "config": {}},
    {"benchmark_code": "pl_top_set_rpe_delta", "target_vector": "fatigue", "target_key": "cns",
     "mapping_type": "direct", "coefficient": 2.5, "intercept": 0.0, "config": {"scale": 3.0, "amp": 1.2}},
    {"benchmark_code": "pl_top_set_rpe_delta", "target_vector": "fatigue", "target_key": "muscular",
     "mapping_type": "direct", "coefficient": 2.0, "intercept": 0.0, "config": {"scale": 3.0, "amp": 1.0}},
    {"benchmark_code": "run_400m_time", "target_vector": "capacity", "target_key": "power",
     "mapping_type": "residual", "coefficient": 0.6, "intercept": 0.0, "config": {}},
    {"benchmark_code": "run_400m_time", "target_vector": "capacity", "target_key": "glycolytic",
     "mapping_type": "residual", "coefficient": 0.5, "intercept": 0.0, "config": {}},
    {"benchmark_code": "run_1mile_time", "target_vector": "capacity", "target_key": "aerobic",
     "mapping_type": "residual", "coefficient": 0.9, "intercept": 0.0, "config": {}},
    {"benchmark_code": "run_5k_time", "target_vector": "capacity", "target_key": "aerobic",
     "mapping_type": "residual", "coefficient": 1.0, "intercept": 0.0, "config": {}},
    {"benchmark_code": "run_vo2_field_test_300m_1p5mi", "target_vector": "capacity",
     "target_key": "aerobic", "mapping_type": "residual", "coefficient": 0.9,
     "intercept": 0.0, "config": {}},
    {"benchmark_code": "wl_snatch_1rm", "target_vector": "capacity", "target_key": "power",
     "mapping_type": "residual", "coefficient": 0.8, "intercept": 0.0, "config": {}},
    {"benchmark_code": "wl_clean_jerk_1rm", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 0.7, "intercept": 0.0, "config": {}},
    {"benchmark_code": "gym_strict_pullup_max", "target_vector": "capacity", "target_key": "skill",
     "mapping_type": "residual", "coefficient": 0.7, "intercept": 0.0, "config": {}},
    {"benchmark_code": "pl_std_load_bar_speed", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 0.4, "intercept": 0.0, "config": {}},

    # Phase-1B capacity-coverage additions: mappings for previously-unmapped
    # anchor defs (all now have standardization_rules — see BENCHMARKS above).
    # Grip domain, tissue-vector targets, and validator-only defs are deferred.
    {"benchmark_code": "run_threshold_pace_30min_tt", "target_vector": "capacity", "target_key": "aerobic",
     "mapping_type": "residual", "coefficient": 0.9, "intercept": 0.0, "config": {}},
    {"benchmark_code": "run_long_run_decoupling", "target_vector": "capacity", "target_key": "aerobic",
     "mapping_type": "residual", "coefficient": 0.6, "intercept": 0.0, "config": {}},
    {"benchmark_code": "sprint_0_30_split", "target_vector": "capacity", "target_key": "power",
     "mapping_type": "residual", "coefficient": 0.8, "intercept": 0.0, "config": {}},
    {"benchmark_code": "sprint_0_30_split", "target_vector": "capacity", "target_key": "skill",
     "mapping_type": "residual", "coefficient": 0.4, "intercept": 0.0, "config": {}},
    {"benchmark_code": "sprint_flying_30", "target_vector": "capacity", "target_key": "power",
     "mapping_type": "residual", "coefficient": 0.9, "intercept": 0.0, "config": {}},
    {"benchmark_code": "sprint_60m_time", "target_vector": "capacity", "target_key": "power",
     "mapping_type": "residual", "coefficient": 0.8, "intercept": 0.0, "config": {}},
    {"benchmark_code": "sprint_150m_time", "target_vector": "capacity", "target_key": "power",
     "mapping_type": "residual", "coefficient": 0.6, "intercept": 0.0, "config": {}},
    {"benchmark_code": "sprint_150m_time", "target_vector": "capacity", "target_key": "glycolytic",
     "mapping_type": "residual", "coefficient": 0.6, "intercept": 0.0, "config": {}},
    {"benchmark_code": "sprint_300m_time", "target_vector": "capacity", "target_key": "glycolytic",
     "mapping_type": "residual", "coefficient": 0.8, "intercept": 0.0, "config": {}},
    {"benchmark_code": "sprint_300m_time", "target_vector": "capacity", "target_key": "power",
     "mapping_type": "residual", "coefficient": 0.4, "intercept": 0.0, "config": {}},
    {"benchmark_code": "wl_front_squat_1rm", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 0.8, "intercept": 0.0, "config": {}},
    {"benchmark_code": "wl_front_squat_1rm", "target_vector": "capacity", "target_key": "skill",
     "mapping_type": "residual", "coefficient": 0.3, "intercept": 0.0, "config": {}},
    {"benchmark_code": "wl_technical_grade_85pct", "target_vector": "capacity", "target_key": "skill",
     "mapping_type": "residual", "coefficient": 0.8, "intercept": 0.0, "config": {}},
    {"benchmark_code": "wl_back_squat_1rm", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 0.85, "intercept": 0.0, "config": {}},
    {"benchmark_code": "gym_ring_support_hold", "target_vector": "capacity", "target_key": "skill",
     "mapping_type": "residual", "coefficient": 0.6, "intercept": 0.0, "config": {}},
    {"benchmark_code": "gym_handstand_hold", "target_vector": "capacity", "target_key": "skill",
     "mapping_type": "residual", "coefficient": 0.7, "intercept": 0.0, "config": {}},
    {"benchmark_code": "gym_strict_dip_max", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 0.6, "intercept": 0.0, "config": {}},
    {"benchmark_code": "gym_strict_dip_max", "target_vector": "capacity", "target_key": "skill",
     "mapping_type": "residual", "coefficient": 0.4, "intercept": 0.0, "config": {}},
    {"benchmark_code": "mm_short_benchmark_wod", "target_vector": "capacity", "target_key": "glycolytic",
     "mapping_type": "residual", "coefficient": 0.7, "intercept": 0.0, "config": {}},
    {"benchmark_code": "mm_short_benchmark_wod", "target_vector": "capacity", "target_key": "work_capacity",
     "mapping_type": "residual", "coefficient": 0.7, "intercept": 0.0, "config": {}},
    {"benchmark_code": "mm_aerobic_skill_benchmark_wod", "target_vector": "capacity", "target_key": "aerobic",
     "mapping_type": "residual", "coefficient": 0.7, "intercept": 0.0, "config": {}},
    {"benchmark_code": "mm_aerobic_skill_benchmark_wod", "target_vector": "capacity", "target_key": "skill",
     "mapping_type": "residual", "coefficient": 0.4, "intercept": 0.0, "config": {}},
    {"benchmark_code": "mm_row_2k", "target_vector": "capacity", "target_key": "aerobic",
     "mapping_type": "residual", "coefficient": 0.8, "intercept": 0.0, "config": {}},
    {"benchmark_code": "mm_row_2k", "target_vector": "capacity", "target_key": "work_capacity",
     "mapping_type": "residual", "coefficient": 0.6, "intercept": 0.0, "config": {}},
    {"benchmark_code": "mm_bike_10min_output", "target_vector": "capacity", "target_key": "aerobic",
     "mapping_type": "residual", "coefficient": 0.8, "intercept": 0.0, "config": {}},
    {"benchmark_code": "mm_repeatability_test", "target_vector": "capacity", "target_key": "work_capacity",
     "mapping_type": "residual", "coefficient": 0.7, "intercept": 0.0, "config": {}},
    {"benchmark_code": "mm_repeatability_test", "target_vector": "capacity", "target_key": "glycolytic",
     "mapping_type": "residual", "coefficient": 0.5, "intercept": 0.0, "config": {}},

    # Grip coverage (ADR-0034 amendment): grip strength is a strength expression but there
    # is no grip capacity axis, so grip benchmarks map WEAKLY into max_strength as partial
    # evidence of general strength. tissue_targets stay metadata (no benchmark->tissue maps).
    {"benchmark_code": "grip_plate_pinch_hold", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 0.3, "intercept": 0.0, "config": {}},
    {"benchmark_code": "grip_thick_bar_hold", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 0.3, "intercept": 0.0, "config": {}},
    {"benchmark_code": "grip_rolling_handle_lift", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 0.35, "intercept": 0.0, "config": {}},
    {"benchmark_code": "grip_crush_test", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 0.3, "intercept": 0.0, "config": {}},
    {"benchmark_code": "grip_farmers_hold", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 0.3, "intercept": 0.0, "config": {}},
    {"benchmark_code": "gym_false_grip_hang", "target_vector": "capacity", "target_key": "max_strength",
     "mapping_type": "residual", "coefficient": 0.3, "intercept": 0.0, "config": {}},
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

        # Idempotent skill-state view-metadata enrichment (ADR-0046/0047).
        # Runs after inserts so it covers both fresh and pre-existing rows.
        b_enriched = 0
        for code, meta in SKILL_VIEW_METADATA.items():
            res = await db.execute(
                select(BenchmarkDefinition).where(BenchmarkDefinition.code == code)
            )
            defn = res.scalars().first()
            if defn is None:
                continue
            defn.domain_lenses = meta.get("domain_lenses")
            defn.movement_skill_mappings = meta.get("movement_skill_mappings")
            defn.assessable_skill_tags = meta.get("assessable_skill_tags")
            defn.measurement_protocol = meta.get("measurement_protocol")
            b_enriched += 1
        await db.commit()
        print(f"Benchmark definitions: enriched {b_enriched} with view metadata.")

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
