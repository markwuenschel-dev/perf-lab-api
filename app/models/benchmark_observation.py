from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.benchmark_definition import BenchmarkDefinition


class BenchmarkObservation(Base):
    __tablename__ = "benchmark_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True, nullable=False
    )
    benchmark_definition_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("benchmark_definitions.id"),
        index=True,
        nullable=False,
    )

    observed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True, nullable=False
    )

    raw_value: Mapped[float] = mapped_column(Float, nullable=False)
    secondary_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    bodyweight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    rpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    heart_rate_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    heart_rate_drift_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    protocol_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    validity_status: Mapped[str] = mapped_column(
        String(20), default="valid", nullable=False
    )
    source: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)

    # --- Evidence authority + provenance (ADR-0055) --------------------------
    # validity_status stays for backward compat; capacity authority is decided by
    # source + evidence_type + these flags via app.logic.strength_evidence (fail-closed).
    evidence_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    value_semantics: Mapped[str | None] = mapped_column(
        String(20), nullable=True, comment="measured | estimated | lower_bound | unknown"
    )
    observation_model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(30), nullable=True)

    affects_capacity: Mapped[bool | None] = mapped_column(nullable=True)
    can_regress_capacity: Mapped[bool | None] = mapped_column(nullable=True)
    affects_prescription: Mapped[bool | None] = mapped_column(nullable=True)

    observation_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Nullable on purpose: unknown confidence is NULL, never a fake 0.5.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Causal provenance — a workout's own extraction must not grade that workout.
    exercise_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("exercises.id"), nullable=True, index=True
    )
    workout_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workout_logs.id"), nullable=True, index=True
    )
    set_log_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("workout_set_logs.id"), nullable=True
    )
    reps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    load_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    rir: Mapped[float | None] = mapped_column(Float, nullable=True)
    formula: Mapped[str | None] = mapped_column(String(30), nullable=True)
    effort_fidelity: Mapped[str | None] = mapped_column(String(20), nullable=True)

    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    quarantine_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)

    benchmark_definition: Mapped["BenchmarkDefinition"] = relationship(
        "BenchmarkDefinition",
        back_populates="observations",
    )
