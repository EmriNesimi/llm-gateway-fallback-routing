import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import require_admin_key
from app.admin.schemas import ApiKeyOut, CreateKeyRequest, CreateKeyResponse
from app.db.models import ApiKeyRecord
from app.db.session import get_session
from app.security.api_keys import hash_key

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_key)])


@router.post("/keys", response_model=CreateKeyResponse)
async def create_key(
    request: CreateKeyRequest, session: AsyncSession = Depends(get_session)
) -> CreateKeyResponse:
    # Shown once, here, and never again — only the hash is persisted.
    raw_key = secrets.token_hex(24)
    session.add(ApiKeyRecord(key_hash=hash_key(raw_key), team=request.team))
    await session.commit()
    return CreateKeyResponse(api_key=raw_key, team=request.team)


@router.get("/keys", response_model=list[ApiKeyOut])
async def list_keys(session: AsyncSession = Depends(get_session)) -> list[ApiKeyRecord]:
    result = await session.execute(select(ApiKeyRecord))
    return list(result.scalars().all())


@router.delete("/keys/{key_id}")
async def revoke_key(key_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    record = await session.get(ApiKeyRecord, key_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")

    record.revoked = True
    await session.commit()
    return {"id": key_id, "revoked": True}
