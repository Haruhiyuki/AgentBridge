from __future__ import annotations

from fastapi.testclient import TestClient

from agentbridge.api import create_app


def test_health_endpoint_reports_memory_storage():
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["storage"] == "memory"


def test_command_execute_api_creates_project_session_and_turn(tmp_path):
    client = TestClient(create_app())
    chat = {
        "bot_instance_id": "bot-test",
        "platform": "onebot.v11",
        "chat_space_id": "group-api",
    }
    actor = {"id": "usr_1", "roles": ["maintainer"]}

    project_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": (
                f"/agent project create --name Backend --path {tmp_path} "
                f"--root {tmp_path} --alias backend"
            ),
            "actor": actor,
            "chat": chat,
            "idempotency_key": "api-project",
        },
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["data"]["project_id"]

    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new API Session",
            "actor": actor,
            "chat": chat,
            "idempotency_key": "api-session",
        },
    )
    assert session_response.status_code == 200
    session_id = session_response.json()["data"]["session_id"]

    turn_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent ask run focused tests",
            "actor": actor,
            "chat": chat,
            "idempotency_key": "api-turn",
        },
    )
    assert turn_response.status_code == 200
    assert turn_response.json()["data"]["project_id"] == project_id
    assert turn_response.json()["data"]["session_id"] == session_id


def test_api_returns_product_error_payload_for_permission_denied():
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent project create --name Backend",
            "actor": {"id": "usr_member", "roles": ["member"]},
            "idempotency_key": "denied",
        },
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "PERMISSION_DENIED"
    assert response.json()["side_effect"] == "未执行副作用。"
