from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


class NotificationTask(Base):
    __tablename__ = "notification_tasks"
    __table_args__ = (
        UniqueConstraint("channel", "idempotency_key", name="uq_task_channel_biz_key"),
        Index("ix_task_dispatch", "status", "next_retry_at", "locked_until"),
        Index("ix_task_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    variables: Mapped[dict] = mapped_column(JSON, nullable=False)
    request_method: Mapped[str] = mapped_column(String(10), nullable=False)
    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    request_headers: Mapped[dict] = mapped_column(JSON, nullable=False)
    request_body: Mapped[Any] = mapped_column(JSON, nullable=True)
    request_timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=5)
    base_retry_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=10)
    max_retry_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=86400)

    total_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lock_token: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    last_http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    succeeded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    attempts: Mapped[list["DeliveryAttempt"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="DeliveryAttempt.id"
    )


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (Index("ix_attempt_task_id", "task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("notification_tasks.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    task: Mapped[NotificationTask] = relationship(back_populates="attempts")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

