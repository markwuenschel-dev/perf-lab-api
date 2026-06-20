from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship

from app.core.db import Base


class DerivedMetricSnapshot(Base):
    __tablename__ = "derived_metric_snapshots"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    derived_metric_definition_id = Column(
        Integer,
        ForeignKey("derived_metric_definitions.id"),
        index=True,
        nullable=False,
    )

    computed_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)
    value = Column(Float, nullable=False)
    confidence = Column(Float, nullable=True)
    contributing_observation_ids = Column(ARRAY(Integer), nullable=True)
    notes = Column(Text, nullable=True)

    derived_metric_definition = relationship(
        "DerivedMetricDefinition",
        back_populates="snapshots",
    )
