"""Oura wearable integration routes (Phase 2), mounted under ``/v1``.

``GET    /v1/integrations/oura/authorize``    (auth)  → OAuth authorize URL to open
``GET    /v1/integrations/oura/callback``     (no auth; identity in ``state``)
``POST   /v1/integrations/oura/connect/pat``  (auth)  → connect via Personal Access Token
``POST   /v1/integrations/oura/sync``         (auth)  → pull the caller's data now
``GET    /v1/integrations/oura/connection``   (auth)  → connection status (no tokens)
``DELETE /v1/integrations/oura/connection``   (auth)  → disconnect
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.crypto import EncryptionKeyMissingError
from app.core.db import get_db
from app.integrations.oura import OuraApiError, OuraAuthError
from app.models.user import User
from app.schemas.wearable import (
    AuthorizeUrlResponse,
    ConnectionStatus,
    PatConnectRequest,
    SyncResult,
    WearableConnectionOut,
)
from app.services import wearable_service

logger = logging.getLogger("perflab.integrations.oura")

router = APIRouter(prefix="/integrations/oura", tags=["Integrations"])

_PROVIDER = "oura"


@router.get("/authorize", response_model=AuthorizeUrlResponse)
async def authorize(
    current_user: User = Depends(get_current_user),
) -> AuthorizeUrlResponse:
    """Return the Oura OAuth authorize URL for the web app to open."""
    try:
        url = wearable_service.get_authorize_url(current_user.id, _PROVIDER)
    except OuraAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AuthorizeUrlResponse(authorize_url=url)


@router.get("/callback", include_in_schema=False)
async def callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """OAuth redirect target. Identity rides ``state`` (no Bearer header here)."""
    dest = f"{settings.WEB_APP_URL.rstrip('/')}/settings"
    try:
        await wearable_service.handle_oauth_callback(db, code=code, state=state)
        return RedirectResponse(url=f"{dest}?oura=connected", status_code=302)
    except ValueError as exc:  # bad/expired state
        logger.warning("Oura callback rejected: %s", exc)
        return RedirectResponse(url=f"{dest}?oura=error", status_code=302)
    except (OuraAuthError, EncryptionKeyMissingError) as exc:
        logger.error("Oura callback failed: %s", exc)
        return RedirectResponse(url=f"{dest}?oura=error", status_code=302)


@router.post("/connect/pat", response_model=WearableConnectionOut)
async def connect_pat(
    payload: PatConnectRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WearableConnectionOut:
    """Connect using an Oura Personal Access Token (single-user fast path)."""
    try:
        conn = await wearable_service.connect_pat(db, current_user.id, payload.token)
    except (OuraAuthError, OuraApiError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Oura token: {exc}") from exc
    except EncryptionKeyMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return WearableConnectionOut.model_validate(conn)


@router.post("/sync", response_model=SyncResult)
async def sync_now(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SyncResult:
    """Pull the caller's Oura data now (on-demand; the cron does this nightly)."""
    conn = await wearable_service.get_connection(db, current_user.id, _PROVIDER)
    if conn is None:
        raise HTTPException(status_code=404, detail="No Oura connection")
    try:
        written = await wearable_service.sync_connection(db, conn)
    except (OuraAuthError, OuraApiError) as exc:
        raise HTTPException(status_code=502, detail=f"Oura sync failed: {exc}") from exc
    except EncryptionKeyMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return SyncResult(
        provider=_PROVIDER, rows_written=written, last_sync_at=conn.last_sync_at
    )


@router.get("/connection", response_model=ConnectionStatus)
async def get_connection_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConnectionStatus:
    conn = await wearable_service.get_connection(db, current_user.id, _PROVIDER)
    if conn is None:
        return ConnectionStatus(connected=False, connection=None)
    return ConnectionStatus(
        connected=True, connection=WearableConnectionOut.model_validate(conn)
    )


@router.delete("/connection", status_code=204, response_model=None)
async def delete_connection(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    removed = await wearable_service.disconnect(db, current_user.id, _PROVIDER)
    if not removed:
        raise HTTPException(status_code=404, detail="No Oura connection")
