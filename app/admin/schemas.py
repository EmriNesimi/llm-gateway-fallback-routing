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
