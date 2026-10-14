"""Wearable-connection API schemas (Phase 2).

Deliberately never exposes token material — only connection status and sync results.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WearableConnectionOut(BaseModel):
    """Public view of a wearable connection — no tokens, ever."""

    provider: str
    auth_type: str
    connected: bool = True
    scope: str | None = None
    last_sync_at: datetime | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ConnectionStatus(BaseModel):
    """Returned by GET /connection whether or not a connection exists."""

    connected: bool
    connection: WearableConnectionOut | None = None


class AuthorizeUrlResponse(BaseModel):
    authorize_url: str


class PatConnectRequest(BaseModel):
    token: str = Field(..., min_length=8, description="Oura Personal Access Token")


class SyncResult(BaseModel):
    provider: str
    rows_written: int
    last_sync_at: datetime | None = None


# --- OAuth attempt (P6b, ADR-0075) -------------------------------------------------

#: A PKCE verifier as RFC 7636 defines it: 43-128 unreserved characters.
PKCE_VERIFIER_PATTERN = r"^[A-Za-z0-9\-._~]{43,128}$"


class OAuthCompleteRequest(BaseModel):
    """What the browser brings back from the provider, plus the verifier it kept."""

    state: str = Field(..., min_length=1, max_length=256)
    code: str = Field(..., min_length=1, max_length=2048)
    code_verifier: str = Field(..., pattern=PKCE_VERIFIER_PATTERN)


class OAuthCancelRequest(BaseModel):
    """The athlete declined at the provider. The verifier proves possession of the attempt."""

    state: str = Field(..., min_length=1, max_length=256)
    code_verifier: str = Field(..., pattern=PKCE_VERIFIER_PATTERN)


class OAuthLinked(BaseModel):
    """The connection a successful attempt linked, as it was then. History, not current state:
    whether the athlete is connected now comes only from ``GET /connection``."""

    connection_id: int
    generation: int


class OAuthAttemptResult(BaseModel):
    """Where one authorization attempt stands. ``succeeded`` carries ``linked``; ``failed``
    carries a ``reason`` code (``cancelled``, ``expired``, ``claim_stale``,
    ``exchange_rejected``, ``exchange_failed``)."""

    status: Literal["succeeded", "failed", "in_progress"]
    reason: str | None = None
    completed_at: datetime | None = None
    linked: OAuthLinked | None = None
