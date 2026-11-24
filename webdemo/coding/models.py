"""浏览器请求与可持久化的会话标识。"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Form(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionRequest(Form):
    directory: str = Field(min_length=1, max_length=4096)
    profile_id: str = Field(default="", max_length=150)


class TurnRequest(Form):
    submit_request_id: UUID
    text: str = Field(min_length=1, max_length=131072)


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
