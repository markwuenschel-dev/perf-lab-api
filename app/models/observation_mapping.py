from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.benchmark_definition import BenchmarkDefinition


class ObservationMapping(Base):
    __tablename__ = "observation_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    benchmark_definition_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("benchmark_definitions.id"),
        index=True,
        nullable=False,
    )

    target_vector: Mapped[str] = mapped_column(String(20), nullable=False)
    target_key: Mapped[str] = mapped_column(String(50), nullable=False)

    mapping_type: Mapped[str] = mapped_column(String(50), nullable=False)
    coefficient: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    intercept: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    config: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    benchmark_definition: Mapped["BenchmarkDefinition"] = relationship(
        "BenchmarkDefinition",
        back_populates="observation_mappings",
    )
