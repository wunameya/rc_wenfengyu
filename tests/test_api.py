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


def test_settings_allow_manual_worker_process_count(monkeypatch):
    from app.settings import Settings

    monkeypatch.setenv("APP_WORKER_PROCESSES", "4")

    assert Settings.from_env().worker_processes == 4


def test_worker_settings_can_be_changed_from_api(app_context):
    _, client, _ = app_context

    initial = client.get("/api/v1/settings/workers")
    assert initial.status_code == 200
    assert initial.json()["worker_processes"] == 2
    assert initial.json()["per_process_concurrency"] == 5
    assert initial.json()["theoretical_max_concurrency"] == 10
    assert initial.json()["max_delivery_retries"] == 10

    updated = client.put(
        "/api/v1/settings/workers",
        json={"worker_processes": 4, "max_delivery_retries": 7},
    )
    assert updated.status_code == 200
    assert updated.json()["worker_processes"] == 4
    assert updated.json()["theoretical_max_concurrency"] == 20
    assert updated.json()["max_delivery_retries"] == 7
    assert client.get("/api/v1/settings/workers").json()["worker_processes"] == 4


def test_worker_settings_reject_process_count_above_limit(app_context):
    _, client, _ = app_context

    response = client.put(
        "/api/v1/settings/workers", json={"worker_processes": 11}
    )

    assert response.status_code == 422

    retry_response = client.put(
        "/api/v1/settings/workers", json={"max_delivery_retries": 11}
    )
    assert retry_response.status_code == 422


def test_global_retry_limit_applies_to_new_tasks(app_context, sample_payload):
    _, client, _ = app_context
    updated = client.put(
        "/api/v1/settings/workers", json={"max_delivery_retries": 1}
    )
    assert updated.status_code == 200

    accepted = client.post("/api/v1/notifications", json=sample_payload).json()
    task = client.get(f"/api/v1/tasks/{accepted['id']}").json()

    assert task["max_attempts"] == 2

