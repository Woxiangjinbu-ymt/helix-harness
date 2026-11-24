"""浏览器请求与可持久化的会话标识。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Form(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionRequest(Form):
    directory: str = Field(min_length=1, max_length=4096)
    profile_id: str = Field(default="", max_length=150)


class TurnRequest(Form):
    submit_request_id: UUID
    text: str = Field(min_length=1, max_length=131072)


class ProfileRequest(Form):
    profile_id: str = ""
    revision: int = 0
    provider: str = Field(min_length=1, max_length=100)
    protocol: Literal["anthropic", "openai", "openai-compat"]
    endpoint: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=160)
    api_key: SecretStr | None = None
    permission_mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] = (
        "default"
    )
    command_timeout: int = Field(default=3600, ge=1, le=86400)
    max_iterations: int = Field(default=40, ge=1, le=1000)
    context_window: int = Field(default=128000, ge=1024, le=4000000)


class SessionOptions(Form):
    profile_id: str = ""
    skills: list[str] = Field(default_factory=list, max_length=100)
    mcp: list[str] = Field(default_factory=list, max_length=100)
    hooks: list[str] = Field(default_factory=list, max_length=100)


class CancelRequest(Form):
    request_id: UUID


class PermissionAnswer(Form):
    request_id: UUID
    decision: Literal["allow", "allow_always", "deny"]
    feedback: str = Field(default="", max_length=4096)


ACTIVE = ("accepted", "running", "waiting_permission", "cancelling")
TERMINAL = ("completed", "failed", "interrupted", "cancelled")


class Conflict(ValueError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details
