import datetime

from pydantic import BaseModel, ConfigDict


class CreateKeyRequest(BaseModel):
    team: str


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
    api_key_hash: str
    team: str
    requested_model: str
    provider: str
    outcome: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
