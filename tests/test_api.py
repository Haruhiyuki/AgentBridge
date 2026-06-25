from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from agentbridge.api import create_app
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

    client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": actor,
            "chat": chat,
            "idempotency_key": "event-api-project",
        },
    )
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

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == first.json()["id"]

    events_response = client.get(f"/api/v1/sessions/{session_id}/events", params={"after_seq": 1})
    assert events_response.status_code == 200
    assert [event["type"] for event in events_response.json()] == ["assistant.delta"]


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

    app = create_app()

    status = app.state.terminal.lifecycle_monitor_status()
    assert status["auto_restart_on_lost"] is True
    assert status["auto_restart_max_attempts"] == 3
    assert status["auto_restart_attempt_count"] == 0


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
