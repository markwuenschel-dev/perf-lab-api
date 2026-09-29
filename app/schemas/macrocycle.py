"""Macrocycle schemas (Phase 5 — goal-anchored program).

``MacrocycleCreate``/``MacrocycleUpdate`` carry the thin container in;
``MacrocycleRead`` adds the computed cross-block ``week_progress`` ("week X of
Y") plus the anchor Objective's ``label``/``target_date`` (denormalized for
display — the Objective stays the source of truth per ADR-0040/PDR-0004).
"""
from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models.macrocycle import MacrocycleStatus
from app.models.mesocycle import BlockGoal, BlockStatus


class MacrocycleCreate(BaseModel):
    # The anchor Objective this program is built toward. Must belong to the
    # caller (enforced in the service); an unknown/foreign id is a 400.
    objective_id: int
    # Defaults to today in the service when omitted (a program starts now).
    start_date: date_cls | None = None


class MacrocycleUpdate(BaseModel):
    start_date: date_cls | None = None
    status: MacrocycleStatus | None = None


class WeekProgress(BaseModel):
    """Cross-block schedule position, derived from the macrocycle's
    ``start_date`` and the anchor Objective's ``target_date`` — never stored.

    - ``current_week`` is 1-indexed and always present (open-ended programs
      still have a "week N"). It is capped at ``total_weeks`` when the horizon
      is known, so it never reads "week 9 of 8".
    - ``total_weeks``/``pct``/``weeks_to_go`` are null when the anchor has no
      ``target_date`` (or a target on/before the start) — an open horizon.
    """

    current_week: int
    total_weeks: int | None = None
    pct: float | None = None
    weeks_to_go: int | None = None


class MacrocycleBlockSummary(BaseModel):
    """One training block under a macrocycle, for the program timeline.

    Blocks are generated one at a time and never persisted ahead (ADR-0040), so this
    lists only blocks that exist. ``phase`` is date-derived (``end_date`` before today →
    completed, ``start_date`` after today → upcoming, else current); it is independent of
    ``status``, which only changes by a manual PATCH. ``deload_weeks``/``benchmark_weeks``
    are the week numbers whose stored sessions are flagged deload/benchmark.
    ``block_taper_week`` is the block-local final-week taper (3+ week blocks), not an
    event taper (ADR-0061/0065).
    """

    id: int
    goal: BlockGoal
    start_date: date_cls
    end_date: date_cls
    duration_weeks: int
    status: BlockStatus
    phase: Literal["completed", "current", "upcoming"]
    deload_weeks: list[int]
    benchmark_weeks: list[int]
    block_taper_week: int | None


class MacrocycleRead(BaseModel):
    id: int
    user_id: int
    objective_id: int
    start_date: date_cls
    status: MacrocycleStatus
    created_at: datetime
    updated_at: datetime

    # Denormalized from the anchor Objective for display.
    objective_label: str
    target_date: date_cls | None
    # How many blocks currently hang under this macrocycle (== len(blocks)).
    block_count: int
    week_progress: WeekProgress
    # The blocks under this macrocycle, ordered start_date then id.
    blocks: list[MacrocycleBlockSummary]
    # Weeks from the last block's end to the anchor's target date — the horizon no
    # block covers yet (blocks are never persisted ahead, ADR-0040). Null when the
    # anchor has no target date; with no blocks it is measured from the macrocycle
    # start. Never negative.
    unplanned_weeks: int | None

    model_config = ConfigDict(from_attributes=True)
