"""Validate session draft against domain rules + state (hard/soft)."""

from app.schemas.prescription import ValidationSummary
from app.schemas.program_template import ProgramTemplate
from app.schemas.session_draft import SessionDraft
from app.schemas.state import UnifiedStateVector
from app.schemas.training_goals import TrainingGoal


def validate_session(
    draft: SessionDraft,
    state: UnifiedStateVector,
    goal: TrainingGoal,
    template: ProgramTemplate | None,
) -> tuple[ValidationSummary, list[str], list[str]]:
    """
    Returns (validation summary, soft constraint messages, hard violation ids).
    Hard violations trigger prescription override in finalize.
    """
    failed: list[str] = []
    soft: list[str] = []
    hard: list[str] = []

    # --- Universal safety (echo prescriber logic for validation audit) ---
    if state.f_met_systemic > 80:
        failed.append("fatigue_ok")
    if state.tissue_t.lumbar > 65 or state.tissue_t.knee > 70:
        failed.append("tissue_safe")
    # --- Domain: Olympic — avoid heavy metabolic before technical work ---
    if goal == "OlympicLifts" and state.f_met_systemic > 65 and draft.metabolic_emphasis > 0.55:
        soft.append("olympic_metabolic_before_technical: elevated systemic fatigue with met-heavy draft")

    # --- Running: majority Z2 principle (soft) ---
    if goal in ("Running", "HalfMarathon", "FullMarathon"):
        if draft.intensity_band in ("high", "max") and state.f_met_systemic > 50:
            soft.append("running_zone2_majority: prefer easy volume when fatigue present")

    # --- Gymnastics: wrist tissue ---
    if goal == "Gymnastics" and state.tissue_t.wrist > 75:
        hard.append("gymnastics_wrist_tissue")
        failed.append("tissue_safe")

    # --- Sprint: neural freshness ---
    if goal == "Sprinting" and state.f_nm_central > 58:
        soft.append("sprint_neural_freshness: CNS elevated — shorten sprint exposure")

    # --- Grip: tendon / frequency ---
    if goal == "Grip" and state.fatigue_f.grip > 55 and draft.neural_emphasis > 0.7:
        soft.append("grip_max_frequency: reduce max crush frequency when grip fatigue high")

    # --- MetCon stacking ---
    if goal == "MetCon" and state.f_met_systemic > 70:
        soft.append("metcon_fatigue_stack: systemic load high — bias recovery or low density")

    # --- Powerlifting deadlift CNS (soft narrative) ---
    if goal == "Powerlifting" and state.f_nm_central > 55 and draft.intensity_band == "high":
        soft.append("pl_deadlift_cns: rotate CNS-heavy lifts when central fatigue high")

    passed = len(hard) == 0

    return (
        ValidationSummary(
            passed=passed,
            failed_checks=failed + soft,
            hard_violations=hard,
        ),
        soft,
        hard,
    )


def derive_state_drivers(state: UnifiedStateVector) -> list[str]:
    """Human-readable drivers for explainability."""
    out: list[str] = []
    if state.f_nm_central > 55:
        out.append("elevated CNS / central fatigue")
    if state.f_nm_peripheral > 55:
        out.append("elevated peripheral / muscular fatigue")
    if state.f_met_systemic > 60:
        out.append("elevated systemic metabolic fatigue")
    if state.tissue_t.lumbar > 50:
        out.append("lumbar tissue stress")
    if state.tissue_t.wrist > 50:
        out.append("wrist tissue stress")
    if state.tissue_t.knee > 55:
        out.append("knee tissue stress")
    if state.fatigue_f.tendon > 45:
        out.append("tendon fatigue")
    if state.c_met_aerobic < 30 and state.c_met_aerobic > 0:
        out.append("low aerobic capacity signal")
    if not out:
        out.append("state within normal twin bands for prescription")
    return out[:8]
