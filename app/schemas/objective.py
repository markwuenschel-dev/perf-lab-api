"""Objective schemas (Phase 4a — goal-anchored program).

``ObjectiveCreate``/``ObjectiveUpdate`` carry the athlete-facing goal shape
in; ``ObjectiveRead`` adds the computed ``progress`` block (direction-aware,
benchmark-linked only) and ``days_to_go`` countdown.
"""
from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime

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
    status: ObjectiveStatus
    created_at: datetime

    progress: ProgressBlock
    days_to_go: int | None

    model_config = ConfigDict(from_attributes=True)
