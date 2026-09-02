from __future__ import annotations


def test_submit_is_persisted_and_idempotent(app_context, sample_payload):
    _, client, _ = app_context

    first = client.post("/api/v1/notifications", json=sample_payload)
    assert first.status_code == 202
    assert first.json()["status"] == "PENDING"
    assert first.json()["duplicated"] is False

    second = client.post("/api/v1/notifications", json=sample_payload)
    assert second.status_code == 202
    assert second.json() == {**first.json(), "duplicated": True}

    task = client.get(f"/api/v1/tasks/{first.json()['id']}").json()
    assert task["target_url"] == "https://supplier.example.test/hooks/order-paid"
    assert task["request_body"]["data"]["amount"] == 299
    assert task["request_headers"]["Authorization"] == "***REDACTED***"


def test_rejects_missing_template_variable(app_context, sample_payload):
    _, client, _ = app_context
    del sample_payload["variables"]["event_type"]

    response = client.post("/api/v1/notifications", json=sample_payload)

    assert response.status_code == 422
    assert "模板变量缺失" in response.json()["detail"]


def test_default_task_list_only_returns_failures(app_context, sample_payload):
    app, client, _ = app_context
    accepted = client.post("/api/v1/notifications", json=sample_payload).json()

    assert client.get("/api/v1/tasks").json()["total"] == 0
    all_pending = client.get("/api/v1/tasks?status=PENDING").json()
    assert all_pending["total"] == 1
    assert all_pending["items"][0]["id"] == accepted["id"]


def test_retry_requires_failed_state(app_context, sample_payload):
    _, client, _ = app_context
    accepted = client.post("/api/v1/notifications", json=sample_payload).json()

    response = client.post(f"/api/v1/tasks/{accepted['id']}/retry")

    assert response.status_code == 409

