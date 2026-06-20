from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.derived_metric_snapshot import DerivedMetricSnapshot


class DerivedMetricDefinition(Base):
    __tablename__ = "derived_metric_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    domain: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    metric_type: Mapped[str] = mapped_column(String(50), nullable=False)
    unit: Mapped[str] = mapped_column(String(50), nullable=False)

    formula_type: Mapped[str] = mapped_column(String(50), nullable=False)
    formula_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    display_priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_dashboard_kpi: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    can_affect_prescriber_rules: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    snapshots: Mapped[list["DerivedMetricSnapshot"]] = relationship(
        "DerivedMetricSnapshot",
        back_populates="derived_metric_definition",
    )
