from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str = (
        "mysql+pymysql://notification:notification@127.0.0.1:3306/notification"
        "?charset=utf8mb4"
    )
    channel_config_path: Path = Path("config/channels.json")
    worker_processes: int = 2
    max_worker_processes: int = 10
    default_max_retries: int = 10
    worker_config_poll_interval_seconds: float = 2.0
    worker_poll_interval_seconds: float = 1.0
    worker_batch_size: int = 20
    worker_concurrency: int = 10
    worker_lease_seconds: int = 60
    response_excerpt_length: int = 2000
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)
    test_allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        origins = os.getenv("APP_CORS_ORIGINS", "http://localhost:5173")
        test_allowed_hosts = os.getenv(
            "APP_TEST_ALLOWED_HOSTS", "localhost,127.0.0.1,::1"
        )
        return cls(
            database_url=os.getenv("APP_DATABASE_URL", cls.database_url),
            channel_config_path=Path(
                os.getenv("APP_CHANNEL_CONFIG_PATH", str(cls.channel_config_path))
            ),
            worker_processes=min(
                10, max(1, int(os.getenv("APP_WORKER_PROCESSES", "2")))
            ),
            max_worker_processes=10,
            default_max_retries=min(
                10, max(0, int(os.getenv("APP_MAX_DELIVERY_RETRIES", "10")))
            ),
            worker_config_poll_interval_seconds=float(
                os.getenv("APP_WORKER_CONFIG_POLL_INTERVAL_SECONDS", "2")
            ),
            worker_poll_interval_seconds=float(
                os.getenv("APP_WORKER_POLL_INTERVAL_SECONDS", "1")
            ),
            worker_batch_size=int(os.getenv("APP_WORKER_BATCH_SIZE", "20")),
            worker_concurrency=int(os.getenv("APP_WORKER_CONCURRENCY", "10")),
            worker_lease_seconds=int(os.getenv("APP_WORKER_LEASE_SECONDS", "60")),
            response_excerpt_length=int(
                os.getenv("APP_RESPONSE_EXCERPT_LENGTH", "2000")
            ),
            cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
            test_allowed_hosts=tuple(
                host.strip().lower()
                for host in test_allowed_hosts.split(",")
                if host.strip()
            ),
        )

