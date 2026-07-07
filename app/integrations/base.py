"""Adapter contract for cloud wearable providers (Phase 2).

A provider adapter does two things:
1. Owns the OAuth2 handshake (authorize URL, code→token exchange, refresh).
2. Pulls the provider's daily data and normalizes it into ``NormalizedWellness`` —
   the canonical six-field wellness vocabulary enforced by
   ``app.logic.wellness_signals.SIGNAL_CONFIG`` (hrv_ms, sleep_hours, sleep_quality,
   resting_hr, soreness, mood). Signals a provider doesn't measure stay ``None``;
   anything outside the vocabulary goes into ``raw`` for provenance.

Keeping this behind a ``Protocol`` means the sync service (``wearable_service``)
never imports a concrete provider — the first-provider choice isn't load-bearing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class TokenBundle:
    """OAuth token-exchange / refresh result, provider-agnostic."""

    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scope: str | None = None


def _empty_raw() -> dict[str, Any]:
    return {}


@dataclass
class NormalizedWellness:
    """One day of wellness in the canonical vocabulary. Unmeasured signals are None."""

    day: date_cls
    hrv_ms: float | None = None
    sleep_hours: float | None = None
    sleep_quality: float | None = None  # 0–100
    resting_hr: float | None = None
    soreness: float | None = None  # 0–10, higher = worse
    mood: float | None = None  # 0–10, higher = better
    raw: dict[str, Any] = field(default_factory=_empty_raw)


@runtime_checkable
class WearableAdapter(Protocol):
    """What every provider adapter must implement."""

    #: Provider slug, also used as WellnessSample.source (e.g. "oura").
    provider: str

    def build_authorize_url(self, state: str) -> str:
        """Return the provider's OAuth2 authorize URL carrying ``state``."""
        ...

    async def exchange_code(self, code: str) -> TokenBundle:
        """Exchange an authorization ``code`` for tokens."""
        ...

    async def refresh_tokens(self, refresh_token: str) -> TokenBundle:
        """Refresh an expired access token."""
        ...

    async def fetch_daily_wellness(
        self, access_token: str, start: date_cls, end: date_cls
    ) -> list[NormalizedWellness]:
        """Pull normalized daily wellness in the inclusive ``[start, end]`` range."""
        ...
