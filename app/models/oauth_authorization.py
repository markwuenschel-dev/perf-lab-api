"""OAuthAuthorization — one wearable OAuth authorization attempt (a061, ADR-0075).

An attempt is created ``pending`` when the athlete starts connecting, and moves only
``pending → claimed → succeeded | failed`` or ``pending → failed`` (cancelled or expired). Its
inputs never change, and a terminal attempt never changes again except that ``connection_id``
becomes NULL when the connection it linked is deleted. ``receipt_connection_id`` and
``receipt_generation`` keep what the attempt linked at the time; they describe that attempt, not
the athlete's current connection.

The database enforces all of this: a061 declares the per-status column shape, time order and
formats as CHECK constraints and the transitions and immutability as a ``BEFORE UPDATE``
trigger. They are not repeated here because the schema is built by migrations, never from this
metadata.

Only the hash of ``state`` is stored; the state itself and the PKCE verifier never are.
"""
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class OAuthAuthorization(Base):
    __tablename__ = "oauth_authorizations"
    __table_args__ = (
        UniqueConstraint("state_hash", name="uq_oauth_authorizations_state_hash"),
        Index("ix_oauth_authorizations_user_created", "user_id", "created_at"),
        Index(
            "ix_oauth_authorizations_connection",
            "connection_id",
            postgresql_where=text("connection_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    # sha256 hex of the state handed to the provider.
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # base64url S256 challenge of the browser's PKCE verifier.
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    # Sent to the authorize endpoint; the exchange must send exactly this again.
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # A reason code for a failed attempt (e.g. "cancelled", "expired"); never provider text.
    error: Mapped[str | None] = mapped_column(String(40), nullable=True)
    receipt_connection_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    receipt_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connection_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("wearable_connections.id", ondelete="SET NULL"), nullable=True
    )
