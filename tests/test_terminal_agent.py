from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agentbridge.api import (
    create_app,
    create_terminal_backend_from_env,
    start_terminal_backend_supervision,
    stop_terminal_backend_supervision,
)
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, AgentBridgeError, ErrorCode, LeaseOwnerType, Visibility
from agentbridge.pty_host import (
    PtyHostServer,
    PtyHostSupervisor,
    PtyHostSupervisorConfig,
    PtyHostTerminalBackend,
)
from agentbridge.pty_host import (
    config_from_env as pty_host_config_from_env,
)
from agentbridge.terminal_agent import (
    FakeTerminalBackend,
    PtyTerminalBackend,
    TerminalAgentService,
    TerminalInputKind,
    TerminalLifecyclePolicy,
    TerminalOutputChunk,
    TerminalStatus,
    TmuxTerminalBackend,
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
    first_output = terminal.read_output(session_id=session.id, after_cursor=0)
    second_output = terminal.read_output(session_id=session.id, after_cursor=6)
    reset_output = terminal.read_output(session_id=session.id, after_cursor=999)
    assert first_output == TerminalOutputChunk(
        cursor=12,
        data="hello\nhuman\n",
        snapshot="hello\nhuman\n",
    )
    assert second_output == TerminalOutputChunk(
        cursor=12,
        data="human\n",
        snapshot="hello\nhuman\n",
    )
    assert reset_output == TerminalOutputChunk(
        cursor=12,
        data="hello\nhuman\n",
        snapshot="hello\nhuman\n",
        reset=True,
    )
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


def test_terminal_lifecycle_monitor_emits_exit_event_once(tmp_path):
    class ExitedBackend(FakeTerminalBackend):
        def status(self, *, session_id: str) -> TerminalStatus:
            self._require_started(session_id)
            return TerminalStatus(
                started=True,
                running=False,
                exit_code=7,
                pid=1234,
                output_cursor=42,
            )

    control = ControlPlane()
    backend = ExitedBackend()
    terminal = TerminalAgentService(control, backend=backend)
    _, session = create_session(control, tmp_path)

    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="terminal-start")
    first_observed = terminal.run_lifecycle_monitor_once(trace_id="terminal-monitor-1")
    second_observed = terminal.run_lifecycle_monitor_once(trace_id="terminal-monitor-2")

    assert first_observed[session.id] == second_observed[session.id]
    exited_events = [
        event
        for event in control.repository.list_events(session_id=session.id)
        if event.type == "terminal.exited"
    ]
    assert len(exited_events) == 1
    assert exited_events[0].payload == {
        "generation": 1,
        "exit_code": 7,
        "pid": 1234,
        "output_cursor": 42,
    }


def test_terminal_lifecycle_monitor_recovers_started_sessions_from_events(tmp_path):
    class RecoveredExitedBackend(FakeTerminalBackend):
        def status(self, *, session_id: str) -> TerminalStatus:
            return TerminalStatus(
                started=True,
                running=False,
                exit_code=8,
                pid=5678,
                output_cursor=99,
            )

    control = ControlPlane()
    first_terminal = TerminalAgentService(control, backend=FakeTerminalBackend())
    _, session = create_session(control, tmp_path)
    first_terminal.start_session(
        session_id=session.id,
        command="fake-cli",
        trace_id="terminal-start-before-restart",
    )

    restarted_terminal = TerminalAgentService(control, backend=RecoveredExitedBackend())
    assert restarted_terminal.lifecycle_monitor_status()["tracked_sessions"] == 1

    observed = restarted_terminal.run_lifecycle_monitor_once(trace_id="terminal-monitor-restart")

    assert observed[session.id].exit_code == 8
    exited_events = [
        event
        for event in control.repository.list_events(session_id=session.id)
        if event.type == "terminal.exited"
    ]
    assert len(exited_events) == 1
    assert exited_events[0].payload["generation"] == 1
    assert exited_events[0].payload["exit_code"] == 8


def test_terminal_lifecycle_recovery_does_not_duplicate_existing_exit(tmp_path):
    class RecoveredExitedBackend(FakeTerminalBackend):
        def status(self, *, session_id: str) -> TerminalStatus:
            return TerminalStatus(
                started=True,
                running=False,
                exit_code=8,
                pid=5678,
                output_cursor=99,
            )

    control = ControlPlane()
    first_terminal = TerminalAgentService(control, backend=RecoveredExitedBackend())
    _, session = create_session(control, tmp_path)
    first_terminal.start_session(
        session_id=session.id,
        command="fake-cli",
        trace_id="terminal-start-before-restart",
    )
    first_terminal.run_lifecycle_monitor_once(trace_id="terminal-monitor-before-restart")

    restarted_terminal = TerminalAgentService(control, backend=RecoveredExitedBackend())
    restarted_terminal.run_lifecycle_monitor_once(trace_id="terminal-monitor-after-restart")

    exited_events = [
        event
        for event in control.repository.list_events(session_id=session.id)
        if event.type == "terminal.exited"
    ]
    assert len(exited_events) == 1


def test_terminal_lifecycle_monitor_reports_lost_recovered_session_once(tmp_path):
    control = ControlPlane()
    first_terminal = TerminalAgentService(control, backend=FakeTerminalBackend())
    _, session = create_session(control, tmp_path)
    first_terminal.start_session(
        session_id=session.id,
        command="fake-cli",
        trace_id="terminal-start-before-lost-recovery",
    )

    restarted_terminal = TerminalAgentService(control, backend=FakeTerminalBackend())
    first_observed = restarted_terminal.run_lifecycle_monitor_once(
        trace_id="terminal-monitor-lost-1"
    )
    second_observed = restarted_terminal.run_lifecycle_monitor_once(
        trace_id="terminal-monitor-lost-2"
    )

    assert first_observed[session.id] == TerminalStatus(started=False, running=False)
    assert second_observed[session.id] == TerminalStatus(started=False, running=False)
    lost_events = [
        event
        for event in control.repository.list_events(session_id=session.id)
        if event.type == "terminal.lost"
    ]
    assert len(lost_events) == 1
    assert lost_events[0].payload == {
        "generation": 1,
        "reason": "backend_state_missing",
        "backend": "FakeTerminalBackend",
    }
    assert restarted_terminal.lifecycle_monitor_status()["reported_lost_count"] == 1

    recovered_terminal = TerminalAgentService(control, backend=FakeTerminalBackend())
    recovered_terminal.run_lifecycle_monitor_once(trace_id="terminal-monitor-lost-3")

    lost_events = [
        event
        for event in control.repository.list_events(session_id=session.id)
        if event.type == "terminal.lost"
    ]
    assert len(lost_events) == 1


def test_terminal_lifecycle_monitor_auto_restarts_lost_session(tmp_path):
    control = ControlPlane()
    first_terminal = TerminalAgentService(control, backend=FakeTerminalBackend())
    _, session = create_session(control, tmp_path)
    first_terminal.start_session(
        session_id=session.id,
        command="fake-cli --resume",
        trace_id="terminal-start-before-auto-restart",
    )
    recovered_backend = FakeTerminalBackend()
    recovered_terminal = TerminalAgentService(
        control,
        backend=recovered_backend,
        lifecycle_policy=TerminalLifecyclePolicy(
            auto_restart_on_lost=True,
            auto_restart_max_attempts=1,
            auto_restart_command_allowlist=("fake-cli *",),
        ),
    )

    observed = recovered_terminal.run_lifecycle_monitor_once(
        trace_id="terminal-monitor-auto-restart"
    )

    assert observed[session.id] == TerminalStatus(started=False, running=False)
    assert recovered_backend.started[session.id] == (str(tmp_path), "fake-cli --resume")
    assert [event.type for event in control.repository.list_events(session_id=session.id)] == [
        "session.created",
        "terminal.started",
        "terminal.lost",
        "terminal.started",
    ]
    started_events = [
        event
        for event in control.repository.list_events(session_id=session.id)
        if event.type == "terminal.started"
    ]
    assert started_events[-1].payload == {
        "workspace_id": session.workspace_id,
        "command": "fake-cli --resume",
        "generation": 2,
        "restart_of_generation": 1,
        "restart_reason": "auto_lost_restart",
    }
    assert recovered_terminal.lifecycle_monitor_status()["auto_restart_attempt_count"] == 1


def test_terminal_lifecycle_auto_restart_requires_allowlisted_command(tmp_path):
    control = ControlPlane()
    first_terminal = TerminalAgentService(control, backend=FakeTerminalBackend())
    _, session = create_session(control, tmp_path)
    first_terminal.start_session(
        session_id=session.id,
        command="dangerous-cli --apply",
        trace_id="terminal-start-before-auto-restart-block",
    )
    recovered_backend = FakeTerminalBackend()
    recovered_terminal = TerminalAgentService(
        control,
        backend=recovered_backend,
        lifecycle_policy=TerminalLifecyclePolicy(
            auto_restart_on_lost=True,
            auto_restart_max_attempts=1,
            auto_restart_command_allowlist=("codex*", "claude*"),
        ),
    )

    observed = recovered_terminal.run_lifecycle_monitor_once(
        trace_id="terminal-monitor-auto-restart-block"
    )

    assert observed[session.id] == TerminalStatus(started=False, running=False)
    assert recovered_backend.started == {}
    events = control.repository.list_events(session_id=session.id)
    assert [event.type for event in events] == [
        "session.created",
        "terminal.started",
        "terminal.lost",
        "terminal.auto_restart.skipped",
    ]
    skipped_event = events[-1]
    assert skipped_event.payload == {
        "generation": 1,
        "reason": "command_not_allowlisted",
        "command": "dangerous-cli --apply",
        "allowed_patterns": ["codex*", "claude*"],
    }
    status = recovered_terminal.lifecycle_monitor_status()
    assert status["auto_restart_attempt_count"] == 0
    assert status["auto_restart_blocked_count"] == 1
    assert status["auto_restart_last_block_reason"] == (
        f"{session.id}: command_not_allowlisted"
    )


def test_terminal_lifecycle_auto_restart_attempts_are_bounded(tmp_path):
    class AlwaysLostBackend(FakeTerminalBackend):
        def status(self, *, session_id: str) -> TerminalStatus:
            return TerminalStatus(started=False, running=False)

    control = ControlPlane()
    first_terminal = TerminalAgentService(control, backend=FakeTerminalBackend())
    _, session = create_session(control, tmp_path)
    first_terminal.start_session(
        session_id=session.id,
        command="fake-cli --resume",
        trace_id="terminal-start-before-auto-restart-limit",
    )
    recovered_backend = AlwaysLostBackend()
    recovered_terminal = TerminalAgentService(
        control,
        backend=recovered_backend,
        lifecycle_policy=TerminalLifecyclePolicy(
            auto_restart_on_lost=True,
            auto_restart_max_attempts=1,
            auto_restart_command_allowlist=("fake-cli *",),
        ),
    )

    recovered_terminal.run_lifecycle_monitor_once(trace_id="terminal-monitor-auto-restart-1")
    recovered_terminal.run_lifecycle_monitor_once(trace_id="terminal-monitor-auto-restart-2")

    started_events = [
        event
        for event in control.repository.list_events(session_id=session.id)
        if event.type == "terminal.started"
    ]
    lost_events = [
        event
        for event in control.repository.list_events(session_id=session.id)
        if event.type == "terminal.lost"
    ]
    assert len(started_events) == 2
    assert len(lost_events) == 2
    assert recovered_terminal.lifecycle_monitor_status()["auto_restart_attempt_count"] == 1


def test_terminal_restart_uses_last_started_command_after_backend_state_loss(tmp_path):
    control = ControlPlane()
    first_terminal = TerminalAgentService(control, backend=FakeTerminalBackend())
    _, session = create_session(control, tmp_path)
    first_generation = first_terminal.start_session(
        session_id=session.id,
        command="fake-cli --resume",
        trace_id="terminal-start-before-restart",
    )
    assert first_generation == 1

    recovered_backend = FakeTerminalBackend()
    recovered_terminal = TerminalAgentService(control, backend=recovered_backend)

    result = recovered_terminal.restart_session(
        session_id=session.id,
        trace_id="terminal-restart-after-loss",
    )

    assert result.to_payload() == {
        "status": "restarted",
        "restarted": True,
        "command": "fake-cli --resume",
        "previous_generation": 1,
        "generation": 2,
    }
    assert recovered_backend.started[session.id] == (str(tmp_path), "fake-cli --resume")
    assert [event.type for event in control.repository.list_events(session_id=session.id)] == [
        "session.created",
        "terminal.started",
        "terminal.lost",
        "terminal.started",
    ]
    started_events = [
        event
        for event in control.repository.list_events(session_id=session.id)
        if event.type == "terminal.started"
    ]
    assert started_events[-1].payload["generation"] == 2


def test_terminal_restart_does_not_replace_running_backend(tmp_path):
    control = ControlPlane()
    backend = FakeTerminalBackend()
    terminal = TerminalAgentService(control, backend=backend)
    _, session = create_session(control, tmp_path)
    terminal.start_session(
        session_id=session.id,
        command="fake-cli",
        trace_id="terminal-start-before-running-restart",
    )

    result = terminal.restart_session(
        session_id=session.id,
        trace_id="terminal-restart-running",
    )

    assert result.to_payload() == {
        "status": "already_running",
        "restarted": False,
        "command": "fake-cli",
        "previous_generation": 1,
        "generation": 1,
    }
    assert [
        event.type for event in control.repository.list_events(session_id=session.id)
    ] == ["session.created", "terminal.started"]


def test_terminal_restart_can_use_explicit_command_without_start_history(tmp_path):
    control = ControlPlane()
    backend = FakeTerminalBackend()
    terminal = TerminalAgentService(control, backend=backend)
    _, session = create_session(control, tmp_path)

    result = terminal.restart_session(
        session_id=session.id,
        command="fake-cli --fresh",
        trace_id="terminal-restart-explicit",
    )

    assert result.to_payload() == {
        "status": "restarted",
        "restarted": True,
        "command": "fake-cli --fresh",
        "previous_generation": 0,
        "generation": 1,
    }
    assert backend.started[session.id] == (str(tmp_path), "fake-cli --fresh")
    assert [
        event.type for event in control.repository.list_events(session_id=session.id)
    ] == ["session.created", "terminal.started"]


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

    status_response = client.get(f"/api/v1/sessions/{session_id}/terminal/status")
    assert status_response.status_code == 200
    assert status_response.json() == {
        "started": True,
        "running": True,
        "exit_code": None,
        "pid": None,
        "output_cursor": 10,
        "output_base_cursor": 0,
        "output_retained_chars": 10,
    }


def test_tmux_backend_reuses_existing_session_after_agent_restart(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    existing_sessions: set[str] = set()

    def fake_which(executable: str) -> str:
        assert executable == "tmux"
        return "/usr/bin/tmux"

    def fake_run(
        args: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert args[0] == "tmux"
        assert capture_output is True
        assert text is True
        command = args[1]
        if command == "has-session":
            name = args[3]
            return subprocess.CompletedProcess(
                args,
                0 if name in existing_sessions else 1,
                stdout="",
                stderr="",
            )
        if command == "new-session":
            assert check is True
            name = args[args.index("-s") + 1]
            existing_sessions.add(name)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if command == "capture-pane":
            assert check is True
            assert args[args.index("-t") + 1] in existing_sessions
            return subprocess.CompletedProcess(args, 0, stdout="still alive\n", stderr="")
        raise AssertionError(f"unexpected tmux command: {command}")

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(subprocess, "run", fake_run)

    first_backend = TmuxTerminalBackend("tmux")
    first_backend.start(session_id="sess/one", cwd=str(tmp_path), command="codex")
    restarted_backend = TmuxTerminalBackend("tmux")
    restarted_backend.start(session_id="sess/one", cwd=str(tmp_path), command="ignored")

    new_session_calls = [call for call in calls if call[1] == "new-session"]
    assert len(new_session_calls) == 1
    assert restarted_backend.snapshot(session_id="sess/one") == "still alive\n"
    assert restarted_backend.read_output(session_id="sess/one", after_cursor=6) == (
        TerminalOutputChunk(cursor=12, data="alive\n", snapshot="still alive\n")
    )


def test_real_tmux_backend_smoke_streams_output_and_reuses_session(tmp_path):
    if os.environ.get("AGENTBRIDGE_RUN_TMUX_TESTS", "").lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        pytest.skip("set AGENTBRIDGE_RUN_TMUX_TESTS=true to run real tmux smoke tests")
    tmux = shutil.which("tmux")
    if tmux is None:
        pytest.skip("tmux executable is required for real tmux smoke test")
    cat = shutil.which("cat")
    if cat is None:
        pytest.skip("cat executable is required for real tmux smoke test")

    backend = TmuxTerminalBackend(tmux)
    session_id = f"real-tmux-{uuid4().hex}"
    tmux_name = TmuxTerminalBackend._tmux_name(session_id)
    marker = f"agentbridge-real-tmux-{uuid4().hex}"

    def kill_session() -> None:
        subprocess.run(
            [tmux, "kill-session", "-t", tmux_name],
            check=False,
            capture_output=True,
            text=True,
        )

    kill_session()
    try:
        backend.start(session_id=session_id, cwd=str(tmp_path), command=cat)
        assert backend.status(session_id=session_id).running is True
        backend.write(
            session_id=session_id,
            data=f"{marker}\n",
            kind=TerminalInputKind.TEXT,
        )

        deadline = time.monotonic() + 3
        chunk = TerminalOutputChunk(cursor=0, data="", snapshot="")
        while time.monotonic() < deadline:
            chunk = backend.read_output(session_id=session_id, after_cursor=0)
            if marker in chunk.snapshot:
                break
            time.sleep(0.05)

        assert marker in chunk.snapshot
        restarted_backend = TmuxTerminalBackend(tmux)
        restarted_backend.start(
            session_id=session_id,
            cwd=str(tmp_path),
            command="printf ignored",
        )
        assert marker in restarted_backend.snapshot(session_id=session_id)
        assert restarted_backend.read_output(
            session_id=session_id,
            after_cursor=chunk.cursor + 1,
        ).reset is True
    finally:
        kill_session()


def test_terminal_backend_env_selects_pty(monkeypatch):
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_BACKEND", "pty")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_OUTPUT_LIMIT_CHARS", "2048")
    monkeypatch.setenv(
        "AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH",
        "/tmp/agentbridge-test-pty-host-state.json",
    )
    try:
        backend = create_terminal_backend_from_env()
    except AgentBridgeError as exc:
        if exc.code == ErrorCode.PLATFORM_CAPABILITY_MISSING:
            pytest.skip("PTY backend is not available on this platform")
        raise

    assert isinstance(backend, PtyTerminalBackend)
    assert backend.max_output_chars == 2048
    assert backend.host_state_store is not None
    assert str(backend.host_state_store.path) == "/tmp/agentbridge-test-pty-host-state.json"


def test_terminal_backend_env_selects_pty_host(monkeypatch, tmp_path):
    socket_path = Path(f"/tmp/agentbridge-pty-host-{uuid4().hex}.sock")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_BACKEND", "pty_host")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET", str(socket_path))
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN", "secret-token")

    backend = create_terminal_backend_from_env()

    assert isinstance(backend, PtyHostTerminalBackend)
    assert backend.socket_path == socket_path
    assert backend.auth_token == "secret-token"
    assert backend.auth_token_file is None


def test_terminal_backend_env_selects_pty_host_token_file(monkeypatch, tmp_path):
    socket_path = Path(f"/tmp/agentbridge-pty-host-{uuid4().hex}.sock")
    token_file = tmp_path / "pty-host.token"
    token_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_BACKEND", "pty_host")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET", str(socket_path))
    monkeypatch.delenv("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN", raising=False)
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_AUTO_START", "true")

    backend = create_terminal_backend_from_env()

    assert isinstance(backend, PtyHostTerminalBackend)
    assert backend.auth_token == ""
    assert backend.auth_token_file == token_file
    assert backend.supervisor is not None
    assert backend.supervisor.config.auth_token_file == token_file


def test_pty_host_config_reads_token_file(monkeypatch, tmp_path):
    socket_path = tmp_path / "pty-host.sock"
    token_file = tmp_path / "pty-host.token"
    token_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET", str(socket_path))
    monkeypatch.delenv("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN", raising=False)
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE", str(token_file))

    config = pty_host_config_from_env()

    assert config.socket_path == socket_path
    assert config.auth_token == ""
    assert config.auth_token_file == token_file


def test_pty_host_token_file_hot_reloads(tmp_path):
    socket_path = Path(f"/tmp/agentbridge-pty-host-{uuid4().hex}.sock")
    token_file = tmp_path / "pty-host.token"
    token_file.write_text("first-token\n", encoding="utf-8")
    server = PtyHostServer(
        socket_path=socket_path,
        auth_token_file=token_file,
        backend=PtyTerminalBackend(),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        reloading_client = PtyHostTerminalBackend(
            socket_path=socket_path,
            auth_token_file=token_file,
        )
        assert reloading_client.health()["status"] == "ok"

        token_file.write_text("second-token\n", encoding="utf-8")

        stale_client = PtyHostTerminalBackend(
            socket_path=socket_path,
            auth_token="first-token",
        )
        with pytest.raises(AgentBridgeError) as exc_info:
            stale_client.health()
        assert exc_info.value.code == ErrorCode.PERMISSION_DENIED
        assert reloading_client.health()["status"] == "ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_pty_host_token_file_missing_fails_closed(tmp_path):
    socket_path = Path(f"/tmp/agentbridge-pty-host-{uuid4().hex}.sock")
    missing_token_file = tmp_path / "missing.token"
    server = PtyHostServer(
        socket_path=socket_path,
        auth_token_file=missing_token_file,
        backend=PtyTerminalBackend(),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = PtyHostTerminalBackend(socket_path=socket_path)
        with pytest.raises(AgentBridgeError) as exc_info:
            client.health()
        assert exc_info.value.code == ErrorCode.PERMISSION_DENIED
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_pty_host_supervisor_preserves_active_socket_on_auth_failure(tmp_path):
    socket_path = Path(f"/tmp/agentbridge-pty-host-{uuid4().hex}.sock")
    server = PtyHostServer(
        socket_path=socket_path,
        auth_token="server-token",
        backend=PtyTerminalBackend(),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        supervisor = PtyHostSupervisor(
            PtyHostSupervisorConfig(
                socket_path=socket_path,
                auth_token="wrong-token",
                startup_timeout_seconds=0.2,
                poll_interval_seconds=0.01,
            )
        )

        with pytest.raises(AgentBridgeError) as exc_info:
            supervisor.ensure_running()

        assert exc_info.value.code == ErrorCode.PERMISSION_DENIED
        assert socket_path.exists()
        assert supervisor.start_count == 0
        assert supervisor.restart_count == 0
        assert supervisor.last_error == "PTY Host 已运行但 token 无效。"
        assert (
            PtyHostTerminalBackend(
                socket_path=socket_path,
                auth_token="server-token",
            ).health()["status"]
            == "ok"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_pty_host_supervisor_preserves_socket_on_health_timeout(tmp_path):
    socket_path = Path(f"/tmp/agentbridge-pty-host-{uuid4().hex}.sock")
    stop_event = Event()
    connections: list[socket.socket] = []
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen()
    listener.settimeout(0.05)

    def accept_connections() -> None:
        while not stop_event.is_set():
            try:
                connection, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            connections.append(connection)

    thread = Thread(target=accept_connections, daemon=True)
    thread.start()
    try:
        supervisor = PtyHostSupervisor(
            PtyHostSupervisorConfig(
                socket_path=socket_path,
                startup_timeout_seconds=0.1,
                poll_interval_seconds=0.01,
            )
        )

        with pytest.raises(AgentBridgeError) as exc_info:
            supervisor.ensure_running()

        assert exc_info.value.code == ErrorCode.RESOURCE_CONFLICT
        assert socket_path.exists()
        assert supervisor.start_count == 0
        assert supervisor.restart_count == 0
        assert supervisor.last_error == "PTY Host socket 存在但健康检查未证明它已过期。"
    finally:
        stop_event.set()
        listener.close()
        for connection in connections:
            connection.close()
        thread.join(timeout=2)
        if socket_path.exists():
            socket_path.unlink()


def test_pty_host_backend_auto_starts_host_and_cleans_stale_socket(monkeypatch, tmp_path):
    socket_path = Path(f"/tmp/agentbridge-pty-host-{uuid4().hex}.sock")
    stale_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale_socket.bind(str(socket_path))
    stale_socket.close()
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_BACKEND", "pty_host")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET", str(socket_path))
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN", "secret-token")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_AUTO_START", "true")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH", str(tmp_path / "host.json"))
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_STARTUP_TIMEOUT_SECONDS", "5")

    backend = create_terminal_backend_from_env()

    assert isinstance(backend, PtyHostTerminalBackend)
    assert backend.supervisor is not None
    backend.start_supervision()
    assert backend.supervision_status()["watchdog_enabled"] is False
    assert backend.supervision_status()["watchdog_running"] is False
    try:
        assert backend.health()["status"] == "ok"
        assert backend.supervisor.process is not None
        assert backend.supervisor.process.poll() is None
        assert socket_path.exists()
    finally:
        if backend.supervisor.process is not None:
            backend.supervisor.process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                backend.supervisor.process.wait(timeout=5)
            if backend.supervisor.process.poll() is None:
                backend.supervisor.process.kill()
                backend.supervisor.process.wait(timeout=5)
        if socket_path.exists():
            socket_path.unlink()


def test_pty_host_watchdog_restarts_crashed_host(monkeypatch, tmp_path):
    socket_path = Path(f"/tmp/agentbridge-pty-host-{uuid4().hex}.sock")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_BACKEND", "pty_host")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET", str(socket_path))
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN", "secret-token")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_WATCHDOG_ENABLED", "true")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH", str(tmp_path / "host.json"))
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_STARTUP_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_WATCHDOG_INTERVAL_SECONDS", "0.05")

    backend = create_terminal_backend_from_env()

    assert isinstance(backend, PtyHostTerminalBackend)
    assert backend.supervisor is not None
    backend.start_supervision()
    try:
        first_process = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            candidate = backend.supervisor.process
            if (
                candidate is not None
                and candidate.poll() is None
                and backend.supervisor.is_healthy()
            ):
                first_process = candidate
                break
            time.sleep(0.05)
        assert first_process is not None

        first_process.kill()
        first_process.wait(timeout=5)

        restarted_process = None
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            candidate = backend.supervisor.process
            if (
                candidate is not None
                and candidate is not first_process
                and candidate.poll() is None
                and backend.supervisor.is_healthy()
            ):
                restarted_process = candidate
                break
            time.sleep(0.05)

        assert restarted_process is not None
        status = backend.supervision_status()
        assert status["watchdog_running"] is True
        assert status["restart_count"] >= 1
    finally:
        backend.stop_supervision()
        process = backend.supervisor.process
        if process is not None and process.poll() is None:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        if socket_path.exists():
            socket_path.unlink()


def test_pty_host_watchdog_restart_can_auto_restart_lost_terminal(
    monkeypatch,
    tmp_path,
):
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("sh executable is required for PTY host recovery test")

    socket_path = Path(f"/tmp/agentbridge-pty-host-{uuid4().hex}.sock")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_BACKEND", "pty_host")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET", str(socket_path))
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN", "secret-token")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_WATCHDOG_ENABLED", "true")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH", str(tmp_path / "host.json"))
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_STARTUP_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("AGENTBRIDGE_TERMINAL_PTY_HOST_WATCHDOG_INTERVAL_SECONDS", "0.05")

    control = ControlPlane()
    _, session = create_session(control, tmp_path)
    backend = create_terminal_backend_from_env()
    terminal = TerminalAgentService(
        control,
        backend=backend,
        lifecycle_policy=TerminalLifecyclePolicy(
            auto_restart_on_lost=True,
            auto_restart_max_attempts=1,
            auto_restart_command_allowlist=(f"{sh} *",),
        ),
    )

    assert isinstance(backend, PtyHostTerminalBackend)
    assert backend.supervisor is not None
    backend.start_supervision()
    command = f"{sh} -c 'printf agentbridge-ready; sleep 30'"
    try:
        terminal.start_session(
            session_id=session.id,
            command=command,
            trace_id="pty-host-recovery-start",
        )
        first_process = backend.supervisor.process
        assert first_process is not None
        assert first_process.poll() is None

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if "agentbridge-ready" in terminal.snapshot(session_id=session.id):
                break
            time.sleep(0.05)
        assert "agentbridge-ready" in terminal.snapshot(session_id=session.id)

        first_process.kill()
        first_process.wait(timeout=5)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            candidate = backend.supervisor.process
            if (
                candidate is not None
                and candidate is not first_process
                and candidate.poll() is None
                and backend.supervisor.is_healthy()
            ):
                break
            time.sleep(0.05)
        else:
            pytest.fail("PTY host watchdog did not restart the host")

        observed = terminal.run_lifecycle_monitor_once(
            trace_id="pty-host-recovery-monitor"
        )

        assert observed[session.id] == TerminalStatus(started=False, running=False)
        deadline = time.monotonic() + 5
        restarted_status = TerminalStatus(started=False, running=False)
        while time.monotonic() < deadline:
            restarted_status = terminal.status(
                session_id=session.id,
                trace_id="pty-host-recovery-status",
            )
            if restarted_status.started and restarted_status.running:
                break
            time.sleep(0.05)
        assert restarted_status.started is True
        assert restarted_status.running is True

        events = control.repository.list_events(session_id=session.id)
        assert [event.type for event in events] == [
            "session.created",
            "terminal.started",
            "terminal.lost",
            "terminal.started",
        ]
        assert events[-1].payload == {
            "workspace_id": session.workspace_id,
            "command": command,
            "generation": 2,
            "restart_of_generation": 1,
            "restart_reason": "auto_lost_restart",
        }
        lifecycle_status = terminal.lifecycle_monitor_status()
        assert lifecycle_status["auto_restart_attempt_count"] == 1
        assert lifecycle_status["backend_supervision"]["watchdog_enabled"] is True
        assert lifecycle_status["backend_supervision"]["restart_count"] >= 1
    finally:
        backend.stop_supervision()
        with contextlib.suppress(Exception):
            backend.terminate(session_id=session.id)
        process = backend.supervisor.process
        if process is not None and process.poll() is None:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        if socket_path.exists():
            socket_path.unlink()


def test_terminal_backend_supervision_hooks_call_backend():
    class SupervisedFakeBackend(FakeTerminalBackend):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[str] = []

        def start_supervision(self) -> None:
            self.calls.append("start")

        def stop_supervision(self) -> None:
            self.calls.append("stop")

    backend = SupervisedFakeBackend()
    terminal = TerminalAgentService(ControlPlane(), backend=backend)

    start_terminal_backend_supervision(terminal)
    stop_terminal_backend_supervision(terminal)

    assert backend.calls == ["start", "stop"]


def test_pty_host_backend_survives_client_recreation(tmp_path):
    cat = shutil.which("cat")
    if cat is None:
        pytest.skip("cat executable is required for PTY host integration test")

    socket_path = Path(f"/tmp/agentbridge-pty-host-{uuid4().hex}.sock")
    host_backend = PtyTerminalBackend(host_state_path=tmp_path / "pty-host-state.json")
    server = PtyHostServer(
        socket_path=socket_path,
        auth_token="secret-token",
        backend=host_backend,
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session_id = "pty-host-one"
    try:
        first_client = PtyHostTerminalBackend(
            socket_path=socket_path,
            auth_token="secret-token",
        )
        first_client.start(session_id=session_id, cwd=str(tmp_path), command=cat)
        first_client.write(
            session_id=session_id,
            data="hello hosted pty\n",
            kind=TerminalInputKind.TEXT,
        )

        second_client = PtyHostTerminalBackend(
            socket_path=socket_path,
            auth_token="secret-token",
        )
        deadline = time.monotonic() + 2
        chunk = TerminalOutputChunk(cursor=0, data="", snapshot="")
        while time.monotonic() < deadline:
            chunk = second_client.read_output(session_id=session_id, after_cursor=0)
            if "hello hosted pty" in chunk.snapshot:
                break
            time.sleep(0.05)

        assert "hello hosted pty" in chunk.snapshot
        status = second_client.status(session_id=session_id)
        assert status.started is True
        assert status.running is True
        assert host_backend.host_state_store is not None
        record = host_backend.host_state_store.get(session_id)
        assert record is not None
        assert record.host_pid == os.getpid()
        assert record.child_pid == status.pid
    finally:
        with contextlib.suppress(Exception):
            PtyHostTerminalBackend(
                socket_path=socket_path,
                auth_token="secret-token",
            ).terminate(session_id=session_id)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_pty_backend_streams_process_output(tmp_path):
    cat = shutil.which("cat")
    if cat is None:
        pytest.skip("cat executable is required for PTY integration test")

    backend = PtyTerminalBackend()
    session_id = "pty-one"
    backend.start(session_id=session_id, cwd=str(tmp_path), command=cat)
    try:
        backend.resize(session_id=session_id, cols=100, rows=30)
        backend.write(
            session_id=session_id,
            data="hello pty\n",
            kind=TerminalInputKind.TEXT,
        )

        deadline = time.monotonic() + 2
        output = ""
        while time.monotonic() < deadline:
            chunk = backend.read_output(session_id=session_id, after_cursor=0)
            output = chunk.snapshot
            if "hello pty" in output:
                break
            time.sleep(0.05)

        assert "hello pty" in output
        reset_chunk = backend.read_output(session_id=session_id, after_cursor=999_999)
        assert reset_chunk.reset is True
        assert "hello pty" in reset_chunk.snapshot
    finally:
        try:
            backend.signal(session_id=session_id, name="eof")
        except AgentBridgeError:
            pass
        backend.terminate(session_id)


def test_pty_backend_status_reports_process_exit(tmp_path):
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("sh executable is required for PTY exit status test")

    backend = PtyTerminalBackend()
    session_id = "pty-exit"
    backend.start(session_id=session_id, cwd=str(tmp_path), command=f"{shell} -c 'exit 7'")
    try:
        deadline = time.monotonic() + 2
        status = backend.status(session_id=session_id)
        while time.monotonic() < deadline:
            status = backend.status(session_id=session_id)
            if not status.running:
                break
            time.sleep(0.05)

        assert status.started is True
        assert status.running is False
        assert status.exit_code == 7
        assert status.pid is not None
    finally:
        backend.terminate(session_id)


def test_pty_backend_persists_host_state(tmp_path):
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("sh executable is required for PTY host state test")

    host_state_path = tmp_path / "pty-host-state.json"
    backend = PtyTerminalBackend(host_state_path=host_state_path)
    session_id = "pty-host-state"
    backend.start(
        session_id=session_id,
        cwd=str(tmp_path),
        command=f"{shell} -c 'sleep 10'",
    )
    try:
        status = backend.status(session_id=session_id)
        assert status.running is True
        assert backend.host_state_store is not None
        record = backend.host_state_store.get(session_id)
        assert record is not None
        assert record.cwd == str(tmp_path)
        assert record.command == f"{shell} -c 'sleep 10'"
        assert record.host_pid == os.getpid()
        assert record.child_pid == status.pid
        assert record.status == "running"
        assert host_state_path.exists()
    finally:
        backend.terminate(session_id)

    assert backend.host_state_store is not None
    record = backend.host_state_store.get(session_id)
    assert record is not None
    assert record.status == "terminated"
    assert record.exit_code is not None


def test_pty_backend_resets_when_cursor_falls_behind_retained_output(tmp_path):
    shell = shutil.which("sh")
    if shell is None:
        pytest.skip("sh executable is required for PTY retention test")

    backend = PtyTerminalBackend(max_output_chars=12)
    session_id = "pty-retention"
    backend.start(
        session_id=session_id,
        cwd=str(tmp_path),
        command=f"{shell} -c 'printf abcdefghijklmnopqrstuvwxyz; sleep 10'",
    )
    try:
        deadline = time.monotonic() + 2
        chunk = TerminalOutputChunk(cursor=0, data="", snapshot="")
        while time.monotonic() < deadline:
            chunk = backend.read_output(session_id=session_id, after_cursor=0)
            if chunk.reset and chunk.cursor >= 26:
                break
            time.sleep(0.05)

        assert chunk.reset is True
        assert chunk.cursor >= 26
        assert len(chunk.snapshot) <= 12
        assert chunk.cursor > len(chunk.snapshot)
        assert chunk.snapshot.endswith("opqrstuvwxyz")

        fresh_chunk = backend.read_output(session_id=session_id, after_cursor=chunk.cursor)
        assert fresh_chunk.reset is False
        assert fresh_chunk.data == ""
    finally:
        backend.terminate(session_id)
