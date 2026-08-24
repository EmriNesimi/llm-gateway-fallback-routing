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


class AdminAuditEntry(Base):
    """One row per key issued or revoked through the admin API.

    Deliberately its own table rather than a row in `audit_log`. That one is
    shaped for chat requests — requested_model, provider, tokens, cost,
    latency — so an admin event would be mostly empty strings and zeros, and
    would surface in `/admin/audit-log` queries that exist to answer questions
    about traffic. Two different questions, two tables.

    Nothing secret is stored: `admin_key_hash` is an HMAC of the admin secret
    used, enough to tell one admin credential from another — and to notice
    activity from one that should have been rotated — without keeping the
    credential itself.
    """

    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC)
    )
    # The same correlation ID as the request audit log, so an admin action and
    # the traffic around it line up on one timeline.
    request_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    action: Mapped[str] = mapped_column(String(32), index=True)  # issued | revoked
    key_id: Mapped[int] = mapped_column(Integer, index=True)
    team: Mapped[str] = mapped_column(String(255), default="")
    admin_key_hash: Mapped[str] = mapped_column(String(64), default="")


class AuditLogEntry(Base):
    """One row per gateway request, success or failure — the "why did this
    fail at 2am" record."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC)
    )
    # Correlation ID from the X-Request-ID header/middleware — lets a support
    # request ("call XYZ failed") be matched to this exact row and its traces.
    request_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    api_key_hash: Mapped[str] = mapped_column(String(64), index=True)
    team: Mapped[str] = mapped_column(String(255))
    requested_model: Mapped[str] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(64), default="")
    outcome: Mapped[str] = mapped_column(String(32))  # success | error
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
