"""Immutable prescription revisions (P1, migration a051).

One row per prescription ISSUED for a planned session. Content never changes after it is
written (a database trigger enforces it); a replacement is a new row with a higher
``revision_no`` and ``planned_sessions.current_revision_id`` moved to it.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

#: ``origin`` values: issued by the revision protocol, or preserved from pre-a051 content
#: whose issuance is unknown (never made current).
ORIGIN_ISSUED = "issued"
ORIGIN_LEGACY_UNKNOWN = "legacy_unknown"


class PrescriptionRevision(Base):
    __tablename__ = "prescription_revisions"
    __table_args__ = (
        UniqueConstraint("planned_session_id", "revision_no", name="uq_prescription_revision_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    planned_session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("planned_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    safety_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    safety_signature: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    versions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str] = mapped_column(String(48), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    evaluation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("now()")
    )
