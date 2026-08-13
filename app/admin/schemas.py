import datetime

from pydantic import BaseModel, ConfigDict, field_validator


class CreateKeyRequest(BaseModel):
    team: str

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
