from __future__ import annotations

import asyncio

import httpx

from app.worker import NotificationWorker


def test_test_delivery_enters_queue_and_is_consumed(app_context):
    app, client, settings = app_context
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        return httpx.Response(202, headers={"X-Request-Id": "req-1"}, text="accepted")

    response = client.post(
        "/api/v1/test-deliveries",
        json={
            "method": "POST",
            "url": "http://127.0.0.1:9000/webhook/test",
            "headers": {"X-Debug": "local-test"},
            "body": {"event": "order-paid", "order_id": 100},
            "timeout_seconds": 2,
        },
    )

    assert response.status_code == 202
    assert response.json()["status"] == "PENDING"
    task_id = response.json()["id"]
    queued = client.get(f"/api/v1/tasks/{task_id}").json()
    assert queued["is_test"] is True
    assert queued["channel"] == "manual-test"
    assert queued["max_attempts"] == 11

    worker = NotificationWorker(
        settings,
        app.state.session_factory,
        app.state.channel_registry,
        transport=httpx.MockTransport(handler),
    )
    assert asyncio.run(worker.run_once()) == 1

    delivered = client.get(f"/api/v1/tasks/{task_id}").json()
    assert delivered["status"] == "SUCCEEDED"
    assert delivered["attempts"][0]["http_status"] == 202
    assert delivered["attempts"][0]["response_excerpt"] == "accepted"
    assert captured == {
        "method": "POST",
        "url": "http://127.0.0.1:9000/webhook/test",
        "body": '{"event":"order-paid","order_id":100}',
    }


def test_test_delivery_rejects_host_outside_allowlist(app_context):
    _, client, _ = app_context

    response = client.post(
        "/api/v1/test-deliveries",
        json={
            "method": "POST",
            "url": "https://untrusted.example.com/webhook",
            "headers": {},
            "body": {},
        },
    )

    assert response.status_code == 422
    assert "不在测试白名单" in response.json()["detail"]


def test_test_delivery_rejects_sensitive_headers(app_context):
    _, client, _ = app_context

    response = client.post(
        "/api/v1/test-deliveries",
        json={
            "method": "POST",
            "url": "http://localhost:9000/webhook/test",
            "headers": {"Authorization": "Bearer secret"},
            "body": {},
        },
    )

    assert response.status_code == 422
    assert "不允许持久化敏感 Header" in response.json()["detail"]


def test_test_delivery_network_error_is_recorded_by_worker(app_context):
    app, client, settings = app_context

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    response = client.post(
        "/api/v1/test-deliveries",
        json={
            "method": "POST",
            "url": "http://localhost:9000/webhook/test",
            "headers": {},
            "body": {"ping": True},
            "max_retries": 0,
        },
    )
    task_id = response.json()["id"]
    worker = NotificationWorker(
        settings,
        app.state.session_factory,
        app.state.channel_registry,
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(worker.run_once())

    task = client.get(f"/api/v1/tasks/{task_id}").json()
    assert task["status"] == "DEAD"
    assert task["last_error"] == "ConnectError: connection refused; 已达到最大尝试次数 1"
    assert task["attempts"][0]["outcome"] == "DEAD"
