"""WearableConnection — a per-athlete link to a cloud wearable provider (Phase 2).

Stores the OAuth2 (or Personal Access Token) credentials needed to pull a user's
daily wellness from a provider like Oura. Tokens are **Fernet-encrypted at rest**
(see ``app.core.crypto``) — the ``*_enc`` columns hold ciphertext, never plaintext.

One connection per (user, provider): reconnecting upserts the same row. The sync
path (``app.services.wearable_service``) decrypts on use, refreshes OAuth tokens as
they expire, and writes normalized readings into ``WellnessSample`` (source="oura").
"""
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.user import User


class WearableConnection(Base):
    """OAuth/PAT credentials + sync bookkeeping for one athlete's wearable."""

    __tablename__ = "wearable_connections"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_wearable_user_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )

    # Provider slug (matches WellnessSample.source, e.g. "oura").
    provider: Mapped[str] = mapped_column(String(30), nullable=False, default="oura")
    # "oauth" (authorize/refresh flow) or "pat" (Personal Access Token, no refresh).
    auth_type: Mapped[str] = mapped_column(String(10), nullable=False, default="oauth")

    # Fernet ciphertext — never plaintext. Refresh token is null for PAT / providers
    # that don't issue one.
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Access-token expiry (UTC). Null for PATs, which do not expire on a schedule.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scope: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Watermark of the most recent successful pull, so sync only fetches new days.
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user: Mapped["User"] = relationship(
        "User", back_populates="wearable_connections"
    )
