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

    # --- Policy-derived capacity authority (ADR-0058) ------------------------
    # Five orthogonal provenance dimensions; capacity authority is the meet of
    # their independent caps (app.logic.observation_authority). affects_capacity /
    # can_regress_capacity above are now DERIVED from capacity_effect, never the
    # reverse. All nullable + additive; conservative backfill in a028.
    source_type: Mapped[str | None] = mapped_column(
        String(40), nullable=True, comment="athlete_entry | workout_extraction | legacy_unknown"
    )
    collection_mode: Mapped[str | None] = mapped_column(
        String(40), nullable=True,
        comment="onboarding_onramp | retest | ad_hoc | workout | legacy_unknown",
    )
    provenance_operation: Mapped[str | None] = mapped_column(String(30), nullable=True)
    migration_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    migrated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actor_type: Mapped[str | None] = mapped_column(String(20), nullable=True)

    requested_capacity_effect: Mapped[str | None] = mapped_column(String(30), nullable=True)
    capacity_effect: Mapped[str | None] = mapped_column(
        String(30), nullable=True,
        comment="none | initialize_prior | upward_lower_bound | bidirectional_update",
    )
    protocol_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    protocol_version: Mapped[str | None] = mapped_column(String(30), nullable=True)
    protocol_validity: Mapped[str | None] = mapped_column(
        String(20), nullable=True, comment="not_evaluated | incomplete | valid | invalid"
    )
    authority_policy_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    authority_resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Confidence hook (ADR-0058 structural; #106 assigns the numbers).
    confidence_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    confidence_model_version: Mapped[str | None] = mapped_column(String(30), nullable=True)

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
