from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.mesocycle import BlockGoal, BlockStatus, SessionStatus
from app.schemas.prescription import WorkoutPrescription


class WeeklyTemplateSlot(BaseModel):
    day_of_week: int = Field(..., ge=1, le=7)
    category: str
    modality: str
    # The canonical domain this slot came from. `modality` is a display label and is lossy
    # (powerlifting and strength both read "Strength"), so the domain travels separately and
    # is what the prescriber keys on. None = fall back to the block goal.
    domain: str | None = None


class BlockCreateRequest(BaseModel):
    goal: BlockGoal
    start_date: date
    duration_weeks: int = Field(8, ge=1, le=24)
    sessions_per_week: int = Field(3, ge=1, le=7)
    weekly_template: list[WeeklyTemplateSlot] = Field(default_factory=lambda: [])
    modality_mix: dict[str, float] = Field(default_factory=dict)
    # Workload preference for the whole block; omitted/None means medium. It shifts targets
    # inside the periodization envelope and never loosens a safety limit.
    intensity: Literal["easy", "medium", "hard"] | None = None
    rationale: str | None = None
    deload_every_n_weeks: int = Field(4, ge=1, le=12)
    deload_volume_factor: float = Field(0.6, gt=0.1, le=1.0)
    benchmark_every_n_weeks: int | None = Field(default=4, ge=1, le=12)
    # Per-block session preferences (Phase 3a). Missing/None accessory_emphasis
    # is treated as "balanced" by the prescriber.
    target_session_minutes: int | None = Field(default=None, ge=20, le=180)
    accessory_emphasis: Literal["minimal", "balanced", "high"] | None = None
    accessory_focus: list[str] | None = None


class BlockUpdateRequest(BaseModel):
    status: BlockStatus | None = None
    rationale: str | None = None
    # Read dynamically at prescription time, so editing them does not desync
    # already-generated planned sessions.
    modality_mix: dict[str, float] | None = None
    deload_volume_factor: float | None = Field(None, gt=0.1, le=1.0)


class BlockRead(BaseModel):
    id: int
    user_id: int
    goal: BlockGoal
    status: BlockStatus
    start_date: date
    end_date: date | None
    duration_weeks: int
    sessions_per_week: int
    weekly_template: list[dict[str, Any]]
    modality_mix: dict[str, Any]
    intensity: str | None = None
    rationale: str | None
    deload_every_n_weeks: int
    deload_volume_factor: float
    target_session_minutes: int | None = None
    accessory_emphasis: str | None = None
    accessory_focus: list[str] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PlannedSessionRead(BaseModel):
    id: int
    block_id: int
    user_id: int
    scheduled_date: date
    original_scheduled_date: date | None = None
    week_number: int
    day_of_week: int
    category: str
    modality: str
    # The canonical domain this day was planned as (a045). `modality` is a lossy display
    # label, so the domain is what says which style the day belongs to.
    domain: str | None = None
    status: SessionStatus
    is_deload: bool
    is_benchmark: bool
    benchmark_key: str | None = None
    prescribed_content: dict[str, Any] | None = None
    workout_log_id: int | None = None
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PlannedSessionUpdateRequest(BaseModel):
    status: SessionStatus | None = None
    scheduled_date: date | None = None


class TodaySessionResponse(BaseModel):
    session: PlannedSessionRead | None
    # The route already sends a serialized WorkoutPrescription here — it builds this
    # field from `rx.to_prescribed_content()`, which is exactly `rx.model_dump()`
    # (app/schemas/prescription.py). Declaring the model makes the published contract
    # match what the wire has always carried, and matches /v1/next-session, which has
    # declared `response_model=WorkoutPrescription` all along (app/api/v1/prescribe.py).
    #
    # Deliberately NOT applied to `PlannedSessionRead.prescribed_content` above: that
    # field reflects persisted historical JSONB, which is not proven to satisfy this
    # model, so typing it would turn a contract cleanup into a runtime behaviour change.
    prescription: WorkoutPrescription | None = None



# --- Week review (GET /v1/planning/week-review) --------------------------------------------
#
# Display-only. Nothing here is an input to prescription or scoring: it reports what a block
# week held, what the state did across it, and the already-determined facts about the week
# after. See app/services/week_review_service.py for the rules each field follows.

WeekReviewUnavailableReason = Literal["no_active_block", "no_state", "state_invalid"]


class WeekReviewWindow(BaseModel):
    block_id: int
    week_number: int = Field(description="1-indexed week within the block.")
    duration_weeks: int
    start: date = Field(description="block.start_date + (week_number - 1) * 7.")
    end: date = Field(description="start + 6 (inclusive).")
    is_current_week: bool = Field(description="Server-local today falls inside the window.")


class WeekReviewSession(BaseModel):
    """One planned session whose ``scheduled_date`` falls in the window (moved sessions are
    counted where they now sit, ADR-0069)."""

    planned_session_id: int
    scheduled_date: date
    original_scheduled_date: date | None = None
    week_number: int = Field(description="The week the session was planned in (unchanged by a move).")
    category: str
    modality: str
    status: SessionStatus
    is_deload: bool
    is_benchmark: bool
    workout_log_id: int | None = None
    felt_rpe: float | None = Field(
        default=None,
        description="Session RPE from the linked workout log; null when no log is linked "
        "(e.g. a session marked completed by PATCH).",
    )
    prescribed_rpe: float | None = Field(
        default=None,
        description="Max exercise rpe_cap in the stored prescription; null when the session "
        "was never prescribed or its prescription names no cap. Never guessed.",
    )
    feedback_status: str | None = Field(default=None, description="SessionFeedback.status, if any.")
    followed_as_prescribed: bool | None = None
    modified: bool = Field(
        description="Completed AND the athlete reported a modification — the same rule the "
        "prescriber's adherence aggregate counts (ADR-0070)."
    )
    modified_volume: bool | None = None
    modified_intensity: bool | None = None
    modified_exercises: bool | None = None
    modification_reason: str | None = None


class WeekReviewCounts(BaseModel):
    planned: int
    completed: int
    skipped: int
    modified: int = Field(description="Subset of completed.")
    pending: int
    due: int = Field(description="Sessions scheduled on or before today — the adherence denominator.")
    adherence_pct: float | None = Field(
        default=None, description="completed / due × 100 (dashboard rule); null when nothing is due yet."
    )


class WeekReviewAxisMove(BaseModel):
    """One capacity axis across the week. An axis whose end status is ``insufficient`` is
    reported as not measured: its value is an unrefined prior, so no value or delta is given."""

    axis: str
    measured: bool
    status_start: str | None = None
    status_end: str | None = None
    start: float | None = None
    end: float | None = None
    delta: float | None = None


class WeekReviewMoved(BaseModel):
    """State snapshots bracketing the week: the latest state at or before each boundary.

    ``start_snapshot_at`` is the latest state at or before the week's first instant — which is
    also the previous week's end. ``previous_week_start_snapshot_at`` brackets the previous
    week so its fatigue movement can be compared with this one's.
    """

    previous_week_start_snapshot_at: datetime | None = None
    start_snapshot_at: datetime | None = None
    end_snapshot_at: datetime | None = None
    mean_fatigue_previous_week_start: float | None = None
    mean_fatigue_start: float | None = None
    mean_fatigue_end: float | None = None
    capacity: list[WeekReviewAxisMove] = Field(default_factory=lambda: [])


class WeekReviewNextItem(BaseModel):
    kind: Literal["plan", "safety", "assess"]
    source: str = Field(
        description="block:deload_week | block:benchmark_session | block:ends | "
        "adherence:lighter_bias | trigger:<axis>"
    )
    title: str
    reason: str


class WeekReview(BaseModel):
    """Week review. ``available=false`` carries a ``reason`` and nothing else is populated
    beyond ``window`` (when one was resolved)."""

    available: bool
    reason: WeekReviewUnavailableReason | None = None
    window: WeekReviewWindow | None = None
    sessions: list[WeekReviewSession] = Field(default_factory=lambda: [])
    counts: WeekReviewCounts | None = None
    moved: WeekReviewMoved | None = None
    next_week: list[WeekReviewNextItem] = Field(
        default_factory=lambda: [],
        description="Already-determined facts about the following week only. Nothing here "
        "was re-planned: future sessions are resolved on the day.",
    )
    next_week_status: Literal["changes_listed", "nothing_scheduled_to_change"] | None = Field(
        default=None,
        description="'nothing_scheduled_to_change' means no determined fact applies — NOT that "
        "a model checked next week and found nothing to change.",
    )
