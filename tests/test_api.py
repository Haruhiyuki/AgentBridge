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
