from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentbridge.api import create_app
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, AgentBridgeError, ErrorCode, LeaseOwnerType, Visibility
from agentbridge.terminal_agent import (
    FakeTerminalBackend,
    TerminalAgentService,
    TerminalInputKind,
)


def create_session(control: ControlPlane, tmp_path):
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    project = control.create_project(actor=maintainer, name="Backend", trace_id="project")
    workspace = control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="workspace",
    )
    session = control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Terminal Test",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="session",
    )
    return maintainer, session


def test_terminal_agent_enforces_current_writer_lease_epoch(tmp_path):
    control = ControlPlane()
    backend = FakeTerminalBackend()
    terminal = TerminalAgentService(control, backend=backend)
    maintainer, session = create_session(control, tmp_path)

    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="terminal-start")

    with pytest.raises(AgentBridgeError) as exc_info:
        terminal.submit_input(
            session_id=session.id,
            epoch=1,
            owner_type=LeaseOwnerType.WEB_ADMIN,
            owner_id=maintainer.id,
            kind=TerminalInputKind.TEXT,
            data="before lease",
            trace_id="no-lease",
            request_id="no-lease",
        )
    assert exc_info.value.code == ErrorCode.LEASE_CONFLICT

    web_lease = control.acquire_lease(
        actor=maintainer,
        session_id=session.id,
        owner_type=LeaseOwnerType.WEB_ADMIN,
        owner_id=maintainer.id,
        ttl_seconds=300,
        trace_id="web-lease",
    )
    terminal.submit_input(
        session_id=session.id,
        epoch=web_lease.epoch,
        owner_type=LeaseOwnerType.WEB_ADMIN,
        owner_id=maintainer.id,
        kind=TerminalInputKind.TEXT,
        data="hello\n",
        trace_id="web-input",
        request_id="web-input",
    )

    human_lease = control.acquire_lease(
        actor=maintainer,
        session_id=session.id,
        owner_type=LeaseOwnerType.HUMAN,
        owner_id="local-user",
        ttl_seconds=300,
        trace_id="human-lease",
    )

    with pytest.raises(AgentBridgeError) as exc_info:
        terminal.submit_input(
            session_id=session.id,
            epoch=web_lease.epoch,
            owner_type=LeaseOwnerType.WEB_ADMIN,
            owner_id=maintainer.id,
            kind=TerminalInputKind.TEXT,
            data="stale\n",
            trace_id="stale-input",
            request_id="stale-input",
        )
    assert exc_info.value.code == ErrorCode.LEASE_CONFLICT

    terminal.submit_input(
        session_id=session.id,
        epoch=human_lease.epoch,
        owner_type=LeaseOwnerType.HUMAN,
        owner_id="local-user",
        kind=TerminalInputKind.PASTE,
        data="human\n",
        trace_id="human-input",
        request_id="human-input",
    )

    assert terminal.snapshot(session_id=session.id) == "hello\nhuman\n"
    assert [event.type for event in control.repository.list_events(session_id=session.id)] == [
        "session.created",
        "terminal.started",
        "terminal.input.rejected",
        "lease.acquired",
        "terminal.input.accepted",
        "lease.acquired",
        "terminal.input.rejected",
        "terminal.input.accepted",
    ]


def test_terminal_api_writes_to_fake_backend_after_lease(tmp_path):
    client = TestClient(create_app())
    actor = {"id": "usr_1", "roles": ["maintainer"]}
    chat = {
        "bot_instance_id": "bot-test",
        "platform": "onebot.v11",
        "chat_space_id": "group-terminal",
    }

    client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
            "actor": actor,
            "chat": chat,
            "idempotency_key": "terminal-api-project",
        },
    )
    session_response = client.post(
        "/api/v1/commands/execute",
        json={
            "raw_text": "/agent session new Terminal API",
            "actor": actor,
            "chat": chat,
            "idempotency_key": "terminal-api-session",
        },
    )
    session_id = session_response.json()["data"]["session_id"]

    start_response = client.post(
        f"/api/v1/sessions/{session_id}/terminal/start",
        json={"actor": actor, "command": "fake-cli", "trace_id": "terminal-api-start"},
    )
    assert start_response.status_code == 200

    lease_response = client.post(
        f"/api/v1/sessions/{session_id}/lease/acquire",
        json={
            "actor": actor,
            "owner_type": "web_admin",
            "owner_id": "usr_1",
            "trace_id": "terminal-api-lease",
        },
    )
    assert lease_response.status_code == 200
    epoch = lease_response.json()["epoch"]

    input_response = client.post(
        f"/api/v1/sessions/{session_id}/terminal/input",
        json={
            "actor": actor,
            "epoch": epoch,
            "owner_type": "web_admin",
            "owner_id": "usr_1",
            "type": "text",
            "data": "hello api\n",
            "request_id": "terminal-api-input",
            "trace_id": "terminal-api-input",
        },
    )
    assert input_response.status_code == 200

    snapshot_response = client.get(f"/api/v1/sessions/{session_id}/terminal/snapshot")
    assert snapshot_response.status_code == 200
    assert snapshot_response.json()["snapshot"] == "hello api\n"
