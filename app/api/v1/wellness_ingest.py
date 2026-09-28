"""Phone-pushed wellness (Apple Watch via an iOS Shortcut, 2026-09-28).

``POST   /v1/wellness/ingest``              push one day's device readings (ingest token)
``POST   /v1/wellness/ingest-tokens``       create a token; the only time it is shown (login)
``GET    /v1/wellness/ingest-tokens``       the athlete's active tokens + last use (login)
``DELETE /v1/wellness/ingest-tokens/{id}``  revoke (login)

An ingest token is not a login: ``get_current_user`` rejects it (it is not a JWT), and the
ingest route accepts nothing else. So a token on a phone can write wellness and do nothing
more.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.db import get_db
from app.models.user import User
from app.models.wellness_ingest_token import WellnessIngestToken
from app.schemas.wellness import (
    IngestTokenCreate,
    IngestTokenCreated,
    IngestTokenOut,
    WellnessIngestIn,
    WellnessSampleOut,
)
from app.services import wellness_ingest_service

router = APIRouter()

_bearer = HTTPBearer(auto_error=False)


async def _ingest_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> WellnessIngestToken:
    token = (
        await wellness_ingest_service.authenticate(db, credentials.credentials)
        if credentials is not None
        else None
    )
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked wellness ingest token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


@router.post("/wellness/ingest", response_model=WellnessSampleOut, tags=["Wellness"])
async def ingest_pushed_wellness(
    body: WellnessIngestIn,
    token: WellnessIngestToken = Depends(_ingest_token),
    db: AsyncSession = Depends(get_db),
) -> WellnessSampleOut:
    return await wellness_ingest_service.ingest(db, token, body)


@router.post(
    "/wellness/ingest-tokens",
    response_model=IngestTokenCreated,
    status_code=status.HTTP_201_CREATED,
    tags=["Wellness"],
)
async def create_ingest_token(
    body: IngestTokenCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IngestTokenCreated:
    row, token = await wellness_ingest_service.create_token(db, current_user.id, body.label)
    return IngestTokenCreated(**IngestTokenOut.model_validate(row).model_dump(), token=token)


@router.get("/wellness/ingest-tokens", response_model=list[IngestTokenOut], tags=["Wellness"])
async def list_ingest_tokens(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[IngestTokenOut]:
    rows = await wellness_ingest_service.list_tokens(db, current_user.id)
    return [IngestTokenOut.model_validate(r) for r in rows]


@router.delete(
    "/wellness/ingest-tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    tags=["Wellness"],
)
async def revoke_ingest_token(
    token_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    if not await wellness_ingest_service.revoke_token(db, current_user.id, token_id):
        raise HTTPException(status_code=404, detail="No such ingest token")
