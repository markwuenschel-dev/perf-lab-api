from sqlalchemy import Column, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.db import Base


class ObservationMapping(Base):
    __tablename__ = "observation_mappings"

    id = Column(Integer, primary_key=True)
    benchmark_definition_id = Column(
        Integer,
        ForeignKey("benchmark_definitions.id"),
        index=True,
        nullable=False,
    )

    target_vector = Column(String(20), nullable=False)
    target_key = Column(String(50), nullable=False)

    mapping_type = Column(String(50), nullable=False)
    coefficient = Column(Float, default=1.0, nullable=False)
    intercept = Column(Float, default=0.0, nullable=False)
    min_value = Column(Float, nullable=True)
    max_value = Column(Float, nullable=True)

    config = Column(JSONB, nullable=True)

    benchmark_definition = relationship(
        "BenchmarkDefinition",
        back_populates="observation_mappings",
    )
