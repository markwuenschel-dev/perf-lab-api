from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class EngineTransitionIdentity(Base):
    """One row per distinct (operator source + parameters) identity that wrote a state row.

    ``digest`` is the overall hash; ``components`` keeps the per-module and parameter digests
    beside it, so a mismatch can be explained rather than just detected. See
    ``app.engine.transition_identity``.
    """

    __tablename__ = "engine_transition_identities"

    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    components: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
