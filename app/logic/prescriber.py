from app.logic.prescription_finalize import finalize_prescription
from app.schemas.prescription import WorkoutPrescription
from app.schemas.state import UnifiedStateVector
from app.schemas.training_goals import TRAINING_GOAL_DEFAULT, TrainingGoal


def _finalize(
    rx: WorkoutPrescription,
    state: UnifiedStateVector,
    goal: TrainingGoal,
    branch_id: str,
    recent_sessions: list[dict] | None = None,
) -> WorkoutPrescription:
    return finalize_prescription(
        rx, state, goal, branch_id, recent_sessions=recent_sessions
    )


def recommend_next_session(
    state: UnifiedStateVector,
    goal: TrainingGoal = TRAINING_GOAL_DEFAULT,
    recent_sessions: list[dict] | None = None,
    kpi_summary: dict[str, float] | None = None,
) -> WorkoutPrescription:
    """
    The greedy Controller / Decision Engine.

    Order of operations:
      1. Safety constraints (hard stops).
      2. Fatigue-based readiness filtering.
      3. Goal-based optimization with skill and habit.

    Each path is finalized with template linkage, session validation, and `why`.

    `kpi_summary` holds latest derived dashboard metrics (codes → values). These are
    soft signals only: state vectors remain the primary controller; KPIs nudge copy
    and light template bias, not hard safety.
    """

    kpi = kpi_summary or {}

    # --- 1. Safety Overrides (Active Constraints) ---

    if state.tissue_t.lumbar > 65.0 or state.tissue_t.knee > 70.0:
        return _finalize(
            WorkoutPrescription(
                type="Recovery",
                focus="Low-Impact Mobility + Swim / Bike Easy",
                rationale=(
                    f"Regional tissue stress is elevated (lumbar {state.tissue_t.lumbar:.0f}, "
                    f"knee {state.tissue_t.knee:.0f}). Deload axial / knee-dominant loading."
                ),
                duration_min=30,
            ),
            state,
            goal,
            "safety_regional_tissue",
            recent_sessions=recent_sessions,
        )

    if state.fatigue_f.tendon > 55.0 or state.fatigue_f.structural > 65.0:
        return _finalize(
            WorkoutPrescription(
                type="Tissue Deload",
                focus="Isometrics + Blood-Flow Circuits",
                rationale=(
                    f"Tendon / structural fatigue is high (tendon {state.fatigue_f.tendon:.0f}, "
                    f"structural {state.fatigue_f.structural:.0f}). Reduce plyometrics and max eccentrics."
                ),
                duration_min=35,
            ),
            state,
            goal,
            "safety_tendon_structural",
            recent_sessions=recent_sessions,
        )

    if state.f_struct_damage > 70.0:
        return _finalize(
            WorkoutPrescription(
                type="Recovery",
                focus="Mobility / Light Movement",
                rationale=(
                    f"Structural fatigue is critical ({state.f_struct_damage:.1f}%). "
                    "Training now would increase injury risk."
                ),
                duration_min=20,
            ),
            state,
            goal,
            "safety_structural_damage",
            recent_sessions=recent_sessions,
        )

    if state.f_met_systemic > 80.0:
        return _finalize(
            WorkoutPrescription(
                type="Recovery",
                focus="Passive Rest / Sleep / Nutrition",
                rationale=(
                    f"Systemic fatigue is very high ({state.f_met_systemic:.1f}%). "
                    "Autonomic recovery is required before loading again."
                ),
                duration_min=0,
            ),
            state,
            goal,
            "safety_systemic_metabolic",
            recent_sessions=recent_sessions,
        )

    # --- 2. Fatigue-Based Filtering (Readiness) ---

    if state.f_nm_central > 60.0:
        if state.c_met_aerobic > 0:
            return _finalize(
                WorkoutPrescription(
                    type="Metabolic Conditioning",
                    focus="Zone 2 Cardio (Bike/Row) @ RPE 4–5",
                    rationale=(
                        f"CNS fatigue is high ({state.f_nm_central:.1f}%). "
                        "Shifting stress toward the aerobic system with low neural load."
                    ),
                    duration_min=45,
                ),
                state,
                goal,
                "readiness_cns_aerobic_shift",
                recent_sessions=recent_sessions,
            )
        return _finalize(
            WorkoutPrescription(
                type="Technique / Flow",
                focus="Movement Drills <50% Intensity",
                rationale=(
                    f"CNS fatigue is high ({state.f_nm_central:.1f}%). "
                    "Focusing on motor patterns without heavy loading."
                ),
                duration_min=30,
            ),
            state,
            goal,
            "readiness_cns_technique",
            recent_sessions=recent_sessions,
        )

    if state.f_nm_peripheral > 60.0:
        if goal in ("Power", "OlympicLifts", "Sprinting"):
            return _finalize(
                WorkoutPrescription(
                    type="Neural Priming",
                    focus="Jumps / Throws (Low Volume, Long Rest)",
                    rationale=(
                        f"Peripheral fatigue is elevated ({state.f_nm_peripheral:.1f}%), "
                        "but CNS appears available; brief neural exposures only."
                    ),
                    duration_min=30,
                ),
                state,
                goal,
                "readiness_peripheral_neural_priming",
                recent_sessions=recent_sessions,
            )
        return _finalize(
            WorkoutPrescription(
                type="Active Recovery",
                focus="Walking / Light Sled Drag",
                rationale=(
                    f"Local muscular fatigue is high ({state.f_nm_peripheral:.1f}%). "
                    "Using low-intensity movement to promote clearance and recovery."
                ),
                duration_min=30,
            ),
            state,
            goal,
            "readiness_peripheral_active_recovery",
            recent_sessions=recent_sessions,
        )

    # --- 3. Goal-Based Optimization (Happy Path) ---

    if goal == "Strength":
        squat_skill = state.skill_state.get("squat", 0.0)

        if squat_skill < 0.5:
            return _finalize(
                WorkoutPrescription(
                    type="Skill Acquisition",
                    focus="Goblet Squats 3x8 (Tempo 3-1-1)",
                    rationale=(
                        "Squat skill is still developing. Prioritizing motor learning "
                        "over maximal loading to improve movement quality."
                    ),
                    duration_min=45,
                ),
                state,
                goal,
                "goal_strength_skill",
                recent_sessions=recent_sessions,
            )
        if state.habit_strength < 0.4:
            return _finalize(
                WorkoutPrescription(
                    type="Strength - Variety Biased",
                    focus="Box Squats + Medicine Ball Slams",
                    rationale=(
                        "Habit strength is low. Using enjoyable variations to keep "
                        "adherence high while still driving a strength signal."
                    ),
                    duration_min=45,
                ),
                state,
                goal,
                "goal_strength_habit",
                recent_sessions=recent_sessions,
            )
        return _finalize(
            WorkoutPrescription(
                type="Max Strength",
                focus="Back Squat 5x3 @ RPE 8",
                rationale=(
                    "System fatigue is manageable and habit strength is adequate. "
                    "Green light for high-tension stimulus to drive force adaptation."
                ),
                duration_min=60,
            ),
            state,
            goal,
            "goal_strength_max",
            recent_sessions=recent_sessions,
        )

    if goal == "Hypertrophy":
        if state.f_nm_peripheral < 10.0:
            return _finalize(
                WorkoutPrescription(
                    type="High Volume Hypertrophy",
                    focus="Leg Press & Hack Squat 4x12 Near Failure",
                    rationale=(
                        "Peripheral fatigue is fully dissipated. Ready for high magnitude "
                        "metabolic stress to maximize hypertrophy signal."
                    ),
                    duration_min=75,
                ),
                state,
                goal,
                "goal_hypertrophy_high_volume",
                recent_sessions=recent_sessions,
            )
        return _finalize(
            WorkoutPrescription(
                type="Maintenance Volume",
                focus="Machine Isolation 3x10 @ RPE 7",
                rationale=(
                    "Residual fatigue is present. Accumulating sufficient volume "
                    "without pushing into overreaching territory."
                ),
                duration_min=45,
            ),
            state,
            goal,
            "goal_hypertrophy_maintenance",
            recent_sessions=recent_sessions,
        )

    if goal == "Power":
        return _finalize(
            WorkoutPrescription(
                type="Power Development",
                focus="Olympic-Style Lifts / Jumps 5x3 @ RPE 6–7",
                rationale=(
                    "No critical fatigue detected and goal is power. "
                    "Prescribing moderate-volume, high-velocity work."
                ),
                duration_min=45,
            ),
            state,
            goal,
            "goal_power",
            recent_sessions=recent_sessions,
        )

    if goal == "OlympicLifts":
        ratio = kpi.get("wl_snatch_cj_ratio")
        extra = ""
        if ratio is not None and ratio < 72.0:
            extra = (
                f" KPI: snatch is a low share of C&J ({ratio:.0f}%) — extra snatch "
                "skill and strength-off-the-floor work this block."
            )
        return _finalize(
            WorkoutPrescription(
                type="Weightlifting Technique",
                focus="Snatch & Clean Drills + Hang Variations @ RPE 6–7",
                rationale=(
                    "Prioritizing positions, pulls, and turnover under the bar — "
                    "classic lifts and complexes before heavy singles." + extra
                ),
                duration_min=60,
            ),
            state,
            goal,
            "goal_olympic",
            recent_sessions=recent_sessions,
        )

    if goal == "Powerlifting":
        rel = kpi.get("pl_relative_total")
        extra = ""
        if rel is not None and rel < 3.0:
            extra = (
                f" KPI hint: relative total ({rel:.2f}×BW) is modest — bias quality reps "
                "and volume before pushing absolute intensity."
            )
        elif kpi.get("pl_projected_total") is not None:
            extra = (
                f" Dashboard projected total ~{kpi['pl_projected_total']:.0f} kg — "
                "keep autoregulation honest on secondary lifts."
            )
        return _finalize(
            WorkoutPrescription(
                type="SBD Strength",
                focus="Squat / Bench / Deadlift — top sets + back-off volume",
                rationale=(
                    "Competition lifts and close variants to drive 1RM-relevant "
                    "strength with managed fatigue." + extra
                ),
                duration_min=75,
            ),
            state,
            goal,
            "goal_powerlifting",
            recent_sessions=recent_sessions,
        )

    if goal == "MetCon":
        return _finalize(
            WorkoutPrescription(
                type="Metabolic Conditioning",
                focus="Mixed Modal Intervals — Row / Bike / KB @ sustainable pace",
                rationale=(
                    "Building work capacity and glycolytic tolerance with "
                    "structured intervals and minimal incomplete rest."
                ),
                duration_min=40,
            ),
            state,
            goal,
            "goal_metcon",
            recent_sessions=recent_sessions,
        )

    if goal == "Calisthenics":
        return _finalize(
            WorkoutPrescription(
                type="Bodyweight Strength",
                focus="Pull-ups / Dips / Push-up Variations + Skill Progressions",
                rationale=(
                    "Progressing horizontal and vertical pressing/pulling patterns "
                    "and straight-arm strength for skills."
                ),
                duration_min=50,
            ),
            state,
            goal,
            "goal_calisthenics",
            recent_sessions=recent_sessions,
        )

    if goal == "Gymnastics":
        return _finalize(
            WorkoutPrescription(
                type="Gymnastics Skill",
                focus="Handstand / Ring Support + Shaping Drills @ submax effort",
                rationale=(
                    "Skill and mobility emphasis — quality reps over volume to "
                    "protect wrists and shoulders."
                ),
                duration_min=55,
            ),
            state,
            goal,
            "goal_gymnastics",
            recent_sessions=recent_sessions,
        )

    if goal == "Grip":
        return _finalize(
            WorkoutPrescription(
                type="Grip & Support",
                focus="Farmer Carries / Hangs / Pinch & Crush @ RPE 7–8",
                rationale=(
                    "Targeting crush, support, and finger flexors with structured "
                    "volume and joint-friendly pairings."
                ),
                duration_min=35,
            ),
            state,
            goal,
            "goal_grip",
            recent_sessions=recent_sessions,
        )

    if goal == "Running":
        ff = kpi.get("run_fatigue_factor")
        extra = ""
        if ff is not None and ff > 14.0:
            extra = (
                f" KPI: 400m–mile fatigue factor is elevated ({ff:.1f}%) — "
                "prioritize threshold and tempo durability, not just easy volume."
            )
        return _finalize(
            WorkoutPrescription(
                type="Aerobic Base",
                focus="Easy–Moderate Run @ Zone 2 (conversational pace)",
                rationale=(
                    "Building cardiac output and durability with low-intensity "
                    "volume; cap intensity if tissue stress is elevated." + extra
                ),
                duration_min=45,
            ),
            state,
            goal,
            "goal_running",
            recent_sessions=recent_sessions,
        )

    if goal == "Sprinting":
        return _finalize(
            WorkoutPrescription(
                type="Speed",
                focus="Acceleration + Max-Velocity Flys (full recovery between reps)",
                rationale=(
                    "Short, high-quality sprints — neural freshness required; "
                    "volume stays low when fatigue is non-trivial."
                ),
                duration_min=35,
            ),
            state,
            goal,
            "goal_sprinting",
            recent_sessions=recent_sessions,
        )

    if goal == "HalfMarathon":
        return _finalize(
            WorkoutPrescription(
                type="Half Marathon Prep",
                focus="Tempo + Progression Long Run (build toward ~21 km)",
                rationale=(
                    "Threshold and aerobic power for sustained race pace; "
                    "long-run progression is the primary driver."
                ),
                duration_min=60,
            ),
            state,
            goal,
            "goal_half_marathon",
            recent_sessions=recent_sessions,
        )

    if goal == "FullMarathon":
        return _finalize(
            WorkoutPrescription(
                type="Marathon Build",
                focus="Long Slow Distance + Marathon-Pace Segments",
                rationale=(
                    "Emphasizing time on feet and glycogen economy; "
                    "periodize intensity around weekly long run."
                ),
                duration_min=75,
            ),
            state,
            goal,
            "goal_full_marathon",
            recent_sessions=recent_sessions,
        )

    return _finalize(
        WorkoutPrescription(
            type="General Physical Prep",
            focus="Full-Body Circuit @ RPE 6–7",
            rationale="No critical constraints and no specific goal — prescribing balanced GPP.",
            duration_min=45,
        ),
        state,
        goal,
        "goal_general_default",
        recent_sessions=recent_sessions,
    )
