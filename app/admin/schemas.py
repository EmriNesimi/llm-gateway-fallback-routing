import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateKeyRequest(BaseModel):
    # max_length matches ApiKeyRecord.team / AuditLogEntry.team's
    # String(255) column. Without this, SQLite (used in dev/tests) silently
    # accepts an oversized value — VARCHAR length isn't enforced there — but
    # the same request against Postgres in production is a hard INSERT
    # failure (StringDataRightTruncationError), a "works in dev, breaks in
    # prod" gap only caught by testing against real Postgres (see decision
    # 007 and the CI job that does exactly that).
    team: str = Field(max_length=255)

    @field_validator("team")
    @classmethod
    def _team_must_be_meaningful(cls, value: str) -> str:
        # Without this, "" or "   " passed validation and silently produced
        # a usable key with no meaningful team attribution — defeating the
        # entire point of the admin API's per-team audit log/spend tracking.
        stripped = value.strip()
        if not stripped:
            raise ValueError("team must not be empty or whitespace-only")
        return stripped


class CreateKeyResponse(BaseModel):
    api_key: str
    team: str


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    team: str
    created_at: datetime.datetime
    revoked: bool


class AuditLogEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime.datetime
    request_id: str
    api_key_hash: str
    team: str
    requested_model: str
    provider: str
    outcome: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float


class AdminAuditEntryOut(BaseModel):
    """A key issuance or revocation. `admin_key_hash` identifies which admin
    credential acted without disclosing it — useful for spotting activity from
    one that should have been rotated."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime.datetime
    request_id: str
    action: str
    key_id: int
    team: str
    admin_key_hash: str
