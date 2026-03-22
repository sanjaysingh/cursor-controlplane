"""Shared Pydantic models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MessageTarget(BaseModel):
    """Routes agent questions back to the user (web / telegram)."""

    session_id: str
    conversation_id: str


class AgentSessionStatus(str, Enum):
    open = "open"
    closed = "closed"


class AgentActivity(str, Enum):
    idle = "idle"
    connecting = "connecting"
    running = "running"
    waiting_user = "waiting_user"
    error = "error"


class AgentSessionPublic(BaseModel):
    id: str
    channel: str
    channel_key: str
    repo_path: str
    repo_name: str = ""
    title: str = ""
    status: str
    activity: str
    acp_session_id: str | None = None
    model: str | None = Field(
        default=None,
        description="`agent --model` chosen at session creation; null = Auto / server default (config / env). Not mutable after create.",
    )
    created_at: str
    updated_at: str
    closed_at: str | None = None
    error_message: str | None = None
    output_preview: str = ""

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump()


class IncomingMessage(BaseModel):
    conversation_id: str
    channel: str
    text: str
    repo_path: str | None = None


class CloneGithubRepoRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name_with_owner: str = Field(
        ...,
        validation_alias=AliasChoices("name_with_owner", "nameWithOwner"),
    )

    @model_validator(mode="after")
    def _strip_nwo(self) -> CloneGithubRepoRequest:
        s = self.name_with_owner.strip()
        if not s:
            raise ValueError("nameWithOwner is required")
        self.name_with_owner = s
        return self


class CreateSessionRequest(BaseModel):
    repo_path: str
    title: str = ""
    model: str | None = None

    @model_validator(mode="after")
    def _normalize_optional_model(self) -> CreateSessionRequest:
        if self.model is not None and not str(self.model).strip():
            self.model = None
        return self


class SendSessionMessageRequest(BaseModel):
    text: str


class AnswerQuestionRequest(BaseModel):
    answer: str
    option_index: int | None = None


# Legacy API compatibility (optional)
class CreateRunRequest(BaseModel):
    conversation_id: str = "web:default"
    repo_path: str
    prompt: str
