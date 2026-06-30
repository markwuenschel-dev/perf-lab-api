"""Attach provenance, validation, explainability, and structured-template scoring."""

from typing import Any

from app.logic.coaching_template_registry import get_structured_template_for_goal
from app.logic.constraint_engine import (
    SessionValidator,
    build_constraint_context,
    encode_session_candidate,
    simple_session_scorer,
)
from app.logic.registries import (
    get_fallback_template,
    get_template_for_goal,
    primitive_names,
)
from app.schemas.prescription import (
    PrescriptionExplanation,
    ValidationSummary,
    WorkoutPrescription,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.training_goals import TrainingGoal


def _derive_state_drivers(state: UnifiedStateVector) -> list[str]:
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


def finalize_prescription(
    rx: WorkoutPrescription,
    state: UnifiedStateVector | None,
    goal: TrainingGoal,
    branch_id: str,
    recent_sessions: list[dict[str, Any]] | None = None,
) -> WorkoutPrescription:
    """
    Enrich prescription with `why`. If state is None (no athlete row), minimal explanation only.
    Hard constraint violations replace with a safe recovery session.
    Universal safety rules always run via SessionValidator; template-specific rules follow.
    """
    if state is None:
        out = rx.model_copy(deep=True)
        out.why = PrescriptionExplanation(
            state_drivers=["No AthleteState history — baseline not established"],
            goal_alignment=str(goal),
            constraints_applied=[],
            source_alignment=["Assessment required before twin-linked validation"],
            prescription_branch=branch_id,
            validation=ValidationSummary(passed=True, failed_checks=[], hard_violations=[]),
            warnings=[],
            score=None,
            structured_template_name=None,
        )
        return out

    program_template = get_template_for_goal(goal) or get_fallback_template()

    structured = get_structured_template_for_goal(goal)
    candidate = encode_session_candidate(rx, goal, branch_id)
    ctx = build_constraint_context(state, recent_sessions, goal)
    srep = SessionValidator(structured).validate(candidate, ctx)
    soft_warnings = srep.soft_warnings
    hard_violations = list(dict.fromkeys(srep.hard_failed))
    skipped_codes = srep.skipped_codes
    score_val: float | None = None
    if not hard_violations:
        score_val = simple_session_scorer(candidate, structured, state)

    out_rx = rx.model_copy(deep=True)
    rationale_suffix = ""

    if hard_violations:
        out_rx = WorkoutPrescription(
            type="Recovery",
            focus="Easy movement + mobility (constraint override)",
            rationale=(
                f"Hard domain constraints triggered: {', '.join(hard_violations[:6])}. "
                "Defaulting to a low-risk session until state improves."
            ),
            duration_min=min(rx.duration_min, 35) if rx.duration_min else 30,
        )
        vsummary = ValidationSummary(
            passed=False,
            failed_checks=soft_warnings,
            hard_violations=hard_violations,
        )
        rationale_suffix = f" [branch: {branch_id} → overridden]"
        score_val = None
    else:
        if soft_warnings:
            rationale_suffix = " " + "; ".join(soft_warnings[:4])
        vsummary = ValidationSummary(
            passed=True,
            failed_checks=soft_warnings,
            hard_violations=[],
        )

    prim_labels = primitive_names(program_template.provenance_primitive_ids)
    sources = [structured.source_name, program_template.source_name] + prim_labels
    if skipped_codes:
        sources.append(f"skipped_unregistered_rules:{len(skipped_codes)}")

    applied = list(
        dict.fromkeys(
            soft_warnings + program_template.constraint_rule_ids[:6] + hard_violations[:4]
        )
    )

    warnings_out = list(dict.fromkeys(soft_warnings))[:12]

    tid = structured.template_id
    st_name = structured.name

    out_rx.why = PrescriptionExplanation(
        state_drivers=_derive_state_drivers(state),
        goal_alignment=str(goal),
        constraints_applied=applied,
        source_alignment=sources[:14],
        template_id=tid,
        prescription_branch=branch_id,
        validation=vsummary,
        warnings=warnings_out,
        score=score_val,
        structured_template_name=st_name,
    )

    if rationale_suffix and not hard_violations:
        out_rx.rationale = (out_rx.rationale + rationale_suffix).strip()

    return out_rx
