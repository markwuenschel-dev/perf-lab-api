"""
Seed benchmark_definitions, derived_metric_definitions, and observation_mappings.

Run from repo root after migrations:

    python -m app.scripts.seed_benchmarks

Idempotent: skips rows that already exist (by code). Seeds mappings only when
observation_mappings is empty.

Two enrichment passes then update rows that already exist, because the insert loop never
touches them: the skill-state view metadata, and ``BENCHMARK_EXPLANATIONS``. The
explanations are code-owned — this module is the source of every benchmark's
``description`` and ``protocol_summary``, and that pass writes those two columns and nothing
else. Edit the text here, not in the database.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.logic.prescription_evidence import STRENGTH_PRESCRIPTION_EVIDENCE_MAX_AGE_DAYS
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
       state_targets=["aerobic", "power"],
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
       standardization_rules={"floor": 25.0, "cap": 70.0},
       domain_lenses=["running"],
       assessable_skill_tags=["aerobic_capacity"],
       measurement_protocol={
           "summary": "Two-part run test: a 300 m all-out and a 1.5 mi time trial.",
           "inputs": ["run_300m_seconds", "run_1p5mi_seconds"],
           "output": "estimated VO₂max (ml/kg/min)",
       }),
    # Sprinting
    _b(code="sprint_0_30_split", name="0–30 m split", domain="running", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["power", "skill"], standardization_rules={"floor": 5.5, "cap": 3.7}),
    _b(code="sprint_flying_30", name="Flying 30 m", domain="running", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["power"], standardization_rules={"floor": 4.0, "cap": 2.5}),
    _b(code="sprint_60m_time", name="60 m time", domain="running", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["power"], standardization_rules={"floor": 9.5, "cap": 6.4}),
    _b(code="sprint_150m_time", name="150 m time", domain="running", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["power", "glycolytic"], standardization_rules={"floor": 24.0, "cap": 14.5}),
    _b(code="sprint_300m_time", name="300 m time", domain="running", metric_type="time",
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
    _b(code="wl_snatch_1rm", name="Snatch 1RM", domain="weightlifting", metric_type="load",
       unit="kg", is_primary_anchor=True, better_direction="higher", observation_weight=1.0,
       state_targets=["power", "skill"], standardization_rules={"floor": 20.0, "cap": 150.0}),
    _b(code="wl_clean_jerk_1rm", name="Clean & jerk 1RM", domain="weightlifting",
       metric_type="load", unit="kg", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["power", "max_strength"],
       standardization_rules={"floor": 30.0, "cap": 200.0}),
    _b(code="wl_front_squat_1rm", name="Front squat 1RM", domain="weightlifting",
       metric_type="load", unit="kg", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.9, state_targets=["max_strength", "skill"],
       standardization_rules={"floor": 30.0, "cap": 220.0}),
    _b(code="wl_technical_grade_85pct", name="Technical grade @ ~85%", domain="weightlifting",
       metric_type="grade", unit="score", is_primary_anchor=True, better_direction="higher",
       observation_weight=0.8, state_targets=["skill"],
       standardization_rules={"floor": 0.0, "cap": 100.0}),
    _b(code="wl_back_squat_1rm", name="Back squat 1RM (weightlifting)", domain="weightlifting",
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
    _b(code="mm_short_benchmark_wod", name="Short benchmark WOD", domain="mixed",
       metric_type="time", unit="seconds", is_primary_anchor=True, better_direction="lower",
       observation_weight=0.9, state_targets=["glycolytic", "work_capacity"],
       standardization_rules={"floor": 600.0, "cap": 180.0}),
    _b(code="mm_aerobic_skill_benchmark_wod", name="Aerobic + skill benchmark WOD",
       domain="mixed", metric_type="time", unit="seconds", is_primary_anchor=True,
       better_direction="lower", observation_weight=0.9,
       state_targets=["aerobic", "skill"],
       standardization_rules={"floor": 1500.0, "cap": 480.0}),
    _b(code="mm_row_2k", name="2k row", domain="mixed", metric_type="time",
       unit="seconds", is_primary_anchor=True, better_direction="lower", observation_weight=1.0,
       state_targets=["aerobic", "work_capacity"],
       standardization_rules={"floor": 600.0, "cap": 360.0}),
    _b(code="mm_bike_10min_output", name="10 min bike output", domain="mixed",
       metric_type="calories", unit="calories", is_primary_anchor=True, better_direction="higher",
       observation_weight=1.0, state_targets=["aerobic"],
       standardization_rules={"floor": 80.0, "cap": 350.0}),
    _b(code="mm_repeatability_test", name="Repeatability / repeat WOD test", domain="mixed",
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
        "domain_lenses": ["weightlifting", "strength"],
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


#: Shown as a benchmark's "how to measure" when no protocol has been established. Saying so is
#: the honest answer; an invented set of instructions would change what the number means.
PROTOCOL_NOT_YET_DEFINED = "Measurement protocol not yet defined."

#: Benchmarks whose measurement protocol is not established anywhere in this codebase. Each
#: shows :data:`PROTOCOL_NOT_YET_DEFINED` until a real protocol is written for it.
UNDEFINED_PROTOCOL_CODES: frozenset[str] = frozenset({
    "pl_top_set_rpe_delta",
    "pl_std_load_bar_speed",
    "grip_crush_test",
    "mm_short_benchmark_wod",
    "mm_aerobic_skill_benchmark_wod",
    "mm_repeatability_test",
})

#: States the qualification rule as the selector applies it (app/logic/prescription_evidence.py
#: and the set gate in app/logic/strength_evidence.py), so the help never promises more than the
#: rule allows. The window comes from the constant; the RPE 8 bar is the set-level gate.
_E1RM_PROTOCOL = (
    "Report a tested 1-rep max, a set you did (load, reps and effort), or your own estimate, "
    "with the date you did it. A dated tested max, or a dated set of 1–5 reps at RPE 8 or "
    f"higher, performed within the last {STRENGTH_PRESCRIPTION_EVIDENCE_MAX_AGE_DAYS} days, can "
    "guide weight recommendations; an estimate is saved but not used for that."
)

#: What each benchmark measures (``description``) and how to measure it (``protocol_summary``).
#: CODE-OWNED: the enrichment pass in :func:`seed` writes exactly these two columns, on every
#: seed, for already-seeded rows too. Wording is grounded in each definition (name, unit,
#: targets, direction) and the protocol text already in this file; it adds no numbers.
BENCHMARK_EXPLANATIONS: dict[str, dict[str, str]] = {
    # Running
    "run_400m_time": {
        "description": "Your time for 400 m. It reflects both speed and aerobic capacity.",
        "protocol_summary": "Run 400 m as fast as you can on a track or a measured course, "
                            "and record your time.",
    },
    "run_1mile_time": {
        "description": "Your time for 1 mile — mainly a measure of aerobic capacity.",
        "protocol_summary": "Run 1 mile as fast as you can on a track or a measured course, "
                            "and record your time.",
    },
    "run_5k_time": {
        "description": "Your time for 5 km — a measure of aerobic capacity and work capacity.",
        "protocol_summary": "Run 5 km as fast as you can on a measured course, and record your time.",
    },
    "run_threshold_pace_30min_tt": {
        "description": "The average pace you can hold for a 30-minute time trial — a measure of "
                       "aerobic (threshold) fitness.",
        "protocol_summary": "Run a 30-minute time trial at the fastest pace you can hold for the "
                            "whole 30 minutes, and record your average pace.",
    },
    "run_long_run_decoupling": {
        "description": "How much your heart rate drifts relative to your pace across a steady "
                       "long run. Lower means better aerobic durability.",
        "protocol_summary": "Run a steady long run while recording heart rate, and record the "
                            "heart-rate drift (decoupling) as a percent.",
    },
    "run_threshold_talk_test": {
        "description": "A check on how you perceive threshold-pace effort, scored 0–100. It is "
                       "used to validate other measurements.",
        "protocol_summary": "Talk-test validator of threshold-pace effort perception, scored 0–100.",
    },
    "run_vo2_field_test_300m_1p5mi": {
        "description": "An estimate of your VO₂max (aerobic capacity). The onramp aerobic "
                       "benchmark.",
        "protocol_summary": "A two-part run test: a 300 m all-out run and a 1.5 mi time trial.",
    },
    # Sprinting
    "sprint_0_30_split": {
        "description": "Your time over the first 30 m of a sprint — a measure of acceleration.",
        "protocol_summary": "Time the first 30 m of an all-out sprint from the start, and record "
                            "the seconds.",
    },
    "sprint_flying_30": {
        "description": "Your time over 30 m at full speed, after a running start — a measure of "
                       "top speed.",
        "protocol_summary": "Build up to full speed, then time 30 m at full speed, and record the "
                            "seconds.",
    },
    "sprint_60m_time": {
        "description": "Your time for a 60 m sprint — a measure of sprint power.",
        "protocol_summary": "Sprint 60 m all-out, and record your time.",
    },
    "sprint_150m_time": {
        "description": "Your time for 150 m — speed together with glycolytic (short-burst "
                       "anaerobic) capacity.",
        "protocol_summary": "Run 150 m all-out, and record your time.",
    },
    "sprint_300m_time": {
        "description": "Your time for 300 m — mainly glycolytic (anaerobic) capacity, with speed.",
        "protocol_summary": "Run 300 m all-out, and record your time.",
    },
    # Powerlifting
    "pl_e1rm_squat": {
        "description": "Your estimated one-rep max (e1RM) for the squat: the most you could lift "
                       "for a single rep.",
        "protocol_summary": _E1RM_PROTOCOL,
    },
    "pl_e1rm_bench": {
        "description": "Your estimated one-rep max (e1RM) for the bench press: the most you could "
                       "lift for a single rep.",
        "protocol_summary": _E1RM_PROTOCOL,
    },
    "pl_e1rm_deadlift": {
        "description": "Your estimated one-rep max (e1RM) for the deadlift: the most you could "
                       "lift for a single rep.",
        "protocol_summary": _E1RM_PROTOCOL,
    },
    "pl_top_set_rpe_delta": {
        "description": "How far the effort (RPE) of your top set was from the planned effort. "
                       "Lower is better.",
        "protocol_summary": PROTOCOL_NOT_YET_DEFINED,
    },
    "pl_std_load_bar_speed": {
        "description": "Bar speed at a standardized load, expressed as a ratio. Higher is better.",
        "protocol_summary": PROTOCOL_NOT_YET_DEFINED,
    },
    # Olympic lifting
    "wl_snatch_1rm": {
        "description": "The heaviest snatch you can complete for one rep.",
        "protocol_summary": "Work up to your heaviest successful single snatch, and record the load.",
    },
    "wl_clean_jerk_1rm": {
        "description": "The heaviest clean & jerk you can complete for one rep.",
        "protocol_summary": "Work up to your heaviest successful single clean & jerk, and record "
                            "the load.",
    },
    "wl_front_squat_1rm": {
        "description": "The heaviest front squat you can complete for one rep.",
        "protocol_summary": "Work up to your heaviest successful single front squat, and record "
                            "the load.",
    },
    "wl_technical_grade_85pct": {
        "description": "The technical quality of your snatch and clean at about 85% of your 1RM, "
                       "scored 0–100.",
        "protocol_summary": "Coach- or self-graded technique rubric on lifts at about 85% of 1RM, "
                            "scored 0–100.",
    },
    "wl_back_squat_1rm": {
        "description": "The heaviest back squat you can complete for one rep.",
        "protocol_summary": "Work up to your heaviest successful single back squat, and record "
                            "the load.",
    },
    # Gymnastics
    "gym_strict_pullup_max": {
        "description": "The most strict pull-ups you can do in one set.",
        "protocol_summary": "Do as many strict (no kipping) pull-ups as you can in one set, and "
                            "record the reps.",
    },
    "gym_ring_support_hold": {
        "description": "How long you can hold a support position on rings.",
        "protocol_summary": "Hold a support position on rings for as long as you can, and record "
                            "the seconds.",
    },
    "gym_handstand_hold": {
        "description": "How long you can hold a handstand.",
        "protocol_summary": "Hold a handstand for as long as you can, and record the seconds.",
    },
    "gym_strict_dip_max": {
        "description": "The most strict dips you can do in one set.",
        "protocol_summary": "Do as many strict dips as you can in one set, and record the reps.",
    },
    "gym_false_grip_hang": {
        "description": "How long you can hang using a false grip.",
        "protocol_summary": "Hang with a false grip for as long as you can, and record the seconds.",
    },
    "gym_transition_quality": {
        "description": "The quality of your ring or bar transitions, such as the ring muscle-up, "
                       "scored 0–100.",
        "protocol_summary": "Rubric-scored quality of ring or bar transitions, 0–100.",
    },
    # Grip
    "grip_plate_pinch_hold": {
        "description": "How long you can hold plates in a pinch grip.",
        "protocol_summary": "Pinch-grip the plates and hold for as long as you can, and record the "
                            "seconds.",
    },
    "grip_thick_bar_hold": {
        "description": "How long you can hold a thick bar.",
        "protocol_summary": "Hold a thick bar for as long as you can, and record the seconds.",
    },
    "grip_rolling_handle_lift": {
        "description": "The heaviest load you can lift with a rolling handle.",
        "protocol_summary": "Work up to your heaviest successful rolling-handle lift, and record "
                            "the load.",
    },
    "grip_crush_test": {
        "description": "Your crushing grip strength, as a score.",
        "protocol_summary": PROTOCOL_NOT_YET_DEFINED,
    },
    "grip_farmers_hold": {
        "description": "How long you can hold farmers-carry handles at a fixed load.",
        "protocol_summary": "Hold farmers-carry handles at a fixed load for as long as you can, and "
                            "record the seconds. Use the same load when you retest.",
    },
    # Mixed modal
    "mm_short_benchmark_wod": {
        "description": "Your time for a short benchmark workout.",
        "protocol_summary": PROTOCOL_NOT_YET_DEFINED,
    },
    "mm_aerobic_skill_benchmark_wod": {
        "description": "Your time for a benchmark workout that combines aerobic work and skill.",
        "protocol_summary": PROTOCOL_NOT_YET_DEFINED,
    },
    "mm_row_2k": {
        "description": "Your time to row 2,000 m — aerobic capacity and work capacity.",
        "protocol_summary": "Row 2,000 m as fast as you can, and record your time.",
    },
    "mm_bike_10min_output": {
        "description": "The calories you can produce on a bike in 10 minutes — a measure of "
                       "aerobic capacity.",
        "protocol_summary": "Ride as hard as you can for 10 minutes, and record the calories shown "
                            "on the bike.",
    },
    "mm_repeatability_test": {
        "description": "How well you can repeat a workout effort, as a score.",
        "protocol_summary": PROTOCOL_NOT_YET_DEFINED,
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
        domain="weightlifting",
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


async def apply_benchmark_explanations(db: AsyncSession) -> int:
    """Write each existing definition's ``description`` and ``protocol_summary`` from
    :data:`BENCHMARK_EXPLANATIONS`. Those two columns only: every other column belongs to
    the definition's own seed row or to the view-metadata pass. Does not commit."""
    written = 0
    for code, text in BENCHMARK_EXPLANATIONS.items():
        res = await db.execute(select(BenchmarkDefinition).where(BenchmarkDefinition.code == code))
        defn = res.scalars().first()
        if defn is None:
            continue
        defn.description = text["description"]
        defn.protocol_summary = text["protocol_summary"]
        written += 1
    return written


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

        # Code-owned explanations: writes description + protocol_summary and nothing else.
        b_explained = await apply_benchmark_explanations(db)
        await db.commit()
        print(f"Benchmark definitions: wrote explanations for {b_explained}.")

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
