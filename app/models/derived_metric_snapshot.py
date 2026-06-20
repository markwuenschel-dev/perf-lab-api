from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.derived_metric_definition import DerivedMetricDefinition


class DerivedMetricSnapshot(Base):
    __tablename__ = "derived_metric_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True, nullable=False
    )
    derived_metric_definition_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("derived_metric_definitions.id"),
        index=True,
        nullable=False,
    )

    computed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True, nullable=False
    )
    value: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    contributing_observation_ids: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    derived_metric_definition: Mapped["DerivedMetricDefinition"] = relationship(
        "DerivedMetricDefinition",
        back_populates="snapshots",
    )
