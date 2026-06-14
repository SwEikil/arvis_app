from pydantic import BaseModel, Field


class ActionIntent(BaseModel):
    action: str
    target: str
    risk: str
    need_confirmation: bool
    params: dict[str, object] = Field(default_factory=dict)


class MemoryIntent(BaseModel):
    type: str
    content: str


class ParsedAssistantResponse(BaseModel):
    message: str
    action_intent: ActionIntent | None = None
    memory_intent: MemoryIntent | None = None
