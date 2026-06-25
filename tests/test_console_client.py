from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from agentbridge.console_client import LocalConsoleClient
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, Visibility
from agentbridge.terminal_agent import FakeTerminalBackend, TerminalAgentService
from agentbridge.terminal_daemon import LocalTerminalAgentClient, LocalTerminalAgentServer


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
    return control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Console Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="session",
    )


def test_console_client_acquires_human_lease_on_first_input_and_releases(tmp_path):
    async def scenario():
        control = ControlPlane()
        backend = FakeTerminalBackend()
        terminal = TerminalAgentService(control, backend=backend)
        session = create_session(control, tmp_path)
        socket_path = Path(f"/tmp/agentbridge-console-{uuid4().hex}.sock")
        server = LocalTerminalAgentServer(
            control=control,
            terminal=terminal,
            auth_token="secret-token",
        )
        await server.start(socket_path)
        try:
            client = LocalConsoleClient(
                daemon=LocalTerminalAgentClient(socket_path, "secret-token"),
                session_id=session.id,
                owner_id="local-user",
            )
            await client.start_session("fake-cli")
            assert client.lease is None

            await client.send_text("first\n", request_id="console-input-1")
            assert client.lease is not None
            assert control.repository.current_lease(session.id).owner_type == "human"

            await client.send_text("first\n", request_id="console-input-1")
            await client.send_paste("second\n", request_id="console-input-2")

            assert await client.snapshot() == "first\nsecond\n"
            assert await client.release() == 2
            assert client.lease is None
            assert control.repository.current_lease(session.id) is None

            stale_response = await client.daemon.request(
                "submit_input",
                {
                    "session_id": session.id,
                    "epoch": 1,
                    "owner_type": "human",
                    "owner_id": "local-user",
                    "type": "text",
                    "data": "stale\n",
                    "request_id": "console-stale",
                },
            )
            assert stale_response["ok"] is False
            assert stale_response["error"]["error_code"] == "LEASE_CONFLICT"
        finally:
            await server.stop()

    asyncio.run(scenario())
