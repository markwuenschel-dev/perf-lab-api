"""Oura Ring adapter — OAuth2 + Personal Access Token, Oura API v2 (Phase 2).

Maps Oura's daily *sleep* documents into the canonical wellness vocabulary and
stashes readiness/temperature/etc. into ``raw``. Oura measures no soreness/mood,
so those stay ``None`` (a manual check-in can still supply them).

Auth header is identical for OAuth access tokens and Personal Access Tokens
(``Authorization: Bearer <token>``), so ``fetch_daily_wellness`` serves both paths.

NOTE ON FIELD NAMES: the v2 ``/usercollection/sleep`` document fields used below
(``average_hrv``, ``total_sleep_duration`` seconds, ``efficiency`` 0–100,
``lowest_heart_rate``, ``day``) are from the documented v2 schema. Confirm against
a live response (the mapping is centralized in ``_sleep_doc_to_wellness`` so it is a
one-function fix if Oura's names differ).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from datetime import date as date_cls
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.integrations.base import NormalizedWellness, TokenBundle

logger = logging.getLogger("perflab.integrations.oura")

# OAuth2 + API hosts (Oura API v2).
AUTHORIZE_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
API_BASE = "https://api.ouraring.com/v2/usercollection"
DEFAULT_SCOPE = "daily personal"

_HTTP_TIMEOUT = httpx.Timeout(20.0)


class OuraAuthError(RuntimeError):
    """Raised on an Oura auth/token failure (bad code, expired refresh, bad PAT)."""


class OuraApiError(RuntimeError):
    """Raised on a non-2xx from an Oura data endpoint."""


class OuraAdapter:
    """Concrete :class:`app.integrations.base.WearableAdapter` for Oura."""

    provider = "oura"

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        redirect_uri: str | None = None,
    ) -> None:
        self.client_id = client_id if client_id is not None else settings.OURA_CLIENT_ID
        self.client_secret = (
            client_secret if client_secret is not None else settings.OURA_CLIENT_SECRET
        )
        self.redirect_uri = (
            redirect_uri if redirect_uri is not None else settings.OURA_REDIRECT_URI
        )

    # --- OAuth2 -----------------------------------------------------------------

    def build_authorize_url(self, state: str) -> str:
        if not self.client_id:
            raise OuraAuthError("OURA_CLIENT_ID is not configured.")
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": DEFAULT_SCOPE,
            "state": state,
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> TokenBundle:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            }
        )

    async def refresh_tokens(self, refresh_token: str) -> TokenBundle:
        return await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    async def _token_request(self, data: dict[str, str]) -> TokenBundle:
        if not self.client_id or not self.client_secret:
            raise OuraAuthError("Oura OAuth client credentials are not configured.")
        payload = {
            **data,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(TOKEN_URL, data=payload)
        if resp.status_code != 200:
            raise OuraAuthError(
                f"Oura token request failed ({resp.status_code}): {resp.text[:200]}"
            )
        body = resp.json()
        expires_at: datetime | None = None
        if isinstance(body.get("expires_in"), (int, float)):
            expires_at = datetime.now(UTC) + timedelta(seconds=int(body["expires_in"]))
        return TokenBundle(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token"),
            expires_at=expires_at,
            scope=body.get("scope"),
        )

    # --- Data pull --------------------------------------------------------------

    async def fetch_daily_wellness(
        self, access_token: str, start: date_cls, end: date_cls
    ) -> list[NormalizedWellness]:
        """Pull sleep (+ daily_readiness, merged into ``raw``) for [start, end]."""
        sleep_docs = await self._get_collection(access_token, "sleep", start, end)
        readiness_docs = await self._get_collection(
            access_token, "daily_readiness", start, end
        )
        # Index readiness by day so it can enrich the matching sleep record's raw.
        readiness_by_day: dict[str, dict[str, Any]] = {
            str(d.get("day")): d for d in readiness_docs if d.get("day")
        }

        # A day can have multiple sleep documents (naps + long sleep); keep the
        # longest per day as the representative night.
        by_day: dict[str, dict[str, Any]] = {}
        for doc in sleep_docs:
            day = str(doc.get("day"))
            if not day or day == "None":
                continue
            cur = by_day.get(day)
            if cur is None or _sleep_seconds(doc) > _sleep_seconds(cur):
                by_day[day] = doc

        out: list[NormalizedWellness] = []
        for day, doc in sorted(by_day.items()):
            readiness = readiness_by_day.get(day)
            out.append(_sleep_doc_to_wellness(day, doc, readiness))
        return out

    async def _get_collection(
        self, access_token: str, endpoint: str, start: date_cls, end: date_cls
    ) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {"start_date": start.isoformat(), "end_date": end.isoformat()}
        rows: list[dict[str, Any]] = []
        url: str | None = f"{API_BASE}/{endpoint}"
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            # Oura paginates via next_token; follow it until exhausted.
            while url is not None:
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code == 401:
                    raise OuraAuthError("Oura rejected the access token (401).")
                if resp.status_code != 200:
                    raise OuraApiError(
                        f"Oura {endpoint} failed ({resp.status_code}): {resp.text[:200]}"
                    )
                body = resp.json()
                rows.extend(body.get("data", []))
                next_token = body.get("next_token")
                if next_token:
                    params = {**params, "next_token": next_token}
                else:
                    url = None
        return rows


def _sleep_seconds(doc: dict[str, Any]) -> float:
    val = doc.get("total_sleep_duration")
    return float(val) if isinstance(val, (int, float)) else 0.0


def _sleep_doc_to_wellness(
    day: str, doc: dict[str, Any], readiness: dict[str, Any] | None
) -> NormalizedWellness:
    """Map one Oura sleep document (+ optional readiness) → NormalizedWellness.

    The single centralized mapping point — adjust here if Oura field names differ.
    """
    hrv = doc.get("average_hrv")
    total_sec = doc.get("total_sleep_duration")
    efficiency = doc.get("efficiency")
    lowest_hr = doc.get("lowest_heart_rate")

    sleep_hours = round(float(total_sec) / 3600.0, 2) if isinstance(total_sec, (int, float)) else None
    # Oura efficiency is already 0–100; clamp defensively in case a 0–1 ratio slips in.
    sleep_quality: float | None = None
    if isinstance(efficiency, (int, float)):
        eff = float(efficiency)
        sleep_quality = round(eff * 100.0, 1) if eff <= 1.0 else round(eff, 1)

    raw: dict[str, Any] = {"provider": "oura", "sleep": doc}
    if readiness is not None:
        raw["daily_readiness"] = readiness

    return NormalizedWellness(
        day=date_cls.fromisoformat(day),
        hrv_ms=float(hrv) if isinstance(hrv, (int, float)) else None,
        sleep_hours=sleep_hours,
        sleep_quality=sleep_quality,
        resting_hr=float(lowest_hr) if isinstance(lowest_hr, (int, float)) else None,
        soreness=None,  # Oura does not measure soreness
        mood=None,  # Oura does not measure mood
        raw=raw,
    )
