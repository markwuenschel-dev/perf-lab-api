from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.db import Base


class BenchmarkObservation(Base):
    __tablename__ = "benchmark_observations"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    benchmark_definition_id = Column(
        Integer,
        ForeignKey("benchmark_definitions.id"),
        index=True,
        nullable=False,
    )

    observed_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)

    raw_value = Column(Float, nullable=False)
    secondary_value = Column(Float, nullable=True)
    normalized_value = Column(Float, nullable=True)

    bodyweight_kg = Column(Float, nullable=True)
    rpe = Column(Float, nullable=True)
    heart_rate_avg = Column(Float, nullable=True)
    heart_rate_drift_pct = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)

    protocol_metadata = Column(JSONB, nullable=True)

    validity_status = Column(String(20), default="valid", nullable=False)
    source = Column(String(50), default="manual", nullable=False)

    benchmark_definition = relationship(
        "BenchmarkDefinition",
        back_populates="observations",
    )
