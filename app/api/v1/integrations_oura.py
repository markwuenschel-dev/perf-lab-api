"""Oura wearable integration routes (Phase 2), mounted under ``/v1``.

``GET    /v1/integrations/oura/authorize``    (auth)  → start an attempt; consent URL to open
``POST   /v1/integrations/oura/complete``     (auth)  → finish an attempt (code + PKCE verifier)
``POST   /v1/integrations/oura/cancel``       (auth)  → the athlete declined at Oura
``*      /v1/integrations/oura/callback``     retired: never exchanges a code (ADR-0075)
``POST   /v1/integrations/oura/connect/pat``  (auth)  → connect via Personal Access Token
``POST   /v1/integrations/oura/sync``         (auth)  → pull the caller's data now
``GET    /v1/integrations/oura/connection``   (auth)  → connection status (no tokens)
``DELETE /v1/integrations/oura/connection``   (auth)  → disconnect
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
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
    OAuthAttemptResult,
    OAuthCancelRequest,
    OAuthCompleteRequest,
    OAuthLinked,
    PatConnectRequest,
    SyncResult,
    WearableConnectionOut,
)
from app.services import wearable_oauth, wearable_service

logger = logging.getLogger("perflab.integrations.oura")

router = APIRouter(prefix="/integrations/oura", tags=["Integrations"])

_PROVIDER = "oura"


#: Shown when a client without PKCE (a tab still running the previous release) asks to connect.
#: ADR-0075, crossing 2.
RELOAD_TO_CONNECT = "Perf Lab was updated. Reload the page, then Connect Oura again."
BAD_CHALLENGE = "code_challenge must be a 43-character base64url S256 challenge"
CALLBACK_RETIRED = "This sign-in link has been retired. Connect Oura again from Settings."


def _attempt_out(result: wearable_oauth.AttemptResult) -> OAuthAttemptResult:
    linked = (
        OAuthLinked(connection_id=result.connection_id, generation=result.generation)
        if result.connection_id is not None and result.generation is not None
        else None
    )
    return OAuthAttemptResult(status=result.status, reason=result.reason,  # type: ignore[arg-type]
                              completed_at=result.completed_at, linked=linked)


def _refused(exc: wearable_oauth.AttemptRefused) -> HTTPException:
    return HTTPException(status_code=exc.status_code,
                         detail={"status": exc.status, "reason": exc.reason})


@router.get("/authorize", response_model=AuthorizeUrlResponse)
async def authorize(
    code_challenge: str | None = Query(None, description="S256 PKCE challenge (base64url, 43 chars)"),
    code_challenge_method: str | None = Query(None, description="Must be S256"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AuthorizeUrlResponse:
    """Start an authorization attempt bound to the browser's PKCE challenge; return its URL."""
    if code_challenge is None:
        # A client from before PKCE: never start an attempt without the binding.
        raise HTTPException(status_code=400, detail=RELOAD_TO_CONNECT)
    if not wearable_oauth.valid_challenge(code_challenge, code_challenge_method):
        raise HTTPException(status_code=400, detail=BAD_CHALLENGE)
    try:
        url = await wearable_oauth.start(
            db, wearable_service.adapter(_PROVIDER), current_user.id, code_challenge
        )
    except (OuraAuthError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return AuthorizeUrlResponse(authorize_url=url)


@router.post("/complete", response_model=OAuthAttemptResult)
async def complete(
    payload: OAuthCompleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OAuthAttemptResult:
    """Finish the attempt the browser came back with. A finished attempt answers with its own
    outcome on every retry; ``in_progress`` and ``failed`` are 409, an unknown outcome 503."""
    try:
        result = await wearable_oauth.complete(
            db, wearable_service.adapter(_PROVIDER), current_user.id,
            state=payload.state, code=payload.code, code_verifier=payload.code_verifier,
        )
    except wearable_oauth.AttemptRefused as exc:
        raise _refused(exc) from None
    except wearable_oauth.OutcomeUnknown:
        raise HTTPException(status_code=503,
                            detail={"status": "unknown", "reason": "outcome_unknown"}) from None
    if result.status != "succeeded":
        raise HTTPException(status_code=409,
                            detail={"status": result.status, "reason": result.reason})
    return _attempt_out(result)


@router.post("/cancel", response_model=OAuthAttemptResult)
async def cancel(
    payload: OAuthCancelRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OAuthAttemptResult:
    """The athlete declined at Oura. Reports where the attempt stands afterwards, which is not
    necessarily ``cancelled``: an attempt already claimed or finished is left as it is."""
    try:
        result = await wearable_oauth.cancel(
            db, _PROVIDER, current_user.id,
            state=payload.state, code_verifier=payload.code_verifier,
        )
    except wearable_oauth.AttemptRefused as exc:
        raise _refused(exc) from None
    return _attempt_out(result)


@router.api_route(
    "/callback",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def retired_callback(request: Request) -> Response:
    """The pre-P6b redirect target. It never exchanges a code and never reads or logs the
    request's parameters. A browser that arrives here (a consent started before the cutover)
    is sent to Settings with the legacy, backward-compatible ``?oura=error`` marker."""
    if request.method == "GET":
        dest = f"{settings.WEB_APP_URL.rstrip('/')}/settings?oura=error&reason=interrupted"
        return RedirectResponse(url=dest, status_code=303)
    return JSONResponse(status_code=410, content={"detail": CALLBACK_RETIRED})


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
    except wearable_service.ConnectionReplaced:
        raise HTTPException(
            status_code=409, detail="The Oura connection changed during this sync. Sync again."
        ) from None
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
