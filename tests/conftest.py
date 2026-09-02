from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import create_app
from app.settings import Settings


@pytest.fixture
def app_context(tmp_path: Path):
    config_path = tmp_path / "channels.json"
    config_path.write_text(
        json.dumps(
            {
                "channels": {
                    "test-channel": {
                        "method": "POST",
                        "url": "https://supplier.example.test/hooks/{{ event_type }}",
                        "headers": {
                            "Content-Type": "application/json",
                            "X-Event": "{{ event_type }}",
                            "Authorization": "should-not-leak",
                        },
                        "secret_headers": {
                            "Authorization": {"env": "TEST_SUPPLIER_TOKEN", "prefix": "Bearer "}
                        },
                        "body": {"event_id": "{{ event_id }}", "data": "{{ data }}"},
                        "timeout_seconds": 1,
                        "max_retries": 2,
                        "max_concurrency": 2,
                        "base_retry_seconds": 0,
                        "max_retry_seconds": 1,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        channel_config_path=config_path,
        worker_poll_interval_seconds=0.01,
        worker_batch_size=10,
        worker_concurrency=5,
        worker_lease_seconds=10,
        response_excerpt_length=2000,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        yield app, client, settings


@pytest.fixture
def sample_payload():
    return {
        "channel": "test-channel",
        "idempotency_key": "order-100-paid",
        "variables": {
            "event_id": "evt-100",
            "event_type": "order-paid",
            "data": {"order_id": "100", "amount": 299},
        },
    }

