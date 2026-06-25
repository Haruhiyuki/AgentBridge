from __future__ import annotations

import asyncio
import stat
from pathlib import Path
from uuid import uuid4

from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, Visibility
from agentbridge.terminal_agent import FakeTerminalBackend, TerminalAgentService, TerminalStatus
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

            status = await client.request("status", {"session_id": session.id})
            assert status["ok"] is True
            assert status["data"] == {
                "started": True,
                "running": True,
                "exit_code": None,
                "pid": None,
                "output_cursor": 13,
                "output_base_cursor": 0,
                "output_retained_chars": 13,
            }

            output = await client.request(
                "read_output",
                {"session_id": session.id, "after_cursor": 6},
            )
            assert output["ok"] is True
            assert output["data"] == {
                "cursor": 13,
                "data": "daemon\n",
                "snapshot": "hello daemon\n",
                "reset": False,
            }

            stream_frames = [
                frame
                async for frame in client.stream_output(
                    {
                        "session_id": session.id,
                        "after_cursor": 0,
                        "poll_interval_seconds": 0.01,
                        "max_frames": 1,
                    }
                )
            ]
            assert stream_frames == [
                {
                    "ok": True,
                    "type": "terminal.output",
                    "data": {
                        "cursor": 13,
                        "data": "hello daemon\n",
                        "snapshot": "hello daemon\n",
                        "reset": False,
                    },
                }
            ]
        finally:
            await server.stop()
            assert not socket_path.exists()

    asyncio.run(scenario())


def test_local_terminal_daemon_client_reconnects_after_socket_restart(tmp_path):
    async def scenario():
        control = ControlPlane()
        backend = FakeTerminalBackend()
        terminal = TerminalAgentService(control, backend=backend)
        session = create_session(control, tmp_path)
        socket_path = Path(f"/tmp/agentbridge-restart-{uuid4().hex}.sock")
        first_server = LocalTerminalAgentServer(
            control=control,
            terminal=terminal,
            auth_token="secret-token",
        )
        client = LocalTerminalAgentClient(
            socket_path,
            "secret-token",
            connect_timeout_seconds=1,
            connect_retry_interval_seconds=0.01,
        )
        second_server: LocalTerminalAgentServer | None = None
        await first_server.start(socket_path)
        try:
            started = await client.request(
                "start_session",
                {
                    "session_id": session.id,
                    "command": "fake-cli",
                    "trace_id": "daemon-restart-start",
                },
            )
            assert started["ok"] is True
            lease_response = await client.request(
                "acquire_human_lease",
                {
                    "session_id": session.id,
                    "owner_id": "local-user",
                    "trace_id": "daemon-restart-lease",
                },
            )
            lease = lease_response["data"]["lease"]
            input_response = await client.request(
                "submit_input",
                {
                    "session_id": session.id,
                    "epoch": lease["epoch"],
                    "owner_type": "human",
                    "owner_id": "local-user",
                    "type": "text",
                    "data": "before restart\n",
                    "request_id": "daemon-restart-input-1",
                    "trace_id": "daemon-restart-input-1",
                },
            )
            assert input_response["ok"] is True

            await first_server.stop()
            assert not socket_path.exists()

            reconnecting_snapshot = asyncio.create_task(
                client.request("snapshot", {"session_id": session.id})
            )
            await asyncio.sleep(0.05)
            second_server = LocalTerminalAgentServer(
                control=control,
                terminal=terminal,
                auth_token="secret-token",
            )
            await second_server.start(socket_path)

            snapshot = await reconnecting_snapshot
            assert snapshot["ok"] is True
            assert snapshot["data"]["snapshot"] == "before restart\n"

            after_restart = await client.request(
                "submit_input",
                {
                    "session_id": session.id,
                    "epoch": lease["epoch"],
                    "owner_type": "human",
                    "owner_id": "local-user",
                    "type": "text",
                    "data": "after restart\n",
                    "request_id": "daemon-restart-input-2",
                    "trace_id": "daemon-restart-input-2",
                },
            )
            assert after_restart["ok"] is True
            snapshot = await client.request("snapshot", {"session_id": session.id})
            assert snapshot["data"]["snapshot"] == "before restart\nafter restart\n"
        finally:
            await first_server.stop()
            if second_server is not None:
                await second_server.stop()

    asyncio.run(scenario())


def test_local_terminal_daemon_lifecycle_monitor_emits_terminal_exit(tmp_path):
    class ExitedBackend(FakeTerminalBackend):
        def status(self, *, session_id: str) -> TerminalStatus:
            self._require_started(session_id)
            return TerminalStatus(
                started=True,
                running=False,
                exit_code=9,
                pid=4321,
                output_cursor=len(self.snapshot(session_id=session_id)),
                output_retained_chars=len(self.snapshot(session_id=session_id)),
            )

    async def scenario():
        control = ControlPlane()
        terminal = TerminalAgentService(control, backend=ExitedBackend())
        session = create_session(control, tmp_path)
        socket_path = Path(f"/tmp/agentbridge-lifecycle-{uuid4().hex}.sock")
        server = LocalTerminalAgentServer(
            control=control,
            terminal=terminal,
            auth_token="secret-token",
            lifecycle_poll_interval_seconds=0.01,
        )
        await server.start(socket_path)
        try:
            client = LocalTerminalAgentClient(socket_path, "secret-token")
            started = await client.request(
                "start_session",
                {
                    "session_id": session.id,
                    "command": "fake-cli",
                    "trace_id": "daemon-lifecycle-start",
                },
            )
            assert started["ok"] is True

            deadline = asyncio.get_running_loop().time() + 2
            exited_events = []
            while asyncio.get_running_loop().time() < deadline:
                exited_events = [
                    event
                    for event in control.repository.list_events(session_id=session.id)
                    if event.type == "terminal.exited"
                ]
                if exited_events:
                    break
                await asyncio.sleep(0.02)

            assert len(exited_events) == 1
            assert exited_events[0].payload["exit_code"] == 9
            assert terminal.lifecycle_monitor_status()["run_count"] > 0
        finally:
            await server.stop()

    asyncio.run(scenario())
