"""Attach provenance, validation, explainability, and structured-template scoring."""

from typing import Any

from app.core.config import settings
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
from app.logic.session_draft_builder import build_session_draft
from app.logic.validate_session import derive_state_drivers, validate_session
from app.schemas.prescription import (
    PrescriptionExplanation,
    ValidationSummary,
    WorkoutPrescription,
)
from app.schemas.state import UnifiedStateVector
from app.schemas.training_goals import TrainingGoal


def finalize_prescription(
    rx: WorkoutPrescription,
    state: UnifiedStateVector | None,
    goal: TrainingGoal,
    branch_id: str,
    recent_sessions: list[dict[str, Any]] | None = None,
) -> WorkoutPrescription:
    """
    Enrich prescription with `why`. If state is None (no athlete row), minimal explanation only.
    Hard constraint violations (legacy + structured) replace with a safe recovery session.
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
    draft = build_session_draft(rx, goal, state, program_template)
    vsummary, soft, hard_legacy = validate_session(draft, state, goal, program_template)

    score_val: float | None = None
    soft_v2: list[str] = []
    hard_v2: list[str] = []
    skipped_codes: list[str] = []

    if settings.USE_STRUCTURED_COACHING_TEMPLATES:
        structured = get_structured_template_for_goal(goal)
        candidate = encode_session_candidate(rx, goal, branch_id, draft)
        ctx = build_constraint_context(state, recent_sessions, goal)
        srep = SessionValidator(structured).validate(candidate, ctx)
        soft_v2 = srep.soft_warnings
        hard_v2 = srep.hard_failed
        skipped_codes = srep.skipped_codes
        if not hard_legacy and not hard_v2:
            score_val = simple_session_scorer(candidate, structured, state)
    else:
        structured = None

    hard_combined = list(dict.fromkeys([*hard_legacy, *hard_v2]))
    out_rx = rx.model_copy(deep=True)
    rationale_suffix = ""

    if hard_combined:
        out_rx = WorkoutPrescription(
            type="Recovery",
            focus="Easy movement + mobility (constraint override)",
            rationale=(
                f"Hard domain constraints triggered: {', '.join(hard_combined[:6])}. "
                "Defaulting to a low-risk session until state improves."
            ),
            duration_min=min(rx.duration_min, 35) if rx.duration_min else 30,
        )
        vsummary = ValidationSummary(
            passed=False,
            failed_checks=vsummary.failed_checks + soft + soft_v2,
            hard_violations=hard_combined,
        )
        rationale_suffix = f" [branch: {branch_id} → overridden]"
        score_val = None
    else:
        if soft or soft_v2:
            rationale_suffix = " " + "; ".join((soft + soft_v2)[:4])
        vsummary = ValidationSummary(
            passed=True,
            failed_checks=vsummary.failed_checks + soft + soft_v2,
            hard_violations=[],
        )

    prim_labels = primitive_names(program_template.provenance_primitive_ids)
    if settings.USE_STRUCTURED_COACHING_TEMPLATES and structured is not None:
        sources = [structured.source_name, program_template.source_name] + prim_labels
    else:
        sources = [program_template.source_name] + prim_labels
    if skipped_codes:
        sources.append(f"skipped_unregistered_rules:{len(skipped_codes)}")

    applied = list(
        dict.fromkeys(
            soft + soft_v2 + program_template.constraint_rule_ids[:6] + hard_combined[:4]
        )
    )

    warnings_out = list(dict.fromkeys([*soft_v2, *soft]))[:12]

    if settings.USE_STRUCTURED_COACHING_TEMPLATES and structured is not None:
        tid = structured.template_id
        st_name = structured.name
    else:
        tid = program_template.id
        st_name = None

    out_rx.why = PrescriptionExplanation(
        state_drivers=derive_state_drivers(state),
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

    if rationale_suffix and not hard_combined:
        out_rx.rationale = (out_rx.rationale + rationale_suffix).strip()

    return out_rx
