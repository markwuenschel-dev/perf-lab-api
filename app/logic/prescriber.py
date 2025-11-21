from typing import Literal

from pydantic import BaseModel

from app.schemas.state import UnifiedStateVector


class WorkoutPrescription(BaseModel):
    type: str
    focus: str
    rationale: str
    duration_min: int


def recommend_next_session(
    state: UnifiedStateVector,
    goal: Literal["Strength", "Hypertrophy", "Power", "General"] = "Strength",
) -> WorkoutPrescription:
    """
    The greedy Controller / Decision Engine.

    Order of operations:
      1. Safety constraints (hard stops).
      2. Fatigue-based readiness filtering.
      3. Goal-based optimization with skill and habit.
    """

    # --- 1. Safety Overrides (Active Constraints) ---

    if state.f_struct_damage > 70.0:
        return WorkoutPrescription(
            type="Recovery",
            focus="Mobility / Light Movement",
            rationale=(
                f"Structural fatigue is critical ({state.f_struct_damage:.1f}%). "
                "Training now would increase injury risk."
            ),
            duration_min=20,
        )

    if state.f_met_systemic > 80.0:
        return WorkoutPrescription(
            type="Recovery",
            focus="Passive Rest / Sleep / Nutrition",
            rationale=(
                f"Systemic fatigue is very high ({state.f_met_systemic:.1f}%). "
                "Autonomic recovery is required before loading again."
            ),
            duration_min=0,
        )

    # --- 2. Fatigue-Based Filtering (Readiness) ---

    if state.f_nm_central > 60.0:
        # CNS is tired → bias toward low-neural stress modalities
        if state.c_met_aerobic > 0:
            return WorkoutPrescription(
                type="Metabolic Conditioning",
                focus="Zone 2 Cardio (Bike/Row) @ RPE 4–5",
                rationale=(
                    f"CNS fatigue is high ({state.f_nm_central:.1f}%). "
                    "Shifting stress toward the aerobic system with low neural load."
                ),
                duration_min=45,
            )
        else:
            return WorkoutPrescription(
                type="Technique / Flow",
                focus="Movement Drills <50% Intensity",
                rationale=(
                    f"CNS fatigue is high ({state.f_nm_central:.1f}%). "
                    "Focusing on motor patterns without heavy loading."
                ),
                duration_min=30,
            )

    if state.f_nm_peripheral > 60.0:
        # Muscles are locally cooked
        if goal == "Power":
            return WorkoutPrescription(
                type="Neural Priming",
                focus="Jumps / Throws (Low Volume, Long Rest)",
                rationale=(
                    f"Peripheral fatigue is elevated ({state.f_nm_peripheral:.1f}%), "
                    "but CNS appears available; brief neural exposures only."
                ),
                duration_min=30,
            )
        else:
            return WorkoutPrescription(
                type="Active Recovery",
                focus="Walking / Light Sled Drag",
                rationale=(
                    f"Local muscular fatigue is high ({state.f_nm_peripheral:.1f}%). "
                    "Using low-intensity movement to promote clearance and recovery."
                ),
                duration_min=30,
            )

    # --- 3. Goal-Based Optimization (Happy Path) ---

    if goal == "Strength":
        squat_skill = state.skill_state.get("squat", 0.0)

        if squat_skill < 0.5:
            return WorkoutPrescription(
                type="Skill Acquisition",
                focus="Goblet Squats 3x8 (Tempo 3-1-1)",
                rationale=(
                    "Squat skill is still developing. Prioritizing motor learning "
                    "over maximal loading to improve movement quality."
                ),
                duration_min=45,
            )
        elif state.habit_strength < 0.4:
            return WorkoutPrescription(
                type="Strength - Variety Biased",
                focus="Box Squats + Medicine Ball Slams",
                rationale=(
                    "Habit strength is low. Using enjoyable variations to keep "
                    "adherence high while still driving a strength signal."
                ),
                duration_min=45,
            )
        else:
            return WorkoutPrescription(
                type="Max Strength",
                focus="Back Squat 5x3 @ RPE 8",
                rationale=(
                    "System fatigue is manageable and habit strength is adequate. "
                    "Green light for high-tension stimulus to drive force adaptation."
                ),
                duration_min=60,
            )

    if goal == "Hypertrophy":
        if state.f_nm_peripheral < 10.0:
            return WorkoutPrescription(
                type="High Volume Hypertrophy",
                focus="Leg Press & Hack Squat 4x12 Near Failure",
                rationale=(
                    "Peripheral fatigue is fully dissipated. Ready for high magnitude "
                    "metabolic stress to maximize hypertrophy signal."
                ),
                duration_min=75,
            )
        else:
            return WorkoutPrescription(
                type="Maintenance Volume",
                focus="Machine Isolation 3x10 @ RPE 7",
                rationale=(
                    "Residual fatigue is present. Accumulating sufficient volume "
                    "without pushing into overreaching territory."
                ),
                duration_min=45,
            )

    if goal == "Power":
        return WorkoutPrescription(
            type="Power Development",
            focus="Olympic-Style Lifts / Jumps 5x3 @ RPE 6–7",
            rationale=(
                "No critical fatigue detected and goal is power. "
                "Prescribing moderate-volume, high-velocity work."
            ),
            duration_min=45,
        )

    # Default / General
    return WorkoutPrescription(
        type="General Physical Prep",
        focus="Full-Body Circuit @ RPE 6–7",
        rationale="No critical constraints and no specific goal — prescribing balanced GPP.",
        duration_min=45,
    )
