from __future__ import annotations

import asyncio
import stat
from pathlib import Path
from uuid import uuid4

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
        name="Daemon Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="session",
    )


def test_local_terminal_daemon_requires_token_and_forwards_terminal_actions(tmp_path):
    async def scenario():
        control = ControlPlane()
        backend = FakeTerminalBackend()
        terminal = TerminalAgentService(control, backend=backend)
        session = create_session(control, tmp_path)
        socket_path = Path(f"/tmp/agentbridge-{uuid4().hex}.sock")
        server = LocalTerminalAgentServer(
            control=control,
            terminal=terminal,
            auth_token="secret-token",
        )
        await server.start(socket_path)
        try:
            assert socket_path.exists()
            assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600

            rejected = await LocalTerminalAgentClient(socket_path, "bad-token").request("health")
            assert rejected["ok"] is False
            assert rejected["error"]["error_code"] == "PERMISSION_DENIED"

            client = LocalTerminalAgentClient(socket_path, "secret-token")
            health = await client.request("health")
            assert health["ok"] is True
            assert health["data"]["status"] == "ok"

            started = await client.request(
                "start_session",
                {
                    "session_id": session.id,
                    "command": "fake-cli",
                    "trace_id": "daemon-start",
                },
            )
            assert started["ok"] is True

            lease_response = await client.request(
                "acquire_human_lease",
                {
                    "session_id": session.id,
                    "owner_id": "local-user",
                    "trace_id": "daemon-lease",
                },
            )
            assert lease_response["ok"] is True
            lease = lease_response["data"]["lease"]

            payload = {
                "session_id": session.id,
                "epoch": lease["epoch"],
                "owner_type": "human",
                "owner_id": "local-user",
                "type": "text",
                "data": "hello daemon\n",
                "request_id": "daemon-input-1",
                "trace_id": "daemon-input",
            }
            first_input = await client.request("submit_input", payload)
            duplicate_input = await client.request("submit_input", payload)
            assert first_input == duplicate_input

            snapshot = await client.request("snapshot", {"session_id": session.id})
            assert snapshot["ok"] is True
            assert snapshot["data"]["snapshot"] == "hello daemon\n"
        finally:
            await server.stop()
            assert not socket_path.exists()

    asyncio.run(scenario())
