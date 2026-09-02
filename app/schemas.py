from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class NotificationCreate(BaseModel):
    channel: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)
    variables: dict[str, Any] = Field(default_factory=dict)


class NotificationAccepted(BaseModel):
    id: str
    status: str
    duplicated: bool = False


class AttemptView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    attempt_number: int
    outcome: str
    http_status: Optional[int]
    error: Optional[str]
    response_excerpt: Optional[str]
    started_at: datetime
    finished_at: datetime
    duration_ms: int


class TaskView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel: str
    idempotency_key: str
    status: str
    request_method: str
    target_url: str
    request_headers: dict[str, Any]
    request_body: Optional[Any]
    total_attempts: int
    current_attempt: int
    max_attempts: int
    next_retry_at: datetime
    last_http_status: Optional[int]
    last_error: Optional[str]
    created_at: datetime
    updated_at: datetime
    succeeded_at: Optional[datetime]


class TaskDetail(TaskView):
    variables: dict[str, Any]
    attempts: list[AttemptView]


class TaskList(BaseModel):
    items: list[TaskView]
    total: int
    page: int
    page_size: int


class DashboardSummary(BaseModel):
    retry_wait: int
    dead: int
    processing: int
    failed_attempts_24h: int
    success_rate_24h: Optional[float]

