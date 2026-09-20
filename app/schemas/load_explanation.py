"""How a prescribed load was (or was not) justified.

Extracted from ``prescription.py`` so ``workout_structure`` can carry it on a block without
importing the module that imports it. Re-exported from ``prescription`` — every existing
import keeps working.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

LoadExplanationReason = Literal[
    "stale",
    "missing_performance_date",
    "estimate_not_used",
    "set_not_qualifying",
    "no_evidence",
    "not_qualifying",
]


class LoadExplanation(BaseModel):
    """Whether this exercise carries a suggested weight, and if not, why (S2, N1).

    * ``recommended`` — a qualifying e1RM sized the load.
    * ``no_qualifying_evidence`` — the lift supports a weight, but nothing qualified at
      ``evaluated_at``; ``reason`` says which kind of evidence came closest.
    * ``not_supported`` — an externally loaded exercise with no e1RM benchmark, so no
      athlete evidence could size it.

    Unloaded, uncatalogued exercises carry no explanation at all. It is persisted with the
    served prescription, so what the athlete was shown can be read back later.
    """

    status: Literal["recommended", "no_qualifying_evidence", "not_supported"]
    reason: LoadExplanationReason | None = Field(
        default=None, description="Set only when status is no_qualifying_evidence."
    )
    benchmark_code: str | None = Field(
        default=None, description="The e1RM benchmark the lift was evaluated against."
    )
    evaluated_at: datetime = Field(
        description="The instant evidence eligibility was evaluated (UTC)."
    )
    evidence_performed_at: datetime | None = Field(
        default=None,
        description=(
            "When the selected evidence (recommended) or the deterministic explanatory "
            "evidence (no_qualifying_evidence) was performed, if known (UTC)."
        ),
    )
