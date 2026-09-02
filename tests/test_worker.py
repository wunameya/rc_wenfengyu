from __future__ import annotations

import asyncio

import httpx

from app.worker import NotificationWorker


def test_worker_retries_then_succeeds(app_context, sample_payload, monkeypatch):
    app, client, settings = app_context
    monkeypatch.setenv("TEST_SUPPLIER_TOKEN", "top-secret")
    seen_headers = []
    responses = iter([503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("authorization"))
        status = next(responses)
        return httpx.Response(status, text=f"supplier-{status}")

    accepted = client.post("/api/v1/notifications", json=sample_payload).json()
    worker = NotificationWorker(
        settings,
        app.state.session_factory,
        app.state.channel_registry,
        transport=httpx.MockTransport(handler),
    )

    assert asyncio.run(worker.run_once()) == 1
    failed = client.get(f"/api/v1/tasks/{accepted['id']}").json()
    assert failed["status"] == "RETRY_WAIT"
    assert failed["last_http_status"] == 503
    assert failed["attempts"][0]["outcome"] == "RETRY_WAIT"

    assert asyncio.run(worker.run_once()) == 1
    succeeded = client.get(f"/api/v1/tasks/{accepted['id']}").json()
    assert succeeded["status"] == "SUCCEEDED"
    assert succeeded["total_attempts"] == 2
    assert len(succeeded["attempts"]) == 2
    assert seen_headers == ["Bearer top-secret", "Bearer top-secret"]
    assert succeeded["request_headers"]["Authorization"] == "***REDACTED***"


def test_non_retryable_response_becomes_dead_and_can_be_retried(
    app_context, sample_payload, monkeypatch
):
    app, client, settings = app_context
    monkeypatch.setenv("TEST_SUPPLIER_TOKEN", "top-secret")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid contact")

    accepted = client.post("/api/v1/notifications", json=sample_payload).json()
    worker = NotificationWorker(
        settings,
        app.state.session_factory,
        app.state.channel_registry,
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(worker.run_once())

    failed_list = client.get("/api/v1/tasks").json()
    assert failed_list["total"] == 1
    assert failed_list["items"][0]["status"] == "DEAD"
    detail = client.get(f"/api/v1/tasks/{accepted['id']}").json()
    assert detail["attempts"][0]["response_excerpt"] == "invalid contact"

    retried = client.post(f"/api/v1/tasks/{accepted['id']}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "PENDING"
    assert retried.json()["current_attempt"] == 0
    assert retried.json()["total_attempts"] == 1


def test_dashboard_summary(app_context, sample_payload, monkeypatch):
    app, client, settings = app_context
    monkeypatch.setenv("TEST_SUPPLIER_TOKEN", "top-secret")

    accepted = client.post("/api/v1/notifications", json=sample_payload)
    assert accepted.status_code == 202
    worker = NotificationWorker(
        settings,
        app.state.session_factory,
        app.state.channel_registry,
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="unauthorized")),
    )
    asyncio.run(worker.run_once())

    summary = client.get("/api/v1/dashboard/summary").json()
    assert summary["dead"] == 1
    assert summary["failed_attempts_24h"] == 1
    assert summary["success_rate_24h"] == 0.0

