from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import relationship

from app.core.db import Base


class BenchmarkDefinition(Base):
    __tablename__ = "benchmark_definitions"

    id = Column(Integer, primary_key=True)
    code = Column(String(100), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=False)

    domain = Column(String(50), index=True, nullable=False)
    metric_type = Column(String(50), nullable=False)
    unit = Column(String(50), nullable=False)

    is_primary_anchor = Column(Boolean, default=False, nullable=False)
    is_derived_only = Column(Boolean, default=False, nullable=False)
    is_validator_only = Column(Boolean, default=False, nullable=False)

    protocol_summary = Column(Text, nullable=True)
    standardization_rules = Column(JSONB, nullable=True)
    minimum_retest_interval_days = Column(Integer, nullable=True)

    better_direction = Column(String(20), nullable=False)
    observation_weight = Column(Float, default=1.0, nullable=False)

    state_targets = Column(ARRAY(String), nullable=True)
    fatigue_targets = Column(ARRAY(String), nullable=True)
    tissue_targets = Column(ARRAY(String), nullable=True)

    provenance = Column(JSONB, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    observations = relationship(
        "BenchmarkObservation",
        back_populates="benchmark_definition",
    )
    observation_mappings = relationship(
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
