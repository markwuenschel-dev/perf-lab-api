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

