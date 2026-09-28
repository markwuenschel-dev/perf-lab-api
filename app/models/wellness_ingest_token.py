"""WellnessIngestToken — a personal, write-only credential for pushing wellness from a device.

Apple Watch data lives in HealthKit on the iPhone; no cloud API exists, so something on the
phone must push it (an iOS Shortcut today; Health Auto Export or a native client could reuse
the same seam). That client cannot hold the athlete's login session, so it gets its own
credential with exactly one power: ``POST /v1/wellness/ingest`` for this athlete.

Only a SHA-256 of the token is stored. The plaintext is shown once, at creation. Revoking
sets ``revoked_at``; the row is kept so "last used" history survives. ``last_used_at`` is the
athlete's "last successful Apple sync": a stale value is how a silently failing phone
automation becomes visible.
"""
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class WellnessIngestToken(Base):
    __tablename__ = "wellness_ingest_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # hex SHA-256 of the full token. The token carries 256 bits of randomness, so a plain
    # digest (no salt, no slow KDF) cannot be brute-forced back to it.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # The token's first characters, so the athlete can tell tokens apart without the secret.
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(60), nullable=False)

    # Naive UTC like every other column here, without the deprecated utcnow (AUD-C18 guard).
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
