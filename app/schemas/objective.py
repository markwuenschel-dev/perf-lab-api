"""Objective schemas (Phase 4a — goal-anchored program).

``ObjectiveCreate``/``ObjectiveUpdate`` carry the athlete-facing goal shape
in; ``ObjectiveRead`` adds the computed ``progress`` block (direction-aware,
benchmark-linked only) and ``days_to_go`` countdown.
"""
from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.objective import ObjectiveStatus


class ObjectiveCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=200)
    benchmark_code: str | None = None
    domain: str | None = None
    target_value: float | None = None
    target_unit: str | None = None
    target_date: date_cls | None = None
    priority: int = Field(default=3, ge=1, le=5)


class ObjectiveUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    benchmark_code: str | None = None
    domain: str | None = None
    target_value: float | None = None
    target_unit: str | None = None
    target_date: date_cls | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    status: ObjectiveStatus | None = None


class ProgressBlock(BaseModel):
    """Direction-aware progress toward a benchmark-linked objective's target.

    ``current``/``pct``/``direction`` are all null for a free-text objective
    (no linked benchmark) — countdown-only via ``days_to_go`` on the parent.
    """

    current: float | None = None
    target: float | None = None
    pct: float | None = None
    direction: str | None = None
    # What ``current`` is, so a client can tell a tested max from a training-set estimate.
    # ``current`` is demonstrated attainment: a modeled (chart) estimate is never the row it
    # reads (ADR-0056 amendment), so a formula switch cannot move a goal toward 100%.
    current_evidence_type: str | None = None
    current_value_semantics: str | None = None


class ObjectiveRead(BaseModel):
    id: int
    user_id: int
    benchmark_code: str | None
    label: str
    domain: str | None
    target_value: float | None
    target_unit: str | None
    target_date: date_cls | None
    priority: int
    # Display only — not a weight (ADR-0061). NULL = never ordered (sorts last).
    display_rank: int | None
    status: ObjectiveStatus
    created_at: datetime

    progress: ProgressBlock
    days_to_go: int | None

    model_config = ConfigDict(from_attributes=True)


class ObjectiveOrderUpdate(BaseModel):
    """``PUT /v1/objectives/order`` body: the caller's ACTIVE objective ids, first to last.

    Must cover exactly the caller's active objectives, each once. Writes
    ``display_rank`` 1..N — display only, never ``priority`` (ADR-0061)."""

    objective_ids: list[int]


DrivingObjectiveSource = Literal["macrocycle_anchor", "priority"]


class DrivingObjectiveRead(BaseModel):
    """The objective that actually drives prescription, chosen by the same selector
    the prescriber's objective signals come from
    (``objective_service.resolve_driving_objective``).

    - ``source="macrocycle_anchor"``: the anchor of the earliest-start active macrocycle.
    - ``source="priority"``: no usable anchor, so the highest-priority active objective
      (priority 1 first, ties by lowest id).
    - both null: nothing drives prescription (no active objectives, no anchor).
    """

    objective_id: int | None
    source: DrivingObjectiveSource | None
