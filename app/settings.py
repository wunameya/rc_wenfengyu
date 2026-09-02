from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./notification.db"
    channel_config_path: Path = Path("config/channels.json")
    worker_poll_interval_seconds: float = 1.0
    worker_batch_size: int = 20
    worker_concurrency: int = 10
    worker_lease_seconds: int = 60
    response_excerpt_length: int = 2000
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)

    @classmethod
    def from_env(cls) -> "Settings":
        origins = os.getenv("APP_CORS_ORIGINS", "http://localhost:5173")
        return cls(
            database_url=os.getenv("APP_DATABASE_URL", cls.database_url),
            channel_config_path=Path(
                os.getenv("APP_CHANNEL_CONFIG_PATH", str(cls.channel_config_path))
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
        )

