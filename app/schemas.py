from pydantic import BaseModel


class ChatMessageIn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessageIn]


class ChatResponseOut(BaseModel):
    content: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
