from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.core.db import Base


class DerivedMetricDefinition(Base):
    __tablename__ = "derived_metric_definitions"

    id = Column(Integer, primary_key=True)
    code = Column(String(100), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=False)

    domain = Column(String(50), index=True, nullable=False)
    metric_type = Column(String(50), nullable=False)
    unit = Column(String(50), nullable=False)

    formula_type = Column(String(50), nullable=False)
    formula_config = Column(JSONB, nullable=False)

    display_priority = Column(Integer, default=100, nullable=False)
    is_dashboard_kpi = Column(Boolean, default=True, nullable=False)
    can_affect_prescriber_rules = Column(Boolean, default=False, nullable=False)

    provenance = Column(JSONB, nullable=True)

    snapshots = relationship(
        "DerivedMetricSnapshot",
        back_populates="derived_metric_definition",
    )
