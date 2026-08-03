from sqlalchemy import select

from app.db.models import ApiKeyRecord, AuditLogEntry
from app.db.session import async_session
from app.security.api_keys import hash_key


async def record_audit_log(
    api_key: str,
    requested_model: str,
    outcome: str,
    provider: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: float = 0.0,
) -> None:
    key_hash = hash_key(api_key)

    async with async_session() as session:
        result = await session.execute(
            select(ApiKeyRecord).where(ApiKeyRecord.key_hash == key_hash)
        )
        record = result.scalar_one_or_none()
        # Keys configured via the legacy GATEWAY_API_KEYS env var have no DB
        # record (they predate the admin API), so their spend still gets audited.
        team = record.team if record else "unlinked"

        session.add(
            AuditLogEntry(
                api_key_hash=key_hash,
                team=team,
                requested_model=requested_model,
                provider=provider,
                outcome=outcome,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
            )
        )
        await session.commit()
