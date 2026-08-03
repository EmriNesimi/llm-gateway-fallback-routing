import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ApiKeyRecord(Base):
    """A client API key issued through the admin API. Only the HMAC hash is
    stored — the raw key is shown once, at creation time, and never again."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    team: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC)
    )
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class AuditLogEntry(Base):
    """One row per gateway request, success or failure — the "why did this
    fail at 2am" record."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC)
    )
    api_key_hash: Mapped[str] = mapped_column(String(64), index=True)
    team: Mapped[str] = mapped_column(String(255))
    requested_model: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(64), default="")
    outcome: Mapped[str] = mapped_column(String(32))  # success | error
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
