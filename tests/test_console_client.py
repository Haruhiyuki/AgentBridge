from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest

from agentbridge.console_client import LocalConsoleClient, RawTerminalMode, run_raw_mode
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


def test_raw_terminal_mode_restores_terminal_state_after_error():
    class FakeTermios:
        TCSADRAIN = 1

        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def tcgetattr(self, fd: int) -> list[str]:
            self.calls.append(("get", fd))
            return ["saved"]

        def tcsetattr(self, fd: int, when: int, attrs: list[str]) -> None:
            self.calls.append(("set", (fd, when, attrs)))

    class FakeTty:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        def setraw(self, fd: int) -> None:
            self.calls.append(("raw", fd))

    fake_termios = FakeTermios()
    fake_tty = FakeTty()

    with pytest.raises(RuntimeError):
        with RawTerminalMode(7, termios_module=fake_termios, tty_module=fake_tty):
            raise RuntimeError("boom")

    assert fake_termios.calls == [("get", 7), ("set", (7, 1, ["saved"]))]
    assert fake_tty.calls == [("raw", 7)]


def test_raw_console_mode_forwards_bytes_signals_resize_and_restores():
    class FakeClient:
        def __init__(self) -> None:
            self.events: list[tuple[str, object]] = []

        async def send_text(self, text: str, *, request_id: str | None = None) -> str:
            self.events.append(("text", text))
            return request_id or "text-id"

        async def send_signal(
            self, name: str, *, request_id: str | None = None
        ) -> str:
            self.events.append(("signal", name))
            return request_id or "signal-id"

        async def resize(
            self, *, cols: int, rows: int, request_id: str | None = None
        ) -> str:
            self.events.append(("resize", (cols, rows)))
            return request_id or "resize-id"

    class PipeInput:
        def __init__(self, fd: int) -> None:
            self.fd = fd

        def fileno(self) -> int:
            return self.fd

        def isatty(self) -> bool:
            return True

    class RecordingRawMode:
        def __init__(self, fd: int, events: list[tuple[str, int]]) -> None:
            self.fd = fd
            self.events = events

        def __enter__(self):
            self.events.append(("enter", self.fd))
            return self

        def __exit__(self, *_: object) -> None:
            self.events.append(("exit", self.fd))

    async def scenario():
        read_fd, write_fd = os.pipe()
        raw_events: list[tuple[str, int]] = []
        client = FakeClient()
        os.write(write_fd, b"abc\x03def\x04\x1dignored")
        os.close(write_fd)
        try:
            await run_raw_mode(
                client,  # type: ignore[arg-type]
                input_file=PipeInput(read_fd),
                size_provider=lambda: os.terminal_size((120, 40)),
                raw_mode_factory=lambda fd: RecordingRawMode(fd, raw_events),
            )
        finally:
            os.close(read_fd)
        return client.events, raw_events

    events, raw_events = asyncio.run(scenario())

    assert events == [
        ("resize", (120, 40)),
        ("text", "abc"),
        ("signal", "interrupt"),
        ("text", "def"),
        ("signal", "eof"),
    ]
    assert raw_events[0][0] == "enter"
    assert raw_events[1][0] == "exit"
