from __future__ import annotations

import uuid
from urllib.parse import urlparse

from sqlalchemy.orm import Session, sessionmaker

from .channels import SENSITIVE_HEADERS
from .models import NotificationTask, utcnow
from .schemas import NotificationAccepted, TestDeliveryRequest


class TestDeliveryValidationError(ValueError):
    pass


def validate_test_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TestDeliveryValidationError("测试地址必须是合法的 HTTP(S) URL")
    if parsed.username or parsed.password:
        raise TestDeliveryValidationError("测试地址不能包含用户名或密码")
    if parsed.hostname.lower() not in set(allowed_hosts):
        raise TestDeliveryValidationError(
            f"目标主机 {parsed.hostname} 不在测试白名单 APP_TEST_ALLOWED_HOSTS 中"
        )


def enqueue_test_delivery(
    session_factory: sessionmaker[Session],
    payload: TestDeliveryRequest,
    allowed_hosts: tuple[str, ...],
    max_retries: int = 10,
) -> NotificationAccepted:
    validate_test_url(payload.url, allowed_hosts)
    sensitive = sorted(
        header for header in payload.headers if header.lower() in SENSITIVE_HEADERS
    )
    if sensitive:
        raise TestDeliveryValidationError(
            f"临时测试不允许持久化敏感 Header: {', '.join(sensitive)}；请使用渠道密钥配置"
        )
    now = utcnow()
    task_id = str(uuid.uuid4())
    task = NotificationTask(
        id=task_id,
        channel="manual-test",
        idempotency_key=f"manual-test:{task_id}",
        status="PENDING",
        is_test=True,
        variables={},
        request_method=payload.method,
        target_url=payload.url,
        request_headers=payload.headers,
        request_body=payload.body,
        request_timeout_seconds=payload.timeout_seconds,
        base_retry_seconds=10,
        max_retry_seconds=300,
        max_attempts=min(payload.max_retries, max_retries) + 1,
        next_retry_at=now,
        created_at=now,
        updated_at=now,
    )
    with session_factory() as session:
        session.add(task)
        session.commit()
    return NotificationAccepted(id=task.id, status=task.status, duplicated=False)

