from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.benchmark_observation import BenchmarkObservation
    from app.models.observation_mapping import ObservationMapping


class BenchmarkDefinition(Base):
    __tablename__ = "benchmark_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    domain: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    metric_type: Mapped[str] = mapped_column(String(50), nullable=False)
    unit: Mapped[str] = mapped_column(String(50), nullable=False)

    is_primary_anchor: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_derived_only: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_validator_only: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    protocol_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    standardization_rules: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    minimum_retest_interval_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )

    better_direction: Mapped[str] = mapped_column(String(20), nullable=False)
    observation_weight: Mapped[float] = mapped_column(
        Float, default=1.0, nullable=False
    )

    state_targets: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    fatigue_targets: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    tissue_targets: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )

    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    observations: Mapped[list["BenchmarkObservation"]] = relationship(
        "BenchmarkObservation",
        back_populates="benchmark_definition",
    )
    observation_mappings: Mapped[list["ObservationMapping"]] = relationship(
        "ObservationMapping",
        back_populates="benchmark_definition",
    )

    __table_args__ = (
        CheckConstraint(
            "NOT (is_primary_anchor AND is_derived_only)",
            name="ck_benchmark_anchor_not_derived_only",
        ),
        CheckConstraint(
            "NOT (is_primary_anchor AND is_validator_only)",
            name="ck_benchmark_anchor_not_validator_only",
        ),
    )
