"""Wearable-connection API schemas (Phase 2).

Deliberately never exposes token material — only connection status and sync results.
"""
from __future__ import annotations

from datetime import datetime

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
