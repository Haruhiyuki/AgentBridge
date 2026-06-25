from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from agentbridge.api import create_app
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, DeviceIdentityScope, Visibility
from agentbridge.terminal_agent import FakeTerminalBackend, TerminalAgentService


def _create_session_with_project(
    client, tmp_path, *, chat_space_id: str, prefix: str, name: str
) -> str:
    chat = {
        "bot_instance_id": "bot-test",
        "platform": "onebot.v11",
        "chat_space_id": chat_space_id,
    }
    actor = {"id": "usr_1", "roles": ["maintainer"]}
    project_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": actor,
            "chat": chat,
            "idempotency_key": f"{prefix}-project",
        },
    )
    assert project_response.status_code == 200
    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent session new {name}",
            "actor": actor,
            "chat": chat,
            "idempotency_key": f"{prefix}-session",
        },
    )
    assert session_response.status_code == 200
    return str(session_response.json()["data"]["session_id"])


def test_health_endpoint_reports_memory_storage():
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["storage"] == "memory"


def test_api_token_gate_protects_rest_api_when_configured(monkeypatch):
    monkeypatch.setenv("AGENTBRIDGE_API_TOKEN", "api-secret")
    client = TestClient(create_app())

    health_response = client.get("/api/v1/health")
    denied_response = client.get("/api/v1/projects")
    locked_admin_response = client.get("/admin")
    unlock_admin_response = client.get(
        "/admin?admin_token=api-secret",
        follow_redirects=False,
    )
    cookie_api_response = client.get("/api/v1/projects")
    header_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-api-token": "api-secret"},
    )
    bearer_response = client.get(
        "/api/v1/projects",
        headers={"authorization": "Bearer api-secret"},
    )

    assert health_response.status_code == 200
    assert denied_response.status_code == 403
    assert denied_response.json()["error_code"] == "PERMISSION_DENIED"
    assert locked_admin_response.status_code == 401
    assert unlock_admin_response.status_code == 303
    assert "agentbridge_admin_token=api-secret" in unlock_admin_response.headers["set-cookie"]
    assert cookie_api_response.status_code == 200
    assert cookie_api_response.json() == []
    assert header_response.status_code == 200
    assert header_response.json() == []
    assert bearer_response.status_code == 200
    assert bearer_response.json() == []


def test_api_token_file_hot_reloads_and_fails_closed(monkeypatch, tmp_path):
    token_file = tmp_path / "api-token"
    token_file.write_text("first-secret\n", encoding="utf-8")
    monkeypatch.delenv("AGENTBRIDGE_API_TOKEN", raising=False)
    monkeypatch.setenv("AGENTBRIDGE_API_TOKEN_FILE", str(token_file))
    client = TestClient(create_app())

    first_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-api-token": "first-secret"},
    )
    token_file.write_text("second-secret\n", encoding="utf-8")
    stale_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-api-token": "first-secret"},
    )
    rotated_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-api-token": "second-secret"},
    )

    assert first_response.status_code == 200
    assert stale_response.status_code == 403
    assert rotated_response.status_code == 200

    monkeypatch.setenv("AGENTBRIDGE_API_TOKEN_FILE", str(tmp_path / "missing-token"))
    missing_file_response = client.get("/api/v1/projects")
    assert missing_file_response.status_code == 403

    empty_file = tmp_path / "empty-token"
    empty_file.write_text("\n", encoding="utf-8")
    monkeypatch.setenv("AGENTBRIDGE_API_TOKEN_FILE", str(empty_file))
    empty_file_response = client.get("/api/v1/projects")
    assert empty_file_response.status_code == 403


def test_admin_cookie_authorizes_rest_api_when_admin_token_configured(monkeypatch):
    monkeypatch.setenv("AGENTBRIDGE_ADMIN_TOKEN", "admin-secret")
    client = TestClient(create_app())

    denied_response = client.get("/api/v1/projects")
    unlock_response = client.get("/admin?admin_token=admin-secret")
    api_response = client.get("/api/v1/projects")

    assert denied_response.status_code == 403
    assert unlock_response.status_code == 200
    assert "AgentBridge Admin" in unlock_response.text
    assert api_response.status_code == 200
    assert api_response.json() == []


def test_admin_token_file_hot_reloads(monkeypatch, tmp_path):
    token_file = tmp_path / "admin-token"
    token_file.write_text("first-admin\n", encoding="utf-8")
    monkeypatch.delenv("AGENTBRIDGE_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("AGENTBRIDGE_ADMIN_TOKEN_FILE", str(token_file))
    client = TestClient(create_app())

    locked_response = client.get("/admin")
    first_unlock = client.get("/admin?admin_token=first-admin")
    token_file.write_text("second-admin\n", encoding="utf-8")
    client.cookies.clear()
    stale_unlock = client.get("/admin?admin_token=first-admin")
    rotated_unlock = client.get("/admin?admin_token=second-admin")

    assert locked_response.status_code == 401
    assert first_unlock.status_code == 200
    assert "AgentBridge Admin" in first_unlock.text
    assert stale_unlock.status_code == 401
    assert rotated_unlock.status_code == 200
    assert "AgentBridge Admin" in rotated_unlock.text


def test_device_key_gate_authorizes_rest_api_when_configured(monkeypatch):
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_KEYS", '{"laptop":"device-secret"}')
    client = TestClient(create_app())

    health_response = client.get("/api/v1/health")
    denied_response = client.get("/api/v1/projects")
    header_response = client.get(
        "/api/v1/projects",
        headers={
            "x-agentbridge-device-id": "laptop",
            "x-agentbridge-device-key": "device-secret",
        },
    )
    bearer_response = client.get(
        "/api/v1/projects",
        headers={
            "x-agentbridge-device-id": "laptop",
            "authorization": "Bearer device-secret",
        },
    )
    wrong_key_response = client.get(
        "/api/v1/projects",
        headers={
            "x-agentbridge-device-id": "laptop",
            "x-agentbridge-device-key": "wrong",
        },
    )

    assert health_response.status_code == 200
    assert denied_response.status_code == 403
    assert denied_response.json()["error_code"] == "PERMISSION_DENIED"
    assert header_response.status_code == 200
    assert header_response.json() == []
    assert bearer_response.status_code == 200
    assert bearer_response.json() == []
    assert wrong_key_response.status_code == 403


def test_client_certificate_fingerprint_gate_authorizes_rest_and_admin(monkeypatch):
    monkeypatch.setenv("AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS", "SHA256:AA:BB:CC")
    client = TestClient(create_app())

    health_response = client.get("/api/v1/health")
    denied_response = client.get("/api/v1/projects")
    denied_admin_response = client.get("/admin")
    header_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    admin_response = client.get(
        "/admin",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert health_response.status_code == 200
    assert denied_response.status_code == 403
    assert denied_response.json()["error_code"] == "PERMISSION_DENIED"
    assert denied_admin_response.status_code == 401
    assert header_response.status_code == 200
    assert header_response.json() == []
    assert admin_response.status_code == 200
    assert "AgentBridge Admin" in admin_response.text


def test_client_certificate_fingerprint_file_hot_reloads_and_fails_closed(
    monkeypatch,
    tmp_path,
):
    fingerprints_file = tmp_path / "client-cert-fingerprints"
    fingerprints_file.write_text("AA:BB:CC\n", encoding="utf-8")
    monkeypatch.delenv("AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS", raising=False)
    monkeypatch.setenv("AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS_FILE", str(fingerprints_file))
    client = TestClient(create_app())

    first_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    fingerprints_file.write_text("DD:EE:FF\n", encoding="utf-8")
    stale_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    rotated_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-client-cert-fingerprint": "dd:ee:ff"},
    )

    assert first_response.status_code == 200
    assert stale_response.status_code == 403
    assert rotated_response.status_code == 200

    monkeypatch.setenv(
        "AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS_FILE",
        str(tmp_path / "missing-fingerprints"),
    )
    missing_file_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-client-cert-fingerprint": "dd:ee:ff"},
    )
    assert missing_file_response.status_code == 403

    empty_file = tmp_path / "empty-fingerprints"
    empty_file.write_text("\n", encoding="utf-8")
    monkeypatch.setenv("AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS_FILE", str(empty_file))
    empty_file_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-client-cert-fingerprint": "dd:ee:ff"},
    )
    assert empty_file_response.status_code == 403


def test_managed_device_identity_gates_rest_api_and_can_be_revoked():
    client = TestClient(create_app())
    actor = {"id": "security-admin", "roles": ["admin"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": actor,
            "device_id": "laptop",
            "display_name": "Maintainer laptop",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "device_manage"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "managed-device-create",
        },
    )
    denied_response = client.get("/api/v1/projects")
    authorized_response = client.get(
        "/api/v1/projects",
        headers={
            "x-agentbridge-device-id": "laptop",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    certificate_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    list_response = client.get(
        "/api/v1/device-identities",
        headers={
            "x-agentbridge-device-id": "laptop",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    revoke_response = client.post(
        "/api/v1/device-identities/laptop/revoke",
        json={"actor": actor, "trace_id": "managed-device-revoke"},
        headers={
            "x-agentbridge-device-id": "laptop",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    revoked_key_response = client.get(
        "/api/v1/projects",
        headers={
            "x-agentbridge-device-id": "laptop",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    revoked_certificate_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    still_gated_response = client.get("/api/v1/projects")

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["device_id"] == "laptop"
    assert created["display_name"] == "Maintainer laptop"
    assert created["status"] == "active"
    assert created["allowed_scopes"] == ["device_manage", "http_api"]
    assert created["certificate_fingerprints"] == ["aabbcc"]
    assert created["device_key"] == "managed-secret"
    assert "key_hash" not in created
    assert "key_salt" not in created
    assert denied_response.status_code == 403
    assert authorized_response.status_code == 200
    assert authorized_response.json() == []
    assert certificate_response.status_code == 200
    assert certificate_response.json() == []
    assert list_response.status_code == 200
    listed = list_response.json()[0]
    assert listed["device_id"] == "laptop"
    assert listed["allowed_scopes"] == ["device_manage", "http_api"]
    assert listed["certificate_fingerprints"] == ["aabbcc"]
    assert listed["last_used_at"] is not None
    assert "device_key" not in listed
    assert revoke_response.status_code == 200
    assert revoke_response.json()["status"] == "revoked"
    assert revoked_key_response.status_code == 403
    assert revoked_certificate_response.status_code == 403
    assert still_gated_response.status_code == 403


def test_managed_device_identity_can_be_certificate_only():
    client = TestClient(create_app())
    actor = {"id": "security-admin", "roles": ["admin"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": actor,
            "device_id": "cert-only",
            "display_name": "Certificate only device",
            "allowed_scopes": ["http_api", "device_manage"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "managed-device-cert-only-create",
        },
    )
    certificate_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    key_response = client.get(
        "/api/v1/projects",
        headers={
            "x-agentbridge-device-id": "cert-only",
            "x-agentbridge-device-key": "unused-key",
        },
    )
    revoke_response = client.post(
        "/api/v1/device-identities/cert-only/revoke",
        json={"actor": actor, "trace_id": "managed-device-cert-only-revoke"},
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    revoked_certificate_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["device_id"] == "cert-only"
    assert created["certificate_fingerprints"] == ["aabbcc"]
    assert "device_key" not in created
    assert certificate_response.status_code == 200
    assert key_response.status_code == 403
    assert revoke_response.status_code == 200
    assert revoked_certificate_response.status_code == 403


def test_managed_device_identity_requires_device_manage_scope_for_device_api():
    client = TestClient(create_app())
    actor = {"id": "security-admin", "roles": ["admin"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": actor,
            "device_id": "readonly-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "managed-device-readonly-create",
        },
    )
    key_http_response = client.get(
        "/api/v1/projects",
        headers={
            "x-agentbridge-device-id": "readonly-device",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    key_device_api_response = client.get(
        "/api/v1/device-identities",
        headers={
            "x-agentbridge-device-id": "readonly-device",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    cert_http_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    cert_device_api_response = client.get(
        "/api/v1/device-identities",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_response.status_code == 200
    assert key_http_response.status_code == 200
    assert cert_http_response.status_code == 200
    assert key_device_api_response.status_code == 403
    assert cert_device_api_response.status_code == 403


def test_managed_device_identity_requires_project_manage_scope_for_project_writes():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "project-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    list_response = client.get("/api/v1/projects", headers=key_headers)
    create_project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": maintainer,
            "name": "Readonly Device Project",
            "trace_id": "project-device-create-project",
        },
        headers=key_headers,
    )
    workspace_response = client.post(
        "/api/v1/projects/project-missing/workspaces",
        json={
            "actor": maintainer,
            "machine_id": "local",
            "path": "/tmp/repo",
            "allowed_root": "/tmp",
            "trace_id": "project-device-workspace",
        },
        headers=key_headers,
    )
    bind_response = client.post(
        "/api/v1/chat-spaces/context-missing/project-bindings",
        json={
            "actor": maintainer,
            "project_id": "project-missing",
            "trace_id": "project-device-bind",
        },
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_response.status_code == 200
    assert list_response.status_code == 200
    assert create_project_response.status_code == 403
    assert workspace_response.status_code == 403
    assert bind_response.status_code == 403


def test_managed_device_identity_project_manage_scope_allows_project_write_apis(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "project-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "project_manage"],
            "trace_id": "project-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "project-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    create_project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": maintainer,
            "name": "Managed Device Project",
            "trace_id": "project-manager-create-project",
        },
        headers=headers,
    )
    project_id = create_project_response.json().get("id")
    workspace_response = client.post(
        f"/api/v1/projects/{project_id}/workspaces",
        json={
            "actor": maintainer,
            "machine_id": "local",
            "path": str(tmp_path),
            "allowed_root": str(tmp_path),
            "trace_id": "project-manager-workspace",
        },
        headers=headers,
    )
    context_response = client.post(
        "/api/v1/chat-contexts",
        json={
            "bot_instance_id": "bot-test",
            "platform": "onebot.v11",
            "chat_space_id": "project-manager-scope",
        },
        headers=headers,
    )
    bind_response = client.post(
        f"/api/v1/chat-spaces/{context_response.json()['id']}/project-bindings",
        json={
            "actor": maintainer,
            "project_id": project_id,
            "alias_in_chat": "managed-device",
            "trace_id": "project-manager-bind",
        },
        headers=headers,
    )

    assert create_identity_response.status_code == 200
    assert create_project_response.status_code == 200
    assert create_project_response.json()["name"] == "Managed Device Project"
    assert workspace_response.status_code == 200
    assert workspace_response.json()["project_id"] == project_id
    assert context_response.status_code == 200
    assert bind_response.status_code == 200
    assert bind_response.json() == {"status": "ok"}


def test_managed_device_identity_requires_session_manage_scope_for_session_writes(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}

    project = client.post(
        "/api/v1/projects",
        json={
            "actor": maintainer,
            "name": "Readonly Session Project",
            "trace_id": "session-scope-project",
        },
    ).json()
    workspace = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": maintainer,
            "machine_id": "local",
            "path": str(tmp_path),
            "allowed_root": str(tmp_path),
            "trace_id": "session-scope-workspace",
        },
    ).json()
    existing_session = client.post(
        "/api/v1/sessions",
        json={
            "actor": maintainer,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Existing Session",
            "trace_id": "session-scope-existing-session",
        },
    ).json()

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "session-scope-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    list_response = client.get(
        "/api/v1/sessions",
        params={"project_id": project["id"]},
        headers=key_headers,
    )
    create_session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": maintainer,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Denied Session",
            "trace_id": "session-scope-denied-create",
        },
        headers=key_headers,
    )
    close_response = client.post(
        f"/api/v1/sessions/{existing_session['id']}/close",
        json=maintainer,
        headers=key_headers,
    )
    lease_response = client.post(
        f"/api/v1/sessions/{existing_session['id']}/lease/acquire",
        json={
            "actor": maintainer,
            "owner_type": "web_admin",
            "owner_id": "usr_maintainer",
            "trace_id": "session-scope-denied-lease",
        },
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_identity_response.status_code == 200
    assert list_response.status_code == 200
    assert [session["id"] for session in list_response.json()] == [
        existing_session["id"]
    ]
    assert create_session_response.status_code == 403
    assert close_response.status_code == 403
    assert lease_response.status_code == 403


def test_managed_device_identity_session_manage_scope_allows_session_write_apis(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}

    project = client.post(
        "/api/v1/projects",
        json={
            "actor": maintainer,
            "name": "Managed Session Project",
            "trace_id": "session-manager-project",
        },
    ).json()
    workspace = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": maintainer,
            "machine_id": "local",
            "path": str(tmp_path),
            "allowed_root": str(tmp_path),
            "trace_id": "session-manager-workspace",
        },
    ).json()

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "session-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "session_manage"],
            "trace_id": "session-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "session-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    create_session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": maintainer,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Managed Device Session",
            "trace_id": "session-manager-create-session",
        },
        headers=headers,
    )
    session_id = create_session_response.json().get("id")
    lease_response = client.post(
        f"/api/v1/sessions/{session_id}/lease/acquire",
        json={
            "actor": maintainer,
            "owner_type": "web_admin",
            "owner_id": "usr_maintainer",
            "trace_id": "session-manager-lease",
        },
        headers=headers,
    )
    release_response = client.post(
        f"/api/v1/sessions/{session_id}/lease/release",
        json={
            "actor": maintainer,
            "epoch": lease_response.json().get("epoch"),
            "trace_id": "session-manager-release",
        },
        headers=headers,
    )
    close_response = client.post(
        f"/api/v1/sessions/{session_id}/close",
        json=maintainer,
        headers=headers,
    )

    assert create_identity_response.status_code == 200
    assert create_session_response.status_code == 200
    assert create_session_response.json()["name"] == "Managed Device Session"
    assert lease_response.status_code == 200
    assert lease_response.json()["owner_id"] == "usr_maintainer"
    assert release_response.status_code == 200
    assert release_response.json()["next_epoch"] == lease_response.json()["epoch"] + 1
    assert close_response.status_code == 200
    assert close_response.json()["status"] == "closed"


def test_project_session_admin_ui_serves_dashboard():
    client = TestClient(create_app())

    response = client.get("/admin/projects")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "AgentBridge Projects & Sessions" in html
    assert "/api/v1/projects" in html
    assert "/api/v1/sessions" in html
    assert "/workspaces" in html
    assert "async function createProject()" in html
    assert "async function createSession()" in html
    assert "async function closeSession()" in html


def test_interaction_admin_ui_serves_dashboard():
    client = TestClient(create_app())

    response = client.get("/admin/interactions")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "AgentBridge Interactions" in html
    assert "/api/v1/interactions" in html
    assert "/interactions/${encodeURIComponent(selectedInteractionId)}/answer" in html
    assert "/interactions/${encodeURIComponent(selectedInteractionId)}/vote" in html
    assert "/interactions/${encodeURIComponent(selectedInteractionId)}/cancel" in html
    assert "async function createInteraction()" in html
    assert "async function answerInteraction()" in html
    assert "async function voteInteraction(approve)" in html


def test_device_identity_admin_ui_serves_dashboard():
    client = TestClient(create_app())

    response = client.get("/admin/device-identities")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "AgentBridge Device Identities" in html
    assert "/api/v1/device-identities" in html
    assert "async function loadDevices()" in html
    assert "async function upsertDevice()" in html
    assert "async function revokeDevice()" in html
    assert "auth-device-key" in html
    assert "allowed-scopes" in html
    assert "command_execute" in html
    assert "device_manage" in html
    assert "policy_manage" in html
    assert "group_role_manage" in html
    assert "project_manage" in html
    assert "session_manage" in html
    assert "terminal_control" in html
    assert "certificate-fingerprints" in html
    assert "generated-key" in html


def test_audit_events_admin_ui_serves_dashboard():
    client = TestClient(create_app())

    response = client.get("/admin/audit")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "AgentBridge Audit & Events" in html
    assert "/api/v1/audit" in html
    assert "/api/v1/events" in html
    assert "/api/v1/sessions/${encodeURIComponent(sessionId)}/events" in html
    assert "/api/v1/sessions/${encodeURIComponent(sessionId)}/events/ws" in html
    assert "async function refreshAudit()" in html
    assert "async function refreshEvents()" in html
    assert "async function searchEvents()" in html
    assert "event-search" in html
    assert "event-project" in html
    assert "event-type" in html
    assert "event-source" in html
    assert "event-trace" in html
    assert "audit-query" in html
    assert "event-query" in html
    assert "event-live-connect" in html
    assert "function connectEventsLive()" in html


def test_project_session_rest_flow_supports_admin_operations(tmp_path):
    client = TestClient(create_app())
    actor = {"id": "admin-ui", "roles": ["admin"]}

    project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": actor,
            "name": "Backend",
            "slug": "backend",
            "aliases": ["api"],
            "description": "Admin managed project",
            "default_agent": "codex",
            "trace_id": "test-admin-project-create",
        },
    )
    assert project_response.status_code == 200
    project = project_response.json()
    assert project["slug"] == "backend"

    workspace_path = tmp_path / "repo"
    workspace_response = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": actor,
            "machine_id": "local",
            "path": str(workspace_path),
            "allowed_root": str(tmp_path),
            "workspace_type": "shared",
            "trace_id": "test-admin-workspace-add",
        },
    )
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    assert workspace["project_id"] == project["id"]

    session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": actor,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Admin Session",
            "agent_type": "codex",
            "visibility": "group",
            "trace_id": "test-admin-session-create",
        },
    )
    assert session_response.status_code == 200
    session = session_response.json()
    assert session["project_id"] == project["id"]
    assert session["workspace_id"] == workspace["id"]

    list_response = client.get("/api/v1/sessions", params={"project_id": project["id"]})
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [session["id"]]

    close_response = client.post(
        f"/api/v1/sessions/{session['id']}/close",
        json=actor,
    )
    assert close_response.status_code == 200
    assert close_response.json()["status"] == "closed"


def test_audit_api_filters_and_limits_records(tmp_path):
    client = TestClient(create_app())
    actor = {"id": "admin-ui", "roles": ["admin"]}

    project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": actor,
            "name": "Audit Backend",
            "slug": "audit-backend",
            "trace_id": "test-audit-project",
        },
    )
    project = project_response.json()
    workspace_response = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": actor,
            "machine_id": "local",
            "path": str(tmp_path),
            "allowed_root": str(tmp_path),
            "trace_id": "test-audit-workspace",
        },
    )
    workspace = workspace_response.json()
    session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": actor,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Audit Session",
            "trace_id": "test-audit-session",
        },
    )
    session = session_response.json()

    filtered_response = client.get(
        "/api/v1/audit",
        params={
            "action": "session.created",
            "actor_id": "admin-ui",
            "session_id": session["id"],
            "limit": 1,
        },
    )
    missing_response = client.get(
        "/api/v1/audit",
        params={"action": "session.created", "actor_id": "other"},
    )
    payload_response = client.get(
        "/api/v1/audit",
        params={"action": "project.workspace_added", "q": workspace["id"]},
    )
    payload_missing_response = client.get(
        "/api/v1/audit",
        params={"action": "project.workspace_added", "q": "missing-workspace"},
    )

    assert filtered_response.status_code == 200
    assert len(filtered_response.json()) == 1
    [audit_event] = filtered_response.json()
    assert audit_event["action"] == "session.created"
    assert audit_event["actor_id"] == "admin-ui"
    assert audit_event["project_id"] == project["id"]
    assert audit_event["session_id"] == session["id"]
    assert payload_response.status_code == 200
    assert [event["details"]["workspace_id"] for event in payload_response.json()] == [
        workspace["id"]
    ]
    assert payload_missing_response.status_code == 200
    assert payload_missing_response.json() == []
    assert missing_response.status_code == 200
    assert missing_response.json() == []


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


def test_managed_device_identity_requires_command_execute_scope_for_command_execute():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    actor = {"id": "usr_1", "roles": ["maintainer"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "command-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    parse_response = client.post(
        "/api/v1/commands/parse",
        json={"raw_text": "/agent project list", "actor": actor},
        headers=key_headers,
    )
    key_execute_response = client.post(
        "/api/v1/commands/execute",
        json={"raw_text": "/agent project list", "actor": actor},
        headers=key_headers,
    )
    cert_execute_response = client.post(
        "/api/v1/commands/execute",
        json={"raw_text": "/agent project list", "actor": actor},
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_response.status_code == 200
    assert parse_response.status_code == 200
    assert key_execute_response.status_code == 403
    assert cert_execute_response.status_code == 403


def test_managed_device_identity_command_execute_scope_allows_command_execute():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    actor = {"id": "usr_1", "roles": ["maintainer"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "command-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "command_execute"],
            "trace_id": "command-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "command-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    execute_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent project list",
            "actor": actor,
            "idempotency_key": "command-manager-project-list",
        },
        headers=headers,
    )

    assert create_response.status_code == 200
    assert execute_response.status_code == 200
    assert execute_response.json()["canonical_command"] == "project.list"
    assert execute_response.json()["data"] == {"projects": []}


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


def test_group_role_api_grants_command_permissions(tmp_path):
    client = TestClient(create_app())
    chat = {
        "bot_instance_id": "bot-test",
        "platform": "onebot.v11",
        "chat_space_id": "group-roles-api",
    }
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    member = {"id": "usr_member", "roles": ["member"]}
    context = client.post("/api/v1/chat-contexts", json=chat).json()

    project_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": maintainer,
            "chat_context_id": context["id"],
            "idempotency_key": "role-api-project",
        },
    )
    assert project_response.status_code == 200

    denied_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new Denied",
            "actor": member,
            "chat_context_id": context["id"],
            "idempotency_key": "role-api-denied",
        },
    )
    assert denied_response.status_code == 403

    grant_response = client.post(
        f"/api/v1/chat-contexts/{context['id']}/roles/grant",
        json={
            "actor": maintainer,
            "target_actor_id": "usr_member",
            "roles": ["operator"],
            "trace_id": "role-api-grant",
        },
    )
    assert grant_response.status_code == 200
    assert grant_response.json()["roles"] == ["operator"]

    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new Granted",
            "actor": member,
            "chat_context_id": context["id"],
            "idempotency_key": "role-api-granted",
        },
    )
    assert session_response.status_code == 200
    assert session_response.json()["data"]["session"]["created_by"] == "usr_member"

    list_response = client.get(f"/api/v1/chat-contexts/{context['id']}/roles")
    assert list_response.status_code == 200
    assert [binding["actor_id"] for binding in list_response.json()] == ["usr_member"]

    revoke_response = client.post(
        f"/api/v1/chat-contexts/{context['id']}/roles/revoke",
        json={
            "actor": maintainer,
            "target_actor_id": "usr_member",
            "roles": ["operator"],
            "trace_id": "role-api-revoke",
        },
    )
    assert revoke_response.status_code == 200
    assert revoke_response.json() is None

    denied_turn = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent ask after revoke",
            "actor": member,
            "chat_context_id": context["id"],
            "idempotency_key": "role-api-denied-turn",
        },
    )
    assert denied_turn.status_code == 403


def test_managed_device_identity_requires_group_role_manage_scope_for_role_apis():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    context = client.post(
        "/api/v1/chat-contexts",
        json={
            "bot_instance_id": "bot-test",
            "platform": "onebot.v11",
            "chat_space_id": "group-role-scope",
        },
    ).json()

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "group-role-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    regular_http_response = client.get("/api/v1/projects", headers=key_headers)
    list_response = client.get(
        f"/api/v1/chat-contexts/{context['id']}/roles",
        headers=key_headers,
    )
    grant_response = client.post(
        f"/api/v1/chat-contexts/{context['id']}/roles/grant",
        json={
            "actor": maintainer,
            "target_actor_id": "usr_member",
            "roles": ["operator"],
            "trace_id": "group-role-device-grant",
        },
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_response.status_code == 200
    assert regular_http_response.status_code == 200
    assert list_response.status_code == 403
    assert grant_response.status_code == 403


def test_managed_device_identity_group_role_manage_scope_allows_role_apis():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    context = client.post(
        "/api/v1/chat-contexts",
        json={
            "bot_instance_id": "bot-test",
            "platform": "onebot.v11",
            "chat_space_id": "group-role-manager-scope",
        },
    ).json()

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "role-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "group_role_manage"],
            "trace_id": "group-role-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "role-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    grant_response = client.post(
        f"/api/v1/chat-contexts/{context['id']}/roles/grant",
        json={
            "actor": maintainer,
            "target_actor_id": "usr_member",
            "roles": ["operator"],
            "trace_id": "group-role-manager-grant",
        },
        headers=headers,
    )
    list_response = client.get(
        f"/api/v1/chat-contexts/{context['id']}/roles",
        headers=headers,
    )
    revoke_response = client.post(
        f"/api/v1/chat-contexts/{context['id']}/roles/revoke",
        json={
            "actor": maintainer,
            "target_actor_id": "usr_member",
            "roles": ["operator"],
            "trace_id": "group-role-manager-revoke",
        },
        headers=headers,
    )

    assert create_response.status_code == 200
    assert grant_response.status_code == 200
    assert grant_response.json()["roles"] == ["operator"]
    assert list_response.status_code == 200
    assert [binding["actor_id"] for binding in list_response.json()] == ["usr_member"]
    assert revoke_response.status_code == 200
    assert revoke_response.json() is None


def test_interaction_api_creates_answers_and_votes(tmp_path):
    client = TestClient(create_app())
    chat = {
        "bot_instance_id": "bot-test",
        "platform": "onebot.v11",
        "chat_space_id": "group-interactions-api",
    }
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    operator = {"id": "usr_operator", "roles": ["operator"]}
    approver = {"id": "usr_approver", "roles": ["approver"]}
    context = client.post("/api/v1/chat-contexts", json=chat).json()

    client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": maintainer,
            "chat_context_id": context["id"],
            "idempotency_key": "interaction-api-project",
        },
    )
    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new Interaction API",
            "actor": maintainer,
            "chat_context_id": context["id"],
            "idempotency_key": "interaction-api-session",
        },
    )
    session_id = session_response.json()["data"]["session_id"]

    question_response = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": operator,
            "type": "question",
            "prompt": "Which migration strategy?",
            "chat_context_id": context["id"],
            "trace_id": "interaction-api-question",
        },
    )
    assert question_response.status_code == 200
    question_id = question_response.json()["id"]

    answer_response = client.post(
        f"/api/v1/interactions/{question_id}/answer",
        json={
            "actor": operator,
            "answer": "Use expand-contract.",
            "chat_context_id": context["id"],
            "trace_id": "interaction-api-answer",
        },
    )
    assert answer_response.status_code == 200
    assert answer_response.json()["status"] == "resolved"
    assert answer_response.json()["answer"] == "Use expand-contract."

    approval_response = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": operator,
            "type": "approval",
            "prompt": "Run destructive command?",
            "required_votes": 1,
            "chat_context_id": context["id"],
            "trace_id": "interaction-api-approval",
        },
    )
    approval_id = approval_response.json()["id"]
    vote_response = client.post(
        f"/api/v1/interactions/{approval_id}/vote",
        json={
            "actor": approver,
            "approve": False,
            "reason": "too risky",
            "chat_context_id": context["id"],
            "trace_id": "interaction-api-vote",
        },
    )
    list_response = client.get(
        "/api/v1/interactions",
        params={"session_id": session_id, "status": "resolved"},
    )

    assert approval_response.status_code == 200
    assert vote_response.status_code == 200
    assert vote_response.json()["status"] == "resolved"
    assert vote_response.json()["votes"] == {"usr_approver": False}
    assert list_response.status_code == 200
    assert {interaction["id"] for interaction in list_response.json()} == {
        question_id,
        approval_id,
    }


def test_interaction_api_expires_due_interactions(tmp_path):
    client = TestClient(create_app())
    chat = {
        "bot_instance_id": "bot-test",
        "platform": "onebot.v11",
        "chat_space_id": "group-interactions-expire",
    }
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    approver = {"id": "usr_approver", "roles": ["approver"]}
    context = client.post("/api/v1/chat-contexts", json=chat).json()

    client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": maintainer,
            "chat_context_id": context["id"],
            "idempotency_key": "interaction-expire-project",
        },
    )
    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new Interaction Expire",
            "actor": maintainer,
            "chat_context_id": context["id"],
            "idempotency_key": "interaction-expire-session",
        },
    )
    session_id = session_response.json()["data"]["session_id"]
    created = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": maintainer,
            "type": "approval",
            "prompt": "Expires immediately",
            "ttl_seconds": 0,
            "chat_context_id": context["id"],
            "trace_id": "interaction-expire-create",
        },
    )
    interaction_id = created.json()["id"]

    pending_response = client.get(
        "/api/v1/interactions",
        params={"session_id": session_id, "status": "pending"},
    )
    expired_response = client.get(
        "/api/v1/interactions",
        params={"session_id": session_id, "status": "expired"},
    )
    vote_response = client.post(
        f"/api/v1/interactions/{interaction_id}/vote",
        json={
            "actor": approver,
            "approve": True,
            "chat_context_id": context["id"],
            "trace_id": "interaction-expire-vote",
        },
    )

    assert created.status_code == 200
    assert pending_response.status_code == 200
    assert pending_response.json() == []
    assert expired_response.status_code == 200
    assert expired_response.json()[0]["id"] == interaction_id
    assert expired_response.json()[0]["status"] == "expired"
    assert vote_response.status_code == 409
    assert vote_response.json()["error_code"] == "INTERACTION_EXPIRED"


def test_high_risk_approval_requires_dangerous_approver(tmp_path):
    client = TestClient(create_app())
    chat = {
        "bot_instance_id": "bot-test",
        "platform": "onebot.v11",
        "chat_space_id": "group-risk-api",
    }
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    operator = {"id": "usr_operator", "roles": ["operator"]}
    approver = {"id": "usr_approver", "roles": ["approver"]}
    dangerous = {"id": "usr_dangerous", "roles": ["dangerous_approver"]}
    context = client.post("/api/v1/chat-contexts", json=chat).json()

    client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": maintainer,
            "chat_context_id": context["id"],
            "idempotency_key": "risk-api-project",
        },
    )
    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new Risk API",
            "actor": maintainer,
            "chat_context_id": context["id"],
            "idempotency_key": "risk-api-session",
        },
    )
    session_id = session_response.json()["data"]["session_id"]
    approval_response = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": operator,
            "type": "approval",
            "risk_level": "high",
            "prompt": "Push to protected branch?",
            "chat_context_id": context["id"],
            "trace_id": "risk-api-approval",
        },
    )
    approval_id = approval_response.json()["id"]
    normal_vote = client.post(
        f"/api/v1/interactions/{approval_id}/vote",
        json={
            "actor": approver,
            "approve": True,
            "chat_context_id": context["id"],
            "trace_id": "risk-api-normal-vote",
        },
    )
    dangerous_vote = client.post(
        f"/api/v1/interactions/{approval_id}/vote",
        json={
            "actor": dangerous,
            "approve": True,
            "chat_context_id": context["id"],
            "trace_id": "risk-api-dangerous-vote",
        },
    )

    assert approval_response.status_code == 200
    assert approval_response.json()["risk_level"] == "high"
    assert approval_response.json()["required_votes"] == 1
    assert approval_response.json()["policy_snapshot"][
        "dangerous_permission_required"
    ] is True
    assert normal_vote.status_code == 403
    assert normal_vote.json()["details"]["required_permission"] == "approval.dangerous"
    assert dangerous_vote.status_code == 200
    assert dangerous_vote.json()["status"] == "resolved"


def test_dangerous_requester_cannot_complete_own_approval(tmp_path):
    client = TestClient(create_app())
    chat = {
        "bot_instance_id": "bot-test",
        "platform": "onebot.v11",
        "chat_space_id": "group-risk-self-api",
    }
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    requester = {"id": "usr_requester", "roles": ["operator", "dangerous_approver"]}
    dangerous = {"id": "usr_dangerous", "roles": ["dangerous_approver"]}
    context = client.post("/api/v1/chat-contexts", json=chat).json()

    client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": maintainer,
            "chat_context_id": context["id"],
            "idempotency_key": "risk-self-project",
        },
    )
    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new Risk Self",
            "actor": maintainer,
            "chat_context_id": context["id"],
            "idempotency_key": "risk-self-session",
        },
    )
    session_id = session_response.json()["data"]["session_id"]
    approval = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": requester,
            "type": "approval",
            "risk_level": "high",
            "prompt": "Delete protected file?",
            "chat_context_id": context["id"],
            "trace_id": "risk-self-approval",
        },
    ).json()

    self_vote = client.post(
        f"/api/v1/interactions/{approval['id']}/vote",
        json={
            "actor": requester,
            "approve": True,
            "chat_context_id": context["id"],
            "trace_id": "risk-self-vote",
        },
    )
    peer_vote = client.post(
        f"/api/v1/interactions/{approval['id']}/vote",
        json={
            "actor": dangerous,
            "approve": True,
            "chat_context_id": context["id"],
            "trace_id": "risk-peer-vote",
        },
    )

    assert self_vote.status_code == 403
    assert self_vote.json()["message"] == "请求人不能单独完成高危审批。"
    assert peer_vote.status_code == 200
    assert peer_vote.json()["status"] == "resolved"


def test_approval_quorum_can_be_configured_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBRIDGE_APPROVAL_QUORUMS", "critical=3")
    client = TestClient(create_app())
    chat = {
        "bot_instance_id": "bot-test",
        "platform": "onebot.v11",
        "chat_space_id": "group-quorum-api",
    }
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    operator = {"id": "usr_operator", "roles": ["operator"]}
    context = client.post("/api/v1/chat-contexts", json=chat).json()

    client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": maintainer,
            "chat_context_id": context["id"],
            "idempotency_key": "quorum-api-project",
        },
    )
    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new Quorum API",
            "actor": maintainer,
            "chat_context_id": context["id"],
            "idempotency_key": "quorum-api-session",
        },
    )
    session_id = session_response.json()["data"]["session_id"]
    approval_response = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": operator,
            "type": "approval",
            "risk_level": "critical",
            "prompt": "Deploy production?",
            "chat_context_id": context["id"],
            "trace_id": "quorum-api-approval",
        },
    )

    assert approval_response.status_code == 200
    assert approval_response.json()["risk_level"] == "critical"
    assert approval_response.json()["required_votes"] == 3
    assert approval_response.json()["policy_snapshot"]["required_votes"] == 3


def test_approval_policy_overrides_apply_by_project_and_chat_context(tmp_path):
    client = TestClient(create_app())
    chat = {
        "bot_instance_id": "bot-test",
        "platform": "onebot.v11",
        "chat_space_id": "group-policy-api",
    }
    actor = {"id": "usr_1", "roles": ["maintainer"]}

    project_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": actor,
            "chat": chat,
            "idempotency_key": "policy-api-project",
        },
    )
    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new Policy API",
            "actor": actor,
            "chat": chat,
            "idempotency_key": "policy-api-session",
        },
    )
    context_response = client.post("/api/v1/chat-contexts", json=chat)
    project_id = project_response.json()["data"]["project_id"]
    session_id = session_response.json()["data"]["session_id"]
    chat_context_id = context_response.json()["id"]

    project_policy = client.put(
        f"/api/v1/projects/{project_id}/approval-policy",
        json={
            "actor": actor,
            "quorum_by_risk": {"critical": 3},
            "trace_id": "policy-api-project-set",
        },
    )
    critical_approval = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": actor,
            "type": "approval",
            "risk_level": "critical",
            "prompt": "Project policy critical quorum?",
            "trace_id": "policy-api-critical",
        },
    )

    assert project_policy.status_code == 200
    assert project_policy.json()["quorum_by_risk"] == {"critical": 3}
    assert critical_approval.status_code == 200
    assert critical_approval.json()["required_votes"] == 3
    assert critical_approval.json()["policy_snapshot"]["applied_overrides"][0][
        "scope_type"
    ] == "project"

    chat_policy = client.put(
        f"/api/v1/chat-contexts/{chat_context_id}/approval-policy",
        json={
            "actor": actor,
            "quorum_by_risk": {"high": 2},
            "trace_id": "policy-api-chat-set",
        },
    )
    policy_state = client.get(f"/api/v1/chat-contexts/{chat_context_id}/approval-policy")
    high_approval = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": actor,
            "type": "approval",
            "risk_level": "high",
            "prompt": "Chat policy high quorum?",
            "chat_context_id": chat_context_id,
            "trace_id": "policy-api-high",
        },
    )

    assert chat_policy.status_code == 200
    assert policy_state.status_code == 200
    assert policy_state.json()["effective_quorum_by_risk"]["high"] == 2
    assert high_approval.status_code == 200
    assert high_approval.json()["required_votes"] == 2
    assert [
        override["scope_type"]
        for override in high_approval.json()["policy_snapshot"]["applied_overrides"]
    ] == ["project", "chat_context"]


def test_session_event_api_supports_ingest_replay_and_idempotency(tmp_path):
    client = TestClient(create_app())
    chat = {
        "bot_instance_id": "bot-test",
        "platform": "onebot.v11",
        "chat_space_id": "group-events",
    }
    actor = {"id": "usr_1", "roles": ["maintainer"]}

    project_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": actor,
            "chat": chat,
            "idempotency_key": "event-api-project",
        },
    )
    project_id = project_response.json()["data"]["project_id"]
    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new Event API",
            "actor": actor,
            "chat": chat,
            "idempotency_key": "event-api-session",
        },
    )
    session_id = session_response.json()["data"]["session_id"]

    first = client.post(
        f"/api/v1/sessions/{session_id}/events",
        json={
            "type": "assistant.delta",
            "source": "terminal_agent",
            "trace_id": "terminal-1",
            "idempotency_key": "terminal-event-1",
            "payload": {"text": "hello"},
        },
    )
    duplicate = client.post(
        f"/api/v1/sessions/{session_id}/events",
        json={
            "type": "assistant.delta",
            "source": "terminal_agent",
            "trace_id": "terminal-1",
            "idempotency_key": "terminal-event-1",
            "payload": {"text": "hello again"},
        },
    )
    second = client.post(
        f"/api/v1/sessions/{session_id}/events",
        json={
            "type": "assistant.delta",
            "source": "terminal_agent",
            "trace_id": "terminal-2",
            "idempotency_key": "terminal-event-2",
            "payload": {"text": "newest"},
        },
    )

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == first.json()["id"]
    assert second.status_code == 200

    events_response = client.get(f"/api/v1/sessions/{session_id}/events", params={"after_seq": 1})
    assert events_response.status_code == 200
    assert [event["type"] for event in events_response.json()] == [
        "assistant.delta",
        "assistant.delta",
    ]

    search_response = client.get(
        "/api/v1/events",
        params={
            "session_id": session_id,
            "event_type": "assistant.delta",
            "source": "terminal_agent",
            "limit": 1,
        },
    )
    trace_response = client.get("/api/v1/events", params={"trace_id": "terminal-1"})
    payload_response = client.get(
        "/api/v1/events",
        params={"session_id": session_id, "q": "newest"},
    )
    payload_missing_response = client.get(
        "/api/v1/events",
        params={"session_id": session_id, "q": "missing-payload"},
    )
    project_response = client.get(
        "/api/v1/events",
        params={
            "project_id": project_id,
            "event_type": "session.created",
            "source": "control_plane",
        },
    )

    assert search_response.status_code == 200
    assert [event["trace_id"] for event in search_response.json()] == ["terminal-2"]
    assert trace_response.status_code == 200
    assert [event["id"] for event in trace_response.json()] == [first.json()["id"]]
    assert payload_response.status_code == 200
    assert [event["id"] for event in payload_response.json()] == [second.json()["id"]]
    assert payload_missing_response.status_code == 200
    assert payload_missing_response.json() == []
    assert project_response.status_code == 200
    assert [event["session_id"] for event in project_response.json()] == [session_id]


def test_rendered_events_api_returns_documents_and_text_messages(tmp_path):
    client = TestClient(create_app())
    chat = {
        "bot_instance_id": "bot-test",
        "platform": "onebot.v11",
        "chat_space_id": "group-render",
    }
    actor = {"id": "usr_1", "roles": ["maintainer"]}

    client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": actor,
            "chat": chat,
            "idempotency_key": "render-project",
        },
    )
    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new Render Session",
            "actor": actor,
            "chat": chat,
            "idempotency_key": "render-session",
        },
    )
    session_id = session_response.json()["data"]["session_id"]

    rendered_response = client.get(f"/api/v1/sessions/{session_id}/rendered-events")

    assert rendered_response.status_code == 200
    rendered = rendered_response.json()
    assert rendered[0]["document"]["blocks"][0]["title"] == "会话"
    assert "Render Session" in rendered[0]["text_messages"][0]


def test_session_events_websocket_replays_semantic_events_and_idle_close(tmp_path):
    client = TestClient(create_app())
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-events-ws",
        prefix="event-ws",
        name="Event WS",
    )
    event_response = client.post(
        f"/api/v1/sessions/{session_id}/events",
        json={
            "type": "assistant.delta",
            "source": "terminal_agent",
            "trace_id": "terminal-ws",
            "idempotency_key": "terminal-event-ws",
            "payload": {"text": "streamed"},
        },
    )
    assert event_response.status_code == 200

    with client.websocket_connect(
        f"/api/v1/sessions/{session_id}/events/ws?after_seq=1&idle_timeout_seconds=0"
    ) as websocket:
        message = websocket.receive_json()
        idle = websocket.receive_json()

    assert message["type"] == "semantic_event"
    assert message["event"]["type"] == "assistant.delta"
    assert message["event"]["payload"] == {"text": "streamed"}
    assert idle == {"type": "idle_timeout", "last_seq": message["event"]["seq"]}


def test_session_events_websocket_streams_new_events(tmp_path):
    app = create_app()
    client = TestClient(app)
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-events-ws-live",
        prefix="event-ws-live",
        name="Event WS Live",
    )

    def emit_event() -> None:
        session = app.state.control.repository.get_session(session_id)
        app.state.control.emit_event(
            event_type="assistant.delta",
            source="terminal_agent",
            trace_id="terminal-ws-live",
            project_id=session.project_id,
            session_id=session_id,
            payload={"text": "live"},
        )

    with client.websocket_connect(
        f"/api/v1/sessions/{session_id}/events/ws"
        "?after_seq=1&poll_interval_seconds=0.05&idle_timeout_seconds=1"
    ) as websocket:
        thread = threading.Thread(target=emit_event)
        thread.start()
        message = websocket.receive_json()
        thread.join(timeout=1)

    assert message["type"] == "semantic_event"
    assert message["event"]["type"] == "assistant.delta"
    assert message["event"]["payload"] == {"text": "live"}


def test_rendered_events_websocket_replays_text_messages(tmp_path):
    client = TestClient(create_app())
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-render-ws",
        prefix="render-ws",
        name="Render WS",
    )

    with client.websocket_connect(
        f"/api/v1/sessions/{session_id}/rendered-events/ws?idle_timeout_seconds=0"
    ) as websocket:
        message = websocket.receive_json()
        idle = websocket.receive_json()

    assert message["type"] == "rendered_event"
    assert message["document"]["blocks"][0]["title"] == "会话"
    assert "Render WS" in message["text_messages"][0]
    assert idle == {"type": "idle_timeout", "last_seq": message["seq"]}


def test_session_events_websocket_reports_missing_session():
    client = TestClient(create_app())

    with client.websocket_connect("/api/v1/sessions/missing/events/ws") as websocket:
        message = websocket.receive_json()

    assert message["type"] == "error"
    assert message["error"]["error_code"] == "NOT_FOUND"


def test_session_events_websocket_requires_token_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBRIDGE_WS_TOKEN", "secret")
    client = TestClient(create_app())
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-events-ws-token",
        prefix="event-ws-token",
        name="Event WS Token",
    )

    with client.websocket_connect(
        f"/api/v1/sessions/{session_id}/events/ws?idle_timeout_seconds=0"
    ) as websocket:
        denied = websocket.receive_json()

    assert denied["type"] == "error"
    assert denied["error"]["error_code"] == "PERMISSION_DENIED"

    with client.websocket_connect(
        f"/api/v1/sessions/{session_id}/events/ws?token=secret&idle_timeout_seconds=0"
    ) as websocket:
        message = websocket.receive_json()
        idle = websocket.receive_json()

    assert message["type"] == "semantic_event"
    assert message["event"]["type"] == "session.created"
    assert idle == {"type": "idle_timeout", "last_seq": message["event"]["seq"]}


def test_session_events_websocket_token_file_hot_reloads(monkeypatch, tmp_path):
    token_file = tmp_path / "ws-token"
    token_file.write_text("first-secret\n", encoding="utf-8")
    monkeypatch.delenv("AGENTBRIDGE_WS_TOKEN", raising=False)
    monkeypatch.setenv("AGENTBRIDGE_WS_TOKEN_FILE", str(token_file))
    client = TestClient(create_app())
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-events-ws-token-file",
        prefix="event-ws-token-file",
        name="Event WS Token File",
    )

    with client.websocket_connect(
        f"/api/v1/sessions/{session_id}/events/ws"
        "?token=first-secret&idle_timeout_seconds=0"
    ) as websocket:
        message = websocket.receive_json()
        idle = websocket.receive_json()

    assert message["type"] == "semantic_event"
    assert idle == {"type": "idle_timeout", "last_seq": message["event"]["seq"]}

    token_file.write_text("second-secret\n", encoding="utf-8")
    with client.websocket_connect(
        f"/api/v1/sessions/{session_id}/events/ws"
        "?token=first-secret&idle_timeout_seconds=0"
    ) as websocket:
        denied = websocket.receive_json()

    assert denied["type"] == "error"
    assert denied["error"]["error_code"] == "PERMISSION_DENIED"

    with client.websocket_connect(
        f"/api/v1/sessions/{session_id}/events/ws"
        "?token=second-secret&idle_timeout_seconds=0"
    ) as websocket:
        rotated_message = websocket.receive_json()
        rotated_idle = websocket.receive_json()

    assert rotated_message["type"] == "semantic_event"
    assert rotated_idle == {
        "type": "idle_timeout",
        "last_seq": rotated_message["event"]["seq"],
    }


def test_session_events_websocket_accepts_device_key(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_KEYS", '{"laptop":"device-secret"}')
    control = ControlPlane()
    actor = Actor(id="usr_1", roles={"maintainer"})
    project = control.create_project(
        actor=actor,
        name="Device Key Backend",
        trace_id="device-key-project",
    )
    workspace = control.add_workspace(
        actor=actor,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="device-key-workspace",
    )
    session = control.create_session(
        actor=actor,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Device Key Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="device-key-session",
    )
    client = TestClient(create_app(control))

    with client.websocket_connect(
        f"/api/v1/sessions/{session.id}/events/ws?idle_timeout_seconds=0"
    ) as websocket:
        denied = websocket.receive_json()

    assert denied["type"] == "error"
    assert denied["error"]["error_code"] == "PERMISSION_DENIED"

    with client.websocket_connect(
        f"/api/v1/sessions/{session.id}/events/ws"
        "?device_id=laptop&device_key=device-secret&idle_timeout_seconds=0"
    ) as websocket:
        message = websocket.receive_json()
        idle = websocket.receive_json()

    assert message["type"] == "semantic_event"
    assert message["event"]["type"] == "session.created"
    assert idle == {"type": "idle_timeout", "last_seq": message["event"]["seq"]}


def test_session_events_websocket_accepts_client_certificate_fingerprint(
    monkeypatch,
    tmp_path,
):
    control = ControlPlane()
    actor = Actor(id="usr_1", roles={"maintainer"})
    project = control.create_project(
        actor=actor,
        name="Client Certificate Backend",
        trace_id="client-cert-project",
    )
    workspace = control.add_workspace(
        actor=actor,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="client-cert-workspace",
    )
    session = control.create_session(
        actor=actor,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Client Certificate Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="client-cert-session",
    )
    monkeypatch.setenv("AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS", "AA:BB:CC")
    client = TestClient(create_app(control))

    with client.websocket_connect(
        f"/api/v1/sessions/{session.id}/events/ws?idle_timeout_seconds=0"
    ) as websocket:
        denied = websocket.receive_json()

    assert denied["type"] == "error"
    assert denied["error"]["error_code"] == "PERMISSION_DENIED"

    with client.websocket_connect(
        f"/api/v1/sessions/{session.id}/events/ws?idle_timeout_seconds=0",
        headers={"x-agentbridge-client-cert-fingerprint": "sha256:aa:bb:cc"},
    ) as websocket:
        message = websocket.receive_json()
        idle = websocket.receive_json()

    assert message["type"] == "semantic_event"
    assert message["event"]["type"] == "session.created"
    assert idle == {"type": "idle_timeout", "last_seq": message["event"]["seq"]}


def test_session_events_websocket_accepts_managed_device_key(tmp_path):
    control = ControlPlane()
    admin = Actor(id="security-admin", roles={"admin"})
    control.upsert_device_identity(
        actor=admin,
        device_id="laptop",
        device_key="managed-secret",
        certificate_fingerprints={"AA:BB:CC"},
        trace_id="managed-device-ws-create",
    )
    actor = Actor(id="usr_1", roles={"maintainer"})
    project = control.create_project(
        actor=actor,
        name="Managed Device Backend",
        trace_id="managed-device-project",
    )
    workspace = control.add_workspace(
        actor=actor,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="managed-device-workspace",
    )
    session = control.create_session(
        actor=actor,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Managed Device Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="managed-device-session",
    )
    client = TestClient(create_app(control))

    with client.websocket_connect(
        f"/api/v1/sessions/{session.id}/events/ws?idle_timeout_seconds=0"
    ) as websocket:
        denied = websocket.receive_json()

    assert denied["type"] == "error"
    assert denied["error"]["error_code"] == "PERMISSION_DENIED"

    with client.websocket_connect(
        f"/api/v1/sessions/{session.id}/events/ws"
        "?device_id=laptop&device_key=managed-secret&idle_timeout_seconds=0"
    ) as websocket:
        message = websocket.receive_json()
        idle = websocket.receive_json()
    with client.websocket_connect(
        f"/api/v1/sessions/{session.id}/events/ws?idle_timeout_seconds=0",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    ) as websocket:
        certificate_message = websocket.receive_json()
        certificate_idle = websocket.receive_json()

    assert message["type"] == "semantic_event"
    assert message["event"]["type"] == "session.created"
    assert idle == {"type": "idle_timeout", "last_seq": message["event"]["seq"]}
    assert certificate_message["type"] == "semantic_event"
    assert certificate_message["event"]["type"] == "session.created"
    assert certificate_idle == {
        "type": "idle_timeout",
        "last_seq": certificate_message["event"]["seq"],
    }


def test_managed_device_identity_scope_limits_websocket(tmp_path):
    control = ControlPlane()
    admin = Actor(id="security-admin", roles={"admin"})
    control.upsert_device_identity(
        actor=admin,
        device_id="laptop",
        device_key="managed-secret",
        allowed_scopes={DeviceIdentityScope.HTTP_API},
        certificate_fingerprints={"AA:BB:CC"},
        trace_id="managed-device-scope-create",
    )
    actor = Actor(id="usr_1", roles={"maintainer"})
    project = control.create_project(
        actor=actor,
        name="Scoped Device Backend",
        trace_id="managed-device-scope-project",
    )
    workspace = control.add_workspace(
        actor=actor,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="managed-device-scope-workspace",
    )
    session = control.create_session(
        actor=actor,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Scoped Device Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="managed-device-scope-session",
    )
    client = TestClient(create_app(control))

    http_response = client.get(
        "/api/v1/projects",
        headers={
            "x-agentbridge-device-id": "laptop",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    http_certificate_response = client.get(
        "/api/v1/projects",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    with client.websocket_connect(
        f"/api/v1/sessions/{session.id}/events/ws"
        "?device_id=laptop&device_key=managed-secret&idle_timeout_seconds=0"
    ) as websocket:
        key_denied = websocket.receive_json()
    with client.websocket_connect(
        f"/api/v1/sessions/{session.id}/events/ws?idle_timeout_seconds=0",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    ) as websocket:
        certificate_denied = websocket.receive_json()

    assert http_response.status_code == 200
    assert http_certificate_response.status_code == 200
    assert key_denied["type"] == "error"
    assert key_denied["error"]["error_code"] == "PERMISSION_DENIED"
    assert certificate_denied["type"] == "error"
    assert certificate_denied["error"]["error_code"] == "PERMISSION_DENIED"
    identity = control.repository.get_device_identity("laptop")
    assert identity.last_used_at is not None


def test_session_events_websocket_accepts_unlocked_admin_cookie(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBRIDGE_WS_TOKEN", "ws-secret")
    monkeypatch.setenv("AGENTBRIDGE_ADMIN_TOKEN", "admin-secret")
    client = TestClient(create_app())

    unlock_response = client.get("/admin?admin_token=admin-secret")
    assert unlock_response.status_code == 200
    assert "AgentBridge Admin" in unlock_response.text
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-events-ws-admin-cookie",
        prefix="event-ws-admin-cookie",
        name="Event WS Admin Cookie",
    )

    with client.websocket_connect(
        f"/api/v1/sessions/{session_id}/events/ws?idle_timeout_seconds=0"
    ) as websocket:
        message = websocket.receive_json()
        idle = websocket.receive_json()

    assert message["type"] == "semantic_event"
    assert message["event"]["type"] == "session.created"
    assert idle == {"type": "idle_timeout", "last_seq": message["event"]["seq"]}


def test_terminal_websocket_rejects_admin_cookie_without_ws_token(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBRIDGE_WS_TOKEN", "ws-secret")
    monkeypatch.setenv("AGENTBRIDGE_ADMIN_TOKEN", "admin-secret")
    client = TestClient(create_app())

    unlock_response = client.get("/admin?admin_token=admin-secret")
    assert unlock_response.status_code == 200
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="terminal-ws-admin-cookie",
        prefix="terminal-ws-admin-cookie",
        name="Terminal WS Admin Cookie",
    )

    with client.websocket_connect(f"/api/v1/sessions/{session_id}/terminal/ws") as websocket:
        denied = websocket.receive_json()

    assert denied["type"] == "error"
    assert denied["error"]["error_code"] == "PERMISSION_DENIED"


def test_terminal_websocket_accepts_commands_with_token(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBRIDGE_WS_TOKEN", "secret")
    client = TestClient(create_app())
    actor = {"id": "usr_1", "roles": ["maintainer"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-terminal-ws-token",
        prefix="terminal-ws-token",
        name="Terminal WS Token",
    )

    with client.websocket_connect(
        f"/api/v1/sessions/{session_id}/terminal/ws?token=secret"
    ) as websocket:
        websocket.send_json(
            {
                "id": "start",
                "type": "start_session",
                "payload": {
                    "actor": actor,
                    "command": "fake-cli",
                    "trace_id": "terminal-ws-start",
                },
            }
        )
        started = websocket.receive_json()
        assert started["type"] == "terminal.result"
        assert started["id"] == "start"
        assert started["ok"] is True
        assert started["data"] == {"status": "started"}

        websocket.send_json(
            {
                "id": "lease",
                "type": "acquire_lease",
                "payload": {
                    "actor": actor,
                    "owner_type": "web_admin",
                    "owner_id": "usr_1",
                    "trace_id": "terminal-ws-lease",
                },
            }
        )
        leased = websocket.receive_json()
        epoch = leased["data"]["lease"]["epoch"]
        assert leased["type"] == "terminal.result"
        assert leased["ok"] is True

        websocket.send_json(
            {
                "id": "input",
                "type": "submit_input",
                "payload": {
                    "actor": actor,
                    "epoch": epoch,
                    "owner_type": "web_admin",
                    "owner_id": "usr_1",
                    "type": "text",
                    "data": "hello ws\n",
                    "request_id": "terminal-ws-input",
                    "trace_id": "terminal-ws-input",
                },
            }
        )
        submitted = websocket.receive_json()
        assert submitted["type"] == "terminal.result"
        assert submitted["data"] == {"request_id": "terminal-ws-input"}

        websocket.send_json(
            {
                "id": "snapshot",
                "type": "snapshot",
                "payload": {"actor": actor},
            }
        )
        snapshot = websocket.receive_json()
        assert snapshot["type"] == "terminal.result"
        assert snapshot["data"] == {"snapshot": "hello ws\n"}

        websocket.send_json(
            {
                "id": "status",
                "type": "status",
                "payload": {"actor": actor, "trace_id": "terminal-ws-status"},
            }
        )
        status = websocket.receive_json()

    assert status["type"] == "terminal.result"
    assert status["data"] == {
        "started": True,
        "running": True,
        "exit_code": None,
        "pid": None,
        "output_cursor": 9,
        "output_base_cursor": 0,
        "output_retained_chars": 9,
    }


def test_terminal_lifecycle_monitor_can_autostart_from_api_env(monkeypatch):
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_LIFECYCLE_MONITOR_ENABLED", "true")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_LIFECYCLE_POLL_INTERVAL_SECONDS", "60")

    app = create_app()

    assert app.state.terminal.is_lifecycle_monitor_running() is False
    with TestClient(app):
        assert app.state.terminal.is_lifecycle_monitor_running() is True
        assert app.state.terminal.lifecycle_monitor_status()["interval_seconds"] == 60.0
    assert app.state.terminal.is_lifecycle_monitor_running() is False


def test_terminal_lifecycle_policy_reads_auto_restart_env(monkeypatch):
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_AUTO_RESTART_ON_LOST", "true")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_AUTO_RESTART_MAX_ATTEMPTS", "3")
    monkeypatch.setenv(
        "AGENTBRIDGE_TERMINAL_AUTO_RESTART_COMMAND_ALLOWLIST",
        "codex*, claude*",
    )

    app = create_app()

    status = app.state.terminal.lifecycle_monitor_status()
    assert status["auto_restart_on_lost"] is True
    assert status["auto_restart_max_attempts"] == 3
    assert status["auto_restart_command_allowlist"] == ["codex*", "claude*"]
    assert status["auto_restart_attempt_count"] == 0
    assert status["auto_restart_blocked_count"] == 0


def test_terminal_lifecycle_monitor_status_and_run_once_api(tmp_path):
    app = create_app()
    client = TestClient(app)
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-terminal-lifecycle",
        prefix="terminal-lifecycle",
        name="Terminal Lifecycle",
    )
    client.post(
        f"/api/v1/sessions/{session_id}/terminal/start",
        json={
            "actor": {"id": "usr_1", "roles": ["maintainer"]},
            "command": "fake-cli --watch",
            "trace_id": "terminal-lifecycle-start",
        },
    )

    status_response = client.get("/api/v1/terminal/lifecycle-monitor")
    assert status_response.status_code == 200
    status = status_response.json()
    assert status["tracked_sessions"] == 1
    assert status["backend_supervision"] == {"enabled": False}

    denied_response = client.post(
        "/api/v1/terminal/lifecycle-monitor/run-once",
        json={"actor": {"id": "usr_member", "roles": ["member"]}},
    )
    assert denied_response.status_code == 403

    run_response = client.post(
        "/api/v1/terminal/lifecycle-monitor/run-once",
        json={
            "actor": {"id": "usr_1", "roles": ["maintainer"]},
            "trace_id": "terminal-lifecycle-run-once",
        },
    )

    assert run_response.status_code == 200
    payload = run_response.json()
    assert payload["monitor"]["run_count"] == 1
    assert payload["observed"][session_id] == {
        "started": True,
        "running": True,
        "exit_code": None,
        "pid": None,
        "output_cursor": 0,
        "output_base_cursor": 0,
        "output_retained_chars": 0,
    }


def test_managed_device_identity_requires_terminal_control_scope_for_terminal_http_apis(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    actor = {"id": "usr_1", "roles": ["maintainer"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-terminal-control-scope",
        prefix="terminal-control-scope",
        name="Terminal Control Scope",
    )

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "terminal-control-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    regular_http_response = client.get("/api/v1/projects", headers=key_headers)
    lifecycle_status_response = client.get(
        "/api/v1/terminal/lifecycle-monitor",
        headers=key_headers,
    )
    start_response = client.post(
        f"/api/v1/sessions/{session_id}/terminal/start",
        json={
            "actor": actor,
            "command": "fake-cli",
            "trace_id": "terminal-control-denied-start",
        },
        headers=key_headers,
    )
    run_once_response = client.post(
        "/api/v1/terminal/lifecycle-monitor/run-once",
        json={"actor": actor, "trace_id": "terminal-control-denied-run-once"},
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_response.status_code == 200
    assert regular_http_response.status_code == 200
    assert lifecycle_status_response.status_code == 200
    assert start_response.status_code == 403
    assert run_once_response.status_code == 403


def test_managed_device_identity_terminal_control_scope_allows_terminal_http_apis(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    actor = {"id": "usr_1", "roles": ["maintainer"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-terminal-control-manager-scope",
        prefix="terminal-control-manager-scope",
        name="Terminal Control Manager Scope",
    )

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "terminal-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "session_manage", "terminal_control"],
            "trace_id": "terminal-control-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "terminal-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    start_response = client.post(
        f"/api/v1/sessions/{session_id}/terminal/start",
        json={
            "actor": actor,
            "command": "fake-cli",
            "trace_id": "terminal-control-manager-start",
        },
        headers=headers,
    )
    lease_response = client.post(
        f"/api/v1/sessions/{session_id}/lease/acquire",
        json={
            "actor": actor,
            "owner_type": "web_admin",
            "owner_id": "usr_1",
            "trace_id": "terminal-control-manager-lease",
        },
        headers=headers,
    )
    input_response = client.post(
        f"/api/v1/sessions/{session_id}/terminal/input",
        json={
            "actor": actor,
            "epoch": lease_response.json().get("epoch"),
            "owner_type": "web_admin",
            "owner_id": "usr_1",
            "type": "text",
            "data": "hello terminal control\n",
            "request_id": "terminal-control-manager-input",
            "trace_id": "terminal-control-manager-input",
        },
        headers=headers,
    )
    run_once_response = client.post(
        "/api/v1/terminal/lifecycle-monitor/run-once",
        json={"actor": actor, "trace_id": "terminal-control-manager-run-once"},
        headers=headers,
    )

    assert create_response.status_code == 200
    assert start_response.status_code == 200
    assert lease_response.status_code == 200
    assert input_response.status_code == 200
    assert input_response.json() == {"request_id": "terminal-control-manager-input"}
    assert run_once_response.status_code == 200
    assert run_once_response.json()["monitor"]["run_count"] == 1


def test_terminal_restart_api_uses_last_started_command_after_backend_state_loss(tmp_path):
    app = create_app()
    client = TestClient(app)
    actor = {"id": "usr_1", "roles": ["maintainer"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-terminal-restart",
        prefix="terminal-restart",
        name="Terminal Restart",
    )
    start_response = client.post(
        f"/api/v1/sessions/{session_id}/terminal/start",
        json={
            "actor": actor,
            "command": "fake-cli --resume",
            "trace_id": "terminal-restart-api-start",
        },
    )
    assert start_response.status_code == 200

    recovered_backend = FakeTerminalBackend()
    app.state.terminal = TerminalAgentService(app.state.control, backend=recovered_backend)

    restart_response = client.post(
        f"/api/v1/sessions/{session_id}/terminal/restart",
        json={"actor": actor, "trace_id": "terminal-restart-api"},
    )

    assert restart_response.status_code == 200
    assert restart_response.json() == {
        "status": "restarted",
        "restarted": True,
        "command": "fake-cli --resume",
        "previous_generation": 1,
        "generation": 2,
    }
    assert recovered_backend.started[session_id] == (str(tmp_path), "fake-cli --resume")


def test_terminal_websocket_returns_error_frames_for_bad_lease(tmp_path):
    client = TestClient(create_app())
    actor = {"id": "usr_1", "roles": ["maintainer"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-terminal-ws-error",
        prefix="terminal-ws-error",
        name="Terminal WS Error",
    )

    with client.websocket_connect(f"/api/v1/sessions/{session_id}/terminal/ws") as websocket:
        websocket.send_json(
            {
                "id": "stale-input",
                "type": "submit_input",
                "payload": {
                    "actor": actor,
                    "epoch": 1,
                    "owner_type": "web_admin",
                    "owner_id": "usr_1",
                    "type": "text",
                    "data": "stale\n",
                    "request_id": "terminal-ws-stale",
                    "trace_id": "terminal-ws-stale",
                },
            }
        )
        error = websocket.receive_json()

    assert error["type"] == "terminal.error"
    assert error["id"] == "stale-input"
    assert error["ok"] is False
    assert error["error"]["error_code"] == "LEASE_CONFLICT"


def test_terminal_websocket_restart_uses_last_started_command(tmp_path):
    app = create_app()
    client = TestClient(app)
    actor = {"id": "usr_1", "roles": ["maintainer"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-terminal-ws-restart",
        prefix="terminal-ws-restart",
        name="Terminal WS Restart",
    )
    start_response = client.post(
        f"/api/v1/sessions/{session_id}/terminal/start",
        json={
            "actor": actor,
            "command": "fake-cli --resume",
            "trace_id": "terminal-ws-restart-start",
        },
    )
    assert start_response.status_code == 200

    recovered_backend = FakeTerminalBackend()
    app.state.terminal = TerminalAgentService(app.state.control, backend=recovered_backend)

    with client.websocket_connect(f"/api/v1/sessions/{session_id}/terminal/ws") as websocket:
        websocket.send_json(
            {
                "id": "restart",
                "type": "restart_session",
                "payload": {"actor": actor, "trace_id": "terminal-ws-restart"},
            }
        )
        result = websocket.receive_json()

    assert result == {
        "type": "terminal.result",
        "id": "restart",
        "action": "restart_session",
        "ok": True,
        "data": {
            "status": "restarted",
            "restarted": True,
            "command": "fake-cli --resume",
            "previous_generation": 1,
            "generation": 2,
        },
    }
    assert recovered_backend.started[session_id] == (str(tmp_path), "fake-cli --resume")
