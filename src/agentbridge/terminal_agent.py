from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    AgentBridgeError,
    ErrorCode,
    LeaseOwnerType,
    SemanticEventSource,
)


class TerminalInputKind(StrEnum):
    TEXT = "text"
    KEY = "key"
    PASTE = "paste"
    SIGNAL = "signal"
    RESIZE = "resize"


class TerminalBackend(Protocol):
    def start(self, *, session_id: str, cwd: str, command: str) -> None: ...

    def write(self, *, session_id: str, data: str, kind: TerminalInputKind) -> None: ...

    def signal(self, *, session_id: str, name: str) -> None: ...

    def resize(self, *, session_id: str, cols: int, rows: int) -> None: ...

    def snapshot(self, *, session_id: str) -> str: ...


@dataclass
class FakeTerminalBackend:
    started: dict[str, tuple[str, str]] = field(default_factory=dict)
    buffers: dict[str, list[str]] = field(default_factory=dict)
    sizes: dict[str, tuple[int, int]] = field(default_factory=dict)
    signals: dict[str, list[str]] = field(default_factory=dict)

    def start(self, *, session_id: str, cwd: str, command: str) -> None:
        self.started[session_id] = (cwd, command)
        self.buffers.setdefault(session_id, [])

    def write(self, *, session_id: str, data: str, kind: TerminalInputKind) -> None:
        self._require_started(session_id)
        self.buffers.setdefault(session_id, []).append(data)

    def signal(self, *, session_id: str, name: str) -> None:
        self._require_started(session_id)
        self.signals.setdefault(session_id, []).append(name)

    def resize(self, *, session_id: str, cols: int, rows: int) -> None:
        self._require_started(session_id)
        self.sizes[session_id] = (cols, rows)

    def snapshot(self, *, session_id: str) -> str:
        self._require_started(session_id)
        return "".join(self.buffers.get(session_id, []))

    def _require_started(self, session_id: str) -> None:
        if session_id not in self.started:
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "终端会话尚未启动。",
                next_step="请先启动终端后再发送输入。",
                status_code=409,
            )


class TmuxTerminalBackend:
    def __init__(self, executable: str = "tmux") -> None:
        self.executable = executable
        if shutil.which(executable) is None:
            raise AgentBridgeError(
                ErrorCode.PLATFORM_CAPABILITY_MISSING,
                "当前环境找不到 tmux。",
                next_step="请安装 tmux，或将终端后端配置为 fake。",
                status_code=503,
            )

    def start(self, *, session_id: str, cwd: str, command: str) -> None:
        name = self._tmux_name(session_id)
        if self._has_session(name):
            return
        Path(cwd).mkdir(parents=True, exist_ok=True)
        self._run(["new-session", "-d", "-s", name, "-c", cwd, command])

    def write(self, *, session_id: str, data: str, kind: TerminalInputKind) -> None:
        name = self._tmux_name(session_id)
        if kind in {TerminalInputKind.TEXT, TerminalInputKind.PASTE}:
            self._run(["send-keys", "-t", name, "-l", data])
            return
        if kind == TerminalInputKind.KEY:
            self._run(["send-keys", "-t", name, data])
            return
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            f"tmux write 不支持输入类型：{kind.value}",
            next_step="请使用 text、paste 或 key 输入类型。",
        )

    def signal(self, *, session_id: str, name: str) -> None:
        tmux_key = {"interrupt": "C-c", "eof": "C-d"}.get(name)
        if not tmux_key:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                f"不支持的终端信号：{name}",
                next_step="当前支持 interrupt 和 eof。",
            )
        self._run(["send-keys", "-t", self._tmux_name(session_id), tmux_key])

    def resize(self, *, session_id: str, cols: int, rows: int) -> None:
        self._run(
            ["resize-window", "-t", self._tmux_name(session_id), "-x", str(cols), "-y", str(rows)]
        )

    def snapshot(self, *, session_id: str) -> str:
        result = self._run(["capture-pane", "-p", "-t", self._tmux_name(session_id)])
        return result.stdout

    def _has_session(self, name: str) -> bool:
        result = subprocess.run(
            [self.executable, "has-session", "-t", name],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.executable, *args],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "tmux 操作失败。",
                next_step="请检查 tmux 会话状态后重试。",
                status_code=409,
                details={"stderr": exc.stderr.strip()},
            ) from exc

    @staticmethod
    def _tmux_name(session_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)
        return f"agentbridge_{safe}"


class TerminalAgentService:
    def __init__(self, control: ControlPlane, backend: TerminalBackend | None = None) -> None:
        self.control = control
        self.backend = backend or FakeTerminalBackend()

    def start_session(
        self,
        *,
        session_id: str,
        command: str = "sh",
        trace_id: str,
    ) -> None:
        session = self.control.repository.get_session(session_id)
        workspace = self.control.repository.get_workspace(session.workspace_id)
        self.backend.start(session_id=session_id, cwd=workspace.path, command=command)
        self.control.emit_event(
            event_type="terminal.started",
            source=SemanticEventSource.TERMINAL_AGENT,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            payload={"workspace_id": workspace.id, "command": command},
        )

    def submit_input(
        self,
        *,
        session_id: str,
        epoch: int,
        owner_type: LeaseOwnerType,
        owner_id: str,
        kind: TerminalInputKind,
        data: str,
        trace_id: str,
        request_id: str | None = None,
        cols: int | None = None,
        rows: int | None = None,
    ) -> str:
        request_id = request_id or f"tin_{uuid4().hex[:12]}"
        session = self.control.repository.get_session(session_id)
        lease = self.control.repository.current_lease(session_id)
        if (
            lease is None
            or lease.epoch != epoch
            or lease.owner_type != owner_type
            or lease.owner_id != owner_id
        ):
            self.control.emit_event(
                event_type="terminal.input.rejected",
                source=SemanticEventSource.TERMINAL_AGENT,
                trace_id=trace_id,
                project_id=session.project_id,
                session_id=session_id,
                payload={
                    "request_id": request_id,
                    "provided_epoch": epoch,
                    "current_epoch": lease.epoch if lease else None,
                    "reason": "lease_mismatch",
                },
            )
            raise AgentBridgeError(
                ErrorCode.LEASE_CONFLICT,
                "终端输入租约不匹配。",
                next_step="请刷新控制状态并使用当前 epoch 重试。",
                status_code=409,
            )

        if kind == TerminalInputKind.SIGNAL:
            self.backend.signal(session_id=session_id, name=data)
        elif kind == TerminalInputKind.RESIZE:
            if cols is None or rows is None:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "resize 输入需要 cols 和 rows。",
                    next_step="请提供终端列数和行数。",
                )
            self.backend.resize(session_id=session_id, cols=cols, rows=rows)
        else:
            self.backend.write(session_id=session_id, data=data, kind=kind)

        self.control.emit_event(
            event_type="terminal.input.accepted",
            source=SemanticEventSource.TERMINAL_AGENT,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            payload={
                "request_id": request_id,
                "kind": kind.value,
                "epoch": epoch,
                "owner_type": owner_type.value,
                "owner_id": owner_id,
            },
            idempotency_key=request_id,
        )
        return request_id

    def snapshot(self, *, session_id: str) -> str:
        self.control.repository.get_session(session_id)
        return self.backend.snapshot(session_id=session_id)
