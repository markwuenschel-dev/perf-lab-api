from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Prefer domain layer as source of truth (schemas re-export for compatibility)
from app.domain.vectors import (
    CapacityConfidence,
    CapacityState,
    FatigueState,
    TissueState,
)
from app.logic.confidence_presentation import POLICY_VERSION, confidence_status


class UnifiedStateVector(BaseModel):
    """
    Digital twin snapshot S(t): legacy scalars + decomposed engine vectors.

    Legacy fields remain for backward-compatible API clients; they are derived
    from (capacity_x, fatigue_f, tissue_t) on each update.
    """

    timestamp: datetime

    capacity_x: CapacityState = Field(default_factory=lambda: CapacityState())
    fatigue_f: FatigueState = Field(default_factory=lambda: FatigueState())
    tissue_t: TissueState = Field(default_factory=lambda: TissueState())

    # Per-axis model uncertainty about capacity_x (variance proxy). Seeded as a
    # weak prior, shrunk by benchmarks, grown by elapsed time. See ADR-0036.
    capacity_confidence: CapacityConfidence = Field(default_factory=lambda: CapacityConfidence())

    # Legacy capacities (mirrors of X / batteries)
    c_met_aerobic: float = Field(..., description="Aerobic capacity (e.g. CS / VO2 proxy)")
    c_nm_force: float = Field(..., description="Maximal strength / force capacity")
    c_struct: float = Field(..., description="Structural capacity / CSA proxy")
    b_met_anaerobic: float = Field(..., description="Anaerobic work capacity (W'/D')")

    # Legacy fatigues (mirrors of F aggregate view)
    f_met_systemic: float = Field(default=0.0, ge=0.0, le=100.0)
    f_nm_peripheral: float = Field(default=0.0, ge=0.0, le=100.0)
    f_nm_central: float = Field(default=0.0, ge=0.0, le=100.0)
    f_struct_damage: float = Field(default=0.0, ge=0.0, le=100.0)

    s_struct_signal: float = Field(default=0.0, ge=0.0)

    habit_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    skill_state: dict[str, float] = Field(default_factory=dict)
    model_version: str = Field(default="v0.3", description="State engine version")

    model_config = ConfigDict(from_attributes=True)


class StateHistorySnapshotRead(UnifiedStateVector):
    """A recorded state snapshot projected for the Digital Twin's time-travel view.

    The canonical ``UnifiedStateVector`` plus a per-axis confidence *presentation*
    band derived from THIS snapshot's own live ``capacity_confidence`` variance
    (confidence_presentation_policy_v1; ADR-0059 keeps provenance separate from
    certainty). Endpoint-specific on purpose: the canonical vector stays free of
    presentation state, and the derivation lives once in
    ``app.logic.confidence_presentation`` so the web app never re-declares the
    thresholds (they would silently drift).
    """

    capacity_confidence_status: dict[str, str] = Field(
        ...,
        description=(
            "Per-capacity-axis certainty band (established | provisional | "
            "insufficient), derived from this snapshot's own capacity_confidence "
            "variance. All 8 capacity axes, even those the Twin does not plot."
        ),
    )
    confidence_presentation_policy_version: str = Field(
        ...,
        description="Version of the confidence-presentation policy that produced the statuses.",
    )

    @classmethod
    def from_state(cls, state: UnifiedStateVector) -> "StateHistorySnapshotRead":
        """Project a domain state vector into the read model, deriving per-axis
        confidence status from that vector's own variance (all 8 axes)."""
        statuses = {
            axis: confidence_status(getattr(state.capacity_confidence, axis))
            for axis in CapacityConfidence.KEYS
        }
        return cls(
            **state.model_dump(),
            capacity_confidence_status=statuses,
            confidence_presentation_policy_version=POLICY_VERSION,
        )
