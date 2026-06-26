from __future__ import annotations

import fcntl
import fnmatch
import json
import os
import pty
import re
import select
import shlex
import shutil
import signal as process_signal
import struct
import subprocess
import termios
import time
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import Event, RLock, Thread, current_thread
from typing import Protocol
from uuid import uuid4

from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    AgentBridgeError,
    AgentType,
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


@dataclass(frozen=True)
class TerminalOutputChunk:
    cursor: int
    data: str
    snapshot: str
    reset: bool = False


@dataclass(frozen=True)
class TerminalStatus:
    started: bool
    running: bool
    exit_code: int | None = None
    pid: int | None = None
    output_cursor: int = 0
    output_base_cursor: int = 0
    output_retained_chars: int = 0

    def to_payload(self) -> dict[str, object]:
        return {
            "started": self.started,
            "running": self.running,
            "exit_code": self.exit_code,
            "pid": self.pid,
            "output_cursor": self.output_cursor,
            "output_base_cursor": self.output_base_cursor,
            "output_retained_chars": self.output_retained_chars,
        }


@dataclass(frozen=True)
class TerminalStartSpec:
    command: str
    workspace_id: str | None
    generation: int
    agent_type: AgentType | None = None
    command_source: str | None = None


@dataclass(frozen=True)
class AgentLaunchProfile:
    agent_type: AgentType
    command: str
    source: str


@dataclass(frozen=True)
class AgentLaunchDetection:
    agent_type: AgentType
    command: str
    source: str
    executable: str | None
    executable_path: str | None
    available: bool
    unavailable_reason: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "agent_type": self.agent_type.value,
            "command": self.command,
            "source": self.source,
            "executable": self.executable,
            "executable_path": self.executable_path,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True)
class AgentLaunchVersionProbe:
    agent_type: AgentType
    command: str
    source: str
    profile_available: bool
    profile_unavailable_reason: str | None
    version_command: str | None
    version_source: str | None
    version_executable: str | None
    version_executable_path: str | None
    status: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    version_text: str | None = None
    duration_ms: int | None = None
    error: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "agent_type": self.agent_type.value,
            "command": self.command,
            "source": self.source,
            "profile_available": self.profile_available,
            "profile_unavailable_reason": self.profile_unavailable_reason,
            "version_command": self.version_command,
            "version_source": self.version_source,
            "version_executable": self.version_executable,
            "version_executable_path": self.version_executable_path,
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "version_text": self.version_text,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


DEFAULT_AGENT_COMMANDS: dict[AgentType, str] = {
    AgentType.CLAUDE: "claude",
    AgentType.CODEX: "codex",
    AgentType.GENERIC_TUI: "sh",
}

AGENT_COMMAND_ENV_BY_TYPE: dict[AgentType, str] = {
    AgentType.CLAUDE: "AGENTBRIDGE_AGENT_CLAUDE_COMMAND",
    AgentType.CODEX: "AGENTBRIDGE_AGENT_CODEX_COMMAND",
    AgentType.GENERIC_TUI: "AGENTBRIDGE_AGENT_GENERIC_TUI_COMMAND",
}

AGENT_VERSION_COMMAND_ENV_BY_TYPE: dict[AgentType, str] = {
    AgentType.CLAUDE: "AGENTBRIDGE_AGENT_CLAUDE_VERSION_COMMAND",
    AgentType.CODEX: "AGENTBRIDGE_AGENT_CODEX_VERSION_COMMAND",
    AgentType.GENERIC_TUI: "AGENTBRIDGE_AGENT_GENERIC_TUI_VERSION_COMMAND",
}

DEFAULT_VERSION_PROBE_AGENT_TYPES = {AgentType.CLAUDE, AgentType.CODEX}


@dataclass(frozen=True)
class AgentLaunchConfig:
    command_by_agent: dict[AgentType, str] = field(default_factory=dict)
    source_by_agent: dict[AgentType, str] = field(default_factory=dict)
    version_command_by_agent: dict[AgentType, str] = field(default_factory=dict)
    version_source_by_agent: dict[AgentType, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> AgentLaunchConfig:
        command_by_agent: dict[AgentType, str] = {}
        source_by_agent: dict[AgentType, str] = {}
        for agent_type, default_command in DEFAULT_AGENT_COMMANDS.items():
            env_name = AGENT_COMMAND_ENV_BY_TYPE[agent_type]
            configured_command = os.environ.get(env_name, "").strip()
            if configured_command:
                command_by_agent[agent_type] = configured_command
                source_by_agent[agent_type] = f"env:{env_name}"
            else:
                command_by_agent[agent_type] = default_command
                source_by_agent[agent_type] = "built_in"

        version_command_by_agent: dict[AgentType, str] = {}
        version_source_by_agent: dict[AgentType, str] = {}
        for agent_type in AgentType:
            env_name = AGENT_VERSION_COMMAND_ENV_BY_TYPE[agent_type]
            configured_command = os.environ.get(env_name, "").strip()
            if configured_command:
                version_command_by_agent[agent_type] = configured_command
                version_source_by_agent[agent_type] = f"env:{env_name}"
                continue
            default_version_command = cls.default_version_command(
                agent_type,
                command_by_agent[agent_type],
            )
            if default_version_command is not None:
                version_command_by_agent[agent_type] = default_version_command
                version_source_by_agent[agent_type] = "built_in"
        return cls(
            command_by_agent=command_by_agent,
            source_by_agent=source_by_agent,
            version_command_by_agent=version_command_by_agent,
            version_source_by_agent=version_source_by_agent,
        )

    def profile_for(self, agent_type: AgentType) -> AgentLaunchProfile:
        command = self.command_by_agent.get(agent_type) or DEFAULT_AGENT_COMMANDS[agent_type]
        source = self.source_by_agent.get(agent_type) or "built_in"
        return AgentLaunchProfile(agent_type=agent_type, command=command, source=source)

    def detect(self, agent_type: AgentType) -> AgentLaunchDetection:
        profile = self.profile_for(agent_type)
        try:
            argv = shlex.split(profile.command)
        except ValueError as exc:
            return AgentLaunchDetection(
                agent_type=profile.agent_type,
                command=profile.command,
                source=profile.source,
                executable=None,
                executable_path=None,
                available=False,
                unavailable_reason=f"parse_error:{exc}",
            )
        if not argv:
            return AgentLaunchDetection(
                agent_type=profile.agent_type,
                command=profile.command,
                source=profile.source,
                executable=None,
                executable_path=None,
                available=False,
                unavailable_reason="empty_command",
            )
        executable = argv[0]
        executable_path = self._resolve_executable_path(executable)
        return AgentLaunchDetection(
            agent_type=profile.agent_type,
            command=profile.command,
            source=profile.source,
            executable=executable,
            executable_path=executable_path,
            available=executable_path is not None,
            unavailable_reason=None if executable_path is not None else "not_found",
        )

    @staticmethod
    def _resolve_executable_path(executable: str) -> str | None:
        if os.path.dirname(executable):
            path = Path(executable).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
            return None
        return shutil.which(executable)

    @classmethod
    def default_version_command(
        cls,
        agent_type: AgentType,
        command: str,
    ) -> str | None:
        if agent_type not in DEFAULT_VERSION_PROBE_AGENT_TYPES:
            return None
        try:
            argv = shlex.split(command)
        except ValueError:
            return None
        if not argv:
            return None
        return shlex.join([argv[0], "--version"])

    def version_command_for(self, agent_type: AgentType) -> tuple[str | None, str | None]:
        return (
            self.version_command_by_agent.get(agent_type),
            self.version_source_by_agent.get(agent_type),
        )

    def probe_version(
        self,
        agent_type: AgentType,
        *,
        timeout_seconds: float,
        output_limit_chars: int = 4096,
    ) -> AgentLaunchVersionProbe:
        detection = self.detect(agent_type)
        version_command, version_source = self.version_command_for(agent_type)
        if version_command is None:
            return AgentLaunchVersionProbe(
                agent_type=agent_type,
                command=detection.command,
                source=detection.source,
                profile_available=detection.available,
                profile_unavailable_reason=detection.unavailable_reason,
                version_command=None,
                version_source=None,
                version_executable=None,
                version_executable_path=None,
                status="skipped",
                error="version_probe_not_configured",
            )
        try:
            version_argv = shlex.split(version_command)
        except ValueError as exc:
            return AgentLaunchVersionProbe(
                agent_type=agent_type,
                command=detection.command,
                source=detection.source,
                profile_available=detection.available,
                profile_unavailable_reason=detection.unavailable_reason,
                version_command=version_command,
                version_source=version_source,
                version_executable=None,
                version_executable_path=None,
                status="parse_error",
                error=str(exc),
            )
        if not version_argv:
            return AgentLaunchVersionProbe(
                agent_type=agent_type,
                command=detection.command,
                source=detection.source,
                profile_available=detection.available,
                profile_unavailable_reason=detection.unavailable_reason,
                version_command=version_command,
                version_source=version_source,
                version_executable=None,
                version_executable_path=None,
                status="parse_error",
                error="empty_version_command",
            )
        version_executable = version_argv[0]
        version_executable_path = self._resolve_executable_path(version_executable)
        if version_executable_path is None:
            return AgentLaunchVersionProbe(
                agent_type=agent_type,
                command=detection.command,
                source=detection.source,
                profile_available=detection.available,
                profile_unavailable_reason=detection.unavailable_reason,
                version_command=version_command,
                version_source=version_source,
                version_executable=version_executable,
                version_executable_path=None,
                status="unavailable",
                error="version_executable_not_found",
            )
        run_argv = [version_executable_path, *version_argv[1:]]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                run_argv,
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return AgentLaunchVersionProbe(
                agent_type=agent_type,
                command=detection.command,
                source=detection.source,
                profile_available=detection.available,
                profile_unavailable_reason=detection.unavailable_reason,
                version_command=version_command,
                version_source=version_source,
                version_executable=version_executable,
                version_executable_path=version_executable_path,
                status="timeout",
                stdout=self._truncate_probe_output(exc.stdout, output_limit_chars),
                stderr=self._truncate_probe_output(exc.stderr, output_limit_chars),
                duration_ms=duration_ms,
                error=f"timed out after {timeout_seconds:.3g}s",
            )
        except OSError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return AgentLaunchVersionProbe(
                agent_type=agent_type,
                command=detection.command,
                source=detection.source,
                profile_available=detection.available,
                profile_unavailable_reason=detection.unavailable_reason,
                version_command=version_command,
                version_source=version_source,
                version_executable=version_executable,
                version_executable_path=version_executable_path,
                status="failed",
                duration_ms=duration_ms,
                error=str(exc),
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = self._truncate_probe_output(completed.stdout, output_limit_chars)
        stderr = self._truncate_probe_output(completed.stderr, output_limit_chars)
        return AgentLaunchVersionProbe(
            agent_type=agent_type,
            command=detection.command,
            source=detection.source,
            profile_available=detection.available,
            profile_unavailable_reason=detection.unavailable_reason,
            version_command=version_command,
            version_source=version_source,
            version_executable=version_executable,
            version_executable_path=version_executable_path,
            status="ok" if completed.returncode == 0 else "failed",
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            version_text=self._first_probe_output_line(stdout, stderr),
            duration_ms=duration_ms,
        )

    @staticmethod
    def _truncate_probe_output(value: object, limit: int) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = str(value)
        if len(text) <= limit:
            return text
        return text[:limit] + "\n[truncated]"

    @staticmethod
    def _first_probe_output_line(stdout: str, stderr: str) -> str | None:
        for text in (stdout, stderr):
            for line in text.splitlines():
                stripped = line.strip()
                if stripped:
                    return stripped
        return None


@dataclass(frozen=True)
class TerminalRestartResult:
    status: str
    restarted: bool
    command: str
    previous_generation: int
    generation: int

    def to_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "restarted": self.restarted,
            "command": self.command,
            "previous_generation": self.previous_generation,
            "generation": self.generation,
        }


@dataclass(frozen=True)
class TerminalLifecyclePolicy:
    auto_restart_on_lost: bool = False
    auto_restart_max_attempts: int = 1
    auto_restart_command_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.auto_restart_max_attempts < 0:
            raise ValueError("auto_restart_max_attempts must be non-negative")
        object.__setattr__(
            self,
            "auto_restart_command_allowlist",
            tuple(
                pattern.strip()
                for pattern in self.auto_restart_command_allowlist
                if pattern.strip()
            ),
        )

    def allows_auto_restart_command(self, command: str) -> bool:
        normalized = command.strip()
        return any(
            fnmatch.fnmatchcase(normalized, pattern)
            for pattern in self.auto_restart_command_allowlist
        )


class TerminalBackend(Protocol):
    def start(self, *, session_id: str, cwd: str, command: str) -> None: ...

    def write(self, *, session_id: str, data: str, kind: TerminalInputKind) -> None: ...

    def signal(self, *, session_id: str, name: str) -> None: ...

    def resize(self, *, session_id: str, cols: int, rows: int) -> None: ...

    def snapshot(self, *, session_id: str) -> str: ...

    def read_output(self, *, session_id: str, after_cursor: int = 0) -> TerminalOutputChunk: ...

    def status(self, *, session_id: str) -> TerminalStatus: ...


DEFAULT_PTY_OUTPUT_LIMIT_CHARS = 1_000_000


@dataclass(frozen=True)
class PtyHostStateRecord:
    session_id: str
    cwd: str
    command: str
    host_pid: int
    child_pid: int
    status: str
    started_at: float
    updated_at: float
    exit_code: int | None = None
    output_cursor: int = 0
    output_base_cursor: int = 0
    output_retained_chars: int = 0

    def to_payload(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "cwd": self.cwd,
            "command": self.command,
            "host_pid": self.host_pid,
            "child_pid": self.child_pid,
            "status": self.status,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "exit_code": self.exit_code,
            "output_cursor": self.output_cursor,
            "output_base_cursor": self.output_base_cursor,
            "output_retained_chars": self.output_retained_chars,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> PtyHostStateRecord:
        return cls(
            session_id=str(payload["session_id"]),
            cwd=str(payload["cwd"]),
            command=str(payload["command"]),
            host_pid=int(payload["host_pid"]),
            child_pid=int(payload["child_pid"]),
            status=str(payload["status"]),
            started_at=float(payload["started_at"]),
            updated_at=float(payload["updated_at"]),
            exit_code=(
                int(payload["exit_code"]) if payload.get("exit_code") is not None else None
            ),
            output_cursor=int(payload.get("output_cursor") or 0),
            output_base_cursor=int(payload.get("output_base_cursor") or 0),
            output_retained_chars=int(payload.get("output_retained_chars") or 0),
        )


class PtyHostStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()
        self._lock = RLock()

    def load(self) -> dict[str, PtyHostStateRecord]:
        with self._lock:
            return self._load_unlocked()

    def get(self, session_id: str) -> PtyHostStateRecord | None:
        return self.load().get(session_id)

    def upsert(self, record: PtyHostStateRecord) -> None:
        with self._lock:
            records = self._load_unlocked()
            records[record.session_id] = record
            self._write_unlocked(records)

    def _load_unlocked(self) -> dict[str, PtyHostStateRecord]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        sessions = raw.get("sessions") or {}
        if not isinstance(sessions, dict):
            return {}
        records: dict[str, PtyHostStateRecord] = {}
        for session_id, payload in sessions.items():
            if isinstance(session_id, str) and isinstance(payload, dict):
                with suppress(KeyError, TypeError, ValueError):
                    records[session_id] = PtyHostStateRecord.from_payload(payload)
        return records

    def _write_unlocked(self, records: dict[str, PtyHostStateRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            self.path.parent.chmod(0o700)
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "sessions": {
                session_id: record.to_payload()
                for session_id, record in sorted(records.items())
            },
        }
        tmp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.path)


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

    def read_output(self, *, session_id: str, after_cursor: int = 0) -> TerminalOutputChunk:
        snapshot = self.snapshot(session_id=session_id)
        cursor = len(snapshot)
        if after_cursor < 0 or after_cursor > cursor:
            return TerminalOutputChunk(
                cursor=cursor,
                data=snapshot,
                snapshot=snapshot,
                reset=True,
            )
        return TerminalOutputChunk(
            cursor=cursor,
            data=snapshot[after_cursor:],
            snapshot=snapshot,
        )

    def status(self, *, session_id: str) -> TerminalStatus:
        if session_id not in self.started:
            return TerminalStatus(started=False, running=False)
        snapshot = self.snapshot(session_id=session_id)
        return TerminalStatus(
            started=True,
            running=True,
            output_cursor=len(snapshot),
            output_retained_chars=len(snapshot),
        )

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

    def read_output(self, *, session_id: str, after_cursor: int = 0) -> TerminalOutputChunk:
        snapshot = self.snapshot(session_id=session_id)
        cursor = len(snapshot)
        if after_cursor < 0 or after_cursor > cursor:
            return TerminalOutputChunk(
                cursor=cursor,
                data=snapshot,
                snapshot=snapshot,
                reset=True,
            )
        return TerminalOutputChunk(
            cursor=cursor,
            data=snapshot[after_cursor:],
            snapshot=snapshot,
        )

    def status(self, *, session_id: str) -> TerminalStatus:
        running = self._has_session(self._tmux_name(session_id))
        return TerminalStatus(started=running, running=running)

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


@dataclass
class PtySession:
    process: subprocess.Popen[bytes]
    master_fd: int
    cwd: str
    command: str
    output: str = ""
    output_base_cursor: int = 0
    closed: bool = False
    lock: RLock = field(default_factory=RLock)
    reader: Thread | None = None


class PtyTerminalBackend:
    def __init__(
        self,
        *,
        max_output_chars: int = DEFAULT_PTY_OUTPUT_LIMIT_CHARS,
        host_state_path: Path | None = None,
    ) -> None:
        if not hasattr(pty, "openpty"):
            raise AgentBridgeError(
                ErrorCode.PLATFORM_CAPABILITY_MISSING,
                "当前平台不支持 PTY 后端。",
                next_step="请使用 fake 或 tmux 终端后端。",
                status_code=503,
            )
        if max_output_chars <= 0:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "PTY 输出保留上限必须大于 0。",
                next_step="请配置正整数的 PTY 输出保留字符数。",
            )
        self.max_output_chars = max_output_chars
        self.host_state_store = (
            PtyHostStateStore(host_state_path) if host_state_path is not None else None
        )
        self.sessions: dict[str, PtySession] = {}
        self._lock = RLock()

    def start(self, *, session_id: str, cwd: str, command: str) -> None:
        with self._lock:
            existing = self.sessions.get(session_id)
            if existing:
                if existing.process.poll() is None:
                    return
                self.terminate(session_id)
            argv = shlex.split(command)
            if not argv:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "PTY 启动命令不能为空。",
                    next_step="请提供要启动的本地命令，例如 sh。",
                )
            Path(cwd).mkdir(parents=True, exist_ok=True)
            master_fd, slave_fd = pty.openpty()
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    close_fds=True,
                    start_new_session=True,
                )
            except OSError as exc:
                os.close(master_fd)
                os.close(slave_fd)
                raise AgentBridgeError(
                    ErrorCode.PLATFORM_CAPABILITY_MISSING,
                    "PTY 命令启动失败。",
                    next_step="请检查本地命令是否存在，或切换终端后端。",
                    status_code=503,
                    details={"reason": str(exc)},
                ) from exc
            finally:
                with suppress(OSError):
                    os.close(slave_fd)
            session = PtySession(
                process=process,
                master_fd=master_fd,
                cwd=cwd,
                command=command,
            )
            reader = Thread(
                target=self._read_loop,
                args=(session,),
                name=f"agentbridge-pty-{session_id}",
                daemon=True,
            )
            session.reader = reader
            self.sessions[session_id] = session
            self._persist_pty_host_state(session_id, session, status="running")
            reader.start()

    def write(self, *, session_id: str, data: str, kind: TerminalInputKind) -> None:
        session = self._require_session(session_id)
        if kind not in {TerminalInputKind.TEXT, TerminalInputKind.PASTE, TerminalInputKind.KEY}:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                f"PTY write 不支持输入类型：{kind.value}",
                next_step="请使用 text、paste 或 key 输入类型。",
            )
        self._write_bytes(session, data.encode("utf-8"))

    def signal(self, *, session_id: str, name: str) -> None:
        session = self._require_session(session_id)
        control_code = {"interrupt": b"\x03", "eof": b"\x04"}.get(name)
        if control_code is None:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                f"不支持的终端信号：{name}",
                next_step="当前支持 interrupt 和 eof。",
            )
        self._write_bytes(session, control_code)

    def resize(self, *, session_id: str, cols: int, rows: int) -> None:
        session = self._require_session(session_id)
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(session.master_fd, termios.TIOCSWINSZ, winsize)
        except OSError as exc:
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "PTY resize 失败。",
                next_step="请检查 PTY 会话状态后重试。",
                status_code=409,
                details={"reason": str(exc)},
            ) from exc

    def snapshot(self, *, session_id: str) -> str:
        session = self._require_session(session_id)
        with session.lock:
            return session.output

    def read_output(self, *, session_id: str, after_cursor: int = 0) -> TerminalOutputChunk:
        session = self._require_session(session_id)
        with session.lock:
            snapshot = session.output
            base_cursor = session.output_base_cursor
            cursor = base_cursor + len(snapshot)
        if after_cursor < base_cursor or after_cursor > cursor:
            return TerminalOutputChunk(
                cursor=cursor,
                data=snapshot,
                snapshot=snapshot,
                reset=True,
            )
        return TerminalOutputChunk(
            cursor=cursor,
            data=snapshot[after_cursor - base_cursor :],
            snapshot=snapshot,
        )

    def status(self, *, session_id: str) -> TerminalStatus:
        session = self.sessions.get(session_id)
        if session is None:
            return TerminalStatus(started=False, running=False)
        exit_code = session.process.poll()
        with session.lock:
            output_retained_chars = len(session.output)
            output_base_cursor = session.output_base_cursor
            output_cursor = output_base_cursor + output_retained_chars
        status = TerminalStatus(
            started=True,
            running=exit_code is None,
            exit_code=exit_code,
            pid=session.process.pid,
            output_cursor=output_cursor,
            output_base_cursor=output_base_cursor,
            output_retained_chars=output_retained_chars,
        )
        self._persist_pty_host_state(
            session_id,
            session,
            status="running" if exit_code is None else "exited",
        )
        return status

    def terminate(self, session_id: str) -> None:
        with self._lock:
            session = self.sessions.pop(session_id, None)
        if session is None:
            return
        was_running = session.process.poll() is None
        if was_running:
            try:
                os.killpg(session.process.pid, process_signal.SIGTERM)
            except OSError:
                session.process.terminate()
            try:
                session.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(session.process.pid, process_signal.SIGKILL)
                except OSError:
                    session.process.kill()
                session.process.wait(timeout=2)
        with session.lock:
            session.closed = True
        self._persist_pty_host_state(
            session_id,
            session,
            status="terminated" if was_running else "exited",
        )
        with suppress(OSError):
            os.close(session.master_fd)
        if session.reader:
            session.reader.join(timeout=1)

    def _read_loop(self, session: PtySession) -> None:
        while True:
            with session.lock:
                if session.closed:
                    return
            try:
                readable, _, _ = select.select([session.master_fd], [], [], 0.1)
            except OSError:
                return
            if session.master_fd in readable:
                try:
                    data = os.read(session.master_fd, 4096)
                except OSError:
                    return
                if not data:
                    return
                text = data.decode("utf-8", errors="replace")
                self._append_output(session, text)
            elif session.process.poll() is not None:
                return

    def _require_session(self, session_id: str) -> PtySession:
        session = self.sessions.get(session_id)
        if not session:
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "PTY 会话尚未启动。",
                next_step="请先启动终端后再发送输入。",
                status_code=409,
            )
        return session

    def _write_bytes(self, session: PtySession, data: bytes) -> None:
        try:
            os.write(session.master_fd, data)
        except OSError as exc:
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "PTY 写入失败。",
                next_step="请检查 PTY 会话状态后重试。",
                status_code=409,
                details={"reason": str(exc)},
            ) from exc

    def _append_output(self, session: PtySession, text: str) -> None:
        with session.lock:
            session.output += text
            overflow = len(session.output) - self.max_output_chars
            if overflow > 0:
                session.output = session.output[overflow:]
                session.output_base_cursor += overflow

    def _persist_pty_host_state(
        self,
        session_id: str,
        session: PtySession,
        *,
        status: str,
    ) -> None:
        if self.host_state_store is None:
            return
        exit_code = session.process.poll()
        now = time.time()
        previous = self.host_state_store.get(session_id)
        with session.lock:
            output_retained_chars = len(session.output)
            output_base_cursor = session.output_base_cursor
            output_cursor = output_base_cursor + output_retained_chars
        started_at = (
            previous.started_at
            if previous is not None and previous.child_pid == session.process.pid
            else now
        )
        record = PtyHostStateRecord(
            session_id=session_id,
            cwd=session.cwd,
            command=session.command,
            host_pid=os.getpid(),
            child_pid=session.process.pid,
            status=status,
            started_at=started_at,
            updated_at=now,
            exit_code=exit_code,
            output_cursor=output_cursor,
            output_base_cursor=output_base_cursor,
            output_retained_chars=output_retained_chars,
        )
        self.host_state_store.upsert(record)


class TerminalAgentService:
    def __init__(
        self,
        control: ControlPlane,
        backend: TerminalBackend | None = None,
        *,
        lifecycle_policy: TerminalLifecyclePolicy | None = None,
        agent_launch_config: AgentLaunchConfig | None = None,
    ) -> None:
        self.control = control
        self.backend = backend or FakeTerminalBackend()
        self.lifecycle_policy = lifecycle_policy or TerminalLifecyclePolicy()
        self.agent_launch_config = agent_launch_config or AgentLaunchConfig.from_env()
        self._terminal_start_generations: dict[str, int] = {}
        self._reported_terminal_exits: set[tuple[str, int]] = set()
        self._reported_terminal_losses: set[tuple[str, int]] = set()
        self._reported_auto_restart_blocks: set[tuple[str, int, str]] = set()
        self._auto_restart_attempts: dict[str, int] = {}
        self._auto_restart_last_block_reason: str | None = None
        self._lifecycle_lock = RLock()
        self._lifecycle_stop_event = Event()
        self._lifecycle_thread: Thread | None = None
        self._lifecycle_interval_seconds = 1.0
        self._lifecycle_run_count = 0
        self._lifecycle_last_error: str | None = None
        self._lifecycle_last_observed_count = 0
        self.recover_lifecycle_state_from_events()

    def start_session(
        self,
        *,
        session_id: str,
        command: str | None = None,
        trace_id: str,
        restart_of_generation: int | None = None,
        restart_reason: str | None = None,
    ) -> int:
        session = self.control.repository.get_session(session_id)
        launch_profile = self._resolve_start_profile(session, command)
        workspace = self.control.repository.get_workspace(session.workspace_id)
        self.backend.start(
            session_id=session_id,
            cwd=workspace.path,
            command=launch_profile.command,
        )
        with self._lifecycle_lock:
            generation = self._terminal_start_generations.get(session_id, 0) + 1
            self._terminal_start_generations[session_id] = generation
        payload = {
            "workspace_id": workspace.id,
            "command": launch_profile.command,
            "generation": generation,
            "agent_type": launch_profile.agent_type.value,
            "command_source": launch_profile.source,
        }
        if restart_of_generation is not None:
            payload["restart_of_generation"] = restart_of_generation
        if restart_reason is not None:
            payload["restart_reason"] = restart_reason
        self.control.emit_event(
            event_type="terminal.started",
            source=SemanticEventSource.TERMINAL_AGENT,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            payload=payload,
        )
        return generation

    def resolve_start_command(
        self,
        *,
        session_id: str,
        command: str | None = None,
    ) -> AgentLaunchProfile:
        session = self.control.repository.get_session(session_id)
        return self._resolve_start_profile(session, command)

    def _resolve_start_profile(self, session, command: str | None) -> AgentLaunchProfile:
        if command is not None and command.strip():
            return AgentLaunchProfile(
                agent_type=session.agent_type,
                command=command,
                source="explicit",
            )
        return self.agent_launch_config.profile_for(session.agent_type)

    def agent_launch_profile_status(self) -> dict[str, dict[str, object]]:
        return {
            agent_type.value: self.agent_launch_config.detect(agent_type).to_payload()
            for agent_type in AgentType
        }

    def probe_agent_launch_versions(
        self,
        *,
        agent_types: list[AgentType] | None = None,
        timeout_seconds: float = 2.0,
    ) -> dict[str, dict[str, object]]:
        selected_agent_types = agent_types or list(AgentType)
        timeout = min(max(timeout_seconds, 0.1), 10.0)
        return {
            agent_type.value: self.agent_launch_config.probe_version(
                agent_type,
                timeout_seconds=timeout,
            ).to_payload()
            for agent_type in selected_agent_types
        }

    def restart_session(
        self,
        *,
        session_id: str,
        command: str | None = None,
        trace_id: str,
        restart_reason: str = "manual_restart",
    ) -> TerminalRestartResult:
        if command is None:
            start_spec = self.latest_start_spec(session_id=session_id)
            restart_command = start_spec.command
        else:
            restart_command = self.resolve_restart_command(
                session_id=session_id,
                command=command,
            )
            try:
                start_spec = self.latest_start_spec(session_id=session_id)
            except AgentBridgeError:
                with self._lifecycle_lock:
                    current_generation = self._terminal_start_generations.get(session_id, 0)
                start_spec = TerminalStartSpec(
                    command=restart_command,
                    workspace_id=None,
                    generation=current_generation,
                )
        status = self.status(session_id=session_id, trace_id=trace_id)
        if status.started and status.running:
            return TerminalRestartResult(
                status="already_running",
                restarted=False,
                command=start_spec.command,
                previous_generation=start_spec.generation,
                generation=start_spec.generation,
            )
        generation = self.start_session(
            session_id=session_id,
            command=restart_command,
            trace_id=trace_id,
            restart_of_generation=start_spec.generation,
            restart_reason=restart_reason,
        )
        return TerminalRestartResult(
            status="restarted",
            restarted=True,
            command=restart_command,
            previous_generation=start_spec.generation,
            generation=generation,
        )

    def resolve_restart_command(self, *, session_id: str, command: str | None = None) -> str:
        if command is not None:
            stripped = command.strip()
            if not stripped:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "终端重启命令不能为空。",
                    next_step="请提供非空命令，或省略 command 以复用上次启动命令。",
                )
            return command
        return self.latest_start_spec(session_id=session_id).command

    def latest_start_spec(self, *, session_id: str) -> TerminalStartSpec:
        self.control.repository.get_session(session_id)
        generation = 0
        latest: TerminalStartSpec | None = None
        for event in self.control.repository.list_events(session_id=session_id, limit=1_000_000):
            if event.type != "terminal.started":
                continue
            generation += 1
            command = event.payload.get("command")
            if not isinstance(command, str) or not command.strip():
                continue
            workspace_id = event.payload.get("workspace_id")
            agent_type = event.payload.get("agent_type")
            command_source = event.payload.get("command_source")
            parsed_agent_type = None
            if isinstance(agent_type, str):
                with suppress(ValueError):
                    parsed_agent_type = AgentType(agent_type)
            latest = TerminalStartSpec(
                command=command,
                workspace_id=workspace_id if isinstance(workspace_id, str) else None,
                generation=generation,
                agent_type=parsed_agent_type,
                command_source=command_source if isinstance(command_source, str) else None,
            )
        if latest is None:
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "找不到可用于重启的终端启动记录。",
                next_step="请先启动终端，或在重启请求中显式提供 command。",
                status_code=409,
            )
        return latest

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
        existing_event = self.control.repository.event_idempotency.get(request_id)
        if existing_event and existing_event.type == "terminal.input.accepted":
            return request_id
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

    def read_output(self, *, session_id: str, after_cursor: int = 0) -> TerminalOutputChunk:
        self.control.repository.get_session(session_id)
        return self.backend.read_output(
            session_id=session_id,
            after_cursor=after_cursor,
        )

    def status(
        self,
        *,
        session_id: str,
        trace_id: str = "terminal-status",
    ) -> TerminalStatus:
        session = self.control.repository.get_session(session_id)
        status = self.backend.status(session_id=session_id)
        if status.started:
            self._emit_terminal_exited_once(session=session, status=status, trace_id=trace_id)
        else:
            self._emit_terminal_lost_once(session=session, trace_id=trace_id)
        return status

    def run_lifecycle_monitor_once(
        self,
        *,
        trace_id: str = "terminal-lifecycle-monitor",
    ) -> dict[str, TerminalStatus]:
        with self._lifecycle_lock:
            session_ids = list(self._terminal_start_generations)
            self._lifecycle_run_count += 1
        observed: dict[str, TerminalStatus] = {}
        errors: list[str] = []
        for session_id in session_ids:
            try:
                status = self.status(session_id=session_id, trace_id=trace_id)
                observed[session_id] = status
                if self._should_auto_restart_lost(
                    session_id=session_id,
                    status=status,
                    trace_id=trace_id,
                ):
                    self.restart_session(
                        session_id=session_id,
                        trace_id=f"{trace_id}:auto-restart",
                        restart_reason="auto_lost_restart",
                    )
            except Exception as exc:
                errors.append(f"{session_id}: {exc}")
        with self._lifecycle_lock:
            self._lifecycle_last_error = "; ".join(errors) if errors else None
            self._lifecycle_last_observed_count = len(observed)
        return observed

    def recover_lifecycle_state_from_events(self) -> dict[str, int]:
        recovered_generations: dict[str, int] = {}
        recovered_exits: set[tuple[str, int]] = set()
        recovered_losses: set[tuple[str, int]] = set()
        recovered_auto_restart_blocks: set[tuple[str, int, str]] = set()
        for session in self.control.repository.list_sessions():
            generation = 0
            events = self.control.repository.list_events(
                session_id=session.id,
                limit=1_000_000,
            )
            for event in events:
                if event.type == "terminal.started":
                    generation += 1
                elif event.type == "terminal.exited":
                    event_generation = event.payload.get("generation")
                    if isinstance(event_generation, int):
                        recovered_exits.add((session.id, event_generation))
                    elif generation > 0:
                        recovered_exits.add((session.id, generation))
                elif event.type == "terminal.lost":
                    event_generation = event.payload.get("generation")
                    if isinstance(event_generation, int):
                        recovered_losses.add((session.id, event_generation))
                    elif generation > 0:
                        recovered_losses.add((session.id, generation))
                elif event.type == "terminal.auto_restart.skipped":
                    event_generation = event.payload.get("generation")
                    reason = str(event.payload.get("reason") or "unknown")
                    if isinstance(event_generation, int):
                        recovered_auto_restart_blocks.add(
                            (session.id, event_generation, reason)
                        )
                    elif generation > 0:
                        recovered_auto_restart_blocks.add((session.id, generation, reason))
            if generation > 0:
                recovered_generations[session.id] = generation

        with self._lifecycle_lock:
            for session_id, generation in recovered_generations.items():
                self._terminal_start_generations[session_id] = max(
                    generation,
                    self._terminal_start_generations.get(session_id, 0),
                )
            self._reported_terminal_exits.update(recovered_exits)
            self._reported_terminal_losses.update(recovered_losses)
            self._reported_auto_restart_blocks.update(recovered_auto_restart_blocks)
        return recovered_generations

    def start_lifecycle_monitor(self, *, interval_seconds: float = 1.0) -> bool:
        with self._lifecycle_lock:
            if self._lifecycle_thread and self._lifecycle_thread.is_alive():
                return False
            self._lifecycle_interval_seconds = max(float(interval_seconds), 0.05)
            self._lifecycle_stop_event.clear()
            self._lifecycle_thread = Thread(
                target=self._run_lifecycle_monitor_loop,
                name="agentbridge-terminal-lifecycle-monitor",
                daemon=True,
            )
            self._lifecycle_thread.start()
            return True

    def stop_lifecycle_monitor(self, timeout: float = 5.0) -> bool:
        with self._lifecycle_lock:
            thread = self._lifecycle_thread
            if thread is None:
                return False
            self._lifecycle_stop_event.set()
        if thread is not current_thread():
            thread.join(timeout=timeout)
        with self._lifecycle_lock:
            stopped = not thread.is_alive()
            if stopped:
                self._lifecycle_thread = None
            return stopped

    def is_lifecycle_monitor_running(self) -> bool:
        with self._lifecycle_lock:
            return bool(self._lifecycle_thread and self._lifecycle_thread.is_alive())

    def lifecycle_monitor_status(self) -> dict[str, object]:
        backend_supervision_status = getattr(self.backend, "supervision_status", None)
        with self._lifecycle_lock:
            return {
                "running": bool(self._lifecycle_thread and self._lifecycle_thread.is_alive()),
                "interval_seconds": self._lifecycle_interval_seconds,
                "tracked_sessions": len(self._terminal_start_generations),
                "run_count": self._lifecycle_run_count,
                "last_error": self._lifecycle_last_error,
                "last_observed_count": self._lifecycle_last_observed_count,
                "reported_exit_count": len(self._reported_terminal_exits),
                "reported_lost_count": len(self._reported_terminal_losses),
                "auto_restart_on_lost": self.lifecycle_policy.auto_restart_on_lost,
                "auto_restart_max_attempts": (
                    self.lifecycle_policy.auto_restart_max_attempts
                ),
                "auto_restart_command_allowlist": list(
                    self.lifecycle_policy.auto_restart_command_allowlist
                ),
                "auto_restart_attempt_count": sum(self._auto_restart_attempts.values()),
                "auto_restart_blocked_count": len(self._reported_auto_restart_blocks),
                "auto_restart_last_block_reason": self._auto_restart_last_block_reason,
                "backend_supervision": (
                    backend_supervision_status()
                    if callable(backend_supervision_status)
                    else {"enabled": False}
                ),
                "agent_launch_profiles": self.agent_launch_profile_status(),
            }

    def _run_lifecycle_monitor_loop(self) -> None:
        while not self._lifecycle_stop_event.is_set():
            self.run_lifecycle_monitor_once()
            self._lifecycle_stop_event.wait(self._lifecycle_interval_seconds)

    def _emit_terminal_exited_once(
        self,
        *,
        session,
        status: TerminalStatus,
        trace_id: str,
    ) -> None:
        if not status.started or status.running:
            return
        with self._lifecycle_lock:
            generation = self._terminal_start_generations.get(session.id, 0)
            exit_key = (session.id, generation)
            if (
                generation <= 0
                or exit_key in self._reported_terminal_exits
                or exit_key in self._reported_terminal_losses
            ):
                return
        self.control.emit_event(
            event_type="terminal.exited",
            source=SemanticEventSource.TERMINAL_AGENT,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session.id,
            payload={
                "generation": generation,
                "exit_code": status.exit_code,
                "pid": status.pid,
                "output_cursor": status.output_cursor,
            },
            idempotency_key=f"terminal-exited:{session.id}:{generation}",
        )
        with self._lifecycle_lock:
            self._reported_terminal_exits.add(exit_key)

    def _emit_terminal_lost_once(
        self,
        *,
        session,
        trace_id: str,
    ) -> None:
        with self._lifecycle_lock:
            generation = self._terminal_start_generations.get(session.id, 0)
            loss_key = (session.id, generation)
            if (
                generation <= 0
                or loss_key in self._reported_terminal_losses
                or loss_key in self._reported_terminal_exits
            ):
                return
        self.control.emit_event(
            event_type="terminal.lost",
            source=SemanticEventSource.TERMINAL_AGENT,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session.id,
            payload={
                "generation": generation,
                "reason": "backend_state_missing",
                "backend": type(self.backend).__name__,
            },
            idempotency_key=f"terminal-lost:{session.id}:{generation}",
        )
        with self._lifecycle_lock:
            self._reported_terminal_losses.add(loss_key)

    def _emit_auto_restart_skipped_once(
        self,
        *,
        session_id: str,
        generation: int,
        command: str | None,
        reason: str,
        trace_id: str,
    ) -> None:
        session = self.control.repository.get_session(session_id)
        block_key = (session_id, generation, reason)
        with self._lifecycle_lock:
            if block_key in self._reported_auto_restart_blocks:
                return
        self.control.emit_event(
            event_type="terminal.auto_restart.skipped",
            source=SemanticEventSource.TERMINAL_AGENT,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            payload={
                "generation": generation,
                "reason": reason,
                "command": command,
                "allowed_patterns": list(
                    self.lifecycle_policy.auto_restart_command_allowlist
                ),
            },
            idempotency_key=(
                f"terminal-auto-restart-skipped:{session_id}:{generation}:{reason}"
            ),
        )
        with self._lifecycle_lock:
            self._reported_auto_restart_blocks.add(block_key)
            self._auto_restart_last_block_reason = f"{session_id}: {reason}"

    def _should_auto_restart_lost(
        self,
        *,
        session_id: str,
        status: TerminalStatus,
        trace_id: str,
    ) -> bool:
        if not self.lifecycle_policy.auto_restart_on_lost:
            return False
        if status.started or status.running:
            return False
        if self.lifecycle_policy.auto_restart_max_attempts <= 0:
            return False
        with self._lifecycle_lock:
            generation = self._terminal_start_generations.get(session_id, 0)
            if generation <= 0:
                return False
        try:
            start_spec = self.latest_start_spec(session_id=session_id)
        except AgentBridgeError:
            self._emit_auto_restart_skipped_once(
                session_id=session_id,
                generation=generation,
                command=None,
                reason="missing_start_command",
                trace_id=trace_id,
            )
            return False
        if not self.lifecycle_policy.allows_auto_restart_command(start_spec.command):
            self._emit_auto_restart_skipped_once(
                session_id=session_id,
                generation=generation,
                command=start_spec.command,
                reason="command_not_allowlisted",
                trace_id=trace_id,
            )
            return False
        with self._lifecycle_lock:
            attempts = self._auto_restart_attempts.get(session_id, 0)
            if attempts >= self.lifecycle_policy.auto_restart_max_attempts:
                return False
            self._auto_restart_attempts[session_id] = attempts + 1
        return True
