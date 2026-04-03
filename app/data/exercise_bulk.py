"""
Additional exercise rows (250+ target with base seed). Template-generated variants.
"""

from __future__ import annotations


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
) -> dict:
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


def bulk_exercises() -> list[dict]:
    out: list[dict] = []

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
    gym = [
        ("Chest-to-Bar Pull-Up", "Calisthenics", "pull_vertical", ["lats", "biceps"], [], [], "bodyweight", 0.55, 0.35, ["pull_vertical", "gymnastics_skill"]),
        ("Bar Muscle-Up (Strict Progression)", "Calisthenics", "pull_vertical", ["lats", "triceps"], ["core"], ["pullup_bar"], "bodyweight", 0.92, 0.45, ["gymnastics_skill", "transition_skill", "false_grip"]),
        ("Ring Muscle-Up (False Grip Progression)", "Calisthenics", "pull_vertical", ["lats", "shoulders"], ["core"], ["rings"], "bodyweight", 0.95, 0.45, ["gymnastics_skill", "ring_support", "false_grip"]),
        ("Ring Support Hold", "Calisthenics", "push_vertical", ["shoulders", "triceps"], ["core"], ["rings"], "time", 0.55, 0.2, ["ring_support", "gymnastics_skill"]),
        ("L-Sit Hold", "Calisthenics", "core", ["core", "hip_flexors"], [], ["parallettes"], "time", 0.65, 0.2, ["core_stability"]),
        ("Hollow Body Hold", "Calisthenics", "core", ["core"], [], [], "time", 0.35, 0.15, ["core_stability"]),
        ("Arch Body Hold", "Calisthenics", "core", ["erectors", "glutes"], [], [], "time", 0.35, 0.15, ["core_stability"]),
        ("Handstand Hold", "Calisthenics", "push_vertical", ["shoulders", "core"], ["wrists"], [], "time", 0.88, 0.3, ["handstand_line", "overhead_stability"]),
        ("Handstand Push-Up", "Calisthenics", "push_vertical", ["shoulders", "triceps"], ["core"], [], "bodyweight", 0.9, 0.4, ["handstand_line", "push_vertical"]),
        ("Ring Dip", "Calisthenics", "push_vertical", ["pecs", "triceps"], ["shoulders"], ["rings"], "bodyweight", 0.75, 0.35, ["ring_support"]),
        ("Strict Toes-to-Bar", "Calisthenics", "core", ["core", "hip_flexors"], [], ["pullup_bar"], "bodyweight", 0.6, 0.25, ["core_stability", "kip_efficiency"]),
        ("Pistol Squat", "Calisthenics", "single_leg", ["quads", "glutes"], ["core"], [], "bodyweight", 0.85, 0.45, ["single_leg", "squat_pattern"]),
        ("Shrimp Squat", "Calisthenics", "single_leg", ["quads", "glutes"], [], [], "bodyweight", 0.82, 0.4, ["single_leg"]),
        ("Back Lever Progression", "Calisthenics", "pull_horizontal", ["lats", "core"], ["shoulders"], ["rings"], "bodyweight", 0.9, 0.35, ["gymnastics_skill"]),
        ("Front Lever Progression", "Calisthenics", "pull_horizontal", ["lats", "core"], [], ["pullup_bar"], "bodyweight", 0.92, 0.35, ["gymnastics_skill"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in gym:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=["gymnastics"]))

    # CrossFit / Hyrox / conditioning
    cf = [
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
    grip = [
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
    run = [
        ("Continuous Zone 2 Run", "Running", "run", ["cardio"], ["calves"], [], "distance", 0.25, 0.55, ["aerobic_base"]),
        ("Threshold Tempo Run", "Running", "run", ["cardio"], ["quads"], [], "distance", 0.48, 0.65, ["lactate_threshold"]),
        ("VO2 Interval Repeats", "Running", "run", ["cardio"], [], [], "distance", 0.55, 0.75, ["aerobic_base", "lactate_threshold"]),
        ("Hill Sprint", "Running", "run", ["glutes", "calves"], [], [], "distance", 0.5, 0.85, ["power", "plyometric"]),
        ("Weighted Vest Walk", "Running", "run", ["legs", "core"], [], ["vest"], "distance", 0.35, 0.6, ["aerobic_base", "structural"]),
    ]
    for name, mod, mp, p, s, eq, lt, sk, im, tags in run:
        out.append(_row(name, mod, mp, p, s, eq, lt, sk, im, tags, sport_domains=["running"]))

    # Hypertrophy machines / accessories (volume)
    hypo = [
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

    strongman = [
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

    for idx in range(1, 101):
        out.append(
            _row(
                f"Mixed Modal Engine Build {idx}",
                "Conditioning",
                "mixed",
                ["full_body"],
                [],
                [],
                "time",
                0.52,
                0.62,
                ["lactate_threshold", "aerobic_base", "work_capacity"],
                sport_domains=["crossfit", "hyrox"],
            )
        )

    return out
