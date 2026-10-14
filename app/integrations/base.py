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

from app.schemas.wellness import HrvMetric


class ProviderRejected(RuntimeError):
    """The provider answered and refused the request (e.g. a non-2xx token reply).

    Adapters raise it (or a subclass of their own) so shared code can tell a refusal from a
    transport or local failure without importing a concrete provider."""


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
    #: 0–10, higher = worse. Added late (ADR-0053 introduced ``stress`` on the wellness
    #: sample, registry and SIGNAL_CONFIG, but this seam was never updated) — so until now
    #: a provider that reported stress had nowhere to put it and the value was dropped in
    #: ``wearable_service``. No current wearable measures it; dataset-backed sources do.
    stress: float | None = None

    #: When the measurement window actually closed, if the provider reports it. Distinct
    #: from ``day`` (a calendar date) and from ingestion time. Naive UTC. ``None`` means the
    #: provider did not say — never a substitute value.
    measured_at: datetime | None = None

    #: 0..1 confidence from the provider's own reliability signals. ``None`` means the
    #: provider said nothing about quality, which is NOT the same as asserting it is
    #: perfect — so no adapter should emit 1.0 merely because no problem was flagged.
    quality: float | None = None

    #: WHICH HRV metric ``hrv_ms`` holds: ``"rmssd"`` or ``"sdnn"``. Providers disagree —
    #: Oura, Whoop and Garmin report rMSSD; Apple Watch reports SDNN, and SDNN runs 10-25%
    #: higher on the same inter-beat intervals. Baselines average like against like, so an
    #: adapter reporting ``hrv_ms`` should declare its metric here. ``None`` means it did not
    #: say, which is read as unknown and never as an assumed rMSSD.
    #: This belongs on the adapter, not on the sync service: the provider seam forbids the
    #: service branching on a provider name (``tests/test_wearable_provider_seam.py``).
    hrv_metric: HrvMetric | None = None

    raw: dict[str, Any] = field(default_factory=_empty_raw)


@runtime_checkable
class WearableAdapter(Protocol):
    """What every provider adapter must implement."""

    #: Provider slug, also used as WellnessSample.source (e.g. "oura").
    provider: str

    def build_authorize_url(self, state: str, *, code_challenge: str, redirect_uri: str) -> str:
        """Return the provider's OAuth2 authorize URL for one attempt: its ``state``, its PKCE
        ``code_challenge`` (S256) and the ``redirect_uri`` stored with it."""
        ...

    async def exchange_code(self, code: str, *, code_verifier: str, redirect_uri: str) -> TokenBundle:
        """Exchange an authorization ``code`` for tokens, proving the attempt's PKCE verifier and
        repeating the exact ``redirect_uri`` the authorize request carried."""
        ...

    async def refresh_tokens(self, refresh_token: str) -> TokenBundle:
        """Refresh an expired access token."""
        ...

    async def fetch_daily_wellness(
        self, access_token: str, start: date_cls, end: date_cls
    ) -> list[NormalizedWellness]:
        """Pull normalized daily wellness in the inclusive ``[start, end]`` range."""
        ...
