from __future__ import annotations

import argparse
import asyncio
import email.utils
import logging
import multiprocessing
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session, sessionmaker

from .channels import Channel, ChannelError, ChannelRegistry
from .database import create_database, init_database
from .models import NotificationTask, utcnow
from .runtime_settings import get_worker_processes, set_worker_processes
from .service import AttemptResult, claim_tasks, finish_attempt
from .settings import Settings


logger = logging.getLogger(__name__)
RETRYABLE_HTTP_STATUSES = {408, 429}
LOG_FORMAT = "%(asctime)s %(levelname)s %(processName)s %(name)s %(message)s"


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
        if task.is_test:
            channel = Channel(
                name=task.channel,
                method=task.request_method,
                url=task.target_url,
                headers=task.request_headers,
                secret_headers={},
                body=task.request_body,
                timeout_seconds=task.request_timeout_seconds,
                max_attempts=task.max_attempts,
                max_concurrency=self.settings.worker_concurrency,
                base_retry_seconds=task.base_retry_seconds,
                max_retry_seconds=task.max_retry_seconds,
            )
        else:
            try:
                channel = self.registry.get(task.channel)
            except ChannelError as exc:
                finish_attempt(
                    self.session_factory,
                    task,
                    _result(outcome="DEAD", started_at=utcnow(), error=str(exc)),
                )
                return

        async with global_semaphore:
            channel_semaphore = channel_semaphores.get(task.channel)
            if channel_semaphore is None:
                result = await deliver_task(
                    client, task, channel, self.settings.response_excerpt_length
                )
            else:
                async with channel_semaphore:
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

    async def run_forever(
        self, stop_event: Optional[multiprocessing.synchronize.Event] = None
    ) -> None:
        logger.info("通知 Worker 已启动 pid=%s", multiprocessing.current_process().pid)
        while stop_event is None or not stop_event.is_set():
            try:
                processed = await self.run_once()
            except Exception:
                logger.exception("Worker 轮询失败")
                processed = 0
            if not processed:
                await asyncio.sleep(self.settings.worker_poll_interval_seconds)


def _run_worker_process(
    settings: Settings,
    run_once: bool,
    worker_number: int,
    stop_event: Optional[multiprocessing.synchronize.Event] = None,
) -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    engine, session_factory = create_database(settings.database_url)
    registry = ChannelRegistry.from_file(settings.channel_config_path)
    worker = NotificationWorker(settings, session_factory, registry)
    try:
        if run_once:
            processed = asyncio.run(worker.run_once())
            logger.info("Worker %d 本次处理 %d 个任务", worker_number, processed)
        else:
            asyncio.run(worker.run_forever(stop_event))
    except KeyboardInterrupt:
        logger.info("Worker %d 已停止", worker_number)
    finally:
        engine.dispose()


class WorkerSupervisor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.context = multiprocessing.get_context("spawn")
        self.processes: list[tuple[multiprocessing.Process, multiprocessing.synchronize.Event]] = []
        self.next_worker_number = 1

    def _start_one(self) -> None:
        stop_event = self.context.Event()
        worker_number = self.next_worker_number
        self.next_worker_number += 1
        process = self.context.Process(
            target=_run_worker_process,
            args=(self.settings, False, worker_number, stop_event),
            name=f"notification-worker-{worker_number}",
        )
        process.start()
        self.processes.append((process, stop_event))
        logger.info("已启动 %s pid=%s", process.name, process.pid)

    @staticmethod
    def _stop_one(
        process: multiprocessing.Process, stop_event: multiprocessing.synchronize.Event
    ) -> None:
        stop_event.set()
        process.join(timeout=60)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)

    def reconcile(self) -> int:
        alive = []
        for process, stop_event in self.processes:
            if process.is_alive():
                alive.append((process, stop_event))
            else:
                process.join(timeout=0)
                logger.warning("%s 已退出 exitcode=%s", process.name, process.exitcode)
        self.processes = alive

        engine, session_factory = create_database(self.settings.database_url)
        try:
            desired = get_worker_processes(
                session_factory,
                self.settings.worker_processes,
                self.settings.max_worker_processes,
            )
        finally:
            engine.dispose()

        while len(self.processes) < desired:
            self._start_one()
        while len(self.processes) > desired:
            process, stop_event = self.processes.pop()
            logger.info("正在停止 %s pid=%s", process.name, process.pid)
            self._stop_one(process, stop_event)
        return desired

    def stop_all(self) -> None:
        for process, stop_event in self.processes:
            self._stop_one(process, stop_event)
        self.processes.clear()

    def run_forever(self) -> None:
        try:
            while True:
                desired = self.reconcile()
                logger.debug("Worker 目标进程数=%d 当前进程数=%d", desired, len(self.processes))
                time.sleep(self.settings.worker_config_poll_interval_seconds)
        except KeyboardInterrupt:
            logger.info("正在停止所有通知 Worker")
        finally:
            self.stop_all()


def run_worker_processes(settings: Settings, process_count: int, run_once: bool = False) -> None:
    if process_count < 1:
        raise ValueError("worker 进程数必须至少为 1")
    if process_count > 1 and settings.database_url.startswith("sqlite"):
        raise ValueError("多 Worker 不支持 SQLite，请配置 MySQL")
    if process_count == 1:
        _run_worker_process(settings, run_once, 1)
        return

    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_run_worker_process,
            args=(settings, True, index + 1),
            name=f"notification-worker-{index + 1}",
        )
        for index in range(process_count)
    ]
    for process in processes:
        process.start()
    logger.info("已启动 %d 个通知 Worker 进程", process_count)

    for process in processes:
        process.join()
    failed = [process for process in processes if process.exitcode != 0]
    if failed:
        names = ", ".join(process.name for process in failed)
        raise RuntimeError(f"Worker 子进程异常退出: {names}")


def main() -> None:
    parser = argparse.ArgumentParser(description="HTTP 通知投递 Worker")
    parser.add_argument("--once", action="store_true", help="只处理一批任务")
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Worker 进程数；覆盖 APP_WORKER_PROCESSES",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    settings = Settings.from_env()
    engine, session_factory = create_database(settings.database_url)
    init_database(engine)
    if args.workers is not None:
        with session_factory() as session:
            set_worker_processes(session, args.workers, settings.max_worker_processes)
    engine.dispose()
    process_count = args.workers if args.workers is not None else settings.worker_processes
    if args.once:
        run_worker_processes(settings, process_count, True)
    else:
        WorkerSupervisor(settings).run_forever()


if __name__ == "__main__":
    main()

