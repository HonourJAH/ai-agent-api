from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = Field(
        default=None,
        description="Omit to start a new session — the API generates and returns one.",
    )
