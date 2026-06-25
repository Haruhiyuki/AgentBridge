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
    Visibility,
)
from agentbridge.policy import Permission


def _create_session(control: ControlPlane, tmp_path, actor: Actor):
    project = control.create_project(
        actor=actor,
        name="Backend",
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
        name="Policy Session",
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
