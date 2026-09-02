from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .channels import ChannelRegistry, render_channel
from .models import DeliveryAttempt, NotificationTask, utcnow
from .schemas import DashboardSummary, NotificationCreate, TaskDetail, TaskList, TaskView


FAILED_STATUSES = ("RETRY_WAIT", "DEAD")
DISPATCHABLE_STATUSES = ("PENDING", "RETRY_WAIT")


class TaskNotFoundError(LookupError):
    pass


class TaskConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class AttemptResult:
    outcome: str
    http_status: Optional[int]
    error: Optional[str]
    response_excerpt: Optional[str]
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    next_retry_at: Optional[datetime] = None


def create_notification(
    session_factory: sessionmaker[Session],
    registry: ChannelRegistry,
    payload: NotificationCreate,
    max_retries: int = 10,
) -> tuple[NotificationTask, bool]:
    channel = registry.get(payload.channel)
    target_url, request_headers, request_body = render_channel(channel, payload.variables)
    now = utcnow()
    task = NotificationTask(
        id=str(uuid.uuid4()),
        channel=payload.channel,
        idempotency_key=payload.idempotency_key,
        status="PENDING",
        is_test=False,
        variables=payload.variables,
        request_method=channel.method,
        target_url=target_url,
        request_headers=request_headers,
        request_body=request_body,
        request_timeout_seconds=channel.timeout_seconds,
        base_retry_seconds=channel.base_retry_seconds,
        max_retry_seconds=channel.max_retry_seconds,
        max_attempts=min(channel.max_attempts, max_retries + 1),
        next_retry_at=now,
        created_at=now,
        updated_at=now,
    )
    with session_factory() as session:
        try:
            session.add(task)
            session.commit()
            return task, False
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(NotificationTask).where(
                    NotificationTask.channel == payload.channel,
                    NotificationTask.idempotency_key == payload.idempotency_key,
                )
            )
            if existing is None:
                raise
            return existing, True


def list_tasks(
    session: Session,
    *,
    statuses: list[str],
    channel: Optional[str],
    query: Optional[str],
    page: int,
    page_size: int,
) -> TaskList:
    filters = []
    if statuses:
        filters.append(NotificationTask.status.in_(statuses))
    if channel:
        filters.append(NotificationTask.channel == channel)
    if query:
        pattern = f"%{query}%"
        filters.append(
            or_(
                NotificationTask.id.ilike(pattern),
                NotificationTask.idempotency_key.ilike(pattern),
                NotificationTask.target_url.ilike(pattern),
                NotificationTask.last_error.ilike(pattern),
            )
        )

    total = session.scalar(select(func.count()).select_from(NotificationTask).where(*filters)) or 0
    tasks = session.scalars(
        select(NotificationTask)
        .where(*filters)
        .order_by(NotificationTask.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return TaskList(
        items=[TaskView.model_validate(task) for task in tasks],
        total=total,
        page=page,
        page_size=page_size,
    )


def get_task_detail(session: Session, task_id: str) -> TaskDetail:
    task = session.scalar(
        select(NotificationTask)
        .options(selectinload(NotificationTask.attempts))
        .where(NotificationTask.id == task_id)
    )
    if task is None:
        raise TaskNotFoundError(task_id)
    return TaskDetail.model_validate(task)


def retry_task(
    session: Session,
    task_id: str,
    registry: ChannelRegistry,
    max_retries: int = 10,
) -> TaskView:
    task = session.get(NotificationTask, task_id)
    if task is None:
        raise TaskNotFoundError(task_id)
    if task.status not in FAILED_STATUSES:
        raise TaskConflictError(f"状态为 {task.status} 的任务不能人工重试")
    if task.is_test:
        request_method = task.request_method
        target_url = task.target_url
        request_headers = task.request_headers
        request_body = task.request_body
        max_attempts = min(task.max_attempts, max_retries + 1)
    else:
        channel = registry.get(task.channel)
        request_method = channel.method
        target_url, request_headers, request_body = render_channel(channel, task.variables)
        max_attempts = min(channel.max_attempts, max_retries + 1)
        task.request_timeout_seconds = channel.timeout_seconds
        task.base_retry_seconds = channel.base_retry_seconds
        task.max_retry_seconds = channel.max_retry_seconds
    now = utcnow()
    task.status = "PENDING"
    task.request_method = request_method
    task.target_url = target_url
    task.request_headers = request_headers
    task.request_body = request_body
    task.max_attempts = max_attempts
    task.current_attempt = 0
    task.next_retry_at = now
    task.locked_until = None
    task.lock_token = None
    task.updated_at = now
    session.commit()
    return TaskView.model_validate(task)


def dashboard_summary(session: Session) -> DashboardSummary:
    counts = dict(
        session.execute(
            select(NotificationTask.status, func.count(NotificationTask.id))
            .where(NotificationTask.status.in_(("RETRY_WAIT", "DEAD", "PROCESSING")))
            .group_by(NotificationTask.status)
        ).all()
    )
    since = utcnow() - timedelta(hours=24)
    attempt_counts = session.execute(
        select(
            func.count(DeliveryAttempt.id),
            func.sum(case((DeliveryAttempt.outcome == "SUCCEEDED", 1), else_=0)),
            func.sum(case((DeliveryAttempt.outcome != "SUCCEEDED", 1), else_=0)),
        ).where(DeliveryAttempt.finished_at >= since)
    ).one()
    total_attempts = int(attempt_counts[0] or 0)
    succeeded = int(attempt_counts[1] or 0)
    failed = int(attempt_counts[2] or 0)
    return DashboardSummary(
        retry_wait=counts.get("RETRY_WAIT", 0),
        dead=counts.get("DEAD", 0),
        processing=counts.get("PROCESSING", 0),
        failed_attempts_24h=failed,
        success_rate_24h=round(succeeded / total_attempts * 100, 2) if total_attempts else None,
    )


def claim_tasks(
    session_factory: sessionmaker[Session], batch_size: int, lease_seconds: int
) -> list[NotificationTask]:
    now = utcnow()
    lease_until = now + timedelta(seconds=lease_seconds)
    eligible = or_(
        and_(
            NotificationTask.status.in_(DISPATCHABLE_STATUSES),
            NotificationTask.next_retry_at <= now,
        ),
        and_(
            NotificationTask.status == "PROCESSING",
            NotificationTask.locked_until < now,
            NotificationTask.current_attempt < NotificationTask.max_attempts,
        ),
    )
    claimed: list[NotificationTask] = []
    with session_factory() as session:
        session.execute(
            update(NotificationTask)
            .where(
                NotificationTask.status == "PROCESSING",
                NotificationTask.locked_until < now,
                NotificationTask.current_attempt >= NotificationTask.max_attempts,
            )
            .values(
                status="DEAD",
                locked_until=None,
                lock_token=None,
                last_error="处理租约过期且已达到最大尝试次数",
                updated_at=now,
            )
        )
        session.commit()

        if session.bind is not None and session.bind.dialect.name == "mysql":
            tasks = session.scalars(
                select(NotificationTask)
                .where(eligible)
                .order_by(NotificationTask.next_retry_at, NotificationTask.created_at)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            ).all()
            for task in tasks:
                task.status = "PROCESSING"
                task.locked_until = lease_until
                task.lock_token = str(uuid.uuid4())
                task.current_attempt += 1
                task.total_attempts += 1
                task.updated_at = now
            session.commit()
            return tasks

        # SQLite 仅用于本地测试。逐条条件更新使多个领取者即使读取到相同
        # 候选任务，也只有一个能将其原子地转换为 PROCESSING。
        candidate_ids = session.scalars(
            select(NotificationTask.id)
            .where(eligible)
            .order_by(NotificationTask.next_retry_at, NotificationTask.created_at)
            .limit(batch_size)
        ).all()
        for task_id in candidate_ids:
            token = str(uuid.uuid4())
            result = session.execute(
                update(NotificationTask)
                .where(NotificationTask.id == task_id, eligible)
                .values(
                    status="PROCESSING",
                    locked_until=lease_until,
                    lock_token=token,
                    current_attempt=NotificationTask.current_attempt + 1,
                    total_attempts=NotificationTask.total_attempts + 1,
                    updated_at=now,
                )
            )
            if result.rowcount == 1:
                session.commit()
                task = session.get(NotificationTask, task_id)
                if task is not None:
                    claimed.append(task)
            else:
                session.rollback()
    return claimed


def finish_attempt(
    session_factory: sessionmaker[Session], task: NotificationTask, result: AttemptResult
) -> bool:
    with session_factory() as session:
        owned_task = session.scalar(
            select(NotificationTask).where(
                NotificationTask.id == task.id,
                NotificationTask.status == "PROCESSING",
                NotificationTask.lock_token == task.lock_token,
            )
        )
        if owned_task is None:
            return False

        session.add(
            DeliveryAttempt(
                task_id=task.id,
                attempt_number=task.total_attempts,
                outcome=result.outcome,
                http_status=result.http_status,
                error=result.error,
                response_excerpt=result.response_excerpt,
                started_at=result.started_at,
                finished_at=result.finished_at,
                duration_ms=result.duration_ms,
            )
        )
        owned_task.last_http_status = result.http_status
        owned_task.last_error = result.error
        owned_task.locked_until = None
        owned_task.lock_token = None
        owned_task.updated_at = result.finished_at
        if result.outcome == "SUCCEEDED":
            owned_task.status = "SUCCEEDED"
            owned_task.succeeded_at = result.finished_at
        elif result.outcome == "RETRY_WAIT":
            owned_task.status = "RETRY_WAIT"
            owned_task.next_retry_at = result.next_retry_at or result.finished_at
        else:
            owned_task.status = "DEAD"
        session.commit()
        return True
