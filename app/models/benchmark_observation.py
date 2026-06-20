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

    benchmark_definition: Mapped["BenchmarkDefinition"] = relationship(
        "BenchmarkDefinition",
        back_populates="observations",
    )
