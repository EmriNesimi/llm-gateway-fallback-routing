import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import require_admin_key
from app.admin.schemas import (
    AdminAuditEntryOut,
    ApiKeyOut,
    AuditLogEntryOut,
    CreateKeyRequest,
    CreateKeyResponse,
)
from app.db.models import AdminAuditEntry, ApiKeyRecord, AuditLogEntry
from app.db.session import get_session
from app.ratelimit.dependency import enforce_admin_rate_limit
from app.security.api_keys import hash_key

# enforce_admin_rate_limit as well as the key check: without it X-Admin-Key
# could be guessed at unlimited rate. The configured key is long enough that
# brute force isn't realistic today, but that's a property of this deployment's
# secret rather than of the endpoint, and the endpoint is where it belongs.
router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    # Rate limit FIRST. FastAPI evaluates these in order, so with the key
    # check ahead of it a wrong key 401s before the bucket is touched —
    # leaving guesses unlimited, which is the one case the limit exists
    # for. Costing every attempt, authenticated or not, is the point.
    dependencies=[Depends(enforce_admin_rate_limit), Depends(require_admin_key)],
)


@router.post("/keys", response_model=CreateKeyResponse)
async def create_key(
    request: CreateKeyRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    admin_key: str = Depends(require_admin_key),
) -> CreateKeyResponse:
    # Shown once, here, and never again — only the hash is persisted.
    raw_key = secrets.token_hex(24)
    record = ApiKeyRecord(key_hash=hash_key(raw_key), team=request.team)
    session.add(record)
    # Flushed rather than committed, so the row gets its id and both writes
    # land in one transaction — a key that exists with no audit row is exactly
    # the gap this closes.
    await session.flush()
    session.add(
        AdminAuditEntry(
            request_id=getattr(http_request.state, "request_id", ""),
            action="issued",
            key_id=record.id,
            team=request.team,
            admin_key_hash=hash_key(admin_key),
        )
    )
    await session.commit()
    return CreateKeyResponse(api_key=raw_key, team=request.team)


@router.get("/keys", response_model=list[ApiKeyOut])
async def list_keys(
    limit: int = 100, offset: int = 0, session: AsyncSession = Depends(get_session)
) -> list[ApiKeyRecord]:
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    query = select(ApiKeyRecord).order_by(desc(ApiKeyRecord.created_at)).limit(limit).offset(offset)
    result = await session.execute(query)
    return list(result.scalars().all())


@router.get("/keys/{key_id}", response_model=ApiKeyOut)
async def get_key(key_id: int, session: AsyncSession = Depends(get_session)) -> ApiKeyRecord:
    record = await session.get(ApiKeyRecord, key_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")
    return record


@router.delete("/keys/{key_id}")
async def revoke_key(
    key_id: int,
    http_request: Request,
    session: AsyncSession = Depends(get_session),
    admin_key: str = Depends(require_admin_key),
) -> dict:
    record = await session.get(ApiKeyRecord, key_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="key not found")

    record.revoked = True
    session.add(
        AdminAuditEntry(
            request_id=getattr(http_request.state, "request_id", ""),
            action="revoked",
            key_id=key_id,
            team=record.team,
            admin_key_hash=hash_key(admin_key),
        )
    )
    await session.commit()
    return {"id": key_id, "revoked": True}


@router.get("/audit-log", response_model=list[AuditLogEntryOut])
async def list_audit_log(
    team: str | None = None,
    request_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> list[AuditLogEntry]:
    """`request_id` is the sharpest filter here — pair it with the
    X-Request-ID a caller reports to jump straight to the row (and, via
    tracing spans tagged with the same ID, the exact provider attempts)
    behind a specific failed or slow request. `offset` paginates past
    `limit`'s 1000-row ceiling for callers paging through a wider window."""
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    query = (
        select(AuditLogEntry).order_by(desc(AuditLogEntry.created_at)).limit(limit).offset(offset)
    )
    if team:
        query = query.where(AuditLogEntry.team == team)
    if request_id:
        query = query.where(AuditLogEntry.request_id == request_id)

    result = await session.execute(query)
    return list(result.scalars().all())


@router.get("/key-events", response_model=list[AdminAuditEntryOut])
async def list_key_events(
    action: str | None = None,
    key_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> list[AdminAuditEntry]:
    """Who issued or revoked which key, and when.

    Separate from /audit-log, which answers questions about traffic. This one
    answers "how did this key come to exist" and "was it really revoked" —
    the questions you have after a key turns up somewhere it shouldn't.
    """
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    query = (
        select(AdminAuditEntry)
        .order_by(desc(AdminAuditEntry.created_at))
        .limit(limit)
        .offset(offset)
    )
    if action:
        query = query.where(AdminAuditEntry.action == action)
    if key_id is not None:
        query = query.where(AdminAuditEntry.key_id == key_id)

    result = await session.execute(query)
    return list(result.scalars().all())
