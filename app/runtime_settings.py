from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from .models import SystemSetting


WORKER_PROCESSES_KEY = "worker_processes"
MAX_DELIVERY_RETRIES_KEY = "max_delivery_retries"


def get_worker_processes(
    session_factory: sessionmaker[Session], default: int, maximum: int
) -> int:
    with session_factory() as session:
        setting = session.get(SystemSetting, WORKER_PROCESSES_KEY)
        if setting is None:
            return min(maximum, max(1, default))
        try:
            return min(maximum, max(1, int(setting.value)))
        except ValueError:
            return min(maximum, max(1, default))


def set_worker_processes(
    session: Session, process_count: int, maximum: int
) -> int:
    if not 1 <= process_count <= maximum:
        raise ValueError(f"Worker 进程数必须在 1 到 {maximum} 之间")
    setting = session.get(SystemSetting, WORKER_PROCESSES_KEY)
    if setting is None:
        setting = SystemSetting(key=WORKER_PROCESSES_KEY, value=str(process_count))
        session.add(setting)
    else:
        setting.value = str(process_count)
    session.commit()
    return process_count


def get_max_delivery_retries(
    session_factory: sessionmaker[Session], default: int, maximum: int = 10
) -> int:
    with session_factory() as session:
        setting = session.get(SystemSetting, MAX_DELIVERY_RETRIES_KEY)
        if setting is None:
            return min(maximum, max(0, default))
        try:
            return min(maximum, max(0, int(setting.value)))
        except ValueError:
            return min(maximum, max(0, default))


def set_max_delivery_retries(
    session: Session, retry_count: int, maximum: int = 10
) -> int:
    if not 0 <= retry_count <= maximum:
        raise ValueError(f"最大重试次数必须在 0 到 {maximum} 之间")
    setting = session.get(SystemSetting, MAX_DELIVERY_RETRIES_KEY)
    if setting is None:
        setting = SystemSetting(key=MAX_DELIVERY_RETRIES_KEY, value=str(retry_count))
        session.add(setting)
    else:
        setting.value = str(retry_count)
    session.commit()
    return retry_count
