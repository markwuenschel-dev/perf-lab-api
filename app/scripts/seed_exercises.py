"""
app/scripts/seed_exercises.py

Populates the exercises table with a starter library.
Run once after migrations (from repo root):

    python -m app.scripts.seed_exercises

Covers: Strength, Hypertrophy, Power, Running, Conditioning,
        Calisthenics, Hyrox/CrossFit, Grip, Olympic lifting.
"""

import asyncio
from app.core.db import AsyncSessionLocal
from app.models.exercise import Exercise


EXERCISES = [
    # ── STRENGTH: Squat pattern ──────────────────────────────────────────
    dict(name="Back Squat", modality="Strength", movement_pattern="squat",
         primary_muscles=["quads", "glutes"], secondary_muscles=["hamstrings", "erectors"],
         equipment_required=["barbell"], load_type="barbell",
         skill_demand=0.7, impact_level=0.6,
         weak_point_tags=["squat_pattern", "anterior_chain"],
         is_benchmark=True,
         coaching_notes="Brace hard, knees track toes, depth below parallel."),

    dict(name="Front Squat", modality="Strength", movement_pattern="squat",
         primary_muscles=["quads", "core"], secondary_muscles=["upper_back"],
         equipment_required=["barbell"], load_type="barbell",
         skill_demand=0.8, impact_level=0.6,
         weak_point_tags=["squat_pattern", "overhead_stability", "thoracic_mobility"]),

    dict(name="Goblet Squat", modality="Strength", movement_pattern="squat",
         primary_muscles=["quads", "glutes"], secondary_muscles=["core"],
         equipment_required=["kettlebell"], load_type="kettlebell",
         skill_demand=0.3, impact_level=0.4,
         weak_point_tags=["squat_pattern", "hip_mobility"],
         coaching_notes="Good teaching tool for depth and bracing."),

    dict(name="Bulgarian Split Squat", modality="Hypertrophy", movement_pattern="single_leg",
         primary_muscles=["quads", "glutes"], secondary_muscles=["hamstrings"],
         equipment_required=["dumbbells"], load_type="dumbbell",
         skill_demand=0.5, impact_level=0.5,
         weak_point_tags=["single_leg", "anterior_chain", "hip_mobility"]),

    dict(name="Leg Press", modality="Hypertrophy", movement_pattern="squat",
         primary_muscles=["quads", "glutes"], secondary_muscles=["hamstrings"],
         equipment_required=["machine"], load_type="machine",
         skill_demand=0.2, impact_level=0.3,
         weak_point_tags=["anterior_chain"]),

    # ── STRENGTH: Hinge pattern ──────────────────────────────────────────
    dict(name="Conventional Deadlift", modality="Strength", movement_pattern="hinge",
         primary_muscles=["hamstrings", "glutes", "erectors"], secondary_muscles=["lats", "traps"],
         equipment_required=["barbell"], load_type="barbell",
         skill_demand=0.7, impact_level=0.7,
         weak_point_tags=["hip_hinge", "posterior_chain", "grip"],
         is_benchmark=True,
         coaching_notes="Lat engagement, neutral spine, push floor away."),

    dict(name="Romanian Deadlift", modality="Hypertrophy", movement_pattern="hinge",
         primary_muscles=["hamstrings", "glutes"], secondary_muscles=["erectors"],
         equipment_required=["barbell"], load_type="barbell",
         skill_demand=0.5, impact_level=0.5,
         weak_point_tags=["hip_hinge", "posterior_chain"]),

    dict(name="Kettlebell Swing", modality="Power", movement_pattern="hinge",
         primary_muscles=["glutes", "hamstrings"], secondary_muscles=["core", "shoulders"],
         equipment_required=["kettlebell"], load_type="kettlebell",
         skill_demand=0.6, impact_level=0.6,
         weak_point_tags=["hip_hinge", "posterior_chain", "work_capacity"]),

    dict(name="Good Morning", modality="Strength", movement_pattern="hinge",
         primary_muscles=["hamstrings", "erectors"], secondary_muscles=["glutes"],
         equipment_required=["barbell"], load_type="barbell",
         skill_demand=0.6, impact_level=0.5,
         weak_point_tags=["hip_hinge", "posterior_chain"]),

    # ── STRENGTH: Push horizontal ────────────────────────────────────────
    dict(name="Bench Press", modality="Strength", movement_pattern="push_horizontal",
         primary_muscles=["pecs", "triceps"], secondary_muscles=["front_delts"],
         equipment_required=["barbell"], load_type="barbell",
         skill_demand=0.5, impact_level=0.4,
         weak_point_tags=["push_horizontal"],
         is_benchmark=True),

    dict(name="Dumbbell Bench Press", modality="Hypertrophy", movement_pattern="push_horizontal",
         primary_muscles=["pecs", "triceps"], secondary_muscles=["front_delts"],
         equipment_required=["dumbbells"], load_type="dumbbell",
         skill_demand=0.4, impact_level=0.4,
         weak_point_tags=["push_horizontal"]),

    dict(name="Push-up", modality="Calisthenics", movement_pattern="push_horizontal",
         primary_muscles=["pecs", "triceps"], secondary_muscles=["core"],
         equipment_required=[], load_type="bodyweight",
         skill_demand=0.2, impact_level=0.2,
         weak_point_tags=["push_horizontal"]),

    # ── STRENGTH: Push vertical ──────────────────────────────────────────
    dict(name="Overhead Press", modality="Strength", movement_pattern="push_vertical",
         primary_muscles=["shoulders", "triceps"], secondary_muscles=["traps", "core"],
         equipment_required=["barbell"], load_type="barbell",
         skill_demand=0.6, impact_level=0.4,
         weak_point_tags=["push_vertical", "overhead_stability"],
         is_benchmark=True),

    dict(name="Dumbbell Shoulder Press", modality="Hypertrophy", movement_pattern="push_vertical",
         primary_muscles=["shoulders", "triceps"], secondary_muscles=[],
         equipment_required=["dumbbells"], load_type="dumbbell",
         skill_demand=0.4, impact_level=0.3,
         weak_point_tags=["push_vertical"]),

    dict(name="Handstand Push-up", modality="Calisthenics", movement_pattern="push_vertical",
         primary_muscles=["shoulders", "triceps"], secondary_muscles=["core"],
         equipment_required=[], load_type="bodyweight",
         skill_demand=0.9, impact_level=0.4,
         weak_point_tags=["push_vertical", "overhead_stability", "gymnastics_skill"]),

    # ── STRENGTH: Pull vertical ──────────────────────────────────────────
    dict(name="Pull-up", modality="Calisthenics", movement_pattern="pull_vertical",
         primary_muscles=["lats", "biceps"], secondary_muscles=["rear_delts"],
         equipment_required=["pullup_bar"], load_type="bodyweight",
         skill_demand=0.5, impact_level=0.3,
         weak_point_tags=["pull_vertical", "grip"],
         is_benchmark=True,
         coaching_notes="Full hang to chin over bar."),

    dict(name="Weighted Pull-up", modality="Strength", movement_pattern="pull_vertical",
         primary_muscles=["lats", "biceps"], secondary_muscles=["rear_delts"],
         equipment_required=["pullup_bar"], load_type="bodyweight",
         skill_demand=0.6, impact_level=0.3,
         weak_point_tags=["pull_vertical", "grip"]),

    dict(name="Lat Pulldown", modality="Hypertrophy", movement_pattern="pull_vertical",
         primary_muscles=["lats"], secondary_muscles=["biceps"],
         equipment_required=["cable"], load_type="cable",
         skill_demand=0.2, impact_level=0.2,
         weak_point_tags=["pull_vertical"]),

    # ── STRENGTH: Pull horizontal ────────────────────────────────────────
    dict(name="Barbell Row", modality="Strength", movement_pattern="pull_horizontal",
         primary_muscles=["upper_back", "lats"], secondary_muscles=["biceps", "erectors"],
         equipment_required=["barbell"], load_type="barbell",
         skill_demand=0.5, impact_level=0.4,
         weak_point_tags=["pull_horizontal", "posterior_chain"]),

    dict(name="Dumbbell Row", modality="Hypertrophy", movement_pattern="pull_horizontal",
         primary_muscles=["lats", "upper_back"], secondary_muscles=["biceps"],
         equipment_required=["dumbbells"], load_type="dumbbell",
         skill_demand=0.3, impact_level=0.3,
         weak_point_tags=["pull_horizontal"]),

    dict(name="Cable Row", modality="Hypertrophy", movement_pattern="pull_horizontal",
         primary_muscles=["upper_back", "lats"], secondary_muscles=["biceps"],
         equipment_required=["cable"], load_type="cable",
         skill_demand=0.2, impact_level=0.2,
         weak_point_tags=["pull_horizontal"]),

    dict(name="Face Pull", modality="Hypertrophy", movement_pattern="pull_horizontal",
         primary_muscles=["rear_delts", "rotator_cuff"], secondary_muscles=["traps"],
         equipment_required=["cable"], load_type="cable",
         skill_demand=0.3, impact_level=0.2,
         weak_point_tags=["pull_horizontal", "overhead_stability"]),

    # ── OLYMPIC LIFTING ──────────────────────────────────────────────────
    dict(name="Power Clean", modality="Power", movement_pattern="hinge",
         primary_muscles=["glutes", "hamstrings", "traps"], secondary_muscles=["quads", "core"],
         equipment_required=["barbell"], load_type="barbell",
         skill_demand=0.9, impact_level=0.7,
         weak_point_tags=["hip_hinge", "olympic_lifting", "posterior_chain"]),

    dict(name="Hang Power Clean", modality="Power", movement_pattern="hinge",
         primary_muscles=["glutes", "hamstrings", "traps"], secondary_muscles=[],
         equipment_required=["barbell"], load_type="barbell",
         skill_demand=0.8, impact_level=0.6,
         weak_point_tags=["hip_hinge", "olympic_lifting"]),

    dict(name="Push Jerk", modality="Power", movement_pattern="push_vertical",
         primary_muscles=["shoulders", "triceps", "glutes"], secondary_muscles=["core"],
         equipment_required=["barbell"], load_type="barbell",
         skill_demand=0.9, impact_level=0.6,
         weak_point_tags=["push_vertical", "olympic_lifting", "overhead_stability"]),

    # ── CARRY ────────────────────────────────────────────────────────────
    dict(name="Farmer Carry", modality="Strength", movement_pattern="carry",
         primary_muscles=["grip", "traps", "core"], secondary_muscles=["quads"],
         equipment_required=["dumbbells"], load_type="dumbbell",
         skill_demand=0.3, impact_level=0.5,
         weak_point_tags=["carry", "grip", "core_stability"]),

    dict(name="Suitcase Carry", modality="Strength", movement_pattern="carry",
         primary_muscles=["core", "grip"], secondary_muscles=["traps"],
         equipment_required=["dumbbells"], load_type="dumbbell",
         skill_demand=0.3, impact_level=0.4,
         weak_point_tags=["carry", "grip", "core_stability", "rotation"]),

    dict(name="Sandbag Carry", modality="Conditioning", movement_pattern="carry",
         primary_muscles=["core", "grip", "quads"], secondary_muscles=["glutes"],
         equipment_required=["sandbag"], load_type="bodyweight",
         skill_demand=0.4, impact_level=0.6,
         weak_point_tags=["carry", "grip", "sled_tolerance"]),

    # ── CORE ─────────────────────────────────────────────────────────────
    dict(name="Plank", modality="Strength", movement_pattern="core",
         primary_muscles=["core"], secondary_muscles=["shoulders"],
         equipment_required=[], load_type="time",
         skill_demand=0.2, impact_level=0.1,
         weak_point_tags=["core_stability"]),

    dict(name="Ab Wheel Rollout", modality="Strength", movement_pattern="core",
         primary_muscles=["core"], secondary_muscles=["lats", "shoulders"],
         equipment_required=["ab_wheel"], load_type="bodyweight",
         skill_demand=0.5, impact_level=0.2,
         weak_point_tags=["core_stability"]),

    dict(name="Toes to Bar", modality="Calisthenics", movement_pattern="core",
         primary_muscles=["core", "hip_flexors"], secondary_muscles=["lats"],
         equipment_required=["pullup_bar"], load_type="bodyweight",
         skill_demand=0.6, impact_level=0.3,
         weak_point_tags=["core_stability", "gymnastics_skill", "grip"]),

    # ── CALISTHENICS / GYMNASTICS ─────────────────────────────────────────
    dict(name="Ring Muscle-up", modality="Calisthenics", movement_pattern="pull_vertical",
         primary_muscles=["lats", "pecs", "triceps"], secondary_muscles=["core"],
         equipment_required=["rings"], load_type="bodyweight",
         skill_demand=0.95, impact_level=0.5,
         weak_point_tags=["pull_vertical", "gymnastics_skill"]),

    dict(name="Bar Muscle-up", modality="Calisthenics", movement_pattern="pull_vertical",
         primary_muscles=["lats", "triceps"], secondary_muscles=["core"],
         equipment_required=["pullup_bar"], load_type="bodyweight",
         skill_demand=0.9, impact_level=0.4,
         weak_point_tags=["pull_vertical", "gymnastics_skill"]),

    dict(name="Ring Dip", modality="Calisthenics", movement_pattern="push_horizontal",
         primary_muscles=["pecs", "triceps"], secondary_muscles=["shoulders"],
         equipment_required=["rings"], load_type="bodyweight",
         skill_demand=0.7, impact_level=0.3,
         weak_point_tags=["push_horizontal", "gymnastics_skill"]),

    # ── RUNNING ───────────────────────────────────────────────────────────
    dict(name="Easy Run", modality="Running", movement_pattern="run",
         primary_muscles=["quads", "glutes", "calves"], secondary_muscles=[],
         equipment_required=[], load_type="distance",
         skill_demand=0.2, impact_level=0.6,
         weak_point_tags=["aerobic_base", "running_economy"]),

    dict(name="Tempo Run", modality="Running", movement_pattern="run",
         primary_muscles=["quads", "glutes", "calves"], secondary_muscles=[],
         equipment_required=[], load_type="distance",
         skill_demand=0.3, impact_level=0.7,
         weak_point_tags=["lactate_threshold", "running_economy"],
         coaching_notes="Comfortably hard — can speak in short sentences."),

    dict(name="400m Intervals", modality="Running", movement_pattern="run",
         primary_muscles=["quads", "glutes", "calves"], secondary_muscles=[],
         equipment_required=[], load_type="distance",
         skill_demand=0.4, impact_level=0.8,
         weak_point_tags=["anaerobic_capacity", "running_economy", "lactate_threshold"]),

    dict(name="1.5 Mile Time Trial", modality="Running", movement_pattern="run",
         primary_muscles=["quads", "glutes", "calves"], secondary_muscles=[],
         equipment_required=[], load_type="distance",
         skill_demand=0.4, impact_level=0.8,
         weak_point_tags=["aerobic_base", "lactate_threshold"],
         is_benchmark=True),

    dict(name="5K Run", modality="Running", movement_pattern="run",
         primary_muscles=["quads", "glutes", "calves"], secondary_muscles=[],
         equipment_required=[], load_type="distance",
         skill_demand=0.3, impact_level=0.7,
         weak_point_tags=["aerobic_base", "running_economy"],
         is_benchmark=True),

    # ── CONDITIONING / CROSSFIT / HYROX ──────────────────────────────────
    dict(name="Assault Bike", modality="Conditioning", movement_pattern="bike",
         primary_muscles=["quads", "glutes", "shoulders"], secondary_muscles=["core"],
         equipment_required=["assault_bike"], load_type="time",
         skill_demand=0.1, impact_level=0.3,
         weak_point_tags=["aerobic_base", "work_capacity", "bike_efficiency"]),

    dict(name="Rowing (Ergometer)", modality="Conditioning", movement_pattern="row",
         primary_muscles=["lats", "hamstrings", "glutes"], secondary_muscles=["core", "biceps"],
         equipment_required=["rower"], load_type="distance",
         skill_demand=0.5, impact_level=0.4,
         weak_point_tags=["aerobic_base", "work_capacity", "row_technique"],
         is_benchmark=True,
         coaching_notes="Drive legs first, then lean back, then arms."),

    dict(name="SkiErg", modality="Conditioning", movement_pattern="row",
         primary_muscles=["lats", "core", "shoulders"], secondary_muscles=[],
         equipment_required=["skierg"], load_type="distance",
         skill_demand=0.4, impact_level=0.3,
         weak_point_tags=["work_capacity", "aerobic_base", "sled_tolerance"]),

    dict(name="Sled Push", modality="Conditioning", movement_pattern="carry",
         primary_muscles=["quads", "glutes", "calves"], secondary_muscles=["core", "shoulders"],
         equipment_required=["sled"], load_type="distance",
         skill_demand=0.3, impact_level=0.6,
         weak_point_tags=["work_capacity", "sled_tolerance", "anterior_chain"]),

    dict(name="Sled Pull", modality="Conditioning", movement_pattern="carry",
         primary_muscles=["hamstrings", "glutes", "core"], secondary_muscles=["calves"],
         equipment_required=["sled"], load_type="distance",
         skill_demand=0.3, impact_level=0.5,
         weak_point_tags=["work_capacity", "sled_tolerance", "posterior_chain"]),

    dict(name="Wall Ball", modality="Conditioning", movement_pattern="squat",
         primary_muscles=["quads", "glutes", "shoulders"], secondary_muscles=["core"],
         equipment_required=["wall_ball"], load_type="reps",
         skill_demand=0.4, impact_level=0.5,
         weak_point_tags=["work_capacity", "squat_pattern", "sled_tolerance"]),

    dict(name="Burpee", modality="Conditioning", movement_pattern="mixed",
         primary_muscles=["full_body"], secondary_muscles=[],
         equipment_required=[], load_type="reps",
         skill_demand=0.3, impact_level=0.5,
         weak_point_tags=["work_capacity", "aerobic_base"]),

    dict(name="Double Unders", modality="Conditioning", movement_pattern="jump",
         primary_muscles=["calves", "core"], secondary_muscles=["shoulders"],
         equipment_required=["jump_rope"], load_type="reps",
         skill_demand=0.6, impact_level=0.5,
         weak_point_tags=["gymnastics_skill", "work_capacity"]),

    dict(name="Box Jump", modality="Power", movement_pattern="jump",
         primary_muscles=["glutes", "quads", "calves"], secondary_muscles=["core"],
         equipment_required=["box"], load_type="reps",
         skill_demand=0.5, impact_level=0.7,
         weak_point_tags=["single_leg", "work_capacity"]),

    # ── GRIP SPECIFIC ──────────────────────────────────────────────────
    dict(name="Dead Hang", modality="Strength", movement_pattern="pull_vertical",
         primary_muscles=["grip", "forearms"], secondary_muscles=["lats"],
         equipment_required=["pullup_bar"], load_type="time",
         skill_demand=0.2, impact_level=0.1,
         weak_point_tags=["grip"]),

    dict(name="Plate Pinch", modality="Strength", movement_pattern="carry",
         primary_muscles=["grip", "forearms"], secondary_muscles=[],
         equipment_required=["barbell"], load_type="time",
         skill_demand=0.2, impact_level=0.1,
         weak_point_tags=["grip"]),

    dict(name="Towel Pull-up", modality="Strength", movement_pattern="pull_vertical",
         primary_muscles=["grip", "lats", "biceps"], secondary_muscles=[],
         equipment_required=["pullup_bar"], load_type="bodyweight",
         skill_demand=0.6, impact_level=0.3,
         weak_point_tags=["grip", "pull_vertical"]),

    # ── ACCESSORY / ISOLATION ─────────────────────────────────────────
    dict(name="Nordic Curl", modality="Strength", movement_pattern="hinge",
         primary_muscles=["hamstrings"], secondary_muscles=[],
         equipment_required=[], load_type="bodyweight",
         skill_demand=0.5, impact_level=0.6,
         weak_point_tags=["posterior_chain", "hip_hinge"],
         coaching_notes="Eccentric hamstring — excellent injury prehab."),

    dict(name="Hip Thrust", modality="Hypertrophy", movement_pattern="hinge",
         primary_muscles=["glutes"], secondary_muscles=["hamstrings"],
         equipment_required=["barbell"], load_type="barbell",
         skill_demand=0.3, impact_level=0.3,
         weak_point_tags=["posterior_chain", "hip_hinge"]),

    dict(name="Copenhagen Plank", modality="Strength", movement_pattern="core",
         primary_muscles=["adductors", "core"], secondary_muscles=[],
         equipment_required=[], load_type="time",
         skill_demand=0.4, impact_level=0.2,
         weak_point_tags=["core_stability", "single_leg"]),

    dict(name="Calf Raise", modality="Hypertrophy", movement_pattern="single_leg",
         primary_muscles=["calves"], secondary_muscles=[],
         equipment_required=[], load_type="bodyweight",
         skill_demand=0.1, impact_level=0.3,
         weak_point_tags=["running_economy"]),

    dict(name="Band Pull-Apart", modality="Hypertrophy", movement_pattern="pull_horizontal",
         primary_muscles=["rear_delts", "rotator_cuff"], secondary_muscles=["traps"],
         equipment_required=["band"], load_type="reps",
         skill_demand=0.1, impact_level=0.1,
         weak_point_tags=["overhead_stability", "pull_horizontal"]),
]


async def seed():
    async with AsyncSessionLocal() as db:
        for data in EXERCISES:
            existing = await db.execute(
                __import__("sqlalchemy.future", fromlist=["select"])
                .select(Exercise).where(Exercise.name == data["name"])
            )
            if existing.scalars().first():
                continue  # idempotent — skip if already exists
            db.add(Exercise(**data))

        await db.commit()
        print(f"Seeded {len(EXERCISES)} exercises.")


if __name__ == "__main__":
    asyncio.run(seed())
