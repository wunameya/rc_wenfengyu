from __future__ import annotations

import argparse
import asyncio
import email.utils
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session, sessionmaker

from .channels import Channel, ChannelError, ChannelRegistry
from .database import create_database, init_database
from .models import NotificationTask, utcnow
from .service import AttemptResult, claim_tasks, finish_attempt
from .settings import Settings


logger = logging.getLogger(__name__)
RETRYABLE_HTTP_STATUSES = {408, 429}


def _retry_delay_seconds(
    channel: Channel, attempt: int, response: Optional[httpx.Response]
) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    parsed = email.utils.parsedate_to_datetime(retry_after)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
    base = min(channel.max_retry_seconds, channel.base_retry_seconds * (2 ** (attempt - 1)))
    return base * random.uniform(0.8, 1.2)


def _result(
    *,
    outcome: str,
    started_at: datetime,
    http_status: Optional[int] = None,
    error: Optional[str] = None,
    response_excerpt: Optional[str] = None,
    next_retry_at: Optional[datetime] = None,
) -> AttemptResult:
    finished_at = utcnow()
    return AttemptResult(
        outcome=outcome,
        http_status=http_status,
        error=error,
        response_excerpt=response_excerpt,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
        next_retry_at=next_retry_at,
    )


async def deliver_task(
    client: httpx.AsyncClient,
    task: NotificationTask,
    channel: Channel,
    response_excerpt_length: int,
) -> AttemptResult:
    started_at = utcnow()
    try:
        headers = dict(task.request_headers)
        headers.update(channel.secret_values())
    except ChannelError as exc:
        return _result(outcome="DEAD", started_at=started_at, error=str(exc))

    response: Optional[httpx.Response] = None
    try:
        response = await client.request(
            method=task.request_method,
            url=task.target_url,
            headers=headers,
            json=task.request_body,
            timeout=channel.timeout_seconds,
        )
        excerpt = response.text[:response_excerpt_length]
        if 200 <= response.status_code < 300:
            return _result(
                outcome="SUCCEEDED",
                started_at=started_at,
                http_status=response.status_code,
                response_excerpt=excerpt,
            )

        retryable = response.status_code in RETRYABLE_HTTP_STATUSES or response.status_code >= 500
        error = f"HTTP {response.status_code}"
    except httpx.RequestError as exc:
        excerpt = None
        retryable = True
        error = f"{type(exc).__name__}: {exc}"

    if retryable and task.current_attempt < task.max_attempts:
        delay = _retry_delay_seconds(channel, task.current_attempt, response)
        return _result(
            outcome="RETRY_WAIT",
            started_at=started_at,
            http_status=response.status_code if response is not None else None,
            error=error,
            response_excerpt=excerpt,
            next_retry_at=utcnow() + timedelta(seconds=delay),
        )
    if retryable:
        error = f"{error}; 已达到最大尝试次数 {task.max_attempts}"
    return _result(
        outcome="DEAD",
        started_at=started_at,
        http_status=response.status_code if response is not None else None,
        error=error,
        response_excerpt=excerpt,
    )


class NotificationWorker:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        registry: ChannelRegistry,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.registry = registry
        self.transport = transport

    async def _deliver_claimed(
        self,
        client: httpx.AsyncClient,
        task: NotificationTask,
        global_semaphore: asyncio.Semaphore,
        channel_semaphores: dict[str, asyncio.Semaphore],
    ) -> None:
        try:
            channel = self.registry.get(task.channel)
        except ChannelError as exc:
            finish_attempt(
                self.session_factory,
                task,
                _result(outcome="DEAD", started_at=utcnow(), error=str(exc)),
            )
            return

        async with global_semaphore, channel_semaphores[task.channel]:
            result = await deliver_task(
                client, task, channel, self.settings.response_excerpt_length
            )
            saved = finish_attempt(self.session_factory, task, result)
            if not saved:
                logger.warning("任务 %s 的租约已失效，忽略本次投递结果", task.id)
            elif result.outcome != "SUCCEEDED":
                logger.warning(
                    "任务 %s 投递失败 outcome=%s status=%s error=%s",
                    task.id,
                    result.outcome,
                    result.http_status,
                    result.error,
                )

    async def run_once(self) -> int:
        tasks = claim_tasks(
            self.session_factory,
            self.settings.worker_batch_size,
            self.settings.worker_lease_seconds,
        )
        if not tasks:
            return 0
        global_semaphore = asyncio.Semaphore(self.settings.worker_concurrency)
        channel_semaphores = {
            name: asyncio.Semaphore(self.registry.get(name).max_concurrency)
            for name in self.registry.names()
        }
        async with httpx.AsyncClient(
            transport=self.transport, follow_redirects=False
        ) as client:
            await asyncio.gather(
                *(
                    self._deliver_claimed(
                        client, task, global_semaphore, channel_semaphores
                    )
                    for task in tasks
                )
            )
        return len(tasks)

    async def run_forever(self) -> None:
        logger.info("通知 Worker 已启动")
        while True:
            try:
                processed = await self.run_once()
            except Exception:
                logger.exception("Worker 轮询失败")
                processed = 0
            if not processed:
                await asyncio.sleep(self.settings.worker_poll_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="HTTP 通知投递 Worker")
    parser.add_argument("--once", action="store_true", help="只处理一批任务")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    settings = Settings.from_env()
    engine, session_factory = create_database(settings.database_url)
    init_database(engine)
    registry = ChannelRegistry.from_file(settings.channel_config_path)
    worker = NotificationWorker(settings, session_factory, registry)
    if args.once:
        processed = asyncio.run(worker.run_once())
        logger.info("本次处理 %d 个任务", processed)
    else:
        try:
            asyncio.run(worker.run_forever())
        except KeyboardInterrupt:
            logger.info("通知 Worker 已停止")


if __name__ == "__main__":
    main()

