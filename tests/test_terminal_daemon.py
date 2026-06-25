from __future__ import annotations

import asyncio
import stat
from pathlib import Path
from uuid import uuid4

import agentbridge.terminal_daemon as terminal_daemon
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, Visibility
from agentbridge.terminal_agent import FakeTerminalBackend, TerminalAgentService, TerminalStatus
from agentbridge.terminal_daemon import (
    DesktopTerminalLauncher,
    LocalTerminalAgentClient,
    LocalTerminalAgentServer,
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
            assert started["data"]["desktop"] == {"launched": False, "pid": None, "error": None}

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


def test_desktop_terminal_launcher_passes_token_in_environment(monkeypatch, tmp_path):
    calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 24680

    def fake_popen(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return FakeProcess()

    socket_path = tmp_path / "terminal-agent.sock"
    monkeypatch.setattr(terminal_daemon.subprocess, "Popen", fake_popen)
    launcher = DesktopTerminalLauncher(
        enabled=True,
        command_template="{console_command} {session_id} --socket {socket_path} --raw",
        socket_path=socket_path,
        auth_token="secret-token",
    )

    result = launcher.launch(session_id="ses_1")

    assert result.to_payload() == {"launched": True, "pid": 24680, "error": None}
    assert calls[0]["argv"] == [
        "agentbridge-console",
        "ses_1",
        "--socket",
        str(socket_path),
        "--raw",
    ]
    assert "secret-token" not in " ".join(calls[0]["argv"])
    env = calls[0]["env"]
    assert env["AGENTBRIDGE_LOCAL_TOKEN"] == "secret-token"
    assert env["AGENTBRIDGE_TERMINAL_SOCKET"] == str(socket_path)


def test_desktop_terminal_launcher_uses_named_preset(monkeypatch, tmp_path):
    calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 97531

    def fake_popen(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return FakeProcess()

    def fake_which(executable: str):
        if executable == "gnome-terminal":
            return "/usr/bin/gnome-terminal"
        return None

    socket_path = tmp_path / "terminal-agent.sock"
    monkeypatch.setattr(terminal_daemon.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(terminal_daemon.shutil, "which", fake_which)
    launcher = DesktopTerminalLauncher(
        enabled=True,
        open_preset="gnome-terminal",
        socket_path=socket_path,
        auth_token="secret-token",
    )

    result = launcher.launch(session_id="ses_1")

    assert result.to_payload() == {"launched": True, "pid": 97531, "error": None}
    assert calls[0]["argv"] == [
        "/usr/bin/gnome-terminal",
        "--",
        "agentbridge-console",
        "ses_1",
        "--socket",
        str(socket_path),
        "--raw",
    ]
    assert "secret-token" not in " ".join(calls[0]["argv"])
    env = calls[0]["env"]
    assert env["AGENTBRIDGE_LOCAL_TOKEN"] == "secret-token"
    assert env["AGENTBRIDGE_TERMINAL_SOCKET"] == str(socket_path)


def test_desktop_terminal_launcher_auto_selects_available_preset(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    class FakeProcess:
        pid = 86420

    def fake_popen(argv, **kwargs):
        calls.append(argv)
        return FakeProcess()

    def fake_which(executable: str):
        if executable == "xterm":
            return "/usr/bin/xterm"
        return None

    monkeypatch.setattr(terminal_daemon.platform, "system", lambda: "Linux")
    monkeypatch.setattr(terminal_daemon.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(terminal_daemon.shutil, "which", fake_which)
    socket_path = tmp_path / "terminal-agent.sock"
    launcher = DesktopTerminalLauncher(
        enabled=True,
        open_preset="auto",
        socket_path=socket_path,
        auth_token="secret-token",
    )

    result = launcher.launch(session_id="ses_auto")

    assert result.to_payload() == {"launched": True, "pid": 86420, "error": None}
    assert calls == [
        [
            "/usr/bin/xterm",
            "-e",
            "agentbridge-console",
            "ses_auto",
            "--socket",
            str(socket_path),
            "--raw",
        ]
    ]


def test_desktop_terminal_launcher_reports_missing_preset_executable(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_popen(argv, **kwargs):
        calls.append(argv)
        raise AssertionError("Popen should not be called")

    monkeypatch.setattr(terminal_daemon.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(terminal_daemon.shutil, "which", lambda executable: None)
    launcher = DesktopTerminalLauncher(
        enabled=True,
        open_preset="xterm",
        socket_path=tmp_path / "terminal-agent.sock",
        auth_token="secret-token",
    )

    result = launcher.launch(session_id="ses_1")

    assert result.launched is False
    assert result.pid is None
    assert result.error == "desktop terminal open preset 'xterm' requires 'xterm' in PATH"
    assert calls == []


def test_desktop_terminal_launcher_macos_preset_keeps_token_out_of_argv(monkeypatch, tmp_path):
    calls: list[dict[str, object]] = []

    class FakeProcess:
        pid = 12345

    def fake_popen(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return FakeProcess()

    def fake_which(executable: str):
        if executable == "osascript":
            return "/usr/bin/osascript"
        return None

    socket_path = tmp_path / "terminal-agent.sock"
    launcher_script_dir = tmp_path / "launchers"
    monkeypatch.setattr(terminal_daemon.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(terminal_daemon.shutil, "which", fake_which)
    launcher = DesktopTerminalLauncher(
        enabled=True,
        open_preset="macos-terminal",
        socket_path=socket_path,
        auth_token="secret-token",
        launcher_script_dir=launcher_script_dir,
    )

    result = launcher.launch(session_id="ses_1")

    assert result.to_payload() == {"launched": True, "pid": 12345, "error": None}
    argv = calls[0]["argv"]
    assert argv[:3] == ["/usr/bin/osascript", "-e", terminal_daemon.MACOS_TERMINAL_APPLESCRIPT]
    assert "secret-token" not in " ".join(argv)
    script_path = Path(argv[3])
    assert script_path.parent == launcher_script_dir
    assert script_path.stat().st_mode & 0o777 == 0o700
    script = script_path.read_text(encoding="utf-8")
    assert "AGENTBRIDGE_LOCAL_TOKEN=secret-token" in script
    assert f"AGENTBRIDGE_TERMINAL_SOCKET={socket_path}" in script
    assert "agentbridge-console ses_1 --socket" in script
    assert 'rm -f "$0"' in script


def test_terminal_daemon_config_reads_desktop_open_preset(monkeypatch, tmp_path):
    socket_path = tmp_path / "terminal-agent.sock"
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_SOCKET", str(socket_path))
    monkeypatch.setenv("AGENTBRIDGE_LOCAL_TOKEN", "secret-token")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_AUTO_OPEN", "true")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_OPEN_PRESET", "auto")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_OPEN_COMMAND", "custom {session_id}")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_LIFECYCLE_POLL_INTERVAL_SECONDS", "2.5")

    config = terminal_daemon.config_from_env()

    assert config.socket_path == socket_path
    assert config.auth_token == "secret-token"
    assert config.lifecycle_poll_interval_seconds == 2.5
    assert config.desktop_auto_open_enabled is True
    assert config.desktop_open_preset == "auto"
    assert config.desktop_open_command == "custom {session_id}"


def test_local_terminal_daemon_auto_opens_desktop_terminal(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    class FakeProcess:
        pid = 13579

    def fake_popen(argv, **kwargs):
        calls.append(argv)
        return FakeProcess()

    async def scenario():
        control = ControlPlane()
        terminal = TerminalAgentService(control, backend=FakeTerminalBackend())
        session = create_session(control, tmp_path)
        socket_path = Path(f"/tmp/agentbridge-auto-open-{uuid4().hex}.sock")
        launcher = DesktopTerminalLauncher(
            enabled=True,
            command_template="{console_command} {session_id}",
            socket_path=socket_path,
            auth_token="secret-token",
        )
        server = LocalTerminalAgentServer(
            control=control,
            terminal=terminal,
            auth_token="secret-token",
            desktop_launcher=launcher,
        )
        await server.start(socket_path)
        try:
            client = LocalTerminalAgentClient(socket_path, "secret-token")
            started = await client.request(
                "start_session",
                {
                    "session_id": session.id,
                    "command": "fake-cli",
                    "trace_id": "daemon-auto-open-start",
                },
            )

            assert started["ok"] is True
            assert started["data"]["desktop"] == {
                "launched": True,
                "pid": 13579,
                "error": None,
            }
            assert calls == [["agentbridge-console", session.id]]
        finally:
            await server.stop()

    monkeypatch.setattr(terminal_daemon.subprocess, "Popen", fake_popen)
    asyncio.run(scenario())


def test_local_terminal_daemon_restarts_from_last_started_command(tmp_path):
    async def scenario():
        control = ControlPlane()
        first_terminal = TerminalAgentService(control, backend=FakeTerminalBackend())
        session = create_session(control, tmp_path)
        first_terminal.start_session(
            session_id=session.id,
            command="fake-cli --resume",
            trace_id="daemon-restart-history-start",
        )

        recovered_backend = FakeTerminalBackend()
        recovered_terminal = TerminalAgentService(control, backend=recovered_backend)
        socket_path = Path(f"/tmp/agentbridge-restart-history-{uuid4().hex}.sock")
        server = LocalTerminalAgentServer(
            control=control,
            terminal=recovered_terminal,
            auth_token="secret-token",
        )
        await server.start(socket_path)
        try:
            client = LocalTerminalAgentClient(socket_path, "secret-token")
            restarted = await client.request(
                "restart_session",
                {
                    "session_id": session.id,
                    "trace_id": "daemon-restart-history",
                },
            )

            assert restarted == {
                "ok": True,
                "data": {
                    "status": "restarted",
                    "restarted": True,
                    "command": "fake-cli --resume",
                    "previous_generation": 1,
                    "generation": 2,
                    "desktop": {"launched": False, "pid": None, "error": None},
                },
            }
            assert recovered_backend.started[session.id] == (str(tmp_path), "fake-cli --resume")
        finally:
            await server.stop()

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
