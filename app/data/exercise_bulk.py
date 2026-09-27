"""
Additional exercise rows (250+ target with base seed). Template-generated variants.
"""

from __future__ import annotations

from typing import Any

# Seed-row tuple shapes. The base row is a fixed 10-tuple; some variant lists
# carry optional trailing fields (benchmark / notes / unilateral / sport_domains)
# unpacked via ``*rest``. Spelling the position types out keeps the unpacked
# locals precisely typed when passed to ``_row``.
_BaseRow = tuple[
    str, str, str, list[str], list[str], list[str], str, float, float, list[str]
]
# Variant rows append optional fields after the base 10 positions.
_VariantRow = tuple[Any, ...]


def _row(
    name: str,
    modality: str,
    movement_pattern: str,
    primary: list[str],
    secondary: list[str],
    equipment: list[str],
    load_type: str,
    skill: float,
    impact: float,
    tags: list[str],
    benchmark: bool = False,
    notes: str | None = None,
    unilateral: bool = False,
    sport_domains: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "modality": modality,
        "movement_pattern": movement_pattern,
        "primary_muscles": primary,
        "secondary_muscles": secondary,
        "equipment_required": equipment,
        "load_type": load_type,
        "skill_demand": skill,
        "impact_level": impact,
        "weak_point_tags": tags,
        "is_benchmark": benchmark,
        "coaching_notes": notes,
        "unilateral": unilateral,
        "sport_domains": sport_domains or [],
    }


def bulk_exercises() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    # Powerlifting / strength variants
    squat_variants = [
        ("Paused Back Squat", 0.75, 0.6),
        ("Tempo Back Squat (3-0-1)", 0.72, 0.55),
        ("Pin Squat", 0.7, 0.55),
        ("Box Squat", 0.65, 0.5),
        ("Safety Bar Squat", 0.68, 0.55),
        ("SSB Squat", 0.7, 0.58),
        ("High-Bar Back Squat", 0.68, 0.55),
        ("Low-Bar Back Squat", 0.72, 0.6),
        ("Zercher Squat", 0.78, 0.65),
        ("Anderson Squat", 0.8, 0.7),
    ]
    for n, sk, im in squat_variants:
        out.append(
            _row(
                n,
                "Strength",
                "squat",
                ["quads", "glutes"],
                ["hamstrings", "erectors"],
                ["barbell"],
                "barbell",
                sk,
                im,
                ["squat_pattern", "lockout_strength", "bracing"],
            )
        )

    dl_variants = [
        ("Snatch-Grip Deadlift", 0.72, 0.65),
        ("Deficit Deadlift", 0.78, 0.72),
        ("Rack Pull", 0.55, 0.55),
        ("Paused Deadlift", 0.76, 0.7),
        ("Sumo Deadlift", 0.7, 0.65),
        ("Trap Bar Deadlift", 0.6, 0.6),
    ]
    for n, sk, im in dl_variants:
        out.append(
            _row(
                n,
                "Strength",
                "hinge",
                ["hamstrings", "glutes", "erectors"],
                ["lats", "traps"],
                ["barbell"],
                "barbell",
                sk,
                im,
                ["hip_hinge", "start_strength", "grip"],
            )
        )

    bench_variants = [
        ("Paused Bench Press", 0.58, 0.45),
        ("Close-Grip Bench Press", 0.55, 0.45),
        ("Spoto Press", 0.62, 0.45),
        ("Board Press", 0.6, 0.5),
        ("Floor Press", 0.52, 0.4),
        ("Larsen Press", 0.55, 0.4),
    ]
    for n, sk, im in bench_variants:
        out.append(
            _row(
                n,
                "Strength",
                "push_horizontal",
                ["pecs", "triceps"],
                ["front_delts"],
                ["barbell"],
                "barbell",
                sk,
                im,
                ["push_horizontal", "lockout_strength"],
            )
        )

    # Olympic lifting
    oly = [
        ("Muscle Snatch", "Power", "mixed", ["shoulders", "traps"], ["legs", "core"], ["barbell"], "barbell", 0.82, 0.55, ["weightlifting", "skill"]),
        ("Power Snatch", "Power", "mixed", ["hips", "shoulders"], ["core"], ["barbell"], "barbell", 0.85, 0.6, ["weightlifting", "power"]),
        ("Hang Snatch", "Power", "mixed", ["hips", "shoulders"], ["core"], ["barbell"], "barbell", 0.84, 0.58, ["weightlifting", "transition_skill"]),
        ("Snatch Balance", "Power", "push_vertical", ["shoulders", "legs"], ["core"], ["barbell"], "barbell", 0.8, 0.5, ["weightlifting", "overhead_stability"]),
        ("Tall Snatch", "Power", "mixed", ["shoulders"], ["core"], ["barbell"], "barbell", 0.78, 0.45, ["weightlifting", "skill"]),
        ("Block Snatch", "Power", "mixed", ["hips", "shoulders"], ["core"], ["barbell"], "barbell", 0.83, 0.55, ["weightlifting"]),
        ("Muscle Clean", "Power", "mixed", ["traps", "biceps"], ["core"], ["barbell"], "barbell", 0.78, 0.5, ["weightlifting"]),
        ("Hang Clean (Full)", "Power", "mixed", ["hips", "back"], ["core"], ["barbell"], "barbell", 0.84, 0.6, ["weightlifting"]),
        ("Split Jerk", "Power", "push_vertical", ["shoulders", "legs"], ["core"], ["barbell"], "barbell", 0.8, 0.55, ["weightlifting", "single_leg"]),
        ("Push Press", "Power", "push_vertical", ["shoulders", "triceps"], ["legs"], ["barbell"], "barbell", 0.68, 0.5, ["weightlifting"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in oly:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=["weightlifting"]))

    # Gymnastics / calisthenics
    gym: list[_BaseRow] = [
        ("Chest-to-Bar Pull-Up", "Calisthenics", "pull_vertical", ["lats", "biceps"], [], [], "bodyweight", 0.55, 0.35, ["pull_vertical", "gymnastics_skill"]),
        ("Bar Muscle-Up (Strict Progression)", "Calisthenics", "pull_vertical", ["lats", "triceps"], ["core"], ["pullup_bar"], "bodyweight", 0.92, 0.45, ["gymnastics_skill", "transition_skill", "false_grip"]),
        ("Ring Muscle-Up (False Grip Progression)", "Calisthenics", "pull_vertical", ["lats", "shoulders"], ["core"], ["rings"], "bodyweight", 0.95, 0.45, ["gymnastics_skill", "ring_support", "false_grip"]),
        ("Ring Support Hold", "Calisthenics", "push_vertical", ["shoulders", "triceps"], ["core"], ["rings"], "time", 0.55, 0.2, ["ring_support", "gymnastics_skill"]),
        ("L-Sit Hold", "Calisthenics", "core", ["core", "hip_flexors"], [], ["parallettes"], "time", 0.65, 0.2, ["core_stability"]),
        ("Hollow Body Hold", "Calisthenics", "core", ["core"], [], [], "time", 0.35, 0.15, ["core_stability"]),
        ("Arch Body Hold", "Calisthenics", "core", ["erectors", "glutes"], [], [], "time", 0.35, 0.15, ["core_stability"]),
        ("Handstand Hold", "Calisthenics", "push_vertical", ["shoulders", "core"], ["wrists"], [], "time", 0.88, 0.3, ["handstand_line", "overhead_stability"]),
        ("Handstand Push-Up", "Calisthenics", "push_vertical", ["shoulders", "triceps"], ["core"], [], "bodyweight", 0.9, 0.4, ["handstand_line", "push_vertical"]),
        ("Ring Dip (Weighted)", "Calisthenics", "push_vertical", ["pecs", "triceps"], ["shoulders"], ["rings"], "bodyweight", 0.82, 0.4, ["ring_support", "gymnastics_skill"]),
        ("Strict Toes-to-Bar", "Calisthenics", "core", ["core", "hip_flexors"], [], ["pullup_bar"], "bodyweight", 0.6, 0.25, ["core_stability", "kip_efficiency"]),
        ("Pistol Squat", "Calisthenics", "single_leg", ["quads", "glutes"], ["core"], [], "bodyweight", 0.85, 0.45, ["single_leg", "squat_pattern"]),
        ("Shrimp Squat", "Calisthenics", "single_leg", ["quads", "glutes"], [], [], "bodyweight", 0.82, 0.4, ["single_leg"]),
        ("Back Lever Progression", "Calisthenics", "pull_horizontal", ["lats", "core"], ["shoulders"], ["rings"], "bodyweight", 0.9, 0.35, ["gymnastics_skill"]),
        ("Front Lever Progression", "Calisthenics", "pull_horizontal", ["lats", "core"], [], ["pullup_bar"], "bodyweight", 0.92, 0.35, ["gymnastics_skill"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in gym:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=["gymnastics"]))

    # CrossFit / Hyrox / conditioning
    cf: list[_BaseRow] = [
        ("Thruster (Cluster Style)", "Mixed", "squat", ["quads", "shoulders"], ["core"], ["barbell"], "barbell", 0.72, 0.55, ["work_capacity", "crossfit"]),
        ("Wall Ball Unbroken Set", "Mixed", "squat", ["quads", "shoulders"], [], ["wall_ball"], "reps", 0.48, 0.45, ["work_capacity"]),
        ("Burpee Over Row Erg", "Conditioning", "mixed", ["full_body"], [], ["rower"], "bodyweight", 0.52, 0.58, ["work_capacity"]),
        ("Devil Press", "Mixed", "hinge", ["back", "shoulders"], ["legs"], ["dumbbells"], "dumbbell", 0.72, 0.55, ["crossfit", "hip_hinge"]),
        ("Man Maker", "Mixed", "push_horizontal", ["chest", "back"], ["core"], ["dumbbells"], "dumbbell", 0.78, 0.6, ["crossfit"]),
        ("Alternating DB Snatch", "Power", "hinge", ["back", "shoulders"], ["legs"], ["dumbbells"], "dumbbell", 0.7, 0.55, ["power", "grip"]),
        ("Depth Drop to Box Jump", "Power", "jump", ["quads", "calves"], [], ["box"], "reps", 0.65, 0.75, ["plyometric", "structural"]),
        ("Jump Rope Double-Unders", "Conditioning", "jump", ["calves"], ["shoulders"], ["jump_rope"], "reps", 0.55, 0.35, ["aerobic_base", "skill"]),
        ("SkiErg 250m Sprint", "Conditioning", "row", ["lats", "core"], ["legs"], ["skierg"], "time", 0.42, 0.48, ["aerobic_base"]),
        ("Echo Bike Intervals", "Conditioning", "bike", ["legs", "arms"], [], ["bike"], "time", 0.38, 0.52, ["lactate_threshold", "aerobic_base"]),
        ("RowErg 2K Pace Work", "Conditioning", "row", ["legs", "back"], ["core"], ["rower"], "time", 0.48, 0.48, ["aerobic_base"]),
        ("Trap Bar Farmer Carry", "Strength", "carry", ["grip", "traps"], ["core"], ["trap_bar"], "reps", 0.42, 0.38, ["grip", "crush", "support"]),
        ("Heavy Sled March", "Conditioning", "run", ["quads", "glutes"], [], ["sled"], "distance", 0.48, 0.52, ["work_capacity", "hyrox"]),
        ("Rope Sled Pull", "Conditioning", "hinge", ["hamstrings", "back"], ["grip"], ["sled"], "distance", 0.52, 0.52, ["grip", "posterior_chain"]),
        ("Sandbag Bear Hug Carry", "Conditioning", "carry", ["core", "grip"], ["legs"], ["sandbag"], "distance", 0.58, 0.55, ["support", "hyrox"]),
        ("Wall Walk", "Calisthenics", "push_vertical", ["shoulders", "core"], [], [], "bodyweight", 0.85, 0.4, ["handstand_line"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in cf:
        sd = ["crossfit"] if "crossfit" in tags else []
        if "hyrox" in tags:
            sd.append("hyrox")
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=sd or ["conditioning"]))

    # Grip specialty
    grip: list[_BaseRow] = [
        ("Captains of Crush Gripper", "Strength", "pull_vertical", ["forearms"], [], ["gripper"], "reps", 0.35, 0.2, ["grip", "crush"]),
        ("Bottom-Up Plate Pinch Hold", "Strength", "carry", ["forearms", "fingers"], [], ["plates"], "time", 0.52, 0.25, ["grip", "pinch", "finger"]),
        ("Fat Grip Towel Hang", "Calisthenics", "pull_vertical", ["grip", "forearms"], [], ["pullup_bar"], "time", 0.55, 0.28, ["grip", "crush"]),
        ("Fat Bar Deadlift", "Strength", "hinge", ["hamstrings", "grip"], [], ["barbell"], "barbell", 0.72, 0.65, ["grip", "thick_bar"]),
        ("Rope Climb", "Calisthenics", "pull_vertical", ["lats", "grip"], ["core"], ["rope"], "bodyweight", 0.78, 0.45, ["grip", "support"]),
        ("Hang from Bar", "Calisthenics", "pull_vertical", ["grip", "lats"], [], ["pullup_bar"], "time", 0.3, 0.2, ["grip", "support", "finger"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in grip:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=["grip"]))

    # Endurance
    run: list[_BaseRow] = [
        ("Continuous Zone 2 Run", "Running", "run", ["cardio"], ["calves"], [], "distance", 0.25, 0.55, ["aerobic_base"]),
        ("Threshold Tempo Run", "Running", "run", ["cardio"], ["quads"], [], "distance", 0.48, 0.65, ["lactate_threshold"]),
        ("VO2 Interval Repeats", "Running", "run", ["cardio"], [], [], "distance", 0.55, 0.75, ["aerobic_base", "lactate_threshold"]),
        ("Hill Sprint", "Running", "run", ["glutes", "calves"], [], [], "distance", 0.5, 0.85, ["power", "plyometric"]),
        ("Weighted Vest Walk", "Running", "run", ["legs", "core"], [], ["vest"], "distance", 0.35, 0.6, ["aerobic_base", "structural"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in run:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=["running"]))

    # Hypertrophy machines / accessories (volume)
    hypo: list[_BaseRow] = [
        ("Pec Deck", "Hypertrophy", "push_horizontal", ["pecs"], [], ["machine"], "machine", 0.2, 0.2, ["hypertrophy"]),
        ("Leg Extension", "Hypertrophy", "squat", ["quads"], [], ["machine"], "machine", 0.2, 0.25, ["anterior_chain"]),
        ("Leg Curl", "Hypertrophy", "hinge", ["hamstrings"], [], ["machine"], "machine", 0.2, 0.25, ["posterior_chain"]),
        ("Cable Fly", "Hypertrophy", "push_horizontal", ["pecs"], [], ["cable"], "cable", 0.35, 0.25, ["push_horizontal"]),
        ("Lat Prayer", "Hypertrophy", "pull_vertical", ["lats"], [], ["cable"], "cable", 0.35, 0.25, ["pull_vertical"]),
        ("Tricep Pushdown", "Hypertrophy", "push_horizontal", ["triceps"], [], ["cable"], "cable", 0.25, 0.2, ["lockout_strength"]),
        ("Hammer Curl", "Hypertrophy", "pull_vertical", ["biceps", "brachialis"], [], ["dumbbells"], "dumbbell", 0.25, 0.2, ["grip"]),
        ("Preacher Curl", "Hypertrophy", "pull_vertical", ["biceps"], [], ["barbell"], "barbell", 0.35, 0.25, ["pull_vertical"]),
        ("Reverse Hyper", "Hypertrophy", "hinge", ["glutes", "hamstrings"], [], ["machine"], "machine", 0.35, 0.3, ["posterior_chain"]),
        ("Back Extension", "Hypertrophy", "hinge", ["erectors", "glutes"], [], ["machine"], "machine", 0.3, 0.3, ["lumbar", "posterior_chain"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in hypo:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags))

    # Single-leg / unilateral extras
    out.extend(
        [
            _row(
                "Single-Leg RDL",
                "Hypertrophy",
                "hinge",
                ["hamstrings", "glutes"],
                ["erectors"],
                ["dumbbells"],
                "dumbbell",
                0.55,
                0.45,
                ["single_leg", "hip_hinge"],
                unilateral=True,
            ),
            _row(
                "Skater Squat",
                "Strength",
                "single_leg",
                ["quads", "glutes"],
                ["core"],
                [],
                "bodyweight",
                0.75,
                0.45,
                ["single_leg", "knee_stability"],
                unilateral=True,
            ),
            _row(
                "Step-Up",
                "Hypertrophy",
                "single_leg",
                ["quads", "glutes"],
                [],
                ["box"],
                "bodyweight",
                0.4,
                0.4,
                ["single_leg"],
                unilateral=True,
            ),
            _row(
                "Walking Lunge",
                "Hypertrophy",
                "single_leg",
                ["quads", "glutes"],
                [],
                ["dumbbells"],
                "dumbbell",
                0.45,
                0.45,
                ["single_leg"],
                unilateral=True,
            ),
        ]
    )

    strongman: list[_BaseRow] = [
        ("Atlas Stone Load", "Power", "hinge", ["hips", "back"], ["grip"], [], "reps", 0.88, 0.85, ["grip", "support", "start_strength"]),
        ("Log Clean and Press", "Power", "push_vertical", ["shoulders", "legs"], ["core"], [], "reps", 0.82, 0.7, ["power", "bracing"]),
        ("Yoke Walk", "Strength", "carry", ["back", "legs"], ["core"], [], "distance", 0.75, 0.75, ["support", "bracing"]),
        ("Circus Dumbbell Press", "Strength", "push_vertical", ["shoulders"], ["core"], ["dumbbells"], "dumbbell", 0.9, 0.55, ["lockout_strength"]),
        ("Tire Flip", "Power", "hinge", ["hips", "back"], [], [], "reps", 0.7, 0.8, ["hip_hinge", "power"]),
        ("Sandbag to Shoulder", "Power", "hinge", ["hips", "back"], ["grip"], ["sandbag"], "reps", 0.78, 0.7, ["grip", "hip_hinge"]),
        ("Keg Carry", "Conditioning", "carry", ["grip", "core"], ["legs"], [], "distance", 0.65, 0.55, ["grip", "support"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in strongman:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=["strongman"]))

    # Kettlebell skills
    kb = [
        ("Kettlebell Clean and Press", "Power", "mixed", ["shoulders", "hips", "legs"], ["core", "traps"], ["kettlebell"], "kettlebell", 0.72, 0.52, ["hip_hinge", "push_vertical", "work_capacity"]),
        ("Turkish Get-Up", "Strength", "mixed", ["shoulders", "core", "hips"], ["glutes"], ["kettlebell"], "kettlebell", 0.85, 0.45, ["overhead_stability", "core_stability", "gymnastics_skill"]),
        ("Kettlebell Snatch", "Power", "hinge", ["hips", "shoulders"], ["traps", "core"], ["kettlebell"], "kettlebell", 0.78, 0.58, ["hip_hinge", "power", "grip"]),
        ("Kettlebell Front Rack Carry", "Strength", "carry", ["core", "shoulders"], ["traps"], ["kettlebell"], "kettlebell", 0.45, 0.4, ["core_stability", "overhead_stability"]),
        ("Kettlebell Goblet Squat to Press", "Mixed", "squat", ["quads", "shoulders"], ["core"], ["kettlebell"], "kettlebell", 0.48, 0.45, ["squat_pattern", "push_vertical"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in kb:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=["conditioning"]))

    # Plyometrics and speed
    plyo: list[_BaseRow] = [
        ("Broad Jump", "Power", "jump", ["quads", "glutes", "calves"], [], [], "reps", 0.55, 0.7, ["plyometric", "power"]),
        ("Bounding Drill", "Power", "run", ["glutes", "calves"], ["quads"], [], "distance", 0.55, 0.65, ["plyometric", "running_economy"]),
        ("Seated Box Jump", "Power", "jump", ["quads", "glutes"], ["calves"], ["box"], "reps", 0.62, 0.72, ["plyometric", "power"]),
        ("Lateral Bound", "Power", "jump", ["glutes", "quads"], ["core"], [], "reps", 0.58, 0.65, ["plyometric", "single_leg"]),
        ("Sprint Acceleration (20m)", "Power", "run", ["glutes", "quads", "calves"], [], [], "distance", 0.55, 0.75, ["power", "running_economy"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in plyo:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=["running", "conditioning"]))

    # Dumbbell compound / multi-joint
    db_compound: list[_VariantRow] = [
        ("Dumbbell Thruster", "Mixed", "squat", ["quads", "shoulders"], ["core", "glutes"], ["dumbbells"], "dumbbell", 0.6, 0.52, ["work_capacity", "squat_pattern", "push_vertical"]),
        ("Renegade Row", "Strength", "pull_horizontal", ["upper_back", "core"], ["shoulders"], ["dumbbells"], "dumbbell", 0.68, 0.42, ["core_stability", "pull_horizontal"]),
        ("Dumbbell Clean", "Power", "hinge", ["hips", "traps"], ["core"], ["dumbbells"], "dumbbell", 0.72, 0.55, ["hip_hinge", "power"]),
        ("Overhead Walking Lunge", "Strength", "single_leg", ["quads", "glutes"], ["core", "shoulders"], ["dumbbells"], "dumbbell", 0.65, 0.5, ["single_leg", "overhead_stability"], True),
        ("Dumbbell Romanian Deadlift to Row", "Hypertrophy", "hinge", ["hamstrings", "upper_back"], ["glutes"], ["dumbbells"], "dumbbell", 0.55, 0.45, ["hip_hinge", "posterior_chain", "pull_horizontal"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags, *rest in db_compound:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, unilateral=bool(rest and rest[0])))

    # Midline / trunk
    midline = [
        ("GHD Sit-Up", "Strength", "core", ["core", "hip_flexors"], ["glutes"], ["machine"], "bodyweight", 0.45, 0.35, ["core_stability", "gymnastics_skill"]),
        ("GHD Hip Extension", "Hypertrophy", "hinge", ["glutes", "hamstrings"], ["erectors"], ["machine"], "bodyweight", 0.38, 0.3, ["posterior_chain"]),
        ("Pallof Press", "Strength", "core", ["core"], ["shoulders"], ["cable"], "cable", 0.35, 0.2, ["core_stability", "rotation"]),
        ("Hanging L-Sit", "Calisthenics", "core", ["core", "hip_flexors"], ["lats"], ["pullup_bar"], "bodyweight", 0.72, 0.25, ["core_stability", "gymnastics_skill"]),
        ("Med Ball Slam", "Power", "mixed", ["core", "lats"], ["shoulders"], ["wall_ball"], "reps", 0.42, 0.45, ["work_capacity", "power"]),
        # Phase 9.1: a true ballistic THROW (released, horizontal), for neural priming.
        ("Med Ball Chest Pass", "Power", "push_horizontal", ["chest", "triceps"], ["shoulders", "core"], ["wall_ball"], "reps", 0.30, 0.25, ["power", "plyometric"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in midline:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags))

    # Barbell Olympic / full lift (competition lifts)
    full_lifts = [
        ("Clean and Jerk", "Power", "mixed", ["hips", "shoulders", "legs"], ["core", "traps"], ["barbell"], "barbell", 0.95, 0.72, ["weightlifting", "power", "hip_hinge", "overhead_stability"], True, None, False, ["weightlifting"]),
        ("Snatch (Full)", "Power", "mixed", ["hips", "shoulders"], ["core"], ["barbell"], "barbell", 0.97, 0.68, ["weightlifting", "power", "overhead_stability"], True, "Full squat catch — highest technical demand.", False, ["weightlifting"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags, bm, notes, uni, sd in full_lifts:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, benchmark=bm, notes=notes, unilateral=uni, sport_domains=sd))

    # Prehab / structural
    prehab: list[_VariantRow] = [
        ("Cossack Squat", "Strength", "single_leg", ["adductors", "quads"], ["glutes"], [], "bodyweight", 0.52, 0.3, ["hip_mobility", "squat_pattern"]),
        ("90/90 Hip Switch", "Strength", "core", ["hip_flexors", "adductors"], [], [], "time", 0.3, 0.1, ["hip_mobility"]),
        ("Jefferson Curl", "Strength", "hinge", ["hamstrings", "erectors"], [], [], "bodyweight", 0.55, 0.35, ["posterior_chain", "hip_hinge"]),
        ("Tibialis Raise", "Hypertrophy", "single_leg", ["tibialis_anterior"], [], [], "bodyweight", 0.2, 0.15, ["running_economy", "structural"]),
        ("Single-Leg Calf Raise (Loaded)", "Hypertrophy", "single_leg", ["calves"], [], [], "bodyweight", 0.25, 0.3, ["running_economy", "structural"], False, None, True),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags, *rest in prehab:
        bm = rest[0] if len(rest) > 0 else False
        notes = rest[1] if len(rest) > 1 else None
        uni = rest[2] if len(rest) > 2 else False
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, benchmark=bm, notes=notes, unilateral=uni))

    # Battle rope / unconventional
    rope: list[_BaseRow] = [
        ("Battle Rope Alternating Waves", "Conditioning", "mixed", ["shoulders", "core"], ["legs"], ["battle_ropes"], "reps", 0.42, 0.45, ["work_capacity", "aerobic_base"]),
        ("Battle Rope Double Slam", "Power", "mixed", ["lats", "core", "shoulders"], [], ["battle_ropes"], "reps", 0.45, 0.48, ["power", "work_capacity"]),
        ("Battle Rope Side-to-Side Waves", "Conditioning", "mixed", ["shoulders", "core"], [], ["battle_ropes"], "time", 0.42, 0.42, ["work_capacity", "rotation"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in rope:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=["conditioning"]))

    # Catalog gap closures — exact names the prescriber's equipment/accessory fallback
    # emits (tests/test_prescribed_exercise_names_resolve.py::KNOWN_CATALOG_GAPS).
    gap_fill: list[_VariantRow] = [
        ("Chest-Supported Row", "Hypertrophy", "pull_horizontal", ["upper_back", "lats"], ["biceps"], ["dumbbells"], "dumbbell", 0.25, 0.25, ["pull_horizontal"]),
        ("DB Floor Press", "Hypertrophy", "push_horizontal", ["pecs", "triceps"], ["front_delts"], ["dumbbells"], "dumbbell", 0.42, 0.35, ["push_horizontal"]),
        ("DB RDL", "Hypertrophy", "hinge", ["hamstrings", "glutes"], ["erectors"], ["dumbbells"], "dumbbell", 0.45, 0.45, ["hip_hinge", "posterior_chain"]),
        ("Dips", "Calisthenics", "push_horizontal", ["pecs", "triceps"], ["shoulders"], ["parallettes"], "bodyweight", 0.4, 0.3, ["push_horizontal"]),
        ("Hanging Knee Raise", "Calisthenics", "core", ["core", "hip_flexors"], [], ["pullup_bar"], "bodyweight", 0.3, 0.2, ["core_stability"]),
        ("Hanging Leg Raise", "Calisthenics", "core", ["core", "hip_flexors"], ["lats"], ["pullup_bar"], "bodyweight", 0.45, 0.25, ["core_stability"]),
        ("Split Squat", "Hypertrophy", "single_leg", ["quads", "glutes"], ["hamstrings"], ["dumbbells"], "dumbbell", 0.4, 0.4, ["single_leg", "anterior_chain"], False, None, True),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags, *rest in gap_fill:
        bm = rest[0] if len(rest) > 0 else False
        notes = rest[1] if len(rest) > 1 else None
        uni = rest[2] if len(rest) > 2 else False
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, benchmark=bm, notes=notes, unilateral=uni))

    # Pattern-breadth deepening — pull_horizontal, single_leg, carry, core/rotation and
    # push_vertical are thin relative to squat/hinge coverage across the rest of the catalog.
    pull_horizontal_extra: list[_VariantRow] = [
        ("Seal Row", "Strength", "pull_horizontal", ["upper_back", "lats"], ["biceps"], ["barbell"], "barbell", 0.45, 0.35, ["pull_horizontal", "posterior_chain"]),
        ("Meadows Row", "Strength", "pull_horizontal", ["lats", "upper_back"], ["biceps"], ["barbell"], "barbell", 0.5, 0.4, ["pull_horizontal"], True),
        ("Chest-Supported T-Bar Row", "Strength", "pull_horizontal", ["upper_back", "lats"], ["biceps"], ["barbell"], "barbell", 0.4, 0.35, ["pull_horizontal"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags, *rest in pull_horizontal_extra:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, unilateral=bool(rest and rest[0])))

    single_leg_extra: list[_BaseRow] = [
        ("Reverse Lunge", "Hypertrophy", "single_leg", ["quads", "glutes"], ["hamstrings"], ["dumbbells"], "dumbbell", 0.35, 0.4, ["single_leg"]),
        ("Lateral Lunge", "Strength", "single_leg", ["adductors", "quads"], ["glutes"], ["dumbbells"], "dumbbell", 0.45, 0.4, ["single_leg", "hip_mobility"]),
        ("Single-Leg Box Squat", "Strength", "single_leg", ["quads", "glutes"], ["core"], ["box"], "bodyweight", 0.65, 0.4, ["single_leg", "knee_stability"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in single_leg_extra:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, unilateral=True))

    carry_extra: list[_BaseRow] = [
        ("Single-Arm Farmer Carry", "Strength", "carry", ["grip", "core"], ["traps"], ["dumbbells"], "dumbbell", 0.4, 0.45, ["carry", "grip", "rotation", "core_stability"]),
        ("Overhead Kettlebell Carry", "Strength", "carry", ["shoulders", "core"], ["traps"], ["kettlebell"], "kettlebell", 0.55, 0.4, ["carry", "overhead_stability", "core_stability"]),
        ("Zercher Carry", "Strength", "carry", ["core", "erectors"], ["biceps"], ["barbell"], "barbell", 0.5, 0.5, ["carry", "core_stability", "bracing"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in carry_extra:
        uni = name in {"Single-Arm Farmer Carry", "Overhead Kettlebell Carry"}
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, unilateral=uni))

    core_rotation_extra: list[_BaseRow] = [
        ("Cable Woodchop", "Strength", "core", ["core"], ["shoulders"], ["cable"], "cable", 0.4, 0.25, ["core_stability", "rotation"]),
        ("Landmine Rotation", "Strength", "core", ["core"], ["shoulders"], ["barbell"], "barbell", 0.45, 0.3, ["core_stability", "rotation"]),
        ("Russian Twist", "Hypertrophy", "core", ["core"], [], [], "bodyweight", 0.25, 0.2, ["core_stability", "rotation"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in core_rotation_extra:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags))

    push_vertical_extra: list[_VariantRow] = [
        ("Landmine Press", "Strength", "push_vertical", ["shoulders", "triceps"], ["core"], ["barbell"], "barbell", 0.4, 0.35, ["push_vertical"], True),
        ("Seated Dumbbell Shoulder Press", "Hypertrophy", "push_vertical", ["shoulders", "triceps"], [], ["dumbbells"], "dumbbell", 0.35, 0.3, ["push_vertical"]),
        ("Bottoms-Up Kettlebell Press", "Strength", "push_vertical", ["shoulders", "forearms"], ["core"], ["kettlebell"], "kettlebell", 0.65, 0.35, ["push_vertical", "overhead_stability", "grip"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags, *rest in push_vertical_extra:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, unilateral=bool(rest and rest[0])))

    # Evox movement-vocabulary expansion — endurance, gymnastics, Olympic, cardio
    # and strength movements verified absent against
    # https://www.getevox.fit/standards/movements (scraped 2026-08-26). A movement
    # already present under a different name (e.g. Evox "Deadlift" ==
    # "Conventional Deadlift", Evox "Strict Press" == "Overhead Press") was
    # skipped rather than duplicated.
    endurance_vocab: list[_VariantRow] = [
        ("Bike Erg", "Conditioning", "bike", ["legs", "arms"], [], ["bike"], "distance", 0.15, 0.25, ["aerobic_base", "work_capacity"], []),
        ("Swim", "Conditioning", "row", ["lats", "shoulders"], ["legs", "core"], [], "distance", 0.5, 0.25, ["aerobic_base", "work_capacity"], []),
        ("Sprint", "Running", "run", ["quads", "glutes", "calves"], [], [], "distance", 0.35, 0.75, ["anaerobic_capacity", "running_economy"], ["running"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags, sd in endurance_vocab:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=sd))

    gymnastics_vocab: list[_BaseRow] = [
        ("Air Squat", "Calisthenics", "squat", ["quads", "glutes"], [], [], "bodyweight", 0.15, 0.3, ["squat_pattern"]),
        ("Butterfly Pull-up", "Calisthenics", "pull_vertical", ["lats", "biceps"], ["core"], ["pullup_bar"], "bodyweight", 0.55, 0.35, ["pull_vertical", "kip_efficiency", "gymnastics_skill"]),
        ("Kipping Pull-up", "Calisthenics", "pull_vertical", ["lats", "biceps"], ["core"], ["pullup_bar"], "bodyweight", 0.45, 0.3, ["pull_vertical", "kip_efficiency"]),
        ("L-sit Pull-up", "Calisthenics", "pull_vertical", ["lats", "core"], ["biceps"], ["pullup_bar"], "bodyweight", 0.85, 0.35, ["pull_vertical", "core_stability", "gymnastics_skill"]),
        ("Deficit Handstand Push-up", "Calisthenics", "push_vertical", ["shoulders", "triceps"], ["core"], ["box"], "bodyweight", 0.95, 0.4, ["push_vertical", "overhead_stability", "handstand_line"]),
        ("Kipping Handstand Push-up", "Calisthenics", "push_vertical", ["shoulders", "triceps"], ["core"], [], "bodyweight", 0.75, 0.45, ["push_vertical", "kip_efficiency", "handstand_line"]),
        ("Handstand Walk", "Calisthenics", "push_vertical", ["shoulders", "core"], [], [], "bodyweight", 0.85, 0.35, ["handstand_line", "overhead_stability", "gymnastics_skill"]),
        ("Legless Rope Climb", "Calisthenics", "pull_vertical", ["lats", "grip"], ["core"], ["rope"], "bodyweight", 0.9, 0.5, ["grip", "gymnastics_skill", "pull_vertical"]),
        ("Ring Row", "Calisthenics", "pull_horizontal", ["upper_back", "lats"], ["biceps"], ["rings"], "bodyweight", 0.25, 0.2, ["pull_horizontal"]),
        ("Knees to Elbow", "Calisthenics", "core", ["core", "hip_flexors"], ["lats"], ["pullup_bar"], "bodyweight", 0.5, 0.25, ["core_stability", "gymnastics_skill"]),
        ("V-up", "Calisthenics", "core", ["core", "hip_flexors"], [], [], "bodyweight", 0.35, 0.15, ["core_stability"]),
        ("Parallette Hold", "Calisthenics", "push_vertical", ["shoulders", "triceps"], ["core"], ["parallettes"], "time", 0.4, 0.15, ["core_stability", "gymnastics_skill"]),
        ("Weighted Dip", "Calisthenics", "push_horizontal", ["pecs", "triceps"], ["shoulders"], ["parallettes", "plates"], "bodyweight", 0.55, 0.35, ["push_horizontal"]),
        ("Diamond Push-up", "Calisthenics", "push_horizontal", ["triceps", "pecs"], ["shoulders"], [], "bodyweight", 0.35, 0.2, ["push_horizontal"]),
        ("Hand Release Push-up", "Calisthenics", "push_horizontal", ["pecs", "triceps"], ["core"], [], "bodyweight", 0.25, 0.2, ["push_horizontal"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in gymnastics_vocab:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=["gymnastics"]))

    olympic_vocab: list[_VariantRow] = [
        ("American Kettlebell Swing", "Power", "hinge", ["hips", "shoulders"], ["core"], ["kettlebell"], "kettlebell", 0.65, 0.65, ["hip_hinge", "overhead_stability", "power"], ["conditioning"]),
        ("Russian Kettlebell Swing", "Power", "hinge", ["glutes", "hamstrings"], ["core"], ["kettlebell"], "kettlebell", 0.55, 0.55, ["hip_hinge", "posterior_chain"], ["conditioning"]),
        ("Cluster", "Power", "mixed", ["quads", "shoulders", "hips"], ["core"], ["barbell"], "barbell", 0.88, 0.65, ["weightlifting", "power", "work_capacity"], ["crossfit"]),
        ("Dumbbell Clean & Jerk", "Power", "mixed", ["hips", "shoulders"], ["core"], ["dumbbells"], "dumbbell", 0.75, 0.55, ["weightlifting", "power", "hip_hinge", "overhead_stability"], ["crossfit"]),
        ("Dumbbell Hang Clean", "Power", "hinge", ["hips", "traps"], ["core"], ["dumbbells"], "dumbbell", 0.68, 0.5, ["hip_hinge", "power"], ["crossfit"]),
        ("Ground-to-Overhead", "Power", "mixed", ["hips", "shoulders", "legs"], ["core"], ["barbell"], "barbell", 0.85, 0.65, ["weightlifting", "power", "work_capacity"], ["crossfit"]),
        ("Hang Power Snatch", "Power", "mixed", ["hips", "shoulders"], ["core"], ["barbell"], "barbell", 0.78, 0.55, ["weightlifting", "transition_skill"], ["weightlifting"]),
        ("Jerk", "Power", "push_vertical", ["shoulders", "legs"], ["core"], ["barbell"], "barbell", 0.82, 0.55, ["weightlifting", "olympic_lifting", "overhead_stability"], ["weightlifting"]),
        ("Kettlebell Clean", "Power", "hinge", ["hips", "traps"], ["core"], ["kettlebell"], "kettlebell", 0.55, 0.45, ["hip_hinge", "power"], ["conditioning"]),
        ("Medicine Ball Clean", "Power", "hinge", ["hips", "back"], ["core"], ["wall_ball"], "reps", 0.35, 0.35, ["hip_hinge", "power"], []),
        ("Squat Clean", "Power", "mixed", ["hips", "back", "legs"], ["core", "traps"], ["barbell"], "barbell", 0.93, 0.65, ["weightlifting", "power", "hip_hinge"], ["weightlifting"]),
        ("Thruster", "Mixed", "squat", ["quads", "shoulders"], ["core"], ["barbell"], "barbell", 0.65, 0.55, ["work_capacity", "squat_pattern", "push_vertical"], ["crossfit"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags, sd in olympic_vocab:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=sd))

    cardio_vocab: list[_VariantRow] = [
        ("Abmat Sit-up", "Calisthenics", "core", ["core", "hip_flexors"], [], [], "bodyweight", 0.2, 0.15, ["core_stability"], []),
        ("Sit-up", "Calisthenics", "core", ["core", "hip_flexors"], [], [], "bodyweight", 0.15, 0.15, ["core_stability"], []),
        ("Bar Facing Burpee", "Conditioning", "mixed", ["full_body"], [], [], "reps", 0.4, 0.55, ["work_capacity", "aerobic_base"], ["crossfit"]),
        ("Burpee Box Jump Over", "Conditioning", "mixed", ["full_body"], [], ["box"], "reps", 0.5, 0.65, ["work_capacity"], ["crossfit"]),
        ("Burpee Broad Jump", "Conditioning", "mixed", ["full_body"], [], [], "reps", 0.5, 0.6, ["work_capacity", "plyometric"], ["crossfit"]),
        ("Burpee Pull-up", "Conditioning", "mixed", ["full_body"], ["lats"], ["pullup_bar"], "reps", 0.45, 0.55, ["work_capacity"], ["crossfit"]),
        ("Box Jump Over", "Power", "jump", ["glutes", "quads", "calves"], ["core"], ["box"], "reps", 0.55, 0.75, ["work_capacity", "plyometric"], ["crossfit"]),
        ("Crossover", "Conditioning", "jump", ["calves", "core"], ["shoulders"], ["jump_rope"], "reps", 0.8, 0.5, ["work_capacity", "skill"], []),
        ("Dumbbell Box Step Over", "Conditioning", "single_leg", ["quads", "glutes"], ["core"], ["dumbbells", "box"], "dumbbell", 0.4, 0.55, ["single_leg", "work_capacity"], ["crossfit"], True),
        ("High Knees", "Conditioning", "run", ["calves", "quads"], ["core"], [], "time", 0.15, 0.4, ["aerobic_base", "running_economy"], []),
        ("Jumping Jack", "Conditioning", "jump", ["calves", "shoulders"], ["core"], [], "time", 0.1, 0.3, ["aerobic_base"], []),
        ("Jumping Lunges", "Power", "single_leg", ["quads", "glutes"], ["calves"], [], "reps", 0.5, 0.7, ["single_leg", "plyometric"], ["crossfit"], True),
        ("Lateral Burpee", "Conditioning", "mixed", ["full_body"], [], [], "reps", 0.4, 0.5, ["work_capacity"], ["crossfit"]),
        ("Med-Ball Box Step-Over", "Conditioning", "single_leg", ["quads", "glutes"], ["core"], ["wall_ball", "box"], "reps", 0.4, 0.55, ["single_leg", "work_capacity"], ["crossfit"], True),
        ("Mountain Climber", "Conditioning", "core", ["core", "hip_flexors"], ["quads"], [], "reps", 0.2, 0.35, ["core_stability", "aerobic_base"], []),
        ("Shuttle Run", "Running", "run", ["quads", "glutes", "calves"], [], [], "distance", 0.35, 0.7, ["running_economy", "anaerobic_capacity"], ["running"]),
        ("Single Under", "Conditioning", "jump", ["calves"], [], ["jump_rope"], "reps", 0.15, 0.25, ["aerobic_base"], []),
        ("Triple Under", "Conditioning", "jump", ["calves", "core"], ["shoulders"], ["jump_rope"], "reps", 0.85, 0.55, ["work_capacity", "skill"], []),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags, sd, *rest in cardio_vocab:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, unilateral=bool(rest and rest[0]), sport_domains=sd))

    strength_vocab: list[_VariantRow] = [
        ("Dumbbell Deadlift", "Hypertrophy", "hinge", ["hamstrings", "glutes"], ["erectors"], ["dumbbells"], "dumbbell", 0.35, 0.4, ["hip_hinge", "posterior_chain"], []),
        ("Dumbbell Front Rack Lunge", "Strength", "single_leg", ["quads", "glutes"], ["core"], ["dumbbells"], "dumbbell", 0.5, 0.45, ["single_leg", "core_stability"], [], True),
        ("Dumbbell Overhead Lunge", "Strength", "single_leg", ["quads", "glutes"], ["core", "shoulders"], ["dumbbells"], "dumbbell", 0.65, 0.5, ["single_leg", "overhead_stability"], [], True),
        ("Dumbbell Shoulder to Overhead", "Strength", "push_vertical", ["shoulders", "triceps"], ["legs"], ["dumbbells"], "dumbbell", 0.45, 0.4, ["push_vertical"], []),
        ("Lunges", "Calisthenics", "single_leg", ["quads", "glutes"], [], [], "bodyweight", 0.2, 0.3, ["single_leg"], [], True),
        ("Lunges (Barbell)", "Strength", "single_leg", ["quads", "glutes"], ["hamstrings"], ["barbell"], "barbell", 0.5, 0.5, ["single_leg"], [], True),
        ("Overhead Squat", "Strength", "squat", ["quads", "shoulders"], ["core"], ["barbell"], "barbell", 0.85, 0.6, ["squat_pattern", "overhead_stability"], []),
        ("Pendlay Row", "Strength", "pull_horizontal", ["upper_back", "lats"], ["biceps", "erectors"], ["barbell"], "barbell", 0.55, 0.45, ["pull_horizontal", "posterior_chain"], []),
        ("Sandbag Lunges", "Strength", "single_leg", ["quads", "glutes"], ["core"], ["sandbag"], "bodyweight", 0.45, 0.5, ["single_leg", "hyrox"], ["hyrox"], True),
        ("Sumo Deadlift High Pull", "Strength", "hinge", ["hamstrings", "glutes", "traps"], ["shoulders"], ["barbell"], "barbell", 0.55, 0.55, ["hip_hinge", "posterior_chain"], []),
        ("Wall Sit", "Strength", "squat", ["quads"], ["glutes"], [], "time", 0.15, 0.15, ["squat_pattern"], []),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags, sd, *rest in strength_vocab:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, unilateral=bool(rest and rest[0]), sport_domains=sd))

    return out
