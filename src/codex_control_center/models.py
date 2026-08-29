from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Locale = Literal["en-US", "zh-CN", "ja-JP", "ko-KR"]


class NotificationSettings(BaseModel):
    enabled: bool = False
    completed: bool = True
    attention: bool = True
    failure: bool = True
    remote_offline: bool = True
    context_pressure: bool = True
    while_focused: bool = False


class Settings(BaseModel):
    locale: Locale = "en-US"
    page_size: int = Field(default=50, ge=10, le=200)
    context_warning_percent: int = Field(default=85, ge=50, le=99)
    rate_limit_warning_percent: int = Field(default=85, ge=50, le=99)
    stale_minutes: int = Field(default=20, ge=2, le=1440)
    history_warning_mb: int = Field(default=2048, ge=64, le=1_000_000)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)


class HostInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    hostname: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=80)
    port: int = Field(default=22, ge=1, le=65535)
    identity_file: str | None = Field(default=None, max_length=1024)
    jump_host: str | None = Field(default=None, max_length=255)
    codex_home: str = Field(default="~/.codex", max_length=1024)
    workspace_roots: list[str] = Field(default_factory=list, max_length=32)
    enabled: bool = True
    enroll_host_key: bool = False
    poll_seconds: int = Field(default=8, ge=2, le=3600)
    manage_app_server: bool = False
    password: str | None = Field(default=None, exclude=True)

    @field_validator("name", "hostname", "username")
    @classmethod
    def no_control_chars(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(ch) < 32 for ch in value):
            raise ValueError("invalid control characters")
        return value

    @field_validator("workspace_roots")
    @classmethod
    def normalize_roots(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            value = value.strip()
            if value and value not in result:
                result.append(value)
        return result

    @model_validator(mode="after")
    def reject_passwords(self) -> "HostInput":
        if self.password:
            raise ValueError("password authentication is not supported; use ssh-agent or an identity file")
        return self


class Host(HostInput):
    id: str
    status: Literal["unknown", "online", "offline", "degraded"] = "unknown"
    last_seen_at: str | None = None
    last_error: str | None = None
    managed_app_server_pid: int | None = None
    created_at: str
    updated_at: str


class Session(BaseModel):
    key: str
    host_id: str
    source_session_id: str
    title: str
    cwd: str | None = None
    repository: str | None = None
    branch: str | None = None
    head: str | None = None
    dirty: bool | None = None
    ahead: int | None = None
    behind: int | None = None
    conflict: bool = False
    lifecycle: str = "unknown"
    stage: str = "unknown"
    interaction: str = "none"
    model: str | None = None
    tags: list[str] = Field(default_factory=list)
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    context_window: int | None = None
    context_percent: float | None = None
    rate_primary_percent: float | None = None
    rate_secondary_percent: float | None = None
    capabilities: list[str] = Field(default_factory=list)
    last_event_at: str
    updated_at: str


class Page(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


class ActionInput(BaseModel):
    text: str | None = Field(default=None, max_length=20_000)
    confirm: bool = False
