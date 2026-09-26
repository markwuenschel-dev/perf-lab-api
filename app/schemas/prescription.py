"""Workout prescription + structured explainability (backward compatible)."""

from typing import Any, Literal, assert_never

from pydantic import BaseModel, Field, computed_field, model_validator

# Same source of truth the Twin's history view uses. Importing the policy rather than
# restating its bands is deliberate: confidence_presentation.py owns the thresholds so
# consumers cannot drift from them (schemas/state.py imports it for the same reason).
from app.logic.confidence_presentation import ConfidenceStatus
from app.schemas.load_explanation import LoadExplanation, LoadExplanationReason
from app.schemas.workout_structure import (
    ContinuousBlock,
    CooldownBlock,
    DurationEstimate,
    IntervalBlock,
    StrengthBlock,
    WarmupBlock,
    WorkoutBlock,
    WorkoutStructure,
    calculate_duration,
)

#: Re-exported for the modules that have always imported them from here.
__all__ = ["LoadExplanation", "LoadExplanationReason"]

#: Version of the prescription engine, published on every prescription.
#:
#: Was a bare ``"v0.3"`` literal in the Field default below and assigned by nothing, so the
#: advertised "engine version" was a constant that could never move no matter how much the
#: prescriber changed. Naming it gives it one place to be bumped and one place to grep.
#:
#: BUMPING THIS IS A CROSS-LANGUAGE CHANGE. The web contract test pins the literal at
#: ``web/src/perflab/screens/overview/todayPrescriptionContract.test.ts`` and
#: ``openapi.json`` carries it as the schema default, so a bump needs all three updated
#: together. ``test_prescription_engine_version_is_assigned_not_defaulted`` pins the Python
#: half of that coupling.
PRESCRIPTION_ENGINE_VERSION = "v0.4"


class ValidationSummary(BaseModel):
    """Result of validate_session checks."""

    passed: bool
    failed_checks: list[str] = Field(default_factory=list)
    hard_violations: list[str] = Field(default_factory=list)


class StateEvidence(BaseModel):
    """The measurement behind one state driver, not just the phrase it produced.

    ``state_drivers`` collapses a threshold test to a string ("elevated CNS / central
    fatigue"), discarding the number that fired it — so a client can read *what* the
    system concluded but never *what it saw*. Each entry here is the same test with its
    evidence intact, emitted from the same rule table, so the label and the number cannot
    disagree.
    """

    axis: str = Field(description="State field the test read, e.g. 'f_nm_central'.")
    label: str = Field(description="The human-readable driver this produced.")
    value: float = Field(description="The observed value on that axis.")
    threshold: float = Field(description="The threshold it was compared against.")
    direction: Literal["above", "below"] = Field(
        description="Whether firing means the value sat above or below the threshold."
    )
    confidence_status: ConfidenceStatus | None = Field(
        default=None,
        description=(
            "Certainty band for this axis, when the axis has a variance model. NULL means "
            "the engine models no uncertainty for it at all (fatigue, tissue and skill "
            "carry no variance) — that is UNKNOWN certainty, not high certainty."
        ),
    )


class PrescriptionConfidence(BaseModel):
    """How certain the twin is about the state this prescription was built on.

    Derived from the live per-axis ``capacity_confidence`` variance via the shared
    ``confidence_presentation_policy``. Reported, not yet acted on: nothing in the
    prescriber currently widens or narrows a recommendation based on these bands.
    """

    policy_version: str = Field(description="Confidence-presentation policy that produced the bands.")
    capacity_axes: dict[str, ConfidenceStatus] = Field(
        default_factory=dict,
        description="Per-capacity-axis certainty band derived from live variance.",
    )
    weakest_capacity_axis: str | None = Field(
        default=None,
        description="The least certain capacity axis — the one that should most constrain trust.",
    )
    weakest_capacity_status: ConfidenceStatus | None = None
    uncertainty_not_modelled: list[str] = Field(
        default_factory=list,
        description=(
            "State families the engine keeps NO uncertainty for. Their contribution to this "
            "prescription has unknown certainty; the absence is reported rather than being "
            "allowed to read as confidence."
        ),
    )


class MeasurementRecommendation(BaseModel):
    """What to measure next to make the twin less unsure about THIS goal.

    The goal's rule is that a missing optional measurement should reduce certainty rather
    than make the app unusable — so the honest response to low confidence is to say what
    would raise it. Ranked so an axis the athlete's own goal actually trains outranks an
    equally-uncertain axis they never touch.
    """

    axis: str = Field(description="Capacity axis whose uncertainty a measurement would reduce.")
    current_status: ConfidenceStatus = Field(description="The axis's certainty band right now.")
    material_to_goal: bool = Field(
        description="Whether this axis is one the athlete's current goal domain actually trains."
    )
    reason: str = Field(description="Why this axis is worth measuring.")


class PlanRevisionTrigger(BaseModel):
    """A concrete state change that would make this session the wrong call.

    Every driver is a threshold test, so each one already implies its own falsification
    condition: the crossing that would start it applying, or stop it. Surfacing those turns
    "here is your session" into "here is your session, and here is what would change it" -
    without any new modelling, because the thresholds are the same ones the prescriber used.
    """

    axis: str = Field(description="State field this trigger watches.")
    label: str = Field(description="The driver that would start or stop applying.")
    currently_active: bool = Field(
        description="True if this driver is firing now, so the trigger describes it switching OFF."
    )
    condition: str = Field(description="The crossing that would revise the plan.")
    current_value: float = Field(description="Where the axis sits today.")
    threshold: float = Field(description="The value it would have to cross.")


class ConservatismSummary(BaseModel):
    """Whether the twin's own uncertainty made this session more cautious.

    Reports the decision either way, including when it declined to act, so "the plan was not
    softened" is visibly a choice rather than an absence. ``applied`` is the only field that
    says the prescription actually changed - in ``shadow`` mode the reduction is described
    but ``effective_rpe_cap`` still equals the baseline.
    """

    mode: str = Field(description="off | shadow | on - the tri-state flag's value for this session.")
    applied: bool = Field(description="True only if the prescribed cap actually moved.")
    basis_status: ConfidenceStatus | None = Field(
        default=None,
        description="Certainty of the weakest capacity axis, which is what the rule acts on.",
    )
    baseline_rpe_cap: float = Field(description="The RPE cap the ADR-0029 envelope produced.")
    effective_rpe_cap: float = Field(description="The cap actually used to resolve load.")
    reason: str = Field(description="Why the rule did or did not act.")


class ExpectedOutcome(BaseModel):
    """What the twin predicts this session will do to one state axis.

    A point prediction from the engine's own forward model - the same path MPC rolls out
    with - not a separate estimate invented for display. ``interval`` is deliberately
    absent rather than fabricated: the forward model is deterministic and the fatigue and
    tissue families carry no variance anywhere in the engine, so there is no honest spread
    to report. See ``PrescriptionConfidence.uncertainty_not_modelled``.
    """

    axis: str = Field(description="State axis the prediction is about, e.g. 'fatigue_f.cns'.")
    current: float = Field(description="Where the axis sits before the session.")
    predicted: float = Field(description="Where the forward model puts it immediately after.")
    delta: float = Field(description="predicted - current. Positive means the session adds load.")


#: How an athlete-facing explanation entry is grouped. ``internal`` entries are engine
#: bookkeeping (an experiment arm, a shadow-only assessment, a rule a template merely checks)
#: and are never shown; everything else describes something that shaped, or was noted for,
#: this session.
AppliedConstraintGroup = Literal[
    "safety",
    "plan_rule",
    "block",
    "objective",
    "adherence",
    "weak_point",
    "state",
    "equipment",
    "advisory",
    "internal",
    "other",
]


class AppliedConstraint(BaseModel):
    """One ``constraints_applied`` code with the words an athlete reads for it.

    Built by ``app.logic.constraint_labels.describe_constraints`` from the same list, so the
    code and its label cannot drift apart. A code the labeller does not recognise gets an
    honest fallback label rather than a guess made from its punctuation.
    """

    code: str = Field(description="The engine code, exactly as in constraints_applied.")
    label: str = Field(description="What the athlete reads.")
    group: AppliedConstraintGroup = Field(description="Where the entry belongs when grouped.")
    athlete_visible: bool = Field(
        description="False for engine bookkeeping that did not shape the session."
    )


class PrescriptionExplanation(BaseModel):
    """Why this session — state drivers, constraints, sources."""

    state_drivers: list[str] = Field(default_factory=list)
    # `default_factory=lambda: []` rather than `list`, matching `exercises` below: with a
    # custom model as the element type, bare `list` infers list[Unknown] and trips the
    # strict-pyright gate on the runtime request path.
    state_evidence: list[StateEvidence] = Field(
        default_factory=lambda: [],
        description=(
            "The numbers behind ``state_drivers``, one entry per driver that fired. Empty "
            "when no driver fired, or when there is no athlete state yet."
        ),
    )
    confidence: "PrescriptionConfidence | None" = Field(
        default=None,
        description="Certainty of the twin state this session was built on. NULL when no state exists.",
    )
    plan_revision_triggers: list[PlanRevisionTrigger] = Field(
        default_factory=lambda: [],
        description=(
            "What would change this plan: the drivers currently applying that would switch "
            "off, and the nearest ones that would switch on. Derived from the same "
            "thresholds the prescriber used, so they cannot disagree."
        ),
    )
    expected_outcomes: list[ExpectedOutcome] = Field(
        default_factory=lambda: [],
        description=(
            "What this session is predicted to do, largest movement first, from the engine's "
            "forward model. Point predictions with no interval - the model is deterministic "
            "and these axes carry no variance. Empty when no state exists to predict from."
        ),
    )
    expected_outcome_horizon: str | None = Field(
        default=None,
        description="What the prediction is *of*, so it cannot be read as a longer-range claim.",
    )
    conservatism: "ConservatismSummary | None" = Field(
        default=None,
        description=(
            "Whether low confidence made this session more cautious. NULL when load was "
            "never resolved for this prescription (no lift with a current e1RM)."
        ),
    )
    measurement_recommendations: list[MeasurementRecommendation] = Field(
        default_factory=lambda: [],
        description=(
            "What to measure to sharpen this plan, worst-first with goal-relevant axes "
            "prioritised. Empty when every capacity axis is already established."
        ),
    )
    goal_alignment: str = ""
    constraints_applied: list[str] = Field(default_factory=list)
    constraint_details: list[AppliedConstraint] = Field(
        default_factory=lambda: [],
        description=(
            "``constraints_applied`` with athlete-facing labels, one entry per code in the same "
            "order. Empty for prescriptions stored before labels existed."
        ),
    )
    source_alignment: list[str] = Field(
        default_factory=list,
        description="Human-readable: templates + primitives + models",
    )
    template_id: str | None = None
    prescription_branch: str | None = Field(
        default=None,
        description="Internal prescriber branch id (safety, readiness, goal path)",
    )
    validation: ValidationSummary | None = None
    warnings: list[str] = Field(
        default_factory=list,
        description="Soft constraint / template warnings (non-blocking)",
    )
    score: float | None = Field(
        default=None,
        description="Template-aligned fit score vs twin state (0–1)",
    )
    structured_template_name: str | None = Field(
        default=None,
        description="Display name for structured coaching template (v2)",
    )


#: Why a loaded exercise has no suggested weight, in the athlete's terms. The values are
#: ``app.logic.prescription_evidence.EXPLAIN_*`` (pinned equal by
#: ``tests/test_load_explanation.py``); they describe prescription ELIGIBILITY, never a
#: verdict that the athlete became weaker.
class ExercisePrescription(BaseModel):
    """A single prescribed exercise within a session."""
    name: str
    sets: int | None = None
    reps: str | None = None
    load_note: str | None = None
    weak_point_tags: list[str] = Field(default_factory=list)
    load_explanation: LoadExplanation | None = Field(
        default=None,
        description="Why this exercise does or does not carry a suggested weight.",
    )

    # ADR-0045: strength prescriptions speak in load. When the athlete has a current
    # e1RM for this lift, the service resolves %e1RM → a suggested working kg against
    # the ADR-0029 intensity envelope, plus an RPE cap. Absent an e1RM these stay null
    # and the lift degrades to RPE-only autoregulation (the ``load_note`` fallback).
    prescribed_load_kg: float | None = Field(
        default=None, description="Suggested working load in kg (pre-fills the log)."
    )
    percent_e1rm: float | None = Field(
        default=None, description="Fraction of estimated 1RM the suggested load targets (0–1)."
    )
    rpe_cap: float | None = Field(
        default=None, description="Upper RPE bound for the working sets (ADR-0029 envelope)."
    )
    e1rm_basis_kg: float | None = Field(
        default=None, description="The current e1RM the suggestion was resolved against."
    )


def project_exercises(structure: "WorkoutStructure") -> list[ExercisePrescription]:
    """The flat ``exercises[]`` a structure means — the ONE place the two are related.

    Strength blocks project their fields. Interval and continuous blocks (phase 5.3) project
    their compatibility view — ``activity`` plus the ``display_*`` text the legacy list always
    showed — so structuring a run changes nothing a client or a log prefill reads. A block
    with nothing to name (a warmup, an endurance block without ``activity``) does not project.
    """
    out: list[ExercisePrescription] = []
    for block in structure:
        if isinstance(block, IntervalBlock | ContinuousBlock):
            if block.activity is None:
                continue
            out.append(
                ExercisePrescription(
                    name=block.activity,
                    sets=block.display_sets,
                    reps=block.display_reps,
                    load_note=block.load_note,
                    weak_point_tags=list(block.weak_point_tags),
                    rpe_cap=block.rpe_cap,
                    load_explanation=block.load_explanation,
                )
            )
            continue
        if isinstance(block, WarmupBlock | CooldownBlock):
            continue
        if not isinstance(block, StrengthBlock):
            # Fail closed: a kind that does not say what it projects must not vanish silently.
            assert_never(block)
        out.append(
            ExercisePrescription(
                name=block.exercise,
                sets=block.sets,
                reps=block.reps,
                load_note=block.load_note,
                weak_point_tags=list(block.weak_point_tags),
                prescribed_load_kg=block.load_target_kg,
                percent_e1rm=block.percent_e1rm,
                rpe_cap=block.rpe_target,
                e1rm_basis_kg=block.e1rm_basis_kg,
                load_explanation=block.load_explanation,
            )
        )
    return out


def endurance_block_for(
    template: IntervalBlock | ContinuousBlock, ex: ExercisePrescription
) -> IntervalBlock | ContinuousBlock | None:
    """``template``'s work shape, named and displayed as ``ex`` — or None if ``ex`` cannot be.

    An exercise carrying a resolved load (kg, %e1RM, e1RM basis) is strength-shaped: an
    endurance block has nowhere to put that, and dropping it would make the two views
    disagree. Such an exercise stays a strength block.
    """
    if (
        ex.prescribed_load_kg is not None
        or ex.percent_e1rm is not None
        or ex.e1rm_basis_kg is not None
    ):
        return None
    return template.model_copy(
        update={
            "activity": ex.name,
            "display_sets": ex.sets,
            "display_reps": ex.reps,
            "load_note": ex.load_note,
            "weak_point_tags": list(ex.weak_point_tags),
            "rpe_cap": ex.rpe_cap,
            "load_explanation": ex.load_explanation,
        }
    )


def structure_from_exercises(
    exercises: list[ExercisePrescription],
    previous: "WorkoutStructure | None" = None,
) -> "WorkoutStructure":
    """Lift the session's exercises into blocks, losslessly.

    Every field ``ExercisePrescription`` carries has a home on the block, including
    ``load_explanation``. A projection that dropped anything would make the two views disagree
    by construction, which the agreement validator refuses.

    ``previous`` is the structure these exercises were last projected from. Exercises are
    edited after selection (loads, weak-point tags, appended accessories), and re-deriving
    from the list alone would turn a structured run back into a strength block. So, walking
    ``previous`` in order: a block that does not project (a warmup) is kept where it was; an
    endurance block keeps its work shape when the exercise at its position is still the same
    activity, refreshed from that exercise; anything else — and any exercise beyond the
    previous structure — becomes a strength block, exactly as before phase 5.3.
    """
    out: WorkoutStructure = []
    remaining = list(exercises)
    for block in previous or []:
        if isinstance(block, IntervalBlock | ContinuousBlock) and block.activity is not None:
            if not remaining:
                break
            ex = remaining.pop(0)
            kept = endurance_block_for(block, ex) if ex.name == block.activity else None
            out.append(kept if kept is not None else _strength_block(ex))
        elif isinstance(block, StrengthBlock):
            if not remaining:
                break
            out.append(_strength_block(remaining.pop(0)))
        elif isinstance(block, IntervalBlock | ContinuousBlock | WarmupBlock | CooldownBlock):
            # Blocks that project nothing (a warmup, an endurance block without an activity)
            # stay where they were.
            out.append(block)
        else:
            assert_never(block)
    out.extend(_strength_block(ex) for ex in remaining)
    return out


def _strength_block(ex: ExercisePrescription) -> WorkoutBlock:
    return StrengthBlock(
        exercise=ex.name,
        sets=ex.sets,
        reps=ex.reps,
        load_target_kg=ex.prescribed_load_kg,
        percent_e1rm=ex.percent_e1rm,
        rpe_target=ex.rpe_cap,
        rest_sec=None,
        load_note=ex.load_note,
        e1rm_basis_kg=ex.e1rm_basis_kg,
        weak_point_tags=list(ex.weak_point_tags),
        load_explanation=ex.load_explanation,
    )


class WorkoutPrescription(BaseModel):
    """
    Next-session recommendation. Legacy fields required; `why` optional for old clients.
    """

    type: str
    focus: str
    rationale: str
    duration_min: int
    model_version: str = Field(
        default=PRESCRIPTION_ENGINE_VERSION, description="Prescription engine version"
    )
    exercises: list[ExercisePrescription] = Field(default_factory=lambda: [])
    # Phase 2.1: what the session IS, as typed blocks. Optional on the wire so existing
    # clients are untouched; `exercises` is a PROJECTION of it (project_exercises) and never
    # an independent source of truth — the validator below refuses a prescription whose two
    # views disagree. Absent on legacy stored content, which reads back exactly as before.
    structure: "WorkoutStructure | None" = None
    why: PrescriptionExplanation | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_estimate(self) -> DurationEstimate | None:
        """How much of this session's time the structure can actually account for (2.2).

        DESCRIPTIVE. It never rewrites ``duration_min``, which remains what the engine
        prescribed and what every existing client reads. Computed rather than stored so it
        cannot drift from the structure it describes.
        """
        return None if self.structure is None else calculate_duration(self.structure)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def calculated_duration_min(self) -> float | None:
        """The structure's own duration in minutes, or None when anything is untimed.

        None means "not known", never zero: a strength session whose rests are recorded but
        whose set execution time is not has a real partial sum, and reporting that partial sum
        as the session length would understate it. The partial sum is still visible in
        ``duration_estimate.known_seconds``, labelled with what is missing.
        """
        estimate = self.duration_estimate
        return None if estimate is None else estimate.minutes

    @model_validator(mode="after")
    def _structure_and_exercises_agree(self) -> "WorkoutPrescription":
        """One session, two views. They may never drift apart.

        This is what makes ``structure`` canonical rather than a parallel copy: any code that
        edits one and forgets the other fails here, at construction, instead of shipping a
        session whose blocks say one thing and whose exercise list says another.
        """
        if self.structure is None:
            return self
        projected = project_exercises(self.structure)
        if projected != self.exercises:
            raise ValueError(
                "structure and exercises disagree — exercises must be "
                "project_exercises(structure); edit the structure, not the projection"
            )
        return self

    def with_structure(self) -> "WorkoutPrescription":
        """This prescription with its structure (re-)derived from its exercises.

        The single seam through which structure is attached, so there is exactly one writer.
        The current structure is passed as ``previous`` so a structured run survives the
        edits made to its exercises since it was built (see ``structure_from_exercises``).
        """
        return self.model_copy(
            update={"structure": structure_from_exercises(self.exercises, self.structure)}
        )

    def to_prescribed_content(self) -> dict[str, Any]:
        """Serialize for persistence into ``PlannedSession.prescribed_content``.

        The single source of truth for that JSON shape — the prescribe-and-persist
        seam (service + planning route) writes it, and state_service reads it back
        by string key (ADR-0031). Keeping it here means a new field flows to all
        three sites from one place.

        JSON mode, because the column is JSONB written through plain ``json.dumps``: a
        ``datetime`` (``LoadExplanation.evaluated_at``) must arrive as an ISO string.
        """
        return self.model_dump(mode="json")
