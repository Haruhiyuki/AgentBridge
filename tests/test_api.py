from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import threading
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi.testclient import TestClient

from agentbridge.api import create_app
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    Actor,
    DeviceCertificateRecord,
    DeviceIdentityScope,
    TurnStatus,
    Visibility,
    utc_now,
)
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


def _write_test_ca(tmp_path):
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AgentBridge Test CA")])
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_subject)
        .issuer_name(ca_subject)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=5))
        .not_valid_after(datetime.now(UTC) + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    ca_certificate_path = tmp_path / "device-ca.pem"
    ca_key_path = tmp_path / "device-ca-key.pem"
    ca_certificate_path.write_bytes(ca_certificate.public_bytes(serialization.Encoding.PEM))
    ca_key_path.write_bytes(
        ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca_certificate_path, ca_key_path


def _device_csr_pem(device_id: str) -> str:
    device_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)]))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(device_id)]),
            critical=False,
        )
        .sign(device_key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode("utf-8")


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
        "/api/v1/commands",
        headers={
            "x-agentbridge-device-id": "laptop",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    certificate_response = client.get(
        "/api/v1/commands",
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
        "/api/v1/commands",
        headers={
            "x-agentbridge-device-id": "laptop",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    revoked_certificate_response = client.get(
        "/api/v1/commands",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    still_gated_response = client.get("/api/v1/projects")

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["device_id"] == "laptop"
    assert created["display_name"] == "Maintainer laptop"
    assert created["status"] == "active"
    assert created["allowed_scopes"] == ["device_manage", "http_api"]
    assert created["allowed_resource_ids"] == []
    assert created["certificate_fingerprints"] == ["aabbcc"]
    assert created["certificate_records"][0]["fingerprint"] == "aabbcc"
    assert created["certificate_records"][0]["source"] == "fingerprint_import"
    assert created["certificate_records"][0]["removed_at"] is None
    assert created["device_key"] == "managed-secret"
    assert "key_hash" not in created
    assert "key_salt" not in created
    assert denied_response.status_code == 403
    assert authorized_response.status_code == 200
    assert "help" in authorized_response.json()["commands"]
    assert certificate_response.status_code == 200
    assert "help" in certificate_response.json()["commands"]
    assert list_response.status_code == 200
    listed = list_response.json()[0]
    assert listed["device_id"] == "laptop"
    assert listed["allowed_scopes"] == ["device_manage", "http_api"]
    assert listed["allowed_resource_ids"] == []
    assert listed["certificate_fingerprints"] == ["aabbcc"]
    assert listed["certificate_records"][0]["fingerprint"] == "aabbcc"
    assert listed["last_used_at"] is not None
    assert "device_key" not in listed
    assert revoke_response.status_code == 200
    assert revoke_response.json()["status"] == "revoked"
    assert revoked_key_response.status_code == 403
    assert revoked_certificate_response.status_code == 403
    assert still_gated_response.status_code == 403


def test_managed_device_identity_resource_ids_limit_rest_api(tmp_path):
    control = ControlPlane()
    admin = Actor(id="security-admin", roles={"admin"})
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    allowed_project = control.create_project(
        actor=maintainer,
        name="Allowed Device Project",
        trace_id="device-resource-allowed-project",
    )
    denied_project = control.create_project(
        actor=maintainer,
        name="Denied Device Project",
        trace_id="device-resource-denied-project",
    )
    allowed_workspace = control.add_workspace(
        actor=maintainer,
        project_id=allowed_project.id,
        machine_id="local",
        path=str(tmp_path / "allowed"),
        allowed_root=str(tmp_path),
        trace_id="device-resource-allowed-workspace",
    )
    denied_workspace = control.add_workspace(
        actor=maintainer,
        project_id=denied_project.id,
        machine_id="local",
        path=str(tmp_path / "denied"),
        allowed_root=str(tmp_path),
        trace_id="device-resource-denied-workspace",
    )
    allowed_session = control.create_session(
        actor=maintainer,
        project_id=allowed_project.id,
        workspace_id=allowed_workspace.id,
        name="Allowed Device Session",
        agent_type=allowed_project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="device-resource-allowed-session",
    )
    denied_session = control.create_session(
        actor=maintainer,
        project_id=denied_project.id,
        workspace_id=denied_workspace.id,
        name="Denied Device Session",
        agent_type=denied_project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="device-resource-denied-session",
    )
    identity, _device_key = control.upsert_device_identity(
        actor=admin,
        device_id="scoped-laptop",
        device_key="managed-secret",
        allowed_scopes={
            DeviceIdentityScope.AUDIT_READ,
            DeviceIdentityScope.SESSION_READ,
        },
        allowed_resource_ids={allowed_project.id, allowed_session.id},
        certificate_fingerprints={"SHA256:AA:BB:CC"},
        trace_id="device-resource-create",
    )
    client = TestClient(create_app(control))
    key_headers = {
        "x-agentbridge-device-id": "scoped-laptop",
        "x-agentbridge-device-key": "managed-secret",
    }
    certificate_headers = {"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"}

    allowed_queue_response = client.get(
        f"/api/v1/sessions/{allowed_session.id}/queue",
        headers=key_headers,
    )
    denied_queue_response = client.get(
        f"/api/v1/sessions/{denied_session.id}/queue",
        headers=key_headers,
    )
    allowed_query_response = client.get(
        f"/api/v1/events?session_id={allowed_session.id}",
        headers=certificate_headers,
    )
    denied_query_response = client.get(
        f"/api/v1/events?session_id={denied_session.id}",
        headers=certificate_headers,
    )
    allowed_project_query_response = client.get(
        f"/api/v1/sessions?project_id={allowed_project.id}",
        headers=key_headers,
    )
    denied_project_query_response = client.get(
        f"/api/v1/sessions?project_id={denied_project.id}",
        headers=key_headers,
    )
    unscoped_collection_response = client.get(
        "/api/v1/sessions",
        headers=key_headers,
    )
    public_payload_response = client.get(
        f"/api/v1/events?session_id={allowed_session.id}",
        headers=key_headers,
    )

    assert identity.allowed_resource_ids == {allowed_project.id, allowed_session.id}
    assert allowed_queue_response.status_code == 200
    assert denied_queue_response.status_code == 403
    assert allowed_query_response.status_code == 200
    assert denied_query_response.status_code == 403
    assert allowed_project_query_response.status_code == 200
    assert denied_project_query_response.status_code == 403
    assert unscoped_collection_response.status_code == 403
    assert public_payload_response.status_code == 200
    used_identity = control.repository.get_device_identity("scoped-laptop")
    assert used_identity.last_used_at is not None


def test_managed_device_identity_resource_ids_limit_body_resources(tmp_path):
    control = ControlPlane()
    admin = Actor(id="security-admin", roles={"admin"})
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    allowed_project = control.create_project(
        actor=maintainer,
        name="Allowed Body Project",
        trace_id="device-body-allowed-project",
    )
    denied_project = control.create_project(
        actor=maintainer,
        name="Denied Body Project",
        trace_id="device-body-denied-project",
    )
    allowed_workspace = control.add_workspace(
        actor=maintainer,
        project_id=allowed_project.id,
        machine_id="local",
        path=str(tmp_path / "allowed"),
        allowed_root=str(tmp_path),
        trace_id="device-body-allowed-workspace",
    )
    denied_workspace = control.add_workspace(
        actor=maintainer,
        project_id=denied_project.id,
        machine_id="local",
        path=str(tmp_path / "denied"),
        allowed_root=str(tmp_path),
        trace_id="device-body-denied-workspace",
    )
    control.upsert_device_identity(
        actor=admin,
        device_id="body-scoped-laptop",
        device_key="managed-secret",
        allowed_scopes={
            DeviceIdentityScope.POLICY_READ,
            DeviceIdentityScope.SESSION_MANAGE,
        },
        allowed_resource_ids={allowed_project.id},
        certificate_fingerprints={"SHA256:AA:BB:CC"},
        trace_id="device-body-create",
    )
    client = TestClient(create_app(control))
    key_headers = {
        "x-agentbridge-device-id": "body-scoped-laptop",
        "x-agentbridge-device-key": "managed-secret",
    }
    certificate_headers = {"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"}
    session_payload = {
        "actor": {"id": "usr_maintainer", "roles": ["maintainer"]},
        "name": "Body Scoped Session",
        "agent_type": "claude",
        "visibility": "group",
    }

    allowed_session_response = client.post(
        "/api/v1/sessions",
        json={
            **session_payload,
            "project_id": allowed_project.id,
            "workspace_id": allowed_workspace.id,
            "trace_id": "device-body-allowed-session",
        },
        headers=key_headers,
    )
    denied_session_response = client.post(
        "/api/v1/sessions",
        json={
            **session_payload,
            "project_id": denied_project.id,
            "workspace_id": denied_workspace.id,
            "trace_id": "device-body-denied-session",
        },
        headers=certificate_headers,
    )
    allowed_policy_response = client.post(
        "/api/v1/access-policy/simulate",
        json={
            "actor": {"id": "security-admin", "roles": ["admin"]},
            "target_actor": {"id": "usr_maintainer", "roles": ["maintainer"]},
            "action": "session.create",
            "resource_type": "project",
            "resource_id": allowed_project.id,
        },
        headers=key_headers,
    )
    denied_policy_response = client.post(
        "/api/v1/access-policy/simulate",
        json={
            "actor": {"id": "security-admin", "roles": ["admin"]},
            "target_actor": {"id": "usr_maintainer", "roles": ["maintainer"]},
            "action": "session.create",
            "resource_type": "project",
            "resource_id": denied_project.id,
        },
        headers=key_headers,
    )

    assert allowed_session_response.status_code == 200
    assert allowed_session_response.json()["project_id"] == allowed_project.id
    assert denied_session_response.status_code == 403
    assert allowed_policy_response.status_code == 200
    assert denied_policy_response.status_code == 403


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
        "/api/v1/commands",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    key_response = client.get(
        "/api/v1/commands",
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
        "/api/v1/commands",
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


def test_managed_device_identity_rotates_certificate_fingerprints():
    control = ControlPlane()
    client = TestClient(create_app(control))
    actor = {"id": "security-admin", "roles": ["admin"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": actor,
            "device_id": "rotating-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "device_manage"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "managed-device-rotation-create",
        },
    )
    rotate_response = client.post(
        "/api/v1/device-identities/rotating-device/certificate-fingerprints/rotate",
        json={
            "actor": actor,
            "add_fingerprints": ["SHA256:DD:EE:FF"],
            "remove_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "managed-device-certificate-rotate",
        },
        headers={
            "x-agentbridge-device-id": "rotating-device",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    old_certificate_response = client.get(
        "/api/v1/commands",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    new_certificate_response = client.get(
        "/api/v1/commands",
        headers={"x-agentbridge-client-cert-fingerprint": "dd:ee:ff"},
    )
    key_response = client.get(
        "/api/v1/commands",
        headers={
            "x-agentbridge-device-id": "rotating-device",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    audit_events = control.repository.list_audit_events(
        action="device_identity.certificate_fingerprints_rotated"
    )

    assert create_response.status_code == 200
    assert rotate_response.status_code == 200
    rotated = rotate_response.json()
    assert rotated["device_id"] == "rotating-device"
    assert rotated["allowed_scopes"] == ["device_manage", "http_api"]
    assert rotated["certificate_fingerprints"] == ["ddeeff"]
    old_record = next(
        record
        for record in rotated["certificate_records"]
        if record["fingerprint"] == "aabbcc"
    )
    new_record = next(
        record
        for record in rotated["certificate_records"]
        if record["fingerprint"] == "ddeeff"
    )
    assert old_record["source"] == "fingerprint_import"
    assert old_record["removed_at"] is not None
    assert old_record["removed_by"] == "security-admin"
    assert new_record["source"] == "fingerprint_rotation"
    assert new_record["removed_at"] is None
    assert old_certificate_response.status_code == 403
    assert new_certificate_response.status_code == 200
    assert key_response.status_code == 200
    assert len(audit_events) == 1
    assert audit_events[0].details["added_fingerprints"] == ["ddeeff"]
    assert audit_events[0].details["removed_fingerprints"] == ["aabbcc"]


def test_managed_device_identity_rejects_removing_last_certificate_only_fingerprint():
    client = TestClient(create_app())
    actor = {"id": "security-admin", "roles": ["admin"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": actor,
            "device_id": "cert-only-rotation",
            "allowed_scopes": ["http_api", "device_manage"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "managed-device-cert-only-rotation-create",
        },
    )
    rotate_response = client.post(
        "/api/v1/device-identities/cert-only-rotation/certificate-fingerprints/rotate",
        json={
            "actor": actor,
            "remove_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "managed-device-cert-only-rotation-remove-last",
        },
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    still_authorized_response = client.get(
        "/api/v1/commands",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_response.status_code == 200
    assert "device_key" not in create_response.json()
    assert rotate_response.status_code == 400
    assert rotate_response.json()["error_code"] == "COMMAND_ARGUMENT_INVALID"
    assert still_authorized_response.status_code == 200


def test_managed_device_identity_issues_ca_backed_certificate(monkeypatch, tmp_path):
    ca_certificate_path, ca_key_path = _write_test_ca(tmp_path)
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_CA_CERT_FILE", str(ca_certificate_path))
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_CA_KEY_FILE", str(ca_key_path))
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_DEFAULT_VALIDITY_DAYS", "14")
    control = ControlPlane()
    client = TestClient(create_app(control))
    actor = {"id": "security-admin", "roles": ["admin"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": actor,
            "device_id": "issued-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "device_manage"],
            "trace_id": "managed-device-issue-create",
        },
    )
    issue_response = client.post(
        "/api/v1/device-identities/issued-device/certificates/issue",
        json={
            "actor": actor,
            "csr_pem": _device_csr_pem("issued-device"),
            "validity_days": 7,
            "trace_id": "managed-device-cert-issue",
        },
        headers={
            "x-agentbridge-device-id": "issued-device",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    issued = issue_response.json()
    issued_certificate = x509.load_pem_x509_certificate(
        issued["certificate_pem"].encode("utf-8")
    )
    issued_fingerprint = issued_certificate.fingerprint(hashes.SHA256()).hex()
    extended_key_usage = issued_certificate.extensions.get_extension_for_class(
        x509.ExtendedKeyUsage
    ).value
    certificate_response = client.get(
        "/api/v1/commands",
        headers={"x-agentbridge-client-cert-fingerprint": issued_fingerprint},
    )
    audit_events = control.repository.list_audit_events(
        action="device_identity.certificate_issued"
    )

    assert create_response.status_code == 200
    assert issue_response.status_code == 200
    assert issued["certificate_fingerprint"] == issued_fingerprint
    assert issued["device_identity"]["certificate_fingerprints"] == [issued_fingerprint]
    issued_record = issued["device_identity"]["certificate_records"][0]
    assert issued_record["fingerprint"] == issued_fingerprint
    assert issued_record["source"] == "managed_ca"
    assert issued_record["serial_number"] == str(issued_certificate.serial_number)
    assert issued_record["subject"] == "CN=issued-device"
    assert issued_record["issuer"] == issued["issuer"]
    assert issued_record["not_after"] == issued["not_after"]
    assert issued_record["removed_at"] is None
    certificate_health = issued["device_identity"]["certificate_health"]
    assert certificate_health["status"] == "expiring"
    assert certificate_health["warning_days"] == 14
    assert certificate_health["expiring_count"] == 1
    assert certificate_health["expiring_fingerprints"] == [issued_fingerprint]
    assert certificate_health["next_expires_at"] == issued["not_after"]
    assert certificate_health["managed_ca_active_certificate_count"] == 1
    assert certificate_health["renewal_status"] == "due"
    assert certificate_health["renewal_due_count"] == 1
    assert certificate_health["renewal_due_fingerprints"] == [issued_fingerprint]
    assert certificate_health["renewal_due_at"] is not None
    assert issued["device_identity"]["allowed_scopes"] == ["device_manage", "http_api"]
    assert ExtendedKeyUsageOID.CLIENT_AUTH in extended_key_usage
    assert certificate_response.status_code == 200
    assert len(audit_events) == 1
    assert audit_events[0].details["certificate_fingerprint"] == issued_fingerprint
    assert audit_events[0].details["device_id"] == "issued-device"


def test_managed_device_identity_issues_certificate_with_external_issuer(
    monkeypatch,
    tmp_path,
):
    ca_certificate_path, ca_key_path = _write_test_ca(tmp_path)
    issuer_script = tmp_path / "device_certificate_issuer.py"
    issuer_script.write_text(
        "\n".join(
            [
                "import hashlib",
                "import json",
                "import os",
                "import sys",
                "from datetime import UTC, datetime, timedelta",
                "from cryptography import x509",
                "from cryptography.hazmat.primitives import hashes, serialization",
                "from cryptography.x509.oid import ExtendedKeyUsageOID",
                "ca_cert_path, ca_key_path = sys.argv[1], sys.argv[2]",
                "payload = json.load(sys.stdin)",
                "csr_pem = payload['csr_pem']",
                "csr_sha256 = hashlib.sha256(csr_pem.encode('utf-8')).hexdigest()",
                "if os.environ['AGENTBRIDGE_DEVICE_CERT_DEVICE_ID'] != payload['device_id']:",
                "    raise SystemExit('device id env mismatch')",
                "if os.environ['AGENTBRIDGE_DEVICE_CERT_CSR_SHA256'] != csr_sha256:",
                "    raise SystemExit('csr digest mismatch')",
                "env_validity = int(os.environ['AGENTBRIDGE_DEVICE_CERT_VALIDITY_DAYS'])",
                "if env_validity != payload['validity_days']:",
                "    raise SystemExit('validity env mismatch')",
                "csr = x509.load_pem_x509_csr(csr_pem.encode('utf-8'))",
                "ca_cert_pem = open(ca_cert_path, encoding='utf-8').read()",
                "ca_cert = x509.load_pem_x509_certificate(ca_cert_pem.encode('utf-8'))",
                "ca_key_pem = open(ca_key_path, 'rb').read()",
                "ca_key = serialization.load_pem_private_key(ca_key_pem, password=None)",
                "not_before = datetime.now(UTC) - timedelta(minutes=5)",
                "not_after = datetime.now(UTC) + timedelta(days=payload['validity_days'])",
                "builder = (",
                "    x509.CertificateBuilder()",
                "    .subject_name(csr.subject)",
                "    .issuer_name(ca_cert.subject)",
                "    .public_key(csr.public_key())",
                "    .serial_number(x509.random_serial_number())",
                "    .not_valid_before(not_before)",
                "    .not_valid_after(not_after)",
                "    .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)",
                "    .add_extension(",
                "        x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),",
                "        False,",
                "    )",
                ")",
                "try:",
                "    san = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName)",
                "except x509.ExtensionNotFound:",
                "    san = None",
                "if san is not None:",
                "    builder = builder.add_extension(san.value, san.critical)",
                "cert = builder.sign(ca_key, hashes.SHA256())",
                "cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode('utf-8')",
                "json.dump({",
                "    'certificate_pem': cert_pem,",
                "    'ca_certificate_pem': ca_cert_pem,",
                "}, sys.stdout)",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("AGENTBRIDGE_DEVICE_CERT_CA_CERT_FILE", raising=False)
    monkeypatch.delenv("AGENTBRIDGE_DEVICE_CERT_CA_KEY_FILE", raising=False)
    monkeypatch.setenv(
        "AGENTBRIDGE_DEVICE_CERT_ISSUER_COMMAND",
        f"{sys.executable} {issuer_script} {ca_certificate_path} {ca_key_path}",
    )
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_DEFAULT_VALIDITY_DAYS", "9")
    control = ControlPlane()
    client = TestClient(create_app(control))
    actor = {"id": "security-admin", "roles": ["admin"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": actor,
            "device_id": "external-issued-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "device_manage"],
            "trace_id": "managed-device-external-issue-create",
        },
    )
    issue_response = client.post(
        "/api/v1/device-identities/external-issued-device/certificates/issue",
        json={
            "actor": actor,
            "csr_pem": _device_csr_pem("external-issued-device"),
            "trace_id": "managed-device-cert-external-issue",
        },
        headers={
            "x-agentbridge-device-id": "external-issued-device",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    issued = issue_response.json()
    certificate = x509.load_pem_x509_certificate(
        issued["certificate_pem"].encode("utf-8")
    )
    certificate_response = client.get(
        "/api/v1/commands",
        headers={
            "x-agentbridge-client-cert-fingerprint": (
                issued["certificate_fingerprint"]
            )
        },
    )
    audit_events = control.repository.list_audit_events(
        action="device_identity.certificate_issued"
    )

    assert create_response.status_code == 200
    assert issue_response.status_code == 200
    assert issued["certificate_fingerprint"] == certificate.fingerprint(
        hashes.SHA256()
    ).hex()
    assert issued["ca_certificate_pem"]
    assert issued["device_identity"]["certificate_fingerprints"] == [
        issued["certificate_fingerprint"]
    ]
    issued_record = issued["device_identity"]["certificate_records"][0]
    assert issued_record["source"] == "managed_ca"
    assert issued_record["issuer"] == "CN=AgentBridge Test CA"
    assert issued_record["not_after"] == issued["not_after"]
    assert certificate.not_valid_after_utc.isoformat().replace("+00:00", "Z") == (
        issued["not_after"]
    )
    assert certificate_response.status_code == 200
    assert len(audit_events) == 1
    assert audit_events[0].details["device_id"] == "external-issued-device"


def test_managed_device_identity_renews_ca_backed_certificate(monkeypatch, tmp_path):
    ca_certificate_path, ca_key_path = _write_test_ca(tmp_path)
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_CA_CERT_FILE", str(ca_certificate_path))
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_CA_KEY_FILE", str(ca_key_path))
    control = ControlPlane()
    client = TestClient(create_app(control))
    actor = {"id": "security-admin", "roles": ["admin"]}
    headers = {
        "x-agentbridge-device-id": "renewed-device",
        "x-agentbridge-device-key": "managed-secret",
    }

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": actor,
            "device_id": "renewed-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "device_manage"],
            "trace_id": "managed-device-renew-create",
        },
    )
    issue_response = client.post(
        "/api/v1/device-identities/renewed-device/certificates/issue",
        json={
            "actor": actor,
            "csr_pem": _device_csr_pem("renewed-device"),
            "validity_days": 3,
            "trace_id": "managed-device-cert-renew-initial-issue",
        },
        headers=headers,
    )
    old_fingerprint = issue_response.json()["certificate_fingerprint"]
    old_certificate_response = client.get(
        "/api/v1/commands",
        headers={"x-agentbridge-client-cert-fingerprint": old_fingerprint},
    )
    renew_response = client.post(
        "/api/v1/device-identities/renewed-device/certificates/renew",
        json={
            "actor": actor,
            "csr_pem": _device_csr_pem("renewed-device"),
            "validity_days": 10,
            "trace_id": "managed-device-cert-renew",
        },
        headers=headers,
    )
    renewed = renew_response.json()
    new_fingerprint = renewed["certificate_fingerprint"]
    old_certificate_after_renew_response = client.get(
        "/api/v1/commands",
        headers={"x-agentbridge-client-cert-fingerprint": old_fingerprint},
    )
    new_certificate_response = client.get(
        "/api/v1/commands",
        headers={"x-agentbridge-client-cert-fingerprint": new_fingerprint},
    )
    audit_events = control.repository.list_audit_events(
        action="device_identity.certificate_renewed"
    )
    semantic_events = control.repository.list_semantic_events(
        event_type="device_identity.certificate_renewed",
        trace_id="managed-device-cert-renew",
    )

    assert create_response.status_code == 200
    assert issue_response.status_code == 200
    assert old_certificate_response.status_code == 200
    assert renew_response.status_code == 200
    assert new_fingerprint != old_fingerprint
    assert renewed["replaced_certificate_fingerprints"] == [old_fingerprint]
    assert renewed["device_identity"]["certificate_fingerprints"] == [new_fingerprint]
    records_by_fingerprint = {
        record["fingerprint"]: record
        for record in renewed["device_identity"]["certificate_records"]
    }
    assert records_by_fingerprint[old_fingerprint]["removed_at"] is not None
    assert records_by_fingerprint[old_fingerprint]["removed_by"] == "security-admin"
    assert records_by_fingerprint[new_fingerprint]["source"] == "managed_ca"
    assert records_by_fingerprint[new_fingerprint]["removed_at"] is None
    assert old_certificate_after_renew_response.status_code == 403
    assert new_certificate_response.status_code == 200
    assert len(audit_events) == 1
    assert audit_events[0].details["certificate_fingerprint"] == new_fingerprint
    assert audit_events[0].details["replaced_fingerprints"] == [old_fingerprint]
    assert len(semantic_events) == 1
    assert semantic_events[0].payload["certificate_fingerprint"] == new_fingerprint
    assert semantic_events[0].payload["replaced_fingerprints"] == [old_fingerprint]


def test_managed_device_identity_rejects_certificate_csr_for_wrong_device(
    monkeypatch,
    tmp_path,
):
    ca_certificate_path, ca_key_path = _write_test_ca(tmp_path)
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_CA_CERT_FILE", str(ca_certificate_path))
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_CA_KEY_FILE", str(ca_key_path))
    client = TestClient(create_app())
    actor = {"id": "security-admin", "roles": ["admin"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": actor,
            "device_id": "issued-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "device_manage"],
            "trace_id": "managed-device-wrong-csr-create",
        },
    )
    issue_response = client.post(
        "/api/v1/device-identities/issued-device/certificates/issue",
        json={
            "actor": actor,
            "csr_pem": _device_csr_pem("other-device"),
            "trace_id": "managed-device-wrong-csr-issue",
        },
        headers={
            "x-agentbridge-device-id": "issued-device",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    identity_response = client.get(
        "/api/v1/device-identities",
        headers={
            "x-agentbridge-device-id": "issued-device",
            "x-agentbridge-device-key": "managed-secret",
        },
    )

    assert create_response.status_code == 200
    assert issue_response.status_code == 400
    assert issue_response.json()["error_code"] == "COMMAND_ARGUMENT_INVALID"
    assert issue_response.json()["details"]["csr_common_name"] == "other-device"
    assert identity_response.json()[0]["certificate_fingerprints"] == []
    assert identity_response.json()[0]["certificate_records"] == []
    assert identity_response.json()[0]["certificate_health"]["status"] == "none"


def test_managed_device_identity_rejects_expired_tracked_certificate_fingerprint():
    control = ControlPlane()
    client = TestClient(create_app(control))
    admin = Actor(id="security-admin", roles={"admin"})
    identity, device_key = control.upsert_device_identity(
        actor=admin,
        device_id="expired-certificate-device",
        display_name="Expired certificate device",
        device_key="managed-secret",
        allowed_scopes={
            DeviceIdentityScope.HTTP_API,
            DeviceIdentityScope.DEVICE_MANAGE,
            DeviceIdentityScope.AUDIT_READ,
        },
        certificate_fingerprints={"SHA256:AA:BB:CC"},
        trace_id="managed-device-expired-certificate-create",
    )
    expired_at = utc_now() - timedelta(minutes=1)
    control.repository.upsert_device_identity(
        device_id=identity.device_id,
        display_name=identity.display_name,
        allowed_scopes=set(identity.allowed_scopes),
        allowed_resource_ids=set(identity.allowed_resource_ids),
        certificate_fingerprints=set(identity.certificate_fingerprints),
        certificate_records=[
            DeviceCertificateRecord(
                fingerprint="aabbcc",
                source="managed_ca",
                serial_number="1234",
                subject="CN=expired-certificate-device",
                issuer="CN=AgentBridge Test CA",
                not_before=expired_at - timedelta(days=1),
                not_after=expired_at,
                issued_by="security-admin",
            )
        ],
        updated_by="security-admin",
    )

    certificate_response = client.get(
        "/api/v1/commands",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    device_headers = {
        "x-agentbridge-device-id": "expired-certificate-device",
        "x-agentbridge-device-key": device_key,
    }
    key_response = client.get(
        "/api/v1/commands",
        headers=device_headers,
    )
    identity_response = client.get(
        "/api/v1/device-identities",
        headers=device_headers,
    )
    worker_status_response = client.get(
        "/api/v1/device-identities/certificates/scan-worker",
        headers=device_headers,
    )
    scan_response = client.post(
        "/api/v1/device-identities/certificates/scan",
        json={
            "actor": {"id": "security-admin", "roles": ["admin"]},
            "warning_days": 7,
            "trace_id": "managed-device-certificate-scan",
        },
        headers=device_headers,
    )
    worker_run_once_response = client.post(
        "/api/v1/device-identities/certificates/scan-worker/run-once",
        json={
            "actor": {"id": "security-admin", "roles": ["admin"]},
            "warning_days": 7,
            "trace_id": "managed-device-certificate-scan-worker-run-once",
        },
        headers=device_headers,
    )
    rendered_scan_response = client.get(
        "/api/v1/events/rendered"
        "?event_type=device_identity.certificates_scanned"
        "&trace_id=managed-device-certificate-scan",
        headers=device_headers,
    )
    audit_events = control.repository.list_audit_events(
        action="device_identity.certificates_scanned"
    )
    semantic_events = control.repository.list_semantic_events(
        event_type="device_identity.certificates_scanned",
        trace_id="managed-device-certificate-scan",
    )
    worker_events = control.repository.list_semantic_events(
        event_type="device_identity.certificates_scanned",
        trace_id="managed-device-certificate-scan-worker-run-once",
    )

    assert certificate_response.status_code == 403
    assert key_response.status_code == 200
    assert identity_response.status_code == 200
    health = identity_response.json()[0]["certificate_health"]
    assert health["status"] == "expired"
    assert health["expired_count"] == 1
    assert health["expired_fingerprints"] == ["aabbcc"]
    assert health["renewal_status"] == "overdue"
    assert health["renewal_overdue_count"] == 1
    assert health["renewal_overdue_fingerprints"] == ["aabbcc"]
    assert health["renewal_due_at"] is not None
    assert health["next_expires_at"] is not None
    assert worker_status_response.status_code == 200
    assert worker_status_response.json()["enabled"] is False
    assert scan_response.status_code == 200
    scan = scan_response.json()
    assert scan["warning_days"] == 7
    assert scan["total_device_count"] == 1
    assert scan["status_counts"]["expired"] == 1
    assert scan["renewal_status_counts"]["overdue"] == 1
    assert scan["action_required_count"] == 1
    assert scan["renewal_action_required_count"] == 1
    assert scan["action_required_devices"][0]["device_id"] == (
        "expired-certificate-device"
    )
    assert scan["action_required_devices"][0]["certificate_health_status"] == "expired"
    assert scan["action_required_devices"][0]["renewal_status"] == "overdue"
    assert scan["action_required_devices"][0]["renewal_overdue_count"] == 1
    assert scan["devices"][0]["certificate_health"]["expired_fingerprints"] == ["aabbcc"]
    assert worker_run_once_response.status_code == 200
    worker_scan = worker_run_once_response.json()
    assert worker_scan["worker"]["run_count"] == 1
    assert worker_scan["worker"]["last_action_required_count"] == 1
    assert worker_scan["worker"]["last_renewal_action_required_count"] == 1
    assert worker_scan["worker"]["last_renewal_status_counts"]["overdue"] == 1
    assert worker_scan["result"]["action_required_count"] == 1
    assert worker_scan["result"]["renewal_action_required_count"] == 1
    assert len(audit_events) == 2
    assert all(event.details["action_required_count"] == 1 for event in audit_events)
    assert all(event.details["status_counts"]["expired"] == 1 for event in audit_events)
    assert all(
        event.details["renewal_status_counts"]["overdue"] == 1
        for event in audit_events
    )
    assert len(semantic_events) == 1
    assert semantic_events[0].payload["action_required_count"] == 1
    assert semantic_events[0].payload["renewal_action_required_count"] == 1
    assert len(worker_events) == 1
    assert worker_events[0].payload["action_required_count"] == 1
    assert worker_events[0].payload["renewal_action_required_count"] == 1
    assert rendered_scan_response.status_code == 200
    rendered_scan = rendered_scan_response.json()[0]
    assert rendered_scan["document"]["blocks"][0]["title"] == "设备证书扫描"
    assert rendered_scan["document"]["visibility"] == "operators"
    assert "需要处理：1" in rendered_scan["text_messages"][0]
    assert "续期需处理：1" in rendered_scan["text_messages"][0]
    assert "expired-certificate-device" in rendered_scan["text_messages"][0]


def test_device_certificate_scan_worker_can_autostart_from_environment(monkeypatch):
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_SCAN_WORKER_ENABLED", "true")
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_SCAN_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_EXPIRY_WARNING_DAYS", "21")
    monkeypatch.setenv(
        "AGENTBRIDGE_DEVICE_CERT_SCAN_NOTIFY_CHAT_CONTEXT_IDS",
        "ctx-alerts, ctx-backup",
    )
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_SCAN_NOTIFY_PLATFORM", "plain_text")
    app = create_app()

    with TestClient(app) as client:
        running_status = client.get("/api/v1/device-identities/certificates/scan-worker")
        assert running_status.status_code == 200
        assert running_status.json()["enabled"] is True
        assert running_status.json()["running"] is True
        assert running_status.json()["warning_days"] == 21
        assert running_status.json()["notify_chat_context_ids"] == [
            "ctx-alerts",
            "ctx-backup",
        ]
        assert running_status.json()["notify_platform"] == "plain_text"

    assert app.state.certificate_scan_worker.is_running() is False


def test_managed_device_identity_rejects_certificate_issue_with_mismatched_ca_key(
    monkeypatch,
    tmp_path,
):
    ca_certificate_path, _ca_key_path = _write_test_ca(tmp_path)
    wrong_ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_ca_key_path = tmp_path / "wrong-device-ca-key.pem"
    wrong_ca_key_path.write_bytes(
        wrong_ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_CA_CERT_FILE", str(ca_certificate_path))
    monkeypatch.setenv("AGENTBRIDGE_DEVICE_CERT_CA_KEY_FILE", str(wrong_ca_key_path))
    client = TestClient(create_app())
    actor = {"id": "security-admin", "roles": ["admin"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": actor,
            "device_id": "issued-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "device_manage"],
            "trace_id": "managed-device-mismatched-ca-create",
        },
    )
    issue_response = client.post(
        "/api/v1/device-identities/issued-device/certificates/issue",
        json={
            "actor": actor,
            "csr_pem": _device_csr_pem("issued-device"),
            "trace_id": "managed-device-mismatched-ca-issue",
        },
        headers={
            "x-agentbridge-device-id": "issued-device",
            "x-agentbridge-device-key": "managed-secret",
        },
    )

    assert create_response.status_code == 200
    assert issue_response.status_code == 503
    assert issue_response.json()["message"] == "设备证书 CA 私钥与 CA 证书不匹配。"


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
        "/api/v1/commands",
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
        "/api/v1/commands",
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
    commands_response = client.get("/api/v1/commands", headers=key_headers)
    list_response = client.get("/api/v1/projects", headers=key_headers)
    detail_response = client.get(
        "/api/v1/projects/project-missing",
        headers=key_headers,
    )
    workspace_read_response = client.get(
        "/api/v1/projects/project-missing/workspaces",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
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
    assert commands_response.status_code == 200
    assert list_response.status_code == 403
    assert detail_response.status_code == 403
    assert workspace_read_response.status_code == 403
    assert create_project_response.status_code == 403
    assert workspace_response.status_code == 403
    assert bind_response.status_code == 403


def test_managed_device_identity_project_read_scope_allows_project_read_apis(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    project = client.post(
        "/api/v1/projects",
        json={
            "actor": maintainer,
            "name": "Readable Device Project",
            "trace_id": "project-read-scope-project",
        },
    ).json()
    workspace = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": maintainer,
            "machine_id": "local",
            "path": str(tmp_path),
            "allowed_root": str(tmp_path),
            "trace_id": "project-read-scope-workspace",
        },
    ).json()

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "project-read-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "project_read"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "project-read-scope-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "project-read-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    list_response = client.get("/api/v1/projects", headers=key_headers)
    detail_response = client.get(
        f"/api/v1/projects/{project['id']}",
        headers=key_headers,
    )
    workspaces_response = client.get(
        f"/api/v1/projects/{project['id']}/workspaces",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    create_project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": maintainer,
            "name": "Denied Project Write",
            "trace_id": "project-read-scope-denied-create",
        },
        headers=key_headers,
    )

    assert create_identity_response.status_code == 200
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()] == [project["id"]]
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == project["id"]
    assert workspaces_response.status_code == 200
    assert [item["id"] for item in workspaces_response.json()] == [workspace["id"]]
    assert create_project_response.status_code == 403


def test_managed_device_identity_project_manage_scope_allows_project_write_apis(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    context_response = client.post(
        "/api/v1/chat-contexts",
        json={
            "bot_instance_id": "bot-test",
            "platform": "onebot.v11",
            "chat_space_id": "project-manager-scope",
        },
    )

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


def test_managed_device_identity_requires_chat_context_manage_scope_for_context_writes(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    context = client.post(
        "/api/v1/chat-contexts",
        json={
            "bot_instance_id": "bot-test",
            "platform": "onebot.v11",
            "chat_space_id": "chat-context-scope",
        },
    ).json()
    project = client.post(
        "/api/v1/projects",
        json={
            "actor": maintainer,
            "name": "Readonly Chat Context Project",
            "trace_id": "chat-context-scope-project",
        },
    ).json()
    workspace = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": maintainer,
            "machine_id": "local",
            "path": str(tmp_path),
            "allowed_root": str(tmp_path),
            "trace_id": "chat-context-scope-workspace",
        },
    ).json()
    session = client.post(
        "/api/v1/sessions",
        json={
            "actor": maintainer,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Existing Chat Context Session",
            "trace_id": "chat-context-scope-session",
        },
    ).json()

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-chat-context-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "chat-context-scope-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-chat-context-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    cert_headers = {"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"}
    commands_response = client.get("/api/v1/commands", headers=key_headers)
    create_context_response = client.post(
        "/api/v1/chat-contexts",
        json={
            "bot_instance_id": "bot-test",
            "platform": "onebot.v11",
            "chat_space_id": "chat-context-scope-denied",
        },
        headers=key_headers,
    )
    active_project_response = client.put(
        f"/api/v1/chat-contexts/{context['id']}/active-project",
        json={
            "actor": maintainer,
            "project": project["id"],
            "trace_id": "chat-context-scope-denied-project",
        },
        headers=cert_headers,
    )
    active_session_response = client.put(
        f"/api/v1/chat-contexts/{context['id']}/active-session",
        json={
            "actor": maintainer,
            "session": session["id"],
            "trace_id": "chat-context-scope-denied-session",
        },
        headers=key_headers,
    )

    assert create_identity_response.status_code == 200
    assert commands_response.status_code == 200
    assert create_context_response.status_code == 403
    assert active_project_response.status_code == 403
    assert active_session_response.status_code == 403


def test_managed_device_identity_chat_context_manage_scope_allows_context_writes(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    project = client.post(
        "/api/v1/projects",
        json={
            "actor": maintainer,
            "name": "Managed Chat Context Project",
            "trace_id": "chat-context-manager-project",
        },
    ).json()
    workspace = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": maintainer,
            "machine_id": "local",
            "path": str(tmp_path),
            "allowed_root": str(tmp_path),
            "trace_id": "chat-context-manager-workspace",
        },
    ).json()
    session = client.post(
        "/api/v1/sessions",
        json={
            "actor": maintainer,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Managed Chat Context Session",
            "trace_id": "chat-context-manager-session",
        },
    ).json()

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "chat-context-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "chat_context_manage"],
            "trace_id": "chat-context-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "chat-context-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    create_context_response = client.post(
        "/api/v1/chat-contexts",
        json={
            "bot_instance_id": "bot-test",
            "platform": "onebot.v11",
            "chat_space_id": "chat-context-manager-scope",
        },
        headers=headers,
    )
    context_id = create_context_response.json().get("id")
    active_project_response = client.put(
        f"/api/v1/chat-contexts/{context_id}/active-project",
        json={
            "actor": maintainer,
            "project": project["id"],
            "trace_id": "chat-context-manager-project-select",
        },
        headers=headers,
    )
    active_session_response = client.put(
        f"/api/v1/chat-contexts/{context_id}/active-session",
        json={
            "actor": maintainer,
            "session": session["id"],
            "trace_id": "chat-context-manager-session-select",
        },
        headers=headers,
    )

    assert create_identity_response.status_code == 200
    assert create_context_response.status_code == 200
    assert active_project_response.status_code == 200
    assert active_project_response.json()["active_project_id"] == project["id"]
    assert active_session_response.status_code == 200
    assert active_session_response.json()["active_session_id"] == session["id"]


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
    commands_response = client.get("/api/v1/commands", headers=key_headers)
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
    assert commands_response.status_code == 200
    assert create_session_response.status_code == 403
    assert close_response.status_code == 403
    assert lease_response.status_code == 403


def test_managed_device_identity_requires_session_read_scope_for_session_reads(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}

    project = client.post(
        "/api/v1/projects",
        json={
            "actor": maintainer,
            "name": "Readonly Session Inventory Project",
            "trace_id": "session-read-scope-project",
        },
    ).json()
    workspace = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": maintainer,
            "machine_id": "local",
            "path": str(tmp_path),
            "allowed_root": str(tmp_path),
            "trace_id": "session-read-scope-workspace",
        },
    ).json()
    existing_session = client.post(
        "/api/v1/sessions",
        json={
            "actor": maintainer,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Existing Read Scope Session",
            "trace_id": "session-read-scope-existing-session",
        },
    ).json()

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "session-readonly-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "session-read-scope-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "session-readonly-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    commands_response = client.get("/api/v1/commands", headers=key_headers)
    list_response = client.get(
        "/api/v1/sessions",
        params={"project_id": project["id"]},
        headers=key_headers,
    )
    detail_response = client.get(
        f"/api/v1/sessions/{existing_session['id']}",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_identity_response.status_code == 200
    assert commands_response.status_code == 200
    assert list_response.status_code == 403
    assert detail_response.status_code == 403


def test_managed_device_identity_session_read_scope_allows_session_read_apis(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    operator = {"id": "usr_operator", "roles": ["operator"]}

    project = client.post(
        "/api/v1/projects",
        json={
            "actor": maintainer,
            "name": "Readable Session Inventory Project",
            "trace_id": "session-read-manager-project",
        },
    ).json()
    workspace = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": maintainer,
            "machine_id": "local",
            "path": str(tmp_path),
            "allowed_root": str(tmp_path),
            "trace_id": "session-read-manager-workspace",
        },
    ).json()
    existing_session = client.post(
        "/api/v1/sessions",
        json={
            "actor": maintainer,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Existing Readable Session",
            "trace_id": "session-read-manager-existing-session",
        },
    ).json()
    queued_turn = client.post(
        f"/api/v1/sessions/{existing_session['id']}/turns",
        json={
            "actor": operator,
            "prompt": "Queue read scope fixture",
            "trace_id": "session-read-manager-turn",
        },
    ).json()
    acquired_lease_response = client.post(
        f"/api/v1/sessions/{existing_session['id']}/lease/acquire",
        json={
            "actor": maintainer,
            "owner_type": "web_admin",
            "owner_id": "usr_maintainer",
            "trace_id": "session-read-manager-lease",
        },
    )

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "session-read-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "session_read"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "session-read-manager-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "session-read-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    list_response = client.get(
        "/api/v1/sessions",
        params={"project_id": project["id"]},
        headers=key_headers,
    )
    detail_response = client.get(
        f"/api/v1/sessions/{existing_session['id']}",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )
    queue_response = client.get(
        f"/api/v1/sessions/{existing_session['id']}/queue",
        headers=key_headers,
    )
    lease_response = client.get(
        f"/api/v1/sessions/{existing_session['id']}/lease",
        headers=key_headers,
    )
    create_session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": maintainer,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Denied Managed Session",
            "trace_id": "session-read-manager-denied-create",
        },
        headers=key_headers,
    )

    assert create_identity_response.status_code == 200
    assert list_response.status_code == 200
    assert [session["id"] for session in list_response.json()] == [
        existing_session["id"]
    ]
    assert detail_response.status_code == 200
    assert detail_response.json()["id"] == existing_session["id"]
    assert queue_response.status_code == 200
    assert queue_response.json()["queue_version"].startswith("qv_")
    assert queue_response.json()["queue_paused"] is False
    assert [turn["id"] for turn in queue_response.json()["turns"]] == [queued_turn["id"]]
    assert acquired_lease_response.status_code == 200
    assert acquired_lease_response.json()["owner_id"] == "usr_maintainer"
    assert lease_response.status_code == 200
    assert lease_response.json()["owner_type"] == "web_admin"
    assert lease_response.json()["owner_id"] == "usr_maintainer"
    assert create_session_response.status_code == 403


def test_managed_device_identity_session_manage_scope_allows_session_write_apis(
    tmp_path,
):
    control = ControlPlane()
    client = TestClient(create_app(control))
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    operator = {"id": "usr_operator", "roles": ["operator"]}

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
    first_queued_turn = control.enqueue_turn(
        actor=Actor(id=operator["id"], roles=set(operator["roles"])),
        session_id=session_id,
        prompt="First queue scope fixture",
        trace_id="session-manager-queued-turn-one",
    )
    second_queued_turn = control.enqueue_turn(
        actor=Actor(id=operator["id"], roles=set(operator["roles"])),
        session_id=session_id,
        prompt="Second queue scope fixture",
        trace_id="session-manager-queued-turn-two",
    )
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
    pause_queue_response = client.post(
        f"/api/v1/sessions/{session_id}/queue/pause",
        json={
            "actor": maintainer,
            "expected_queue_version": control.repository.queue_version(session_id),
            "trace_id": "session-manager-pause-queue",
        },
        headers=headers,
    )
    resume_queue_response = client.post(
        f"/api/v1/sessions/{session_id}/queue/resume",
        json={
            "actor": maintainer,
            "expected_queue_version": pause_queue_response.json().get("queue_version"),
            "trace_id": "session-manager-resume-queue",
        },
        headers=headers,
    )
    reorder_queue_response = client.post(
        f"/api/v1/sessions/{session_id}/queue/reorder",
        json={
            "actor": maintainer,
            "turn_id": second_queued_turn.id,
            "before_turn_id": first_queued_turn.id,
            "expected_queue_version": resume_queue_response.json().get("queue_version"),
            "trace_id": "session-manager-reorder-queue",
        },
        headers=headers,
    )
    clear_queue_response = client.post(
        f"/api/v1/sessions/{session_id}/queue/clear",
        json={
            "actor": maintainer,
            "expected_queue_version": reorder_queue_response.json().get("queue_version"),
            "confirm_count": 2,
            "trace_id": "session-manager-clear-queue",
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
    assert pause_queue_response.status_code == 200
    assert pause_queue_response.json()["queue_paused"] is True
    assert resume_queue_response.status_code == 200
    assert resume_queue_response.json()["queue_paused"] is False
    assert reorder_queue_response.status_code == 200
    assert [turn["id"] for turn in reorder_queue_response.json()["turns"]] == [
        second_queued_turn.id,
        first_queued_turn.id,
    ]
    assert clear_queue_response.status_code == 200
    assert clear_queue_response.json()["queue_version"].startswith("qv_")
    assert [turn["id"] for turn in clear_queue_response.json()["turns"]] == [
        second_queued_turn.id,
        first_queued_turn.id,
    ]
    assert close_response.status_code == 200
    assert close_response.json()["status"] == "closed"


def test_managed_device_identity_requires_session_send_scope_for_turn_writes(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    operator = {"id": "usr_operator", "roles": ["operator"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-session-send-scope",
        prefix="session-send-scope",
        name="Session Send Scope",
    )

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-session-send-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "session-send-scope-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-session-send-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    cert_headers = {"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"}
    commands_response = client.get("/api/v1/commands", headers=key_headers)
    read_response = client.get(f"/api/v1/sessions/{session_id}", headers=key_headers)
    key_turn_response = client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "actor": operator,
            "prompt": "Denied via key",
            "trace_id": "session-send-scope-denied-key",
        },
        headers=key_headers,
    )
    cert_turn_response = client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "actor": operator,
            "prompt": "Denied via certificate",
            "trace_id": "session-send-scope-denied-cert",
        },
        headers=cert_headers,
    )

    assert create_identity_response.status_code == 200
    assert commands_response.status_code == 200
    assert read_response.status_code == 403
    assert key_turn_response.status_code == 403
    assert cert_turn_response.status_code == 403


def test_managed_device_identity_session_send_scope_allows_turn_writes(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    operator = {"id": "usr_operator", "roles": ["operator"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-session-send-manager",
        prefix="session-send-manager",
        name="Session Send Manager",
    )

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "session-send-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "session_send"],
            "trace_id": "session-send-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "session-send-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    turn_response = client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={
            "actor": operator,
            "prompt": "Run the focused test suite.",
            "trace_id": "session-send-manager-turn",
        },
        headers=headers,
    )
    remove_response = client.request(
        "DELETE",
        f"/api/v1/sessions/{session_id}/queue/{turn_response.json().get('id')}",
        json={
            "actor": operator,
            "trace_id": "session-send-manager-remove-turn",
        },
        headers=headers,
    )

    assert create_identity_response.status_code == 200
    assert turn_response.status_code == 200
    assert turn_response.json()["actor_id"] == "usr_operator"
    assert turn_response.json()["prompt"] == "Run the focused test suite."
    assert turn_response.json()["status"] == "queued"
    assert remove_response.status_code == 200
    assert remove_response.json()["queue_version"].startswith("qv_")
    assert remove_response.json()["turn"]["status"] == "cancelled"


def test_managed_device_identity_requires_session_event_ingest_scope_for_event_writes(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-event-ingest-scope",
        prefix="event-ingest-scope",
        name="Event Ingest Scope",
    )

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-event-ingest-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "event-ingest-scope-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-event-ingest-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    cert_headers = {"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"}
    replay_response = client.get(
        f"/api/v1/sessions/{session_id}/events",
        headers=key_headers,
    )
    key_ingest_response = client.post(
        f"/api/v1/sessions/{session_id}/events",
        json={
            "type": "assistant.delta",
            "source": "terminal_agent",
            "trace_id": "event-ingest-scope-denied-key",
            "idempotency_key": "event-ingest-scope-denied-key",
            "payload": {"text": "denied via key"},
        },
        headers=key_headers,
    )
    cert_ingest_response = client.post(
        f"/api/v1/sessions/{session_id}/events",
        json={
            "type": "assistant.delta",
            "source": "terminal_agent",
            "trace_id": "event-ingest-scope-denied-cert",
            "idempotency_key": "event-ingest-scope-denied-cert",
            "payload": {"text": "denied via certificate"},
        },
        headers=cert_headers,
    )

    assert create_identity_response.status_code == 200
    assert replay_response.status_code == 403
    assert key_ingest_response.status_code == 403
    assert cert_ingest_response.status_code == 403


def test_managed_device_identity_session_event_ingest_scope_allows_event_writes(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-event-ingest-manager",
        prefix="event-ingest-manager",
        name="Event Ingest Manager",
    )

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "event-ingest-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "session_event_ingest"],
            "trace_id": "event-ingest-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "event-ingest-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    ingest_response = client.post(
        f"/api/v1/sessions/{session_id}/events",
        json={
            "type": "assistant.delta",
            "source": "terminal_agent",
            "trace_id": "event-ingest-manager-event",
            "idempotency_key": "event-ingest-manager-event",
            "payload": {"text": "hello"},
        },
        headers=headers,
    )
    replay_response = client.get(
        f"/api/v1/sessions/{session_id}/events",
        headers=headers,
    )

    assert create_identity_response.status_code == 200
    assert ingest_response.status_code == 200
    assert ingest_response.json()["type"] == "assistant.delta"
    assert ingest_response.json()["source"] == "terminal_agent"
    assert ingest_response.json()["payload"] == {"text": "hello"}
    assert replay_response.status_code == 403


def test_managed_device_identity_requires_audit_read_scope_for_audit_event_http_reads(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-audit-read-scope",
        prefix="audit-read-scope",
        name="Audit Read Scope",
    )
    event_response = client.post(
        f"/api/v1/sessions/{session_id}/events",
        json={
            "type": "assistant.delta",
            "source": "terminal_agent",
            "trace_id": "audit-read-scope-event",
            "idempotency_key": "audit-read-scope-event",
            "payload": {"text": "audit read denied"},
        },
    )

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-audit-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "audit-read-scope-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-audit-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    regular_http_response = client.get("/api/v1/commands", headers=key_headers)
    audit_response = client.get("/api/v1/audit", headers=key_headers)
    audit_export_response = client.get("/api/v1/audit/export", headers=key_headers)
    event_search_response = client.get("/api/v1/events", headers=key_headers)
    event_replay_response = client.get(
        f"/api/v1/sessions/{session_id}/events",
        headers=key_headers,
    )
    rendered_response = client.get(
        f"/api/v1/sessions/{session_id}/rendered-events",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert event_response.status_code == 200
    assert create_response.status_code == 200
    assert regular_http_response.status_code == 200
    assert audit_response.status_code == 403
    assert audit_export_response.status_code == 403
    assert event_search_response.status_code == 403
    assert event_replay_response.status_code == 403
    assert rendered_response.status_code == 403


def test_managed_device_identity_audit_read_scope_allows_audit_event_http_reads(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-audit-read-manager",
        prefix="audit-read-manager",
        name="Audit Read Manager",
    )
    event_response = client.post(
        f"/api/v1/sessions/{session_id}/events",
        json={
            "type": "assistant.delta",
            "source": "terminal_agent",
            "trace_id": "audit-read-manager-event",
            "idempotency_key": "audit-read-manager-event",
            "payload": {"text": "audit read allowed"},
        },
    )

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "audit-read-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "audit_read"],
            "trace_id": "audit-read-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "audit-read-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    audit_response = client.get("/api/v1/audit", headers=headers)
    audit_export_response = client.get(
        "/api/v1/audit/export",
        params={"format": "csv"},
        headers=headers,
    )
    event_search_response = client.get(
        "/api/v1/events",
        params={"trace_id": "audit-read-manager-event"},
        headers=headers,
    )
    event_replay_response = client.get(
        f"/api/v1/sessions/{session_id}/events",
        headers=headers,
    )
    rendered_response = client.get(
        f"/api/v1/sessions/{session_id}/rendered-events",
        headers=headers,
    )

    assert event_response.status_code == 200
    assert create_response.status_code == 200
    assert audit_response.status_code == 200
    assert audit_export_response.status_code == 200
    assert audit_export_response.headers["content-type"].startswith("text/csv")
    assert event_search_response.status_code == 200
    assert [event["id"] for event in event_search_response.json()] == [
        event_response.json()["id"]
    ]
    assert event_replay_response.status_code == 200
    assert any(
        event["id"] == event_response.json()["id"]
        for event in event_replay_response.json()
    )
    assert rendered_response.status_code == 200
    assert rendered_response.json()


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
    assert "project-max-active-sessions" in html
    assert "function readProjectMaxActiveSessions()" in html
    assert "project-max-running-turns" in html
    assert "function readProjectMaxRunningTurns()" in html
    assert "project-max-queued-turns" in html
    assert "function readProjectMaxQueuedTurns()" in html
    assert "project-daily-turns-per-user" in html
    assert "function readProjectDailyTurnsPerUser()" in html
    assert "workspace-writable" in html
    assert "workspace-max-write-sessions" in html
    assert "function syncWorkspaceWritePolicy()" in html
    assert "queue-refresh" in html
    assert "queue-pause" in html
    assert "queue-resume" in html
    assert "queue-clear" in html
    assert "queue-version" in html
    assert "Active Turn" in html
    assert "Pending Approvals" in html
    assert "Lease" in html
    assert "sessionQueues" in html
    assert "sessionLeases" in html
    assert "sessionPendingApprovals" in html
    assert "function formatSessionPendingApprovals(session)" in html
    assert "async function refreshSessionOperations(sessions)" in html
    assert "queue_paused" in html
    assert "async function loadQueue()" in html
    assert "async function setQueuePaused(paused)" in html
    assert "async function clearQueue()" in html
    assert "/queue/${action}" in html
    assert "/queue/clear" in html
    assert "/lease" in html
    assert "status=pending" in html
    assert "status=partially_approved" in html


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
    assert "async function issueDeviceCertificate()" in html
    assert "async function renewDeviceCertificate()" in html
    assert "async function rotateDeviceCertificates()" in html
    assert "async function revokeDevice()" in html
    assert "auth-device-key" in html
    assert "allowed-scopes" in html
    assert "allowed-resource-ids" in html
    assert "audit_read" in html
    assert "bot_gateway_read" in html
    assert "bot_gateway_manage" in html
    assert "onebot_event_ingest" in html
    assert "command_parse" in html
    assert "command_execute" in html
    assert "device_manage" in html
    assert "policy_read" in html
    assert "policy_manage" in html
    assert "group_role_read" in html
    assert "group_role_manage" in html
    assert "chat_context_manage" in html
    assert "project_read" in html
    assert "project_manage" in html
    assert "session_read" in html
    assert "session_manage" in html
    assert "session_send" in html
    assert "session_event_ingest" in html
    assert "interaction_read" in html
    assert "interaction_manage" in html
    assert "terminal_read" in html
    assert "terminal_control" in html
    assert "certificate-fingerprints" in html
    assert "certificate-fingerprints-add" in html
    assert "certificate-fingerprints-remove" in html
    assert "certificate-csr" in html
    assert "certificate-validity-days" in html
    assert "scan-certificates" in html
    assert "async function scanCertificates()" in html
    assert "/api/v1/device-identities/certificates/scan" in html
    assert "Cert Health" in html
    assert "formatCertificateHealth" in html
    assert "certificate_health" in html
    assert "/certificates/issue" in html
    assert "/certificates/renew" in html
    assert "/certificate-fingerprints/rotate" in html
    assert "allowed_resource_ids" in html
    assert "generated-key" in html


def test_audit_events_admin_ui_serves_dashboard():
    client = TestClient(create_app())

    response = client.get("/admin/audit")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "AgentBridge Audit & Events" in html
    assert "/api/v1/audit" in html
    assert "/api/v1/audit/export" in html
    assert "/api/v1/events" in html
    assert "/api/v1/sessions/${encodeURIComponent(sessionId)}/events" in html
    assert "/api/v1/sessions/${encodeURIComponent(sessionId)}/events/ws" in html
    assert "async function refreshAudit()" in html
    assert "function downloadAudit(format)" in html
    assert "audit-export-json" in html
    assert "audit-export-csv" in html
    assert "audit-export-archive" in html
    assert "async function refreshEvents()" in html
    assert "async function searchEvents()" in html
    assert "event-search" in html
    assert "event-project" in html
    assert "event-type" in html
    assert "event-source" in html
    assert "event-trace" in html
    assert "audit-query" in html
    assert "audit-created-from" in html
    assert "audit-created-to" in html
    assert "event-query" in html
    assert "event-created-from" in html
    assert "event-created-to" in html
    assert "event-live-connect" in html
    assert "function connectEventsLive()" in html


def test_workspace_api_configures_read_only_write_policy(tmp_path):
    client = TestClient(create_app())
    actor = {"id": "admin-ui", "roles": ["admin"]}

    project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": actor,
            "name": "Read Only Backend",
            "trace_id": "test-read-only-workspace-project",
        },
    )
    assert project_response.status_code == 200
    project = project_response.json()
    workspace_response = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": actor,
            "machine_id": "local",
            "path": str(tmp_path / "repo"),
            "allowed_root": str(tmp_path),
            "workspace_type": "read_only",
            "is_writable": True,
            "max_write_sessions": 3,
            "trace_id": "test-read-only-workspace-add",
        },
    )

    assert project_response.status_code == 200
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    assert workspace["type"] == "read_only"
    assert workspace["is_writable"] is False
    assert workspace["max_write_sessions"] == 0

    session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": actor,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Read Only Session",
            "trace_id": "test-read-only-workspace-session",
        },
    )
    lease_response = client.post(
        f"/api/v1/sessions/{session_response.json()['id']}/lease/acquire",
        json={
            "actor": actor,
            "owner_type": "web_admin",
            "owner_id": "admin-ui",
            "trace_id": "test-read-only-workspace-lease",
        },
    )

    assert session_response.status_code == 200
    assert lease_response.status_code == 409
    assert lease_response.json()["error_code"] == "LEASE_CONFLICT"
    assert lease_response.json()["details"]["workspace_id"] == workspace["id"]


def test_workspace_api_rejects_zero_write_capacity_for_writable_workspace(tmp_path):
    client = TestClient(create_app())
    actor = {"id": "admin-ui", "roles": ["admin"]}

    project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": actor,
            "name": "Invalid Workspace Policy",
            "trace_id": "test-invalid-workspace-policy-project",
        },
    )
    assert project_response.status_code == 200
    project = project_response.json()
    workspace_response = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": actor,
            "machine_id": "local",
            "path": str(tmp_path / "repo"),
            "allowed_root": str(tmp_path),
            "workspace_type": "shared",
            "is_writable": True,
            "max_write_sessions": 0,
            "trace_id": "test-invalid-workspace-policy-add",
        },
    )

    assert project_response.status_code == 200
    assert workspace_response.status_code == 400
    assert workspace_response.json()["error_code"] == "COMMAND_ARGUMENT_INVALID"


def test_project_active_session_quota_blocks_new_sessions_until_close(tmp_path):
    client = TestClient(create_app())
    actor = {"id": "admin-ui", "roles": ["admin"]}

    project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": actor,
            "name": "Quota Backend",
            "max_active_sessions": 1,
            "trace_id": "test-project-quota-project",
        },
    )
    project = project_response.json()
    workspace_response = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": actor,
            "machine_id": "local",
            "path": str(tmp_path / "repo"),
            "allowed_root": str(tmp_path),
            "trace_id": "test-project-quota-workspace",
        },
    )
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    first_session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": actor,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Quota One",
            "trace_id": "test-project-quota-first-session",
        },
    )
    assert first_session_response.status_code == 200
    blocked_session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": actor,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Quota Two Blocked",
            "trace_id": "test-project-quota-blocked-session",
        },
    )
    close_response = client.post(
        f"/api/v1/sessions/{first_session_response.json()['id']}/close",
        json=actor,
    )
    second_session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": actor,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Quota Two",
            "trace_id": "test-project-quota-second-session",
        },
    )

    assert project["max_active_sessions"] == 1
    assert blocked_session_response.status_code == 409
    blocked_payload = blocked_session_response.json()
    assert blocked_payload["error_code"] == "QUOTA_EXCEEDED"
    assert blocked_payload["details"] == {
        "project_id": project["id"],
        "active_sessions": 1,
        "max_active_sessions": 1,
    }
    assert close_response.status_code == 200
    assert second_session_response.status_code == 200


def test_project_queued_turn_quota_blocks_excess_turns_per_project(tmp_path):
    client = TestClient(create_app())
    actor = {"id": "admin-ui", "roles": ["admin"]}

    project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": actor,
            "name": "Turn Quota Backend",
            "max_queued_turns": 1,
            "trace_id": "test-turn-quota-project",
        },
    )
    assert project_response.status_code == 200
    project = project_response.json()
    workspace_response = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": actor,
            "machine_id": "local",
            "path": str(tmp_path / "repo"),
            "allowed_root": str(tmp_path),
            "trace_id": "test-turn-quota-workspace",
        },
    )
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": actor,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Turn Quota",
            "trace_id": "test-turn-quota-session",
        },
    )
    assert session_response.status_code == 200
    session = session_response.json()
    first_turn_response = client.post(
        f"/api/v1/sessions/{session['id']}/turns",
        json={
            "actor": actor,
            "prompt": "First queued turn",
            "trace_id": "test-turn-quota-first",
        },
    )
    blocked_turn_response = client.post(
        f"/api/v1/sessions/{session['id']}/turns",
        json={
            "actor": actor,
            "prompt": "Blocked queued turn",
            "trace_id": "test-turn-quota-blocked",
        },
    )

    assert project["max_queued_turns"] == 1
    assert first_turn_response.status_code == 200
    assert blocked_turn_response.status_code == 409
    blocked_payload = blocked_turn_response.json()
    assert blocked_payload["error_code"] == "QUOTA_EXCEEDED"
    assert blocked_payload["details"] == {
        "project_id": project["id"],
        "queued_turns": 1,
        "max_queued_turns": 1,
        "queue_position": 2,
    }


def test_project_daily_turn_quota_blocks_same_user_only(tmp_path):
    client = TestClient(create_app())
    admin = {"id": "admin-ui", "roles": ["admin"]}
    first_actor = {"id": "usr_daily_one", "roles": ["admin"]}
    second_actor = {"id": "usr_daily_two", "roles": ["admin"]}

    project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": admin,
            "name": "Daily Turn Quota",
            "daily_turns_per_user": 1,
            "trace_id": "test-daily-turn-project",
        },
    )
    assert project_response.status_code == 200
    project = project_response.json()
    workspace_response = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": admin,
            "machine_id": "local",
            "path": str(tmp_path / "repo"),
            "allowed_root": str(tmp_path),
            "trace_id": "test-daily-turn-workspace",
        },
    )
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": admin,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Daily Turn Session",
            "trace_id": "test-daily-turn-session",
        },
    )
    assert session_response.status_code == 200
    session = session_response.json()
    first_turn_response = client.post(
        f"/api/v1/sessions/{session['id']}/turns",
        json={
            "actor": first_actor,
            "prompt": "First daily turn",
            "trace_id": "test-daily-turn-first",
        },
    )
    blocked_turn_response = client.post(
        f"/api/v1/sessions/{session['id']}/turns",
        json={
            "actor": first_actor,
            "prompt": "Blocked daily turn",
            "trace_id": "test-daily-turn-blocked",
        },
    )
    other_user_turn_response = client.post(
        f"/api/v1/sessions/{session['id']}/turns",
        json={
            "actor": second_actor,
            "prompt": "Other user daily turn",
            "trace_id": "test-daily-turn-other-user",
        },
    )

    assert project["daily_turns_per_user"] == 1
    assert first_turn_response.status_code == 200
    assert blocked_turn_response.status_code == 409
    blocked_payload = blocked_turn_response.json()
    assert blocked_payload["error_code"] == "QUOTA_EXCEEDED"
    assert blocked_payload["details"]["project_id"] == project["id"]
    assert blocked_payload["details"]["actor_id"] == "usr_daily_one"
    assert blocked_payload["details"]["daily_turns"] == 1
    assert blocked_payload["details"]["daily_turns_per_user"] == 1
    assert "reset_at" in blocked_payload["details"]
    assert other_user_turn_response.status_code == 200


def test_session_queue_api_lists_removes_and_clears_queued_turns(tmp_path):
    control = ControlPlane()
    client = TestClient(create_app(control))
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    operator_one = {"id": "usr_operator_one", "roles": ["operator"]}
    operator_two = {"id": "usr_operator_two", "roles": ["operator"]}

    project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": maintainer,
            "name": "Queue Backend",
            "trace_id": "test-queue-project",
        },
    )
    assert project_response.status_code == 200
    project = project_response.json()
    workspace_response = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": maintainer,
            "machine_id": "local",
            "path": str(tmp_path / "repo"),
            "allowed_root": str(tmp_path),
            "trace_id": "test-queue-workspace",
        },
    )
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": maintainer,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Queue Session",
            "trace_id": "test-queue-session",
        },
    )
    assert session_response.status_code == 200
    session = session_response.json()
    first_turn_response = client.post(
        f"/api/v1/sessions/{session['id']}/turns",
        json={
            "actor": operator_one,
            "prompt": "First queued turn",
            "trace_id": "test-queue-first-turn",
        },
    )
    second_turn_response = client.post(
        f"/api/v1/sessions/{session['id']}/turns",
        json={
            "actor": operator_two,
            "prompt": "Second queued turn",
            "trace_id": "test-queue-second-turn",
        },
    )
    third_turn_response = client.post(
        f"/api/v1/sessions/{session['id']}/turns",
        json={
            "actor": operator_one,
            "prompt": "Third queued turn",
            "trace_id": "test-queue-third-turn",
        },
    )
    assert first_turn_response.status_code == 200
    assert second_turn_response.status_code == 200
    assert third_turn_response.status_code == 200
    first_turn = first_turn_response.json()
    second_turn = second_turn_response.json()
    third_turn = third_turn_response.json()

    list_response = client.get(f"/api/v1/sessions/{session['id']}/queue")
    queue_version = list_response.json()["queue_version"]
    pause_response = client.post(
        f"/api/v1/sessions/{session['id']}/queue/pause",
        json={
            "actor": maintainer,
            "expected_queue_version": queue_version,
            "trace_id": "test-queue-pause",
        },
    )
    paused_queue_version = pause_response.json()["queue_version"]
    paused_start_response = client.post(
        f"/api/v1/sessions/{session['id']}/events",
        json={
            "type": "turn.started",
            "turn_id": first_turn["id"],
            "trace_id": "test-queue-paused-start",
        },
    )
    resume_response = client.post(
        f"/api/v1/sessions/{session['id']}/queue/resume",
        json={
            "actor": maintainer,
            "expected_queue_version": paused_queue_version,
            "trace_id": "test-queue-resume",
        },
    )
    resumed_queue_version = resume_response.json()["queue_version"]
    reorder_response = client.post(
        f"/api/v1/sessions/{session['id']}/queue/reorder",
        json={
            "actor": maintainer,
            "turn_id": third_turn["id"],
            "before_turn_id": first_turn["id"],
            "expected_queue_version": resumed_queue_version,
            "trace_id": "test-queue-reorder",
        },
    )
    reordered_queue_version = reorder_response.json()["queue_version"]
    stale_reorder_response = client.post(
        f"/api/v1/sessions/{session['id']}/queue/reorder",
        json={
            "actor": maintainer,
            "turn_id": second_turn["id"],
            "before_turn_id": third_turn["id"],
            "expected_queue_version": queue_version,
            "trace_id": "test-queue-stale-reorder",
        },
    )
    denied_remove_response = client.request(
        "DELETE",
        f"/api/v1/sessions/{session['id']}/queue/{first_turn['id']}",
        json={
            "actor": operator_two,
            "expected_queue_version": reordered_queue_version,
            "trace_id": "test-queue-denied-remove",
        },
    )
    own_remove_response = client.request(
        "DELETE",
        f"/api/v1/sessions/{session['id']}/queue/{first_turn['id']}",
        json={
            "actor": operator_one,
            "expected_queue_version": reordered_queue_version,
            "trace_id": "test-queue-own-remove",
        },
    )
    remove_queue_version = own_remove_response.json()["queue_version"]
    unconfirmed_clear_response = client.post(
        f"/api/v1/sessions/{session['id']}/queue/clear",
        json={
            "actor": maintainer,
            "expected_queue_version": remove_queue_version,
            "trace_id": "test-queue-unconfirmed-clear",
        },
    )
    clear_response = client.post(
        f"/api/v1/sessions/{session['id']}/queue/clear",
        json={
            "actor": maintainer,
            "expected_queue_version": remove_queue_version,
            "confirm_count": 2,
            "trace_id": "test-queue-clear",
        },
    )
    final_list_response = client.get(f"/api/v1/sessions/{session['id']}/queue")

    assert list_response.status_code == 200
    assert queue_version.startswith("qv_")
    assert list_response.json()["queue_paused"] is False
    assert [turn["id"] for turn in list_response.json()["turns"]] == [
        first_turn["id"],
        second_turn["id"],
        third_turn["id"],
    ]
    assert pause_response.status_code == 200
    assert pause_response.json()["queue_paused"] is True
    assert pause_response.json()["queue_version"] != queue_version
    assert paused_start_response.status_code == 409
    assert paused_start_response.json()["details"]["queue_paused"] is True
    assert resume_response.status_code == 200
    assert resume_response.json()["queue_paused"] is False
    assert resume_response.json()["queue_version"] != paused_queue_version
    assert reorder_response.status_code == 200
    assert reordered_queue_version.startswith("qv_")
    assert reordered_queue_version != resumed_queue_version
    assert [turn["id"] for turn in reorder_response.json()["turns"]] == [
        third_turn["id"],
        first_turn["id"],
        second_turn["id"],
    ]
    assert stale_reorder_response.status_code == 409
    assert stale_reorder_response.json()["error_code"] == "RESOURCE_CONFLICT"
    assert (
        stale_reorder_response.json()["details"]["current_queue_version"]
        == reordered_queue_version
    )
    assert denied_remove_response.status_code == 403
    assert denied_remove_response.json()["error_code"] == "PERMISSION_DENIED"
    assert own_remove_response.status_code == 200
    assert own_remove_response.json()["turn"]["status"] == "cancelled"
    assert unconfirmed_clear_response.status_code == 400
    assert unconfirmed_clear_response.json()["error_code"] == "COMMAND_ARGUMENT_INVALID"
    assert unconfirmed_clear_response.json()["details"]["current_count"] == 2
    assert clear_response.status_code == 200
    assert clear_response.json()["count"] == 2
    assert [turn["id"] for turn in clear_response.json()["turns"]] == [
        third_turn["id"],
        second_turn["id"],
    ]
    assert final_list_response.status_code == 200
    assert final_list_response.json()["queue_paused"] is False
    assert final_list_response.json()["turns"] == []
    assert control.repository.turns[first_turn["id"]].status == TurnStatus.CANCELLED
    assert control.repository.turns[second_turn["id"]].status == TurnStatus.CANCELLED
    assert control.repository.turns[third_turn["id"]].status == TurnStatus.CANCELLED


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
            "max_active_sessions": 3,
            "max_running_turns": 2,
            "max_queued_turns": 5,
            "daily_turns_per_user": 7,
            "trace_id": "test-admin-project-create",
        },
    )
    assert project_response.status_code == 200
    project = project_response.json()
    assert project["slug"] == "backend"
    assert project["max_active_sessions"] == 3
    assert project["max_running_turns"] == 2
    assert project["max_queued_turns"] == 5
    assert project["daily_turns_per_user"] == 7

    workspace_path = tmp_path / "repo"
    workspace_response = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": actor,
            "machine_id": "local",
            "path": str(workspace_path),
            "allowed_root": str(tmp_path),
            "workspace_type": "shared",
            "is_writable": True,
            "max_write_sessions": 2,
            "trace_id": "test-admin-workspace-add",
        },
    )
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    assert workspace["project_id"] == project["id"]
    assert workspace["is_writable"] is True
    assert workspace["max_write_sessions"] == 2

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


def test_audit_api_filters_and_limits_records(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY", "audit-secret")
    monkeypatch.setenv("AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_ID", "test-key")
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
    export_json_response = client.get(
        "/api/v1/audit/export",
        params={
            "action": "session.created",
            "actor_id": "admin-ui",
            "session_id": session["id"],
            "limit": 1,
            "format": "json",
        },
    )
    export_csv_response = client.get(
        "/api/v1/audit/export",
        params={
            "action": "session.created",
            "actor_id": "admin-ui",
            "session_id": session["id"],
            "limit": 1,
            "format": "csv",
        },
    )
    export_archive_response = client.get(
        "/api/v1/audit/export",
        params={
            "action": "session.created",
            "actor_id": "admin-ui",
            "session_id": session["id"],
            "limit": 1,
            "format": "archive",
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
    audit_created_at = datetime.fromisoformat(
        audit_event["created_at"].replace("Z", "+00:00")
    )
    audit_window_from = (audit_created_at - timedelta(minutes=1)).isoformat()
    audit_window_to = (audit_created_at + timedelta(minutes=1)).isoformat()
    audit_future_from = (audit_created_at + timedelta(days=1)).isoformat()
    audit_window_response = client.get(
        "/api/v1/audit",
        params={
            "action": "session.created",
            "session_id": session["id"],
            "created_from": audit_window_from,
            "created_to": audit_window_to,
        },
    )
    audit_future_response = client.get(
        "/api/v1/audit",
        params={
            "action": "session.created",
            "session_id": session["id"],
            "created_from": audit_future_from,
        },
    )
    export_archive_window_response = client.get(
        "/api/v1/audit/export",
        params={
            "action": "session.created",
            "session_id": session["id"],
            "created_from": audit_window_from,
            "created_to": audit_window_to,
            "format": "archive",
        },
    )
    assert audit_window_response.status_code == 200
    assert [event["id"] for event in audit_window_response.json()] == [
        audit_event["id"]
    ]
    assert audit_future_response.status_code == 200
    assert audit_future_response.json() == []
    assert export_archive_window_response.status_code == 200
    archive_filters = export_archive_window_response.json()["archive"]["filters"]
    assert archive_filters["created_from"] == audit_window_from
    assert archive_filters["created_to"] == audit_window_to
    assert export_json_response.status_code == 200
    assert export_json_response.headers["content-disposition"].endswith(
        'filename="agentbridge-audit.json"'
    )
    assert export_json_response.json()["count"] == 1
    assert export_json_response.json()["records"][0]["id"] == audit_event["id"]
    assert export_csv_response.status_code == 200
    assert export_csv_response.headers["content-type"].startswith("text/csv")
    assert export_csv_response.headers["content-disposition"].endswith(
        'filename="agentbridge-audit.csv"'
    )
    assert "session.created" in export_csv_response.text
    assert session["id"] in export_csv_response.text
    assert export_archive_response.status_code == 200
    assert export_archive_response.headers["content-disposition"].endswith(
        'filename="agentbridge-audit-archive.json"'
    )
    archive_payload = export_archive_response.json()
    archive = archive_payload["archive"]
    canonical_archive = json.dumps(
        archive,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_signature = hmac.new(
        b"audit-secret",
        canonical_archive.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert archive["format"] == "signed_audit_archive"
    assert archive["record_count"] == 1
    assert archive["records"][0]["id"] == audit_event["id"]
    assert archive["newest_entry_hash"] == audit_event["entry_hash"]
    assert archive["oldest_entry_hash"] == audit_event["entry_hash"]
    assert archive_payload["signature"]["key_id"] == "test-key"
    assert archive_payload["signature"]["archive_sha256"] == hashlib.sha256(
        canonical_archive.encode("utf-8")
    ).hexdigest()
    assert archive_payload["signature"]["value"] == expected_signature
    assert payload_response.status_code == 200
    assert [event["details"]["workspace_id"] for event in payload_response.json()] == [
        workspace["id"]
    ]
    assert payload_missing_response.status_code == 200
    assert payload_missing_response.json() == []
    assert missing_response.status_code == 200
    assert missing_response.json() == []


def test_audit_archive_export_requires_signing_key(tmp_path):
    client = TestClient(create_app())
    actor = {"id": "admin-ui", "roles": ["admin"]}
    project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": actor,
            "name": "Unsigned Audit Backend",
            "trace_id": "test-audit-unsigned-project",
        },
    )
    workspace_response = client.post(
        f"/api/v1/projects/{project_response.json()['id']}/workspaces",
        json={
            "actor": actor,
            "machine_id": "local",
            "path": str(tmp_path),
            "allowed_root": str(tmp_path),
            "trace_id": "test-audit-unsigned-workspace",
        },
    )
    client.post(
        "/api/v1/sessions",
        json={
            "actor": actor,
            "project_id": project_response.json()["id"],
            "workspace_id": workspace_response.json()["id"],
            "name": "Unsigned Audit Session",
            "trace_id": "test-audit-unsigned-session",
        },
    )

    response = client.get("/api/v1/audit/export", params={"format": "archive"})

    assert response.status_code == 400
    assert response.json()["error_code"] == "COMMAND_ARGUMENT_INVALID"
    assert "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY" in response.json()["next_step"]


def test_audit_archive_export_supports_asymmetric_private_key(monkeypatch, tmp_path):
    private_key = ed25519.Ed25519PrivateKey.generate()
    private_key_path = tmp_path / "audit-signing-key.pem"
    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv(
        "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_PRIVATE_KEY_FILE",
        str(private_key_path),
    )
    monkeypatch.setenv("AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY", "hmac-fallback")
    monkeypatch.setenv("AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_ID", "ed25519-test-key")
    client = TestClient(create_app())
    actor = {"id": "admin-ui", "roles": ["admin"]}
    project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": actor,
            "name": "Asymmetric Audit Backend",
            "trace_id": "test-audit-asymmetric-project",
        },
    )

    response = client.get(
        "/api/v1/audit/export",
        params={
            "action": "project.created",
            "project_id": project_response.json()["id"],
            "format": "archive",
        },
    )

    assert response.status_code == 200
    archive_payload = response.json()
    archive = archive_payload["archive"]
    canonical_archive = json.dumps(
        archive,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    signature = archive_payload["signature"]
    private_key.public_key().verify(
        base64.b64decode(signature["value"]),
        canonical_archive.encode("utf-8"),
    )
    public_key_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    assert archive["algorithm"] == "Ed25519"
    assert signature["algorithm"] == "Ed25519"
    assert signature["encoding"] == "base64"
    assert signature["key_id"] == "ed25519-test-key"
    assert signature["archive_sha256"] == hashlib.sha256(
        canonical_archive.encode("utf-8")
    ).hexdigest()
    assert signature["public_key_sha256"] == hashlib.sha256(public_key_der).hexdigest()


def test_audit_archive_export_supports_external_signing_command(monkeypatch, tmp_path):
    signer_script = tmp_path / "audit_external_signer.py"
    signer_script.write_text(
        "\n".join(
            [
                "import base64",
                "import hashlib",
                "import json",
                "import os",
                "import sys",
                "data = sys.stdin.buffer.read()",
                "digest = hashlib.sha256(data).hexdigest()",
                "if os.environ.get('AGENTBRIDGE_AUDIT_ARCHIVE_SHA256') != digest:",
                "    raise SystemExit('digest mismatch')",
                "signature = hashlib.sha256(b'external:' + data).digest()",
                "json.dump({",
                "    'encoding': 'base64',",
                "    'value': base64.b64encode(signature).decode('ascii'),",
                "    'public_key_sha256': 'external-public-key',",
                "    'kms_key_version': 'v3',",
                "    'signature_id': 'sig-001',",
                "    'metadata': {",
                "        'algorithm_env': os.environ.get(",
                "            'AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_ALGORITHM'",
                "        ),",
                "        'key_id_env': os.environ.get(",
                "            'AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_ID'",
                "        ),",
                "    },",
                "}, sys.stdout)",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND",
        f"{sys.executable} {signer_script}",
    )
    monkeypatch.setenv(
        "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND_ALGORITHM",
        "AWS-KMS-RSASSA-PSS-SHA256",
    )
    monkeypatch.setenv("AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_ID", "kms-key")
    monkeypatch.setenv("AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY", "hmac-fallback")
    client = TestClient(create_app())
    actor = {"id": "admin-ui", "roles": ["admin"]}
    project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": actor,
            "name": "External Audit Backend",
            "trace_id": "test-audit-external-project",
        },
    )

    response = client.get(
        "/api/v1/audit/export",
        params={
            "action": "project.created",
            "project_id": project_response.json()["id"],
            "format": "archive",
        },
    )

    assert response.status_code == 200
    archive_payload = response.json()
    archive = archive_payload["archive"]
    canonical_archive = json.dumps(
        archive,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    expected_signature = base64.b64encode(
        hashlib.sha256(b"external:" + canonical_archive.encode("utf-8")).digest()
    ).decode("ascii")
    signature = archive_payload["signature"]
    assert archive["algorithm"] == "AWS-KMS-RSASSA-PSS-SHA256"
    assert signature["algorithm"] == "AWS-KMS-RSASSA-PSS-SHA256"
    assert signature["key_id"] == "kms-key"
    assert signature["encoding"] == "base64"
    assert signature["value"] == expected_signature
    assert signature["archive_sha256"] == hashlib.sha256(
        canonical_archive.encode("utf-8")
    ).hexdigest()
    assert signature["public_key_sha256"] == "external-public-key"
    assert signature["kms_key_version"] == "v3"
    assert signature["signature_id"] == "sig-001"
    assert signature["metadata"] == {
        "algorithm_env": "AWS-KMS-RSASSA-PSS-SHA256",
        "key_id_env": "kms-key",
    }


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


def test_managed_device_identity_requires_command_parse_scope_for_command_parse():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    actor = {"id": "usr_1", "roles": ["maintainer"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-command-parse-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "command-parse-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-command-parse-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    commands_response = client.get("/api/v1/commands", headers=key_headers)
    key_parse_response = client.post(
        "/api/v1/commands/parse",
        json={"raw_text": "/agent project list", "actor": actor},
        headers=key_headers,
    )
    cert_parse_response = client.post(
        "/api/v1/commands/parse",
        json={"raw_text": "/agent project list", "actor": actor},
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_response.status_code == 200
    assert commands_response.status_code == 200
    assert key_parse_response.status_code == 403
    assert cert_parse_response.status_code == 403


def test_managed_device_identity_command_parse_scope_allows_command_parse():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    actor = {"id": "usr_1", "roles": ["maintainer"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "command-parse-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "command_parse"],
            "trace_id": "command-parse-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "command-parse-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    parse_response = client.post(
        "/api/v1/commands/parse",
        json={"raw_text": "/agent project list", "actor": actor},
        headers=headers,
    )

    assert create_response.status_code == 200
    assert parse_response.status_code == 200
    assert parse_response.json()["canonical_command"] == "project.list"
    assert parse_response.json()["args"] == {"all": False}
    assert parse_response.json()["actor"]["id"] == "usr_1"
    assert parse_response.json()["chat_context_id"].startswith("ctx_")


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
    commands_response = client.get("/api/v1/commands", headers=key_headers)
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
    assert commands_response.status_code == 200
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


def test_managed_device_identity_requires_bot_gateway_manage_scope_for_bot_gateway_posts():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "bot-gateway-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    status_response = client.get(
        "/api/v1/bot-gateway/retry-worker",
        headers=key_headers,
    )
    retry_response = client.post(
        "/api/v1/bot-gateway/retry-worker/run-once",
        json={},
        headers=key_headers,
    )
    delivery_result_response = client.post(
        "/api/v1/bot-gateway/delivery-results",
        json={"idempotency_key": "missing", "action": "acknowledge"},
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_response.status_code == 200
    assert status_response.status_code == 403
    assert retry_response.status_code == 403
    assert delivery_result_response.status_code == 403


def test_managed_device_identity_requires_bot_gateway_read_scope_for_bot_gateway_gets():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-bot-gateway-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "bot-gateway-read-scope-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-bot-gateway-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    regular_http_response = client.get("/api/v1/commands", headers=key_headers)
    deliveries_response = client.get(
        "/api/v1/bot-gateway/deliveries",
        headers=key_headers,
    )
    rate_limits_response = client.get(
        "/api/v1/bot-gateway/rate-limits",
        headers=key_headers,
    )
    retry_worker_response = client.get(
        "/api/v1/bot-gateway/retry-worker",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_response.status_code == 200
    assert regular_http_response.status_code == 200
    assert deliveries_response.status_code == 403
    assert rate_limits_response.status_code == 403
    assert retry_worker_response.status_code == 403


def test_managed_device_identity_bot_gateway_read_scope_allows_bot_gateway_gets():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "bot-gateway-read-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "bot_gateway_read"],
            "trace_id": "bot-gateway-read-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "bot-gateway-read-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    deliveries_response = client.get(
        "/api/v1/bot-gateway/deliveries",
        headers=headers,
    )
    rate_limits_response = client.get(
        "/api/v1/bot-gateway/rate-limits",
        headers=headers,
    )
    retry_worker_response = client.get(
        "/api/v1/bot-gateway/retry-worker",
        headers=headers,
    )
    run_once_response = client.post(
        "/api/v1/bot-gateway/retry-worker/run-once",
        json={},
        headers=headers,
    )

    assert create_response.status_code == 200
    assert deliveries_response.status_code == 200
    assert deliveries_response.json() == []
    assert rate_limits_response.status_code == 200
    assert "policies" in rate_limits_response.json()
    assert retry_worker_response.status_code == 200
    assert retry_worker_response.json()["enabled"] is False
    assert run_once_response.status_code == 403


def test_managed_device_identity_bot_gateway_manage_scope_allows_bot_gateway_posts():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "bot-gateway-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "bot_gateway_manage"],
            "trace_id": "bot-gateway-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "bot-gateway-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    retry_worker_response = client.post(
        "/api/v1/bot-gateway/retry-worker/run-once",
        json={},
        headers=headers,
    )
    retry_failed_response = client.post(
        "/api/v1/bot-gateway/retry-failed-deliveries",
        json={},
        headers=headers,
    )

    assert create_response.status_code == 200
    assert retry_worker_response.status_code == 200
    assert retry_worker_response.json()["records"] == []
    assert retry_failed_response.status_code == 200
    assert retry_failed_response.json() == []


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
    regular_http_response = client.get("/api/v1/commands", headers=key_headers)
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


def test_managed_device_identity_group_role_read_scope_allows_role_reads():
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    context = client.post(
        "/api/v1/chat-contexts",
        json={
            "bot_instance_id": "bot-test",
            "platform": "onebot.v11",
            "chat_space_id": "group-role-read-scope",
        },
    ).json()
    setup_grant_response = client.post(
        f"/api/v1/chat-contexts/{context['id']}/roles/grant",
        json={
            "actor": maintainer,
            "target_actor_id": "usr_member",
            "roles": ["operator"],
            "trace_id": "group-role-read-setup-grant",
        },
    )

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "role-read-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "group_role_read"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "group-role-read-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "role-read-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    list_response = client.get(
        f"/api/v1/chat-contexts/{context['id']}/roles",
        headers=key_headers,
    )
    grant_response = client.post(
        f"/api/v1/chat-contexts/{context['id']}/roles/grant",
        json={
            "actor": maintainer,
            "target_actor_id": "usr_other",
            "roles": ["operator"],
            "trace_id": "group-role-read-denied-grant",
        },
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert setup_grant_response.status_code == 200
    assert create_response.status_code == 200
    assert list_response.status_code == 200
    assert [binding["actor_id"] for binding in list_response.json()] == ["usr_member"]
    assert grant_response.status_code == 403


def test_managed_device_identity_group_role_manage_scope_allows_role_writes():
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
    assert list_response.status_code == 403
    assert revoke_response.status_code == 200
    assert revoke_response.json() is None


def test_managed_device_identity_requires_interaction_manage_scope_for_writes(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    operator = {"id": "usr_operator", "roles": ["operator"]}
    approver = {"id": "usr_approver", "roles": ["approver"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-interaction-scope",
        prefix="interaction-scope",
        name="Interaction Scope",
    )
    question = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": operator,
            "type": "question",
            "prompt": "Existing question?",
            "trace_id": "interaction-scope-existing-question",
        },
    ).json()
    approval = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": operator,
            "type": "approval",
            "prompt": "Existing approval?",
            "required_votes": 1,
            "trace_id": "interaction-scope-existing-approval",
        },
    ).json()

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-interaction-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "interaction-scope-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-interaction-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    cert_headers = {"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"}
    list_response = client.get(
        "/api/v1/interactions",
        params={"session_id": session_id},
        headers=key_headers,
    )
    create_response = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": operator,
            "type": "question",
            "prompt": "Denied question?",
            "trace_id": "interaction-scope-denied-create",
        },
        headers=key_headers,
    )
    answer_response = client.post(
        f"/api/v1/interactions/{question['id']}/answer",
        json={
            "actor": operator,
            "answer": "Denied",
            "trace_id": "interaction-scope-denied-answer",
        },
        headers=cert_headers,
    )
    cancel_response = client.post(
        f"/api/v1/interactions/{question['id']}/cancel",
        json={
            "actor": maintainer,
            "reason": "Denied",
            "trace_id": "interaction-scope-denied-cancel",
        },
        headers=key_headers,
    )
    vote_response = client.post(
        f"/api/v1/interactions/{approval['id']}/vote",
        json={
            "actor": approver,
            "approve": True,
            "reason": "Denied",
            "trace_id": "interaction-scope-denied-vote",
        },
        headers=key_headers,
    )

    assert create_identity_response.status_code == 200
    assert list_response.status_code == 403
    assert create_response.status_code == 403
    assert answer_response.status_code == 403
    assert cancel_response.status_code == 403
    assert vote_response.status_code == 403


def test_managed_device_identity_requires_interaction_read_scope_for_reads(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    operator = {"id": "usr_operator", "roles": ["operator"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-interaction-read-scope",
        prefix="interaction-read-scope",
        name="Interaction Read Scope",
    )
    question = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": operator,
            "type": "question",
            "prompt": "Existing question?",
            "trace_id": "interaction-read-scope-existing-question",
        },
    ).json()

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-interaction-read-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "interaction-read-scope-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-interaction-read-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    regular_http_response = client.get("/api/v1/commands", headers=key_headers)
    list_response = client.get(
        "/api/v1/interactions",
        params={"session_id": session_id},
        headers=key_headers,
    )
    show_response = client.get(
        f"/api/v1/interactions/{question['id']}",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_response.status_code == 200
    assert regular_http_response.status_code == 200
    assert list_response.status_code == 403
    assert show_response.status_code == 403


def test_managed_device_identity_interaction_read_scope_allows_reads(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    operator = {"id": "usr_operator", "roles": ["operator"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-interaction-read-manager",
        prefix="interaction-read-manager",
        name="Interaction Read Manager",
    )
    question = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": operator,
            "type": "question",
            "prompt": "Existing question?",
            "trace_id": "interaction-read-manager-existing-question",
        },
    ).json()

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "interaction-read-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "interaction_read"],
            "trace_id": "interaction-read-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "interaction-read-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    list_response = client.get(
        "/api/v1/interactions",
        params={"session_id": session_id},
        headers=headers,
    )
    show_response = client.get(
        f"/api/v1/interactions/{question['id']}",
        headers=headers,
    )
    create_interaction_response = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": operator,
            "type": "question",
            "prompt": "Denied write?",
            "trace_id": "interaction-read-manager-denied-create",
        },
        headers=headers,
    )

    assert create_response.status_code == 200
    assert list_response.status_code == 200
    assert [interaction["id"] for interaction in list_response.json()] == [
        question["id"]
    ]
    assert show_response.status_code == 200
    assert show_response.json()["id"] == question["id"]
    assert create_interaction_response.status_code == 403


def test_managed_device_identity_interaction_manage_scope_allows_writes(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    operator = {"id": "usr_operator", "roles": ["operator"]}
    approver = {"id": "usr_approver", "roles": ["approver"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-interaction-manager",
        prefix="interaction-manager",
        name="Interaction Manager",
    )

    create_identity_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "interaction-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "interaction_manage"],
            "trace_id": "interaction-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "interaction-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    question_response = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": operator,
            "type": "question",
            "prompt": "Which migration strategy?",
            "trace_id": "interaction-manager-question",
        },
        headers=headers,
    )
    answer_response = client.post(
        f"/api/v1/interactions/{question_response.json().get('id')}/answer",
        json={
            "actor": operator,
            "answer": "Use expand-contract.",
            "trace_id": "interaction-manager-answer",
        },
        headers=headers,
    )
    approval_response = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": operator,
            "type": "approval",
            "prompt": "Ship change?",
            "required_votes": 1,
            "trace_id": "interaction-manager-approval",
        },
        headers=headers,
    )
    vote_response = client.post(
        f"/api/v1/interactions/{approval_response.json().get('id')}/vote",
        json={
            "actor": approver,
            "approve": True,
            "reason": "Looks good",
            "trace_id": "interaction-manager-vote",
        },
        headers=headers,
    )
    cancel_target_response = client.post(
        f"/api/v1/sessions/{session_id}/interactions",
        json={
            "actor": operator,
            "type": "question",
            "prompt": "Cancel me?",
            "trace_id": "interaction-manager-cancel-target",
        },
        headers=headers,
    )
    cancel_response = client.post(
        f"/api/v1/interactions/{cancel_target_response.json().get('id')}/cancel",
        json={
            "actor": maintainer,
            "reason": "Superseded",
            "trace_id": "interaction-manager-cancel",
        },
        headers=headers,
    )

    assert create_identity_response.status_code == 200
    assert question_response.status_code == 200
    assert answer_response.status_code == 200
    assert answer_response.json()["status"] == "resolved"
    assert approval_response.status_code == 200
    assert vote_response.status_code == 200
    assert vote_response.json()["votes"] == {"usr_approver": True}
    assert cancel_target_response.status_code == 200
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"


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
    second_created_at = datetime.fromisoformat(
        second.json()["created_at"].replace("Z", "+00:00")
    )
    event_window_from = (second_created_at - timedelta(minutes=1)).isoformat()
    event_window_to = (second_created_at + timedelta(minutes=1)).isoformat()
    event_future_from = (second_created_at + timedelta(days=1)).isoformat()
    time_response = client.get(
        "/api/v1/events",
        params={
            "session_id": session_id,
            "event_type": "assistant.delta",
            "created_from": event_window_from,
            "created_to": event_window_to,
        },
    )
    time_missing_response = client.get(
        "/api/v1/events",
        params={
            "session_id": session_id,
            "event_type": "assistant.delta",
            "created_from": event_future_from,
        },
    )
    rendered_time_response = client.get(
        "/api/v1/events/rendered",
        params={
            "session_id": session_id,
            "q": "newest",
            "created_from": event_window_from,
            "created_to": event_window_to,
        },
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
    assert time_response.status_code == 200
    assert [event["id"] for event in time_response.json()] == [
        second.json()["id"],
        first.json()["id"],
    ]
    assert time_missing_response.status_code == 200
    assert time_missing_response.json() == []
    assert rendered_time_response.status_code == 200
    assert [item["event_id"] for item in rendered_time_response.json()] == [
        second.json()["id"]
    ]
    assert project_response.status_code == 200
    assert [event["session_id"] for event in project_response.json()] == [session_id]


def test_session_event_ingest_updates_turn_lifecycle_and_running_quota(tmp_path):
    control = ControlPlane()
    client = TestClient(create_app(control))
    actor = {"id": "admin-ui", "roles": ["admin"]}

    project_response = client.post(
        "/api/v1/projects",
        json={
            "actor": actor,
            "name": "Running Turn Quota",
            "max_running_turns": 1,
            "trace_id": "test-running-turn-project",
        },
    )
    assert project_response.status_code == 200
    project = project_response.json()
    workspace_response = client.post(
        f"/api/v1/projects/{project['id']}/workspaces",
        json={
            "actor": actor,
            "machine_id": "local",
            "path": str(tmp_path / "repo"),
            "allowed_root": str(tmp_path),
            "trace_id": "test-running-turn-workspace",
        },
    )
    assert workspace_response.status_code == 200
    workspace = workspace_response.json()
    first_session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": actor,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Running One",
            "trace_id": "test-running-turn-session-one",
        },
    )
    second_session_response = client.post(
        "/api/v1/sessions",
        json={
            "actor": actor,
            "project_id": project["id"],
            "workspace_id": workspace["id"],
            "name": "Running Two",
            "trace_id": "test-running-turn-session-two",
        },
    )
    assert first_session_response.status_code == 200
    assert second_session_response.status_code == 200
    first_session = first_session_response.json()
    second_session = second_session_response.json()
    first_turn_response = client.post(
        f"/api/v1/sessions/{first_session['id']}/turns",
        json={
            "actor": actor,
            "prompt": "First running turn",
            "trace_id": "test-running-turn-one",
        },
    )
    second_turn_response = client.post(
        f"/api/v1/sessions/{second_session['id']}/turns",
        json={
            "actor": actor,
            "prompt": "Second running turn",
            "trace_id": "test-running-turn-two",
        },
    )
    assert first_turn_response.status_code == 200
    assert second_turn_response.status_code == 200
    first_turn = first_turn_response.json()
    second_turn = second_turn_response.json()
    same_session_turn_response = client.post(
        f"/api/v1/sessions/{first_session['id']}/turns",
        json={
            "actor": actor,
            "prompt": "Same session second running turn",
            "trace_id": "test-running-turn-same-session",
        },
    )
    assert same_session_turn_response.status_code == 200
    same_session_turn = same_session_turn_response.json()

    first_start = client.post(
        f"/api/v1/sessions/{first_session['id']}/events",
        json={
            "type": "turn.started",
            "source": "terminal_agent",
            "trace_id": "test-running-turn-start-one",
            "turn_id": first_turn["id"],
            "idempotency_key": "running-turn-start-one",
        },
    )
    duplicate_start = client.post(
        f"/api/v1/sessions/{first_session['id']}/events",
        json={
            "type": "turn.started",
            "source": "terminal_agent",
            "trace_id": "test-running-turn-start-one-duplicate",
            "turn_id": first_turn["id"],
            "idempotency_key": "running-turn-start-one",
        },
    )
    same_session_blocked = client.post(
        f"/api/v1/sessions/{first_session['id']}/events",
        json={
            "type": "turn.started",
            "source": "terminal_agent",
            "trace_id": "test-running-turn-same-session-blocked",
            "turn_id": same_session_turn["id"],
            "idempotency_key": "running-turn-same-session-blocked",
        },
    )
    blocked_start = client.post(
        f"/api/v1/sessions/{second_session['id']}/events",
        json={
            "type": "turn.started",
            "source": "terminal_agent",
            "trace_id": "test-running-turn-start-blocked",
            "turn_id": second_turn["id"],
            "idempotency_key": "running-turn-start-two",
        },
    )
    first_complete = client.post(
        f"/api/v1/sessions/{first_session['id']}/events",
        json={
            "type": "turn.completed",
            "source": "terminal_agent",
            "trace_id": "test-running-turn-complete-one",
            "turn_id": first_turn["id"],
            "idempotency_key": "running-turn-complete-one",
        },
    )
    second_start = client.post(
        f"/api/v1/sessions/{second_session['id']}/events",
        json={
            "type": "turn.started",
            "source": "terminal_agent",
            "trace_id": "test-running-turn-start-two",
            "turn_id": second_turn["id"],
            "idempotency_key": "running-turn-start-two",
        },
    )

    assert first_start.status_code == 200
    assert duplicate_start.status_code == 200
    assert duplicate_start.json()["id"] == first_start.json()["id"]
    assert same_session_blocked.status_code == 409
    same_session_payload = same_session_blocked.json()
    assert same_session_payload["error_code"] == "RESOURCE_CONFLICT"
    assert same_session_payload["details"] == {
        "session_id": first_session["id"],
        "active_turn_id": first_turn["id"],
    }
    assert blocked_start.status_code == 409
    blocked_payload = blocked_start.json()
    assert blocked_payload["error_code"] == "QUOTA_EXCEEDED"
    assert blocked_payload["details"] == {
        "project_id": project["id"],
        "running_turns": 1,
        "max_running_turns": 1,
    }
    assert first_complete.status_code == 200
    assert second_start.status_code == 200
    assert control.repository.turns[first_turn["id"]].status == TurnStatus.COMPLETED
    assert control.repository.turns[second_turn["id"]].status == TurnStatus.RUNNING
    assert control.repository.sessions[first_session["id"]].active_turn_id is None
    assert control.repository.sessions[second_session["id"]].active_turn_id == second_turn["id"]


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


def test_managed_device_identity_resource_ids_limit_websocket(tmp_path):
    control = ControlPlane()
    admin = Actor(id="security-admin", roles={"admin"})
    actor = Actor(id="usr_1", roles={"maintainer"})
    project = control.create_project(
        actor=actor,
        name="Managed Device Resource Backend",
        trace_id="managed-device-resource-project",
    )
    workspace = control.add_workspace(
        actor=actor,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="managed-device-resource-workspace",
    )
    allowed_session = control.create_session(
        actor=actor,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Allowed Managed Device Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="managed-device-resource-allowed-session",
    )
    denied_session = control.create_session(
        actor=actor,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Denied Managed Device Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="managed-device-resource-denied-session",
    )
    control.upsert_device_identity(
        actor=admin,
        device_id="scoped-laptop",
        device_key="managed-secret",
        allowed_scopes={DeviceIdentityScope.SESSION_EVENTS_WS},
        allowed_resource_ids={allowed_session.id},
        certificate_fingerprints={"AA:BB:CC"},
        trace_id="managed-device-resource-ws-create",
    )
    client = TestClient(create_app(control))

    with client.websocket_connect(
        f"/api/v1/sessions/{allowed_session.id}/events/ws"
        "?device_id=scoped-laptop&device_key=managed-secret&idle_timeout_seconds=0"
    ) as websocket:
        message = websocket.receive_json()
        idle = websocket.receive_json()
    with client.websocket_connect(
        f"/api/v1/sessions/{denied_session.id}/events/ws"
        "?device_id=scoped-laptop&device_key=managed-secret&idle_timeout_seconds=0"
    ) as websocket:
        key_denied = websocket.receive_json()
    with client.websocket_connect(
        f"/api/v1/sessions/{denied_session.id}/events/ws?idle_timeout_seconds=0",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    ) as websocket:
        certificate_denied = websocket.receive_json()

    assert message["type"] == "semantic_event"
    assert message["event"]["type"] == "session.created"
    assert idle == {"type": "idle_timeout", "last_seq": message["event"]["seq"]}
    assert key_denied["type"] == "error"
    assert key_denied["error"]["error_code"] == "PERMISSION_DENIED"
    assert certificate_denied["type"] == "error"
    assert certificate_denied["error"]["error_code"] == "PERMISSION_DENIED"


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
        "/api/v1/commands",
        headers={
            "x-agentbridge-device-id": "laptop",
            "x-agentbridge-device-key": "managed-secret",
        },
    )
    http_certificate_response = client.get(
        "/api/v1/commands",
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
    regular_http_response = client.get("/api/v1/commands", headers=key_headers)
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
    assert start_response.status_code == 403
    assert run_once_response.status_code == 403


def test_managed_device_identity_requires_terminal_read_scope_for_terminal_http_reads(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    actor = {"id": "usr_1", "roles": ["maintainer"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-terminal-read-scope",
        prefix="terminal-read-scope",
        name="Terminal Read Scope",
    )
    start_response = client.post(
        f"/api/v1/sessions/{session_id}/terminal/start",
        json={
            "actor": actor,
            "command": "fake-cli",
            "trace_id": "terminal-read-scope-start",
        },
    )

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "readonly-terminal-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api"],
            "certificate_fingerprints": ["SHA256:AA:BB:CC"],
            "trace_id": "terminal-read-scope-device-create",
        },
    )
    key_headers = {
        "x-agentbridge-device-id": "readonly-terminal-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    regular_http_response = client.get("/api/v1/commands", headers=key_headers)
    lifecycle_status_response = client.get(
        "/api/v1/terminal/lifecycle-monitor",
        headers=key_headers,
    )
    snapshot_response = client.get(
        f"/api/v1/sessions/{session_id}/terminal/snapshot",
        headers=key_headers,
    )
    status_response = client.get(
        f"/api/v1/sessions/{session_id}/terminal/status",
        headers={"x-agentbridge-client-cert-fingerprint": "aa:bb:cc"},
    )

    assert create_response.status_code == 200
    assert start_response.status_code == 200
    assert regular_http_response.status_code == 200
    assert lifecycle_status_response.status_code == 403
    assert snapshot_response.status_code == 403
    assert status_response.status_code == 403


def test_managed_device_identity_terminal_read_scope_allows_terminal_http_reads(
    tmp_path,
):
    client = TestClient(create_app())
    admin = {"id": "security-admin", "roles": ["admin"]}
    actor = {"id": "usr_1", "roles": ["maintainer"]}
    session_id = _create_session_with_project(
        client,
        tmp_path,
        chat_space_id="group-terminal-read-manager-scope",
        prefix="terminal-read-manager-scope",
        name="Terminal Read Manager Scope",
    )
    start_response = client.post(
        f"/api/v1/sessions/{session_id}/terminal/start",
        json={
            "actor": actor,
            "command": "fake-cli",
            "trace_id": "terminal-read-manager-start",
        },
    )

    create_response = client.post(
        "/api/v1/device-identities",
        json={
            "actor": admin,
            "device_id": "terminal-read-device",
            "device_key": "managed-secret",
            "allowed_scopes": ["http_api", "terminal_read"],
            "trace_id": "terminal-read-manager-device-create",
        },
    )
    headers = {
        "x-agentbridge-device-id": "terminal-read-device",
        "x-agentbridge-device-key": "managed-secret",
    }
    lifecycle_status_response = client.get(
        "/api/v1/terminal/lifecycle-monitor",
        headers=headers,
    )
    snapshot_response = client.get(
        f"/api/v1/sessions/{session_id}/terminal/snapshot",
        headers=headers,
    )
    status_response = client.get(
        f"/api/v1/sessions/{session_id}/terminal/status",
        headers=headers,
    )

    assert create_response.status_code == 200
    assert start_response.status_code == 200
    assert lifecycle_status_response.status_code == 200
    assert snapshot_response.status_code == 200
    assert snapshot_response.json() == {"snapshot": ""}
    assert status_response.status_code == 200
    assert status_response.json()["started"] is True
    assert status_response.json()["running"] is True


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
