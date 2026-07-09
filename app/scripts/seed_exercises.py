"""
app/scripts/seed_exercises.py

Populates the exercises table with a starter library.
Run once after migrations (from repo root):

    python -m app.scripts.seed_exercises

Covers: Strength, Hypertrophy, Power, Running, Conditioning,
        Calisthenics, Hyrox/CrossFit, Grip, Olympic lifting.
"""

import asyncio
from typing import Any

from sqlalchemy.future import select

from app.core.db import AsyncSessionLocal
from app.data.exercise_bulk import bulk_exercises
from app.engine.phi_table import default_phi_for_row
from app.models.exercise import Exercise

_EXERCISE_COLUMNS = {c.key for c in Exercise.__table__.columns} - {"id"}


def _pattern_family(movement_pattern: str) -> str:
    mp = movement_pattern.lower()
    if "squat" in mp or mp == "single_leg":
        return "squat_family"
    if "hinge" in mp:
        return "hinge_family"
    if "push" in mp:
        return "press_family"
    if "pull" in mp:
        return "pull_family"
    if "run" in mp or "row" in mp or "bike" in mp:
        return "locomotion"
    if "carry" in mp:
        return "carry_family"
    if "jump" in mp:
        return "jump_family"
    if mp == "core":
        return "core_family"
    return "general"


def _enrich_exercise_row(data: dict[str, Any]) -> dict[str, Any]:
    row = dict(data)
    sd = float(row.get("skill_demand", 0.5))
    il = float(row.get("impact_level", 0.5))
    pack = default_phi_for_row(row["modality"], row["movement_pattern"], sd, il)
    row.setdefault("phi_adapt", pack["phi_adapt"])
    row.setdefault("phi_fatigue", pack["phi_fatigue"])
    row.setdefault("phi_tissue", pack["phi_tissue"])
    row.setdefault("energy_mix", pack["energy_mix"])
    row.setdefault("pattern_family", _pattern_family(row["movement_pattern"]))
    row.setdefault("technical_ceiling", min(0.98, sd + 0.12))
    row.setdefault("recovery_cost", 0.25 + il * 0.45)
    row.setdefault("novelty_penalty", 0.08 + sd * 0.08)
    return {k: v for k, v in row.items() if k in _EXERCISE_COLUMNS}


EXERCISES = [
    # ── STRENGTH: Squat pattern ──────────────────────────────────────────
    {"name": "Back Squat", "modality": "Strength", "movement_pattern": "squat",
         "primary_muscles": ["quads", "glutes"], "secondary_muscles": ["hamstrings", "erectors"],
         "equipment_required": ["barbell"], "load_type": "barbell",
         "skill_demand": 0.7, "impact_level": 0.6,
         "weak_point_tags": ["squat_pattern", "anterior_chain"],
         "is_benchmark": True,
         "e1rm_benchmark_code": "pl_e1rm_squat",
         "coaching_notes": "Brace hard, knees track toes, depth below parallel.",
         "meta": {
             "provenance_primitive_ids": [
                 "sheiko_volume_distribution",
                 "rpe_autoregulation",
                 "twin_state_engine",
             ],
         }},

    {"name": "Front Squat", "modality": "Strength", "movement_pattern": "squat",
         "primary_muscles": ["quads", "core"], "secondary_muscles": ["upper_back"],
         "equipment_required": ["barbell"], "load_type": "barbell",
         "skill_demand": 0.8, "impact_level": 0.6,
         "weak_point_tags": ["squat_pattern", "overhead_stability", "thoracic_mobility"]},

    {"name": "Goblet Squat", "modality": "Strength", "movement_pattern": "squat",
         "primary_muscles": ["quads", "glutes"], "secondary_muscles": ["core"],
         "equipment_required": ["kettlebell"], "load_type": "kettlebell",
         "skill_demand": 0.3, "impact_level": 0.4,
         "weak_point_tags": ["squat_pattern", "hip_mobility"],
         "coaching_notes": "Good teaching tool for depth and bracing."},

    {"name": "Bulgarian Split Squat", "modality": "Hypertrophy", "movement_pattern": "single_leg",
         "primary_muscles": ["quads", "glutes"], "secondary_muscles": ["hamstrings"],
         "equipment_required": ["dumbbells"], "load_type": "dumbbell",
         "skill_demand": 0.5, "impact_level": 0.5,
         "weak_point_tags": ["single_leg", "anterior_chain", "hip_mobility"]},

    {"name": "Leg Press", "modality": "Hypertrophy", "movement_pattern": "squat",
         "primary_muscles": ["quads", "glutes"], "secondary_muscles": ["hamstrings"],
         "equipment_required": ["machine"], "load_type": "machine",
         "skill_demand": 0.2, "impact_level": 0.3,
         "weak_point_tags": ["anterior_chain"]},

    # ── STRENGTH: Hinge pattern ──────────────────────────────────────────
    {"name": "Conventional Deadlift", "modality": "Strength", "movement_pattern": "hinge",
         "primary_muscles": ["hamstrings", "glutes", "erectors"], "secondary_muscles": ["lats", "traps"],
         "equipment_required": ["barbell"], "load_type": "barbell",
         "skill_demand": 0.7, "impact_level": 0.7,
         "weak_point_tags": ["hip_hinge", "posterior_chain", "grip"],
         "is_benchmark": True,
         "e1rm_benchmark_code": "pl_e1rm_deadlift",
         "coaching_notes": "Lat engagement, neutral spine, push floor away."},

    {"name": "Romanian Deadlift", "modality": "Hypertrophy", "movement_pattern": "hinge",
         "primary_muscles": ["hamstrings", "glutes"], "secondary_muscles": ["erectors"],
         "equipment_required": ["barbell"], "load_type": "barbell",
         "skill_demand": 0.5, "impact_level": 0.5,
         "weak_point_tags": ["hip_hinge", "posterior_chain"]},

    {"name": "Kettlebell Swing", "modality": "Power", "movement_pattern": "hinge",
         "primary_muscles": ["glutes", "hamstrings"], "secondary_muscles": ["core", "shoulders"],
         "equipment_required": ["kettlebell"], "load_type": "kettlebell",
         "skill_demand": 0.6, "impact_level": 0.6,
         "weak_point_tags": ["hip_hinge", "posterior_chain", "work_capacity"]},

    {"name": "Good Morning", "modality": "Strength", "movement_pattern": "hinge",
         "primary_muscles": ["hamstrings", "erectors"], "secondary_muscles": ["glutes"],
         "equipment_required": ["barbell"], "load_type": "barbell",
         "skill_demand": 0.6, "impact_level": 0.5,
         "weak_point_tags": ["hip_hinge", "posterior_chain"]},

    # ── STRENGTH: Push horizontal ────────────────────────────────────────
    {"name": "Bench Press", "modality": "Strength", "movement_pattern": "push_horizontal",
         "primary_muscles": ["pecs", "triceps"], "secondary_muscles": ["front_delts"],
         "equipment_required": ["barbell"], "load_type": "barbell",
         "skill_demand": 0.5, "impact_level": 0.4,
         "weak_point_tags": ["push_horizontal"],
         "is_benchmark": True,
         "e1rm_benchmark_code": "pl_e1rm_bench"},

    {"name": "Dumbbell Bench Press", "modality": "Hypertrophy", "movement_pattern": "push_horizontal",
         "primary_muscles": ["pecs", "triceps"], "secondary_muscles": ["front_delts"],
         "equipment_required": ["dumbbells"], "load_type": "dumbbell",
         "skill_demand": 0.4, "impact_level": 0.4,
         "weak_point_tags": ["push_horizontal"]},

    {"name": "Push-up", "modality": "Calisthenics", "movement_pattern": "push_horizontal",
         "primary_muscles": ["pecs", "triceps"], "secondary_muscles": ["core"],
         "equipment_required": [], "load_type": "bodyweight",
         "skill_demand": 0.2, "impact_level": 0.2,
         "weak_point_tags": ["push_horizontal"]},

    # ── STRENGTH: Push vertical ──────────────────────────────────────────
    {"name": "Overhead Press", "modality": "Strength", "movement_pattern": "push_vertical",
         "primary_muscles": ["shoulders", "triceps"], "secondary_muscles": ["traps", "core"],
         "equipment_required": ["barbell"], "load_type": "barbell",
         "skill_demand": 0.6, "impact_level": 0.4,
         "weak_point_tags": ["push_vertical", "overhead_stability"],
         "is_benchmark": True},

    {"name": "Dumbbell Shoulder Press", "modality": "Hypertrophy", "movement_pattern": "push_vertical",
         "primary_muscles": ["shoulders", "triceps"], "secondary_muscles": [],
         "equipment_required": ["dumbbells"], "load_type": "dumbbell",
         "skill_demand": 0.4, "impact_level": 0.3,
         "weak_point_tags": ["push_vertical"]},

    {"name": "Handstand Push-up", "modality": "Calisthenics", "movement_pattern": "push_vertical",
         "primary_muscles": ["shoulders", "triceps"], "secondary_muscles": ["core"],
         "equipment_required": [], "load_type": "bodyweight",
         "skill_demand": 0.9, "impact_level": 0.4,
         "weak_point_tags": ["push_vertical", "overhead_stability", "gymnastics_skill"]},

    # ── STRENGTH: Pull vertical ──────────────────────────────────────────
    {"name": "Pull-up", "modality": "Calisthenics", "movement_pattern": "pull_vertical",
         "primary_muscles": ["lats", "biceps"], "secondary_muscles": ["rear_delts"],
         "equipment_required": ["pullup_bar"], "load_type": "bodyweight",
         "skill_demand": 0.5, "impact_level": 0.3,
         "weak_point_tags": ["pull_vertical", "grip"],
         "is_benchmark": True,
         "coaching_notes": "Full hang to chin over bar."},

    {"name": "Weighted Pull-up", "modality": "Strength", "movement_pattern": "pull_vertical",
         "primary_muscles": ["lats", "biceps"], "secondary_muscles": ["rear_delts"],
         "equipment_required": ["pullup_bar"], "load_type": "bodyweight",
         "skill_demand": 0.6, "impact_level": 0.3,
         "weak_point_tags": ["pull_vertical", "grip"]},

    {"name": "Lat Pulldown", "modality": "Hypertrophy", "movement_pattern": "pull_vertical",
         "primary_muscles": ["lats"], "secondary_muscles": ["biceps"],
         "equipment_required": ["cable"], "load_type": "cable",
         "skill_demand": 0.2, "impact_level": 0.2,
         "weak_point_tags": ["pull_vertical"]},

    # ── STRENGTH: Pull horizontal ────────────────────────────────────────
    {"name": "Barbell Row", "modality": "Strength", "movement_pattern": "pull_horizontal",
         "primary_muscles": ["upper_back", "lats"], "secondary_muscles": ["biceps", "erectors"],
         "equipment_required": ["barbell"], "load_type": "barbell",
         "skill_demand": 0.5, "impact_level": 0.4,
         "weak_point_tags": ["pull_horizontal", "posterior_chain"]},

    {"name": "Dumbbell Row", "modality": "Hypertrophy", "movement_pattern": "pull_horizontal",
         "primary_muscles": ["lats", "upper_back"], "secondary_muscles": ["biceps"],
         "equipment_required": ["dumbbells"], "load_type": "dumbbell",
         "skill_demand": 0.3, "impact_level": 0.3,
         "weak_point_tags": ["pull_horizontal"]},

    {"name": "Cable Row", "modality": "Hypertrophy", "movement_pattern": "pull_horizontal",
         "primary_muscles": ["upper_back", "lats"], "secondary_muscles": ["biceps"],
         "equipment_required": ["cable"], "load_type": "cable",
         "skill_demand": 0.2, "impact_level": 0.2,
         "weak_point_tags": ["pull_horizontal"]},

    {"name": "Face Pull", "modality": "Hypertrophy", "movement_pattern": "pull_horizontal",
         "primary_muscles": ["rear_delts", "rotator_cuff"], "secondary_muscles": ["traps"],
         "equipment_required": ["cable"], "load_type": "cable",
         "skill_demand": 0.3, "impact_level": 0.2,
         "weak_point_tags": ["pull_horizontal", "overhead_stability"]},

    # ── OLYMPIC LIFTING ──────────────────────────────────────────────────
    {"name": "Power Clean", "modality": "Power", "movement_pattern": "hinge",
         "primary_muscles": ["glutes", "hamstrings", "traps"], "secondary_muscles": ["quads", "core"],
         "equipment_required": ["barbell"], "load_type": "barbell",
         "skill_demand": 0.9, "impact_level": 0.7,
         "weak_point_tags": ["hip_hinge", "olympic_lifting", "posterior_chain"]},

    {"name": "Hang Power Clean", "modality": "Power", "movement_pattern": "hinge",
         "primary_muscles": ["glutes", "hamstrings", "traps"], "secondary_muscles": [],
         "equipment_required": ["barbell"], "load_type": "barbell",
         "skill_demand": 0.8, "impact_level": 0.6,
         "weak_point_tags": ["hip_hinge", "olympic_lifting"]},

    {"name": "Push Jerk", "modality": "Power", "movement_pattern": "push_vertical",
         "primary_muscles": ["shoulders", "triceps", "glutes"], "secondary_muscles": ["core"],
         "equipment_required": ["barbell"], "load_type": "barbell",
         "skill_demand": 0.9, "impact_level": 0.6,
         "weak_point_tags": ["push_vertical", "olympic_lifting", "overhead_stability"]},

    # ── CARRY ────────────────────────────────────────────────────────────
    {"name": "Farmer Carry", "modality": "Strength", "movement_pattern": "carry",
         "primary_muscles": ["grip", "traps", "core"], "secondary_muscles": ["quads"],
         "equipment_required": ["dumbbells"], "load_type": "dumbbell",
         "skill_demand": 0.3, "impact_level": 0.5,
         "weak_point_tags": ["carry", "grip", "core_stability"]},

    {"name": "Suitcase Carry", "modality": "Strength", "movement_pattern": "carry",
         "primary_muscles": ["core", "grip"], "secondary_muscles": ["traps"],
         "equipment_required": ["dumbbells"], "load_type": "dumbbell",
         "skill_demand": 0.3, "impact_level": 0.4,
         "weak_point_tags": ["carry", "grip", "core_stability", "rotation"]},

    {"name": "Sandbag Carry", "modality": "Conditioning", "movement_pattern": "carry",
         "primary_muscles": ["core", "grip", "quads"], "secondary_muscles": ["glutes"],
         "equipment_required": ["sandbag"], "load_type": "bodyweight",
         "skill_demand": 0.4, "impact_level": 0.6,
         "weak_point_tags": ["carry", "grip", "sled_tolerance"]},

    # ── CORE ─────────────────────────────────────────────────────────────
    {"name": "Plank", "modality": "Strength", "movement_pattern": "core",
         "primary_muscles": ["core"], "secondary_muscles": ["shoulders"],
         "equipment_required": [], "load_type": "time",
         "skill_demand": 0.2, "impact_level": 0.1,
         "weak_point_tags": ["core_stability"]},

    {"name": "Ab Wheel Rollout", "modality": "Strength", "movement_pattern": "core",
         "primary_muscles": ["core"], "secondary_muscles": ["lats", "shoulders"],
         "equipment_required": ["ab_wheel"], "load_type": "bodyweight",
         "skill_demand": 0.5, "impact_level": 0.2,
         "weak_point_tags": ["core_stability"]},

    {"name": "Toes to Bar", "modality": "Calisthenics", "movement_pattern": "core",
         "primary_muscles": ["core", "hip_flexors"], "secondary_muscles": ["lats"],
         "equipment_required": ["pullup_bar"], "load_type": "bodyweight",
         "skill_demand": 0.6, "impact_level": 0.3,
         "weak_point_tags": ["core_stability", "gymnastics_skill", "grip"]},

    # ── CALISTHENICS / GYMNASTICS ─────────────────────────────────────────
    {"name": "Ring Muscle-up", "modality": "Calisthenics", "movement_pattern": "pull_vertical",
         "primary_muscles": ["lats", "pecs", "triceps"], "secondary_muscles": ["core"],
         "equipment_required": ["rings"], "load_type": "bodyweight",
         "skill_demand": 0.95, "impact_level": 0.5,
         "weak_point_tags": ["pull_vertical", "gymnastics_skill"]},

    {"name": "Bar Muscle-up", "modality": "Calisthenics", "movement_pattern": "pull_vertical",
         "primary_muscles": ["lats", "triceps"], "secondary_muscles": ["core"],
         "equipment_required": ["pullup_bar"], "load_type": "bodyweight",
         "skill_demand": 0.9, "impact_level": 0.4,
         "weak_point_tags": ["pull_vertical", "gymnastics_skill"]},

    {"name": "Ring Dip", "modality": "Calisthenics", "movement_pattern": "push_horizontal",
         "primary_muscles": ["pecs", "triceps"], "secondary_muscles": ["shoulders"],
         "equipment_required": ["rings"], "load_type": "bodyweight",
         "skill_demand": 0.7, "impact_level": 0.3,
         "weak_point_tags": ["push_horizontal", "gymnastics_skill"]},

    # ── RUNNING ───────────────────────────────────────────────────────────
    {"name": "Easy Run", "modality": "Running", "movement_pattern": "run",
         "primary_muscles": ["quads", "glutes", "calves"], "secondary_muscles": [],
         "equipment_required": [], "load_type": "distance",
         "skill_demand": 0.2, "impact_level": 0.6,
         "weak_point_tags": ["aerobic_base", "running_economy"]},

    {"name": "Tempo Run", "modality": "Running", "movement_pattern": "run",
         "primary_muscles": ["quads", "glutes", "calves"], "secondary_muscles": [],
         "equipment_required": [], "load_type": "distance",
         "skill_demand": 0.3, "impact_level": 0.7,
         "weak_point_tags": ["lactate_threshold", "running_economy"],
         "coaching_notes": "Comfortably hard — can speak in short sentences."},

    {"name": "400m Intervals", "modality": "Running", "movement_pattern": "run",
         "primary_muscles": ["quads", "glutes", "calves"], "secondary_muscles": [],
         "equipment_required": [], "load_type": "distance",
         "skill_demand": 0.4, "impact_level": 0.8,
         "weak_point_tags": ["anaerobic_capacity", "running_economy", "lactate_threshold"]},

    {"name": "1.5 Mile Time Trial", "modality": "Running", "movement_pattern": "run",
         "primary_muscles": ["quads", "glutes", "calves"], "secondary_muscles": [],
         "equipment_required": [], "load_type": "distance",
         "skill_demand": 0.4, "impact_level": 0.8,
         "weak_point_tags": ["aerobic_base", "lactate_threshold"],
         "is_benchmark": True},

    {"name": "5K Run", "modality": "Running", "movement_pattern": "run",
         "primary_muscles": ["quads", "glutes", "calves"], "secondary_muscles": [],
         "equipment_required": [], "load_type": "distance",
         "skill_demand": 0.3, "impact_level": 0.7,
         "weak_point_tags": ["aerobic_base", "running_economy"],
         "is_benchmark": True},

    # ── CONDITIONING / CROSSFIT / HYROX ──────────────────────────────────
    {"name": "Assault Bike", "modality": "Conditioning", "movement_pattern": "bike",
         "primary_muscles": ["quads", "glutes", "shoulders"], "secondary_muscles": ["core"],
         "equipment_required": ["assault_bike"], "load_type": "time",
         "skill_demand": 0.1, "impact_level": 0.3,
         "weak_point_tags": ["aerobic_base", "work_capacity", "bike_efficiency"]},

    {"name": "Rowing (Ergometer)", "modality": "Conditioning", "movement_pattern": "row",
         "primary_muscles": ["lats", "hamstrings", "glutes"], "secondary_muscles": ["core", "biceps"],
         "equipment_required": ["rower"], "load_type": "distance",
         "skill_demand": 0.5, "impact_level": 0.4,
         "weak_point_tags": ["aerobic_base", "work_capacity", "row_technique"],
         "is_benchmark": True,
         "coaching_notes": "Drive legs first, then lean back, then arms."},

    {"name": "SkiErg", "modality": "Conditioning", "movement_pattern": "row",
         "primary_muscles": ["lats", "core", "shoulders"], "secondary_muscles": [],
         "equipment_required": ["skierg"], "load_type": "distance",
         "skill_demand": 0.4, "impact_level": 0.3,
         "weak_point_tags": ["work_capacity", "aerobic_base", "sled_tolerance"]},

    {"name": "Sled Push", "modality": "Conditioning", "movement_pattern": "carry",
         "primary_muscles": ["quads", "glutes", "calves"], "secondary_muscles": ["core", "shoulders"],
         "equipment_required": ["sled"], "load_type": "distance",
         "skill_demand": 0.3, "impact_level": 0.6,
         "weak_point_tags": ["work_capacity", "sled_tolerance", "anterior_chain"]},

    {"name": "Sled Pull", "modality": "Conditioning", "movement_pattern": "carry",
         "primary_muscles": ["hamstrings", "glutes", "core"], "secondary_muscles": ["calves"],
         "equipment_required": ["sled"], "load_type": "distance",
         "skill_demand": 0.3, "impact_level": 0.5,
         "weak_point_tags": ["work_capacity", "sled_tolerance", "posterior_chain"]},

    {"name": "Wall Ball", "modality": "Conditioning", "movement_pattern": "squat",
         "primary_muscles": ["quads", "glutes", "shoulders"], "secondary_muscles": ["core"],
         "equipment_required": ["wall_ball"], "load_type": "reps",
         "skill_demand": 0.4, "impact_level": 0.5,
         "weak_point_tags": ["work_capacity", "squat_pattern", "sled_tolerance"]},

    {"name": "Burpee", "modality": "Conditioning", "movement_pattern": "mixed",
         "primary_muscles": ["full_body"], "secondary_muscles": [],
         "equipment_required": [], "load_type": "reps",
         "skill_demand": 0.3, "impact_level": 0.5,
         "weak_point_tags": ["work_capacity", "aerobic_base"]},

    {"name": "Double Unders", "modality": "Conditioning", "movement_pattern": "jump",
         "primary_muscles": ["calves", "core"], "secondary_muscles": ["shoulders"],
         "equipment_required": ["jump_rope"], "load_type": "reps",
         "skill_demand": 0.6, "impact_level": 0.5,
         "weak_point_tags": ["gymnastics_skill", "work_capacity"]},

    {"name": "Box Jump", "modality": "Power", "movement_pattern": "jump",
         "primary_muscles": ["glutes", "quads", "calves"], "secondary_muscles": ["core"],
         "equipment_required": ["box"], "load_type": "reps",
         "skill_demand": 0.5, "impact_level": 0.7,
         "weak_point_tags": ["single_leg", "work_capacity"]},

    # ── GRIP SPECIFIC ──────────────────────────────────────────────────
    {"name": "Dead Hang", "modality": "Strength", "movement_pattern": "pull_vertical",
         "primary_muscles": ["grip", "forearms"], "secondary_muscles": ["lats"],
         "equipment_required": ["pullup_bar"], "load_type": "time",
         "skill_demand": 0.2, "impact_level": 0.1,
         "weak_point_tags": ["grip"]},

    {"name": "Plate Pinch", "modality": "Strength", "movement_pattern": "carry",
         "primary_muscles": ["grip", "forearms"], "secondary_muscles": [],
         "equipment_required": ["barbell"], "load_type": "time",
         "skill_demand": 0.2, "impact_level": 0.1,
         "weak_point_tags": ["grip"]},

    {"name": "Towel Pull-up", "modality": "Strength", "movement_pattern": "pull_vertical",
         "primary_muscles": ["grip", "lats", "biceps"], "secondary_muscles": [],
         "equipment_required": ["pullup_bar"], "load_type": "bodyweight",
         "skill_demand": 0.6, "impact_level": 0.3,
         "weak_point_tags": ["grip", "pull_vertical"]},

    # ── ACCESSORY / ISOLATION ─────────────────────────────────────────
    {"name": "Nordic Curl", "modality": "Strength", "movement_pattern": "hinge",
         "primary_muscles": ["hamstrings"], "secondary_muscles": [],
         "equipment_required": [], "load_type": "bodyweight",
         "skill_demand": 0.5, "impact_level": 0.6,
         "weak_point_tags": ["posterior_chain", "hip_hinge"],
         "coaching_notes": "Eccentric hamstring — excellent injury prehab."},

    {"name": "Hip Thrust", "modality": "Hypertrophy", "movement_pattern": "hinge",
         "primary_muscles": ["glutes"], "secondary_muscles": ["hamstrings"],
         "equipment_required": ["barbell"], "load_type": "barbell",
         "skill_demand": 0.3, "impact_level": 0.3,
         "weak_point_tags": ["posterior_chain", "hip_hinge"]},

    {"name": "Copenhagen Plank", "modality": "Strength", "movement_pattern": "core",
         "primary_muscles": ["adductors", "core"], "secondary_muscles": [],
         "equipment_required": [], "load_type": "time",
         "skill_demand": 0.4, "impact_level": 0.2,
         "weak_point_tags": ["core_stability", "single_leg"]},

    {"name": "Calf Raise", "modality": "Hypertrophy", "movement_pattern": "single_leg",
         "primary_muscles": ["calves"], "secondary_muscles": [],
         "equipment_required": [], "load_type": "bodyweight",
         "skill_demand": 0.1, "impact_level": 0.3,
         "weak_point_tags": ["running_economy"]},

    {"name": "Band Pull-Apart", "modality": "Hypertrophy", "movement_pattern": "pull_horizontal",
         "primary_muscles": ["rear_delts", "rotator_cuff"], "secondary_muscles": ["traps"],
         "equipment_required": ["band"], "load_type": "reps",
         "skill_demand": 0.1, "impact_level": 0.1,
         "weak_point_tags": ["overhead_stability", "pull_horizontal"]},
]


# P9 (ADR-0045): estimated-1RM benchmark code per lift. The base seeder is
# insert-only, so already-seeded databases need an idempotent enrichment pass to
# populate the column added in migration a024 on existing rows.
_E1RM_CODE_BY_EXERCISE = {
    "Back Squat": "pl_e1rm_squat",
    "Bench Press": "pl_e1rm_bench",
    "Conventional Deadlift": "pl_e1rm_deadlift",
}


async def seed() -> None:
    combined = EXERCISES + bulk_exercises()
    async with AsyncSessionLocal() as db:
        inserted = 0
        for raw in combined:
            clean = _enrich_exercise_row(raw)
            res = await db.execute(select(Exercise).where(Exercise.name == clean["name"]))
            if res.scalars().first():
                continue
            db.add(Exercise(**clean))
            inserted += 1

        # Idempotent enrichment: backfill e1rm_benchmark_code on lifts that predate
        # a024 (insert-only above skips them). Only writes where currently unset.
        enriched = 0
        for name, code in _E1RM_CODE_BY_EXERCISE.items():
            res = await db.execute(select(Exercise).where(Exercise.name == name))
            row = res.scalars().first()
            if row is not None and row.e1rm_benchmark_code != code:
                row.e1rm_benchmark_code = code
                enriched += 1

        await db.commit()
        print(
            f"Exercise seed: inserted {inserted} new rows "
            f"({len(combined)} in catalog); enriched {enriched} e1RM code(s)."
        )


if __name__ == "__main__":
    asyncio.run(seed())
