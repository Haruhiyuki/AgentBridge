from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentbridge.api import create_app
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    AccessPolicyEffect,
    Actor,
    AgentBridgeError,
    ErrorCode,
    LeaseOwnerType,
    Visibility,
)
from agentbridge.policy import Permission


def _create_session(
    control: ControlPlane,
    tmp_path,
    actor: Actor,
    *,
    project_name: str = "Backend",
    session_name: str = "Policy Session",
):
    project = control.create_project(
        actor=actor,
        name=project_name,
        trace_id="policy-project",
    )
    workspace = control.add_workspace(
        actor=actor,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="policy-workspace",
    )
    return control.create_session(
        actor=actor,
        project_id=project.id,
        workspace_id=workspace.id,
        name=session_name,
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="policy-session",
    )


def test_access_policy_deny_rule_overrides_role_permission(tmp_path):
    control = ControlPlane()
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    operator = Actor(id="usr_operator", roles={"operator"})
    session = _create_session(control, tmp_path, maintainer)

    rule = control.set_access_policy_rule(
        actor=maintainer,
        effect=AccessPolicyEffect.DENY,
        action=Permission.SESSION_SEND.value,
        roles=["operator"],
        trace_id="policy-deny",
    )

    with pytest.raises(AgentBridgeError) as exc_info:
        control.enqueue_turn(
            actor=operator,
            session_id=session.id,
            prompt="run tests",
            trace_id="policy-denied-turn",
        )

    assert exc_info.value.code == ErrorCode.PERMISSION_DENIED
    assert exc_info.value.details["policy_source"] == "access_policy"
    assert exc_info.value.details["matched_rule_id"] == rule.id


def test_access_policy_resource_rule_denies_only_matching_session(tmp_path):
    control = ControlPlane()
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    operator = Actor(id="usr_operator", roles={"operator"})
    blocked_session = _create_session(
        control,
        tmp_path,
        maintainer,
        project_name="Blocked Backend",
        session_name="Blocked Session",
    )
    allowed_session = _create_session(
        control,
        tmp_path,
        maintainer,
        project_name="Allowed Backend",
        session_name="Allowed Session",
    )

    rule = control.set_access_policy_rule(
        actor=maintainer,
        effect=AccessPolicyEffect.DENY,
        action=Permission.SESSION_SEND.value,
        resource_type="session",
        resource_id=blocked_session.id,
        roles=["operator"],
        trace_id="policy-deny-one-session",
    )

    with pytest.raises(AgentBridgeError) as exc_info:
        control.enqueue_turn(
            actor=operator,
            session_id=blocked_session.id,
            prompt="blocked",
            trace_id="policy-denied-specific-turn",
        )
    allowed_turn = control.enqueue_turn(
        actor=operator,
        session_id=allowed_session.id,
        prompt="allowed",
        trace_id="policy-allowed-specific-turn",
    )

    assert exc_info.value.details["matched_rule_id"] == rule.id
    assert allowed_turn.session_id == allowed_session.id


def test_access_policy_terminal_rule_grants_specific_session_control(tmp_path):
    control = ControlPlane()
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    operator = Actor(id="usr_operator", roles={"operator"})
    allowed_session = _create_session(
        control,
        tmp_path,
        maintainer,
        project_name="Terminal Allowed",
        session_name="Terminal Allowed",
    )
    denied_session = _create_session(
        control,
        tmp_path,
        maintainer,
        project_name="Terminal Denied",
        session_name="Terminal Denied",
    )

    rule = control.set_access_policy_rule(
        actor=maintainer,
        effect=AccessPolicyEffect.ALLOW,
        action=Permission.TERMINAL_CONTROL.value,
        resource_type="terminal",
        resource_id=allowed_session.id,
        roles=["operator"],
        trace_id="policy-terminal-allow",
    )
    lease = control.acquire_lease(
        actor=operator,
        session_id=allowed_session.id,
        owner_type=LeaseOwnerType.HUMAN,
        owner_id=operator.id,
        ttl_seconds=300,
        trace_id="policy-terminal-lease",
    )

    with pytest.raises(AgentBridgeError) as exc_info:
        control.acquire_lease(
            actor=operator,
            session_id=denied_session.id,
            owner_type=LeaseOwnerType.HUMAN,
            owner_id=operator.id,
            ttl_seconds=300,
            trace_id="policy-terminal-denied-lease",
        )

    assert lease.session_id == allowed_session.id
    assert control.policy.evaluate(
        operator,
        Permission.TERMINAL_CONTROL,
        resource_type="terminal",
        resource_id=allowed_session.id,
    ).matched_rule_id == rule.id
    assert exc_info.value.details["policy_source"] == "role"


def test_access_policy_simulation_matches_resource_attributes(tmp_path):
    control = ControlPlane()
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    operator = Actor(id="usr_operator", roles={"operator"})
    session = _create_session(control, tmp_path, maintainer)

    rule = control.set_access_policy_rule(
        actor=maintainer,
        effect=AccessPolicyEffect.ALLOW,
        action=Permission.TERMINAL_CONTROL.value,
        resource_type="session",
        resource_id=session.id,
        roles=["operator"],
        attributes={"risk": "low"},
        trace_id="policy-allow-terminal",
    )

    allowed = control.simulate_access_policy(
        actor=maintainer,
        target_actor=operator,
        action=Permission.TERMINAL_CONTROL.value,
        resource_type="session",
        resource_id=session.id,
        attributes={"risk": "low"},
    )
    denied = control.simulate_access_policy(
        actor=maintainer,
        target_actor=operator,
        action=Permission.TERMINAL_CONTROL.value,
        resource_type="session",
        resource_id=session.id,
        attributes={"risk": "high"},
    )

    assert allowed["decision"]["allowed"] is True
    assert allowed["decision"]["source"] == "access_policy"
    assert allowed["decision"]["matched_rule_id"] == rule.id
    assert denied["decision"]["allowed"] is False
    assert denied["decision"]["source"] == "role"


def test_access_policy_api_terminal_route_uses_terminal_resource(tmp_path):
    control = ControlPlane()
    client = TestClient(create_app(control))
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    session = _create_session(control, tmp_path, maintainer)
    rule = control.set_access_policy_rule(
        actor=maintainer,
        effect=AccessPolicyEffect.DENY,
        action=Permission.TERMINAL_CONTROL.value,
        resource_type="terminal",
        resource_id=session.id,
        roles=["maintainer"],
        trace_id="policy-api-terminal-deny",
    )

    response = client.post(
        f"/api/v1/sessions/{session.id}/terminal/start",
        json={
            "actor": {"id": maintainer.id, "roles": ["maintainer"]},
            "command": "sh",
            "trace_id": "policy-api-terminal-start",
        },
    )

    assert response.status_code == 403
    assert response.json()["details"]["matched_rule_id"] == rule.id


def test_access_policy_api_manages_and_simulates_rules():
    client = TestClient(create_app())
    maintainer = {"id": "usr_maintainer", "roles": ["maintainer"]}
    operator = {"id": "usr_operator", "roles": ["operator"]}

    create_response = client.post(
        "/api/v1/access-policy/rules",
        json={
            "actor": maintainer,
            "effect": "allow",
            "action": "terminal.control",
            "resource_type": "session",
            "roles": ["operator"],
            "attributes": {"risk": "low"},
            "trace_id": "policy-api-create",
        },
    )
    assert create_response.status_code == 200
    rule_id = create_response.json()["id"]

    list_response = client.get("/api/v1/access-policy/rules", params={"enabled": True})
    simulate_response = client.post(
        "/api/v1/access-policy/simulate",
        json={
            "actor": maintainer,
            "target_actor": operator,
            "action": "terminal.control",
            "resource_type": "session",
            "attributes": {"risk": "low"},
        },
    )
    delete_response = client.post(
        f"/api/v1/access-policy/rules/{rule_id}/delete",
        json={"actor": maintainer, "trace_id": "policy-api-delete"},
    )
    denied_response = client.post(
        "/api/v1/access-policy/simulate",
        json={
            "actor": maintainer,
            "target_actor": operator,
            "action": "terminal.control",
            "resource_type": "session",
            "attributes": {"risk": "low"},
        },
    )

    assert list_response.status_code == 200
    assert [rule["id"] for rule in list_response.json()] == [rule_id]
    assert simulate_response.status_code == 200
    assert simulate_response.json()["decision"]["allowed"] is True
    assert simulate_response.json()["decision"]["matched_rule_id"] == rule_id
    assert delete_response.status_code == 200
    assert delete_response.json()["id"] == rule_id
    assert denied_response.status_code == 200
    assert denied_response.json()["decision"]["allowed"] is False


def test_access_policy_admin_ui_serves_editor():
    client = TestClient(create_app())

    response = client.get("/admin/access-policy")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert "AgentBridge Access Policy" in html
    assert "/api/v1/access-policy/rules" in html
    assert "/api/v1/access-policy/simulate" in html
    assert "async function saveRule()" in html
    assert "await simulatePolicy();" in html
