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
import sys
import termios
import time
import zlib
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import Event, RLock, Thread, current_thread
from typing import Any, Protocol
from uuid import uuid4

from agentbridge.agent_adapter_events import (
    AGENT_ADAPTER_HANDSHAKE_PROTOCOL,
    adapter_provider_version_verification,
    adapter_schema_version_supported,
    supported_adapter_schema_versions_for,
)
from agentbridge.claude_hook_deploy import (
    ClaudeHookDeploymentConfig,
    deploy_claude_hooks,
)
from agentbridge.codex_output import extract_codex_answer
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    Actor,
    AgentBridgeError,
    AgentType,
    ErrorCode,
    LeaseOwnerType,
    SemanticEventSource,
    SessionStatus,
    TurnStatus,
)


def _env_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def open_desktop_terminal(
    attach_command: str,
    *,
    preset: str = "auto",
    command_template: str | None = None,
) -> None:
    """打开一个可见的桌面终端窗口并在其中执行 attach 命令，让用户看到并接管会话。

    优先使用自定义模板（``{attach}`` 占位）；否则按 preset 选择：macOS 用 osascript 开
    Terminal.app，其它平台用常见的 ``<terminal> -e``。窗口里执行 attach（如
    ``tmux attach -t <name>``）即可看到 agent 并接管。"""
    if command_template:
        full = command_template.replace("{attach}", attach_command)
        subprocess.Popen(["/bin/sh", "-c", full])  # noqa: S603
        return
    resolved = preset
    if resolved in {"", "auto"}:
        resolved = "macos-terminal" if sys.platform == "darwin" else "xterm"
    if resolved == "macos-terminal":
        script = (
            'tell application "Terminal"\n'
            "    activate\n"
            f'    do script "{attach_command}"\n'
            "end tell"
        )
        subprocess.Popen(["osascript", "-e", script])  # noqa: S603,S607
    elif resolved == "iterm":
        script = (
            'tell application "iTerm"\n'
            "    create window with default profile\n"
            f'    tell current session of current window to write text "{attach_command}"\n'
            "end tell"
        )
        subprocess.Popen(["osascript", "-e", script])  # noqa: S603,S607
    else:
        subprocess.Popen([resolved, "-e", attach_command])  # noqa: S603


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


@dataclass(frozen=True)
class AgentAdapterHandshakeProbe:
    agent_type: AgentType
    adapter: str
    command: str | None
    source: str | None
    executable: str | None
    executable_path: str | None
    status: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int | None = None
    payload: dict[str, object] | None = None
    protocol: str | None = None
    schema_version: str | None = None
    capabilities: list[str] = field(default_factory=list)
    compatible: bool | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "agent_type": self.agent_type.value,
            "adapter": self.adapter,
            "command": self.command,
            "source": self.source,
            "executable": self.executable,
            "executable_path": self.executable_path,
            "status": self.status,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "payload": self.payload,
            "protocol": self.protocol,
            "schema_version": self.schema_version,
            "capabilities": self.capabilities,
            "compatible": self.compatible,
            "warnings": self.warnings,
            "error": self.error,
        }


@dataclass(frozen=True)
class AgentAdapterCapabilityReport:
    agent_type: AgentType
    adapter: str
    structured_adapter: bool
    launch_profile: AgentLaunchDetection
    version_probe: AgentLaunchVersionProbe
    handshake_probe: AgentAdapterHandshakeProbe
    status: str
    schema_gate: dict[str, object]
    capabilities: list[str]
    expected_capabilities: list[str]
    input_transports: list[str]
    recommended_mode: str
    next_step: str

    def to_payload(self) -> dict[str, object]:
        return {
            "agent_type": self.agent_type.value,
            "adapter": self.adapter,
            "structured_adapter": self.structured_adapter,
            "launch_profile": self.launch_profile.to_payload(),
            "version_probe": self.version_probe.to_payload(),
            "handshake_probe": self.handshake_probe.to_payload(),
            "status": self.status,
            "schema_gate": self.schema_gate,
            "capabilities": self.capabilities,
            "expected_capabilities": self.expected_capabilities,
            "input_transports": self.input_transports,
            "recommended_mode": self.recommended_mode,
            "next_step": self.next_step,
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

AGENT_ADAPTER_HANDSHAKE_COMMAND_ENV_BY_TYPE: dict[AgentType, str] = {
    AgentType.CLAUDE: "AGENTBRIDGE_AGENT_CLAUDE_HANDSHAKE_COMMAND",
    AgentType.CODEX: "AGENTBRIDGE_AGENT_CODEX_HANDSHAKE_COMMAND",
    AgentType.GENERIC_TUI: "AGENTBRIDGE_AGENT_GENERIC_TUI_HANDSHAKE_COMMAND",
}

DEFAULT_VERSION_PROBE_AGENT_TYPES = {AgentType.CLAUDE, AgentType.CODEX}
STRUCTURED_ADAPTER_AGENT_TYPES = {AgentType.CLAUDE, AgentType.CODEX}

# 各 agent 的「续上次对话」命令后缀：重启/重开终端时用它续接，而非另起新对话。
# Claude: `--continue` 续 cwd 内最近一次对话；Codex: `resume --last` 续最近一次会话。
AGENT_RESUME_SUFFIX_BY_TYPE: dict[AgentType, str] = {
    AgentType.CLAUDE: "--continue",
    AgentType.CODEX: "resume --last",
}


def resume_command_for(agent_type: AgentType, base_command: str) -> str:
    """在基础启动命令后拼接该 agent 的 resume 后缀；不支持 resume 的 agent 原样返回。"""
    suffix = AGENT_RESUME_SUFFIX_BY_TYPE.get(agent_type)
    base = base_command.strip()
    if not suffix or not base:
        return base_command
    return f"{base} {suffix}"

AGENT_ADAPTER_KIND_BY_TYPE: dict[AgentType, str] = {
    AgentType.CLAUDE: "claude_hooks",
    AgentType.CODEX: "codex_app_server",
    AgentType.GENERIC_TUI: "generic_tui",
}

AGENT_ADAPTER_EXPECTED_CAPABILITIES: dict[AgentType, list[str]] = {
    AgentType.CLAUDE: [
        "claude.hooks.session_start",
        "claude.hooks.message_display",
        "claude.hooks.permission_request",
        "claude.hooks.stop",
    ],
    AgentType.CODEX: [
        "codex.app_server.json_rpc",
        "codex.turn.start",
        "codex.approval.request",
        "codex.event_stream",
    ],
    AgentType.GENERIC_TUI: [
        "pty.input",
        "pty.output",
        "human_lease",
    ],
}

AGENT_ADAPTER_INPUT_TRANSPORTS: dict[AgentType, list[str]] = {
    AgentType.CLAUDE: ["pty_input_driver", "claude_channel_optional"],
    AgentType.CODEX: ["codex_app_server_turn_start", "codex_remote_tui"],
    AgentType.GENERIC_TUI: ["pty_input_driver"],
}

AGENT_ADAPTER_RECOMMENDED_MODE: dict[AgentType, str] = {
    AgentType.CLAUDE: "pty_with_claude_hooks",
    AgentType.CODEX: "app_server_with_remote_tui",
    AgentType.GENERIC_TUI: "pty_only",
}


@dataclass(frozen=True)
class AgentLaunchConfig:
    command_by_agent: dict[AgentType, str] = field(default_factory=dict)
    source_by_agent: dict[AgentType, str] = field(default_factory=dict)
    version_command_by_agent: dict[AgentType, str] = field(default_factory=dict)
    version_source_by_agent: dict[AgentType, str] = field(default_factory=dict)
    handshake_command_by_agent: dict[AgentType, str] = field(default_factory=dict)
    handshake_source_by_agent: dict[AgentType, str] = field(default_factory=dict)

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
        handshake_command_by_agent: dict[AgentType, str] = {}
        handshake_source_by_agent: dict[AgentType, str] = {}
        for agent_type in AgentType:
            env_name = AGENT_ADAPTER_HANDSHAKE_COMMAND_ENV_BY_TYPE[agent_type]
            configured_command = os.environ.get(env_name, "").strip()
            if configured_command:
                handshake_command_by_agent[agent_type] = configured_command
                handshake_source_by_agent[agent_type] = f"env:{env_name}"
        return cls(
            command_by_agent=command_by_agent,
            source_by_agent=source_by_agent,
            version_command_by_agent=version_command_by_agent,
            version_source_by_agent=version_source_by_agent,
            handshake_command_by_agent=handshake_command_by_agent,
            handshake_source_by_agent=handshake_source_by_agent,
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

    def handshake_command_for(self, agent_type: AgentType) -> tuple[str | None, str | None]:
        return (
            self.handshake_command_by_agent.get(agent_type),
            self.handshake_source_by_agent.get(agent_type),
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

    def probe_adapter_handshake(
        self,
        agent_type: AgentType,
        *,
        version_probe: AgentLaunchVersionProbe,
        timeout_seconds: float,
        output_limit_chars: int = 4096,
    ) -> AgentAdapterHandshakeProbe:
        adapter = AGENT_ADAPTER_KIND_BY_TYPE[agent_type]
        if agent_type not in STRUCTURED_ADAPTER_AGENT_TYPES:
            return AgentAdapterHandshakeProbe(
                agent_type=agent_type,
                adapter=adapter,
                command=None,
                source=None,
                executable=None,
                executable_path=None,
                status="skipped",
                error="structured_adapter_not_supported",
            )
        handshake_command, handshake_source = self.handshake_command_for(agent_type)
        if handshake_command is None:
            return AgentAdapterHandshakeProbe(
                agent_type=agent_type,
                adapter=adapter,
                command=None,
                source=None,
                executable=None,
                executable_path=None,
                status="skipped",
                error="handshake_command_not_configured",
            )
        try:
            handshake_argv = shlex.split(handshake_command)
        except ValueError as exc:
            return AgentAdapterHandshakeProbe(
                agent_type=agent_type,
                adapter=adapter,
                command=handshake_command,
                source=handshake_source,
                executable=None,
                executable_path=None,
                status="parse_error",
                error=str(exc),
            )
        if not handshake_argv:
            return AgentAdapterHandshakeProbe(
                agent_type=agent_type,
                adapter=adapter,
                command=handshake_command,
                source=handshake_source,
                executable=None,
                executable_path=None,
                status="parse_error",
                error="empty_handshake_command",
            )
        executable = handshake_argv[0]
        executable_path = self._resolve_executable_path(executable)
        if executable_path is None:
            return AgentAdapterHandshakeProbe(
                agent_type=agent_type,
                adapter=adapter,
                command=handshake_command,
                source=handshake_source,
                executable=executable,
                executable_path=None,
                status="unavailable",
                error="handshake_executable_not_found",
            )
        launch_detection = self.detect(agent_type)
        env = {
            **os.environ,
            "AGENTBRIDGE_AGENT_TYPE": agent_type.value,
            "AGENTBRIDGE_AGENT_ADAPTER": adapter,
            "AGENTBRIDGE_AGENT_LAUNCH_COMMAND": launch_detection.command,
            "AGENTBRIDGE_AGENT_LAUNCH_EXECUTABLE_PATH": (
                launch_detection.executable_path or ""
            ),
            "AGENTBRIDGE_AGENT_VERSION_TEXT": version_probe.version_text or "",
            "AGENTBRIDGE_AGENT_VERSION_STATUS": version_probe.status,
            "AGENTBRIDGE_AGENT_EXECUTABLE_PATH": launch_detection.executable_path
            or "",
            "AGENTBRIDGE_AGENT_VERSION_EXECUTABLE_PATH": (
                version_probe.version_executable_path or ""
            ),
            "AGENTBRIDGE_ADAPTER_HANDSHAKE_PROTOCOL": AGENT_ADAPTER_HANDSHAKE_PROTOCOL,
        }
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [executable_path, *handshake_argv[1:]],
                capture_output=True,
                check=False,
                env=env,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return AgentAdapterHandshakeProbe(
                agent_type=agent_type,
                adapter=adapter,
                command=handshake_command,
                source=handshake_source,
                executable=executable,
                executable_path=executable_path,
                status="timeout",
                stdout=self._truncate_probe_output(exc.stdout, output_limit_chars),
                stderr=self._truncate_probe_output(exc.stderr, output_limit_chars),
                duration_ms=duration_ms,
                error=f"timed out after {timeout_seconds:.3g}s",
            )
        except OSError as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            return AgentAdapterHandshakeProbe(
                agent_type=agent_type,
                adapter=adapter,
                command=handshake_command,
                source=handshake_source,
                executable=executable,
                executable_path=executable_path,
                status="failed",
                duration_ms=duration_ms,
                error=str(exc),
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = self._truncate_probe_output(completed.stdout, output_limit_chars)
        stderr = self._truncate_probe_output(completed.stderr, output_limit_chars)
        if completed.returncode != 0:
            return AgentAdapterHandshakeProbe(
                agent_type=agent_type,
                adapter=adapter,
                command=handshake_command,
                source=handshake_source,
                executable=executable,
                executable_path=executable_path,
                status="failed",
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
            )
        try:
            parsed_payload = json.loads(completed.stdout or "{}")
            handshake = self._normalize_handshake_payload(parsed_payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return AgentAdapterHandshakeProbe(
                agent_type=agent_type,
                adapter=adapter,
                command=handshake_command,
                source=handshake_source,
                executable=executable,
                executable_path=executable_path,
                status="parse_error",
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                error=str(exc),
            )
        status = "ok"
        error = None
        if handshake["protocol"] != AGENT_ADAPTER_HANDSHAKE_PROTOCOL:
            status = "schema_mismatch"
            error = "adapter_protocol_mismatch"
        elif handshake["compatible"] is False:
            status = "incompatible"
            error = "adapter_reported_incompatible"
        return AgentAdapterHandshakeProbe(
            agent_type=agent_type,
            adapter=adapter,
            command=handshake_command,
            source=handshake_source,
            executable=executable,
            executable_path=executable_path,
            status=status,
            exit_code=completed.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            payload=handshake["payload"],
            protocol=handshake["protocol"],
            schema_version=handshake["schema_version"],
            capabilities=handshake["capabilities"],
            compatible=handshake["compatible"],
            warnings=handshake["warnings"],
            error=error,
        )

    def adapter_capability_report(
        self,
        agent_type: AgentType,
        *,
        timeout_seconds: float,
        output_limit_chars: int = 4096,
    ) -> AgentAdapterCapabilityReport:
        launch_profile = self.detect(agent_type)
        version_probe = self.probe_version(
            agent_type,
            timeout_seconds=timeout_seconds,
            output_limit_chars=output_limit_chars,
        )
        handshake_probe = self.probe_adapter_handshake(
            agent_type,
            version_probe=version_probe,
            timeout_seconds=timeout_seconds,
            output_limit_chars=output_limit_chars,
        )
        schema_gate = self._adapter_schema_gate(
            agent_type,
            launch_profile=launch_profile,
            version_probe=version_probe,
            handshake_probe=handshake_probe,
        )
        return AgentAdapterCapabilityReport(
            agent_type=agent_type,
            adapter=AGENT_ADAPTER_KIND_BY_TYPE[agent_type],
            structured_adapter=agent_type in STRUCTURED_ADAPTER_AGENT_TYPES,
            launch_profile=launch_profile,
            version_probe=version_probe,
            handshake_probe=handshake_probe,
            status=str(schema_gate["status"]),
            schema_gate=schema_gate,
            capabilities=handshake_probe.capabilities,
            expected_capabilities=AGENT_ADAPTER_EXPECTED_CAPABILITIES[agent_type],
            input_transports=AGENT_ADAPTER_INPUT_TRANSPORTS[agent_type],
            recommended_mode=AGENT_ADAPTER_RECOMMENDED_MODE[agent_type],
            next_step=str(schema_gate["next_step"]),
        )

    @classmethod
    def _adapter_schema_gate(
        cls,
        agent_type: AgentType,
        *,
        launch_profile: AgentLaunchDetection,
        version_probe: AgentLaunchVersionProbe,
        handshake_probe: AgentAdapterHandshakeProbe,
    ) -> dict[str, object]:
        if agent_type not in STRUCTURED_ADAPTER_AGENT_TYPES:
            return {
                "status": "pty_only",
                "reason": "generic_tui_has_no_structured_adapter",
                "required_protocol": None,
                "supported_schema_versions": [],
                "next_step": (
                    "Use PTY input/output only; structured approvals require "
                    "a native adapter."
                ),
            }
        supported_schema_versions = sorted(supported_adapter_schema_versions_for(agent_type))
        if not launch_profile.available:
            return {
                "status": "launch_unavailable",
                "reason": launch_profile.unavailable_reason,
                "required_protocol": AGENT_ADAPTER_HANDSHAKE_PROTOCOL,
                "supported_schema_versions": supported_schema_versions,
                "next_step": "Install the agent CLI or configure its launch command.",
            }
        if version_probe.status != "ok":
            return {
                "status": "version_probe_failed",
                "reason": version_probe.error or version_probe.status,
                "required_protocol": AGENT_ADAPTER_HANDSHAKE_PROTOCOL,
                "supported_schema_versions": supported_schema_versions,
                "next_step": "Fix the version probe before enabling structured adapter APIs.",
            }
        if handshake_probe.status == "ok":
            provider_version_verification = adapter_provider_version_verification(
                agent_type=agent_type,
                schema_version=handshake_probe.schema_version,
                provider_version_text=version_probe.version_text,
            )
            if not adapter_schema_version_supported(
                agent_type=agent_type,
                schema_version=handshake_probe.schema_version,
            ):
                return {
                    "status": "schema_mismatch",
                    "reason": "adapter_schema_version_unsupported",
                    "required_protocol": AGENT_ADAPTER_HANDSHAKE_PROTOCOL,
                    "schema_version": handshake_probe.schema_version,
                    "supported_schema_versions": supported_schema_versions,
                    "provider_version_verification": provider_version_verification,
                    "next_step": "Use a schema version listed in the compatibility matrix.",
                }
            return {
                "status": "ready",
                "reason": "handshake_accepted",
                "required_protocol": AGENT_ADAPTER_HANDSHAKE_PROTOCOL,
                "schema_version": handshake_probe.schema_version,
                "supported_schema_versions": supported_schema_versions,
                "provider_version_verification": provider_version_verification,
                "next_step": "Structured adapter capability gate is open for this profile.",
            }
        if handshake_probe.status == "skipped":
            return {
                "status": "handshake_not_configured",
                "reason": handshake_probe.error,
                "required_protocol": AGENT_ADAPTER_HANDSHAKE_PROTOCOL,
                "supported_schema_versions": supported_schema_versions,
                "next_step": (
                    "Configure an adapter handshake command before enabling "
                    "structured events."
                ),
            }
        return {
            "status": handshake_probe.status,
            "reason": handshake_probe.error or handshake_probe.status,
            "required_protocol": AGENT_ADAPTER_HANDSHAKE_PROTOCOL,
            "schema_version": handshake_probe.schema_version,
            "supported_schema_versions": supported_schema_versions,
            "next_step": "Review adapter handshake output and schema compatibility.",
        }

    @staticmethod
    def _normalize_handshake_payload(value: Any) -> dict[str, object]:
        if not isinstance(value, dict):
            raise ValueError("handshake payload must be a JSON object")
        payload = {str(key): item for key, item in value.items()}
        protocol_value = (
            payload.get("protocol")
            or payload.get("adapter_protocol")
            or payload.get("adapter_protocol_version")
        )
        protocol = str(protocol_value) if protocol_value is not None else None
        schema_version_value = payload.get("schema_version")
        schema_version = (
            str(schema_version_value) if schema_version_value is not None else None
        )
        capabilities_value = payload.get("capabilities") or []
        if not isinstance(capabilities_value, list) or not all(
            isinstance(item, str) for item in capabilities_value
        ):
            raise ValueError("capabilities must be a string array")
        compatible_value = payload.get("compatible", True)
        if not isinstance(compatible_value, bool):
            raise ValueError("compatible must be a boolean")
        warnings_value = payload.get("warnings") or []
        if not isinstance(warnings_value, list) or not all(
            isinstance(item, str) for item in warnings_value
        ):
            raise ValueError("warnings must be a string array")
        return {
            "payload": payload,
            "protocol": protocol,
            "schema_version": schema_version,
            "capabilities": capabilities_value,
            "compatible": compatible_value,
            "warnings": warnings_value,
        }

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


def is_transient_terminal_event_error(exc: Exception) -> bool:
    if isinstance(exc, AgentBridgeError):
        return exc.status_code in {408, 425, 429} or exc.status_code >= 500
    return isinstance(exc, (TimeoutError, ConnectionError, OSError))


class TerminalEventOutbox:
    schema_version = "agentbridge.terminal_event_outbox.v1"

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, request_payload: Mapping[str, object]) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "schema_version": self.schema_version,
            "payload": dict(request_payload),
            "enqueued_at_monotonic": time.monotonic(),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return len(self.read_entries())

    def flush(
        self,
        sender: Callable[[dict[str, object]], object],
        is_transient_error: Callable[[Exception], bool],
    ) -> int:
        entries = self.read_entries()
        sent = 0
        for index, entry in enumerate(entries):
            payload = entry["payload"]
            try:
                sender(payload)
            except Exception as exc:
                if is_transient_error(exc):
                    self.replace_entries(entries[index:])
                    raise
                raise
            sent += 1
        self.replace_entries([])
        return sent

    def read_entries(self) -> list[dict[str, dict[str, object]]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, dict[str, object]]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    decoded = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"terminal event outbox line {line_number} is not valid JSON"
                    ) from exc
                if not isinstance(decoded, dict):
                    raise ValueError(
                        f"terminal event outbox line {line_number} must be an object"
                    )
                payload = decoded.get("payload")
                if not isinstance(payload, dict):
                    raise ValueError(
                        f"terminal event outbox line {line_number} must include payload"
                    )
                entries.append({"payload": {str(key): value for key, value in payload.items()}})
        return entries

    def replace_entries(self, entries: list[dict[str, dict[str, object]]]) -> None:
        if not entries:
            with suppress(FileNotFoundError):
                self.path.unlink()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(
                    json.dumps(
                        {
                            "schema_version": self.schema_version,
                            "payload": entry["payload"],
                            "enqueued_at_monotonic": time.monotonic(),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
        os.replace(tmp_path, self.path)


class TerminalBackend(Protocol):
    def start(self, *, session_id: str, cwd: str, command: str) -> None: ...

    def write(self, *, session_id: str, data: str, kind: TerminalInputKind) -> None: ...

    def signal(self, *, session_id: str, name: str) -> None: ...

    def resize(self, *, session_id: str, cols: int, rows: int) -> None: ...

    def snapshot(self, *, session_id: str) -> str: ...

    def read_output(self, *, session_id: str, after_cursor: int = 0) -> TerminalOutputChunk: ...

    def status(self, *, session_id: str) -> TerminalStatus: ...


DEFAULT_PTY_OUTPUT_LIMIT_CHARS = 1_000_000
# TUI 类 agent（如 Codex 的 ratatui 界面）在 0×0 终端下无法渲染；给新 PTY 一个合理的初始尺寸。
DEFAULT_PTY_COLS = int(os.environ.get("AGENTBRIDGE_TERMINAL_PTY_COLS", "120") or 120)
DEFAULT_PTY_ROWS = int(os.environ.get("AGENTBRIDGE_TERMINAL_PTY_ROWS", "40") or 40)


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

    def stop(self, *, session_id: str) -> None:
        self.started.pop(session_id, None)

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
        name = self._tmux_name(session_id)
        if not self._has_session(name):
            return TerminalStatus(started=False, running=False)
        # tmux 没有累积输出游标；用可见内容的校验和近似：内容变化（TUI 工作/动画）则游标变化，
        # 内容稳定（回到提示符空闲）则游标不变，从而让空闲完成启发式可用。
        snapshot = self.snapshot(session_id=session_id)
        cursor = zlib.crc32(snapshot.encode("utf-8", "replace"))
        return TerminalStatus(
            started=True,
            running=True,
            output_cursor=cursor,
            output_retained_chars=len(snapshot),
        )

    def stop(self, *, session_id: str) -> None:
        """杀掉该会话的 tmux 会话（连同里面的 agent 进程）。会话不存在则静默返回。"""
        name = self._tmux_name(session_id)
        if not self._has_session(name):
            return
        with suppress(AgentBridgeError):
            self._run(["kill-session", "-t", name])

    def is_attached(self, *, session_id: str) -> bool:
        """该 tmux 会话当前是否有客户端 attach（即是否有可见窗口连着它）。"""
        name = self._tmux_name(session_id)
        if not self._has_session(name):
            return False
        try:
            result = self._run(
                ["display-message", "-p", "-t", name, "#{session_attached}"]
            )
        except AgentBridgeError:
            return False
        return result.stdout.strip() not in ("", "0")

    def attach_command(self, *, session_id: str) -> str:
        """返回一条可在桌面终端里执行、用于 attach 到该会话 tmux 的命令（供可见窗口接管）。
        用 tmux 的绝对路径，避免新开终端窗口的 PATH 里找不到 tmux。"""
        name = self._tmux_name(session_id)
        executable = shutil.which(self.executable) or self.executable
        return f"{shlex.quote(executable)} attach -t {shlex.quote(name)}"

    def pane_title(self, *, session_id: str) -> str | None:
        """读取该 tmux 会话当前窗格标题（即 agent 通过终端标题转义设置的内容）。"""
        name = self._tmux_name(session_id)
        if not self._has_session(name):
            return None
        result = subprocess.run(
            [self.executable, "display-message", "-p", "-t", name, "#{pane_title}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        title = result.stdout.strip()
        return title or None

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
            # 给 PTY 一个非零初始窗口大小，否则 TUI agent 在 0×0 终端下无法渲染。
            with suppress(OSError):
                winsize = struct.pack("HHHH", DEFAULT_PTY_ROWS, DEFAULT_PTY_COLS, 0, 0)
                fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
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
        event_outbox_path: Path | None = None,
        auto_advance_queues: bool | None = None,
        idle_turn_completion: bool | None = None,
        idle_completion_agent_types: set[AgentType] | None = None,
        idle_complete_seconds: float | None = None,
        idle_min_active_seconds: float | None = None,
        submit_warmup_seconds: float | None = None,
        claude_hook_deploy: ClaudeHookDeploymentConfig | None = None,
        auto_open_terminal: bool | None = None,
        terminal_open_preset: str | None = None,
        terminal_open_command: str | None = None,
        terminal_opener: Callable[[str], None] | None = None,
    ) -> None:
        self.control = control
        self.backend = backend or FakeTerminalBackend()
        # Claude 会话启动前把 Hooks 部署进工作区，让语义通道通电（默认从环境读取、默认关闭）。
        self.claude_hook_deploy = claude_hook_deploy or ClaudeHookDeploymentConfig.from_env()
        self._claude_hook_deploy_last_error: str | None = None
        # 开启后，生命周期监控每一拍都会为空闲、有排队任务的会话接力提交下一轮，
        # 从而把"持久会话连续多轮执行"自动化（默认关闭，保持既有行为；可经环境变量打开）。
        self.auto_advance_queues = (
            auto_advance_queues
            if auto_advance_queues is not None
            else _env_truthy(os.environ.get("AGENTBRIDGE_TERMINAL_AUTO_ADVANCE_QUEUES"))
        )
        # 统一 TUI+PTY 模型下，没有结构化完成事件的 agent（Codex / 通用终端）用
        # "PTY 输出静默 N 秒"启发式判定一轮结束；Claude 走 Stop hook，不在此集合内。
        self.idle_turn_completion = (
            idle_turn_completion
            if idle_turn_completion is not None
            else _env_truthy(os.environ.get("AGENTBRIDGE_TERMINAL_IDLE_TURN_COMPLETION"))
        )
        self.idle_completion_agent_types = (
            idle_completion_agent_types
            if idle_completion_agent_types is not None
            else {AgentType.CODEX, AgentType.GENERIC_TUI}
        )
        self.idle_complete_seconds = (
            idle_complete_seconds
            if idle_complete_seconds is not None
            else float(os.environ.get("AGENTBRIDGE_CODEX_IDLE_COMPLETE_SECONDS", "8") or 8)
        )
        self.idle_min_active_seconds = (
            idle_min_active_seconds
            if idle_min_active_seconds is not None
            else float(os.environ.get("AGENTBRIDGE_CODEX_IDLE_MIN_ACTIVE_SECONDS", "2") or 2)
        )
        self._turn_idle_watch: dict[str, dict[str, object]] = {}
        # 终端刚启动时原生 TUI 还在初始化，过早写入会丢键/不被当作回车；推进器在终端
        # 启动后先等待这段预热时间再提交任务（默认 0，生产经环境变量设为数秒）。
        self.submit_warmup_seconds = (
            submit_warmup_seconds
            if submit_warmup_seconds is not None
            else float(os.environ.get("AGENTBRIDGE_TERMINAL_SUBMIT_WARMUP_SECONDS", "0") or 0)
        )
        self._terminal_started_monotonic: dict[str, float] = {}
        # 会话启动后自动打开一个可见的桌面终端窗口 attach 到该会话，满足"电脑上看得见 + 可接管"。
        self.auto_open_terminal = (
            auto_open_terminal
            if auto_open_terminal is not None
            else _env_truthy(os.environ.get("AGENTBRIDGE_TERMINAL_AUTO_OPEN"))
        )
        self.terminal_open_preset = (
            terminal_open_preset
            if terminal_open_preset is not None
            else os.environ.get("AGENTBRIDGE_TERMINAL_OPEN_PRESET", "auto")
        )
        self.terminal_open_command = (
            terminal_open_command
            if terminal_open_command is not None
            else os.environ.get("AGENTBRIDGE_TERMINAL_OPEN_COMMAND")
        )
        self._terminal_opener = terminal_opener
        self._opened_visible_terminals: set[str] = set()
        self._terminal_open_last_error: str | None = None
        self.lifecycle_policy = lifecycle_policy or TerminalLifecyclePolicy()
        self.agent_launch_config = agent_launch_config or AgentLaunchConfig.from_env()
        self.event_outbox = (
            TerminalEventOutbox(event_outbox_path) if event_outbox_path is not None else None
        )
        self._terminal_start_generations: dict[str, int] = {}
        self._reported_terminal_exits: set[tuple[str, int]] = set()
        self._reported_terminal_losses: set[tuple[str, int]] = set()
        self._reported_auto_restart_blocks: set[tuple[str, int, str]] = set()
        self._auto_restart_attempts: dict[str, int] = {}
        self._auto_restart_last_block_reason: str | None = None
        self._terminal_event_outbox_last_flush_count = 0
        self._terminal_event_outbox_last_flush_error: str | None = None
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
        open_window: bool | None = None,
    ) -> int:
        session = self.control.repository.get_session(session_id)
        launch_profile = self._resolve_start_profile(session, command)
        workspace = self.control.repository.get_workspace(session.workspace_id)
        if self.claude_hook_deploy.enabled and session.agent_type == AgentType.CLAUDE:
            try:
                deploy_claude_hooks(
                    session_id=session_id,
                    workspace_path=workspace.path,
                    config=self.claude_hook_deploy,
                )
                self._claude_hook_deploy_last_error = None
            except Exception as exc:  # 部署是尽力而为，不应阻断终端启动。
                self._claude_hook_deploy_last_error = str(exc)
        self.backend.start(
            session_id=session_id,
            cwd=workspace.path,
            command=launch_profile.command,
        )
        self._terminal_started_monotonic[session_id] = time.monotonic()
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
        self._emit_terminal_event(
            event_type="terminal.started",
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            payload=payload,
            idempotency_key=f"terminal-started:{session_id}:{generation}",
        )
        self._disable_terminal_offline_protection_if_needed(
            session_id=session_id,
            trace_id=f"{trace_id}:offline-protection",
        )
        # open_window: None=自动（受 auto_open 与每会话一次限制）；True=强制开窗；False=不开窗。
        if open_window is True:
            self.open_terminal_window(session_id, force=True)
        elif open_window is None:
            self._maybe_open_visible_terminal(session_id)
        return generation

    def _maybe_open_visible_terminal(self, session_id: str) -> None:
        """会话启动后自动打开可见桌面终端（仅当 auto_open 开启且该会话尚未开过窗）。"""
        if not self.auto_open_terminal:
            return
        self.open_terminal_window(session_id, force=False)

    def open_terminal_window(self, session_id: str, *, force: bool = True) -> bool:
        """打开（或重开）一个 attach 到该会话 tmux 的可见桌面窗口。

        force=False 时每会话只开一次（供 auto_open 自动开窗）；force=True 时无视一次性限制，
        用于控制台「打开/重开终端」这类显式动作——重新 attach 到仍存活的 tmux 即等于 resume。
        返回是否成功打开。后端不支持 attach（如 PTY/fake）或开窗失败则返回 False。
        """
        if not force and session_id in self._opened_visible_terminals:
            return False
        attach_command = getattr(self.backend, "attach_command", None)
        if attach_command is None:
            self._terminal_open_last_error = "backend_attach_unsupported"
            return False
        command = attach_command(session_id=session_id)
        if not command:
            return False
        try:
            if self._terminal_opener is not None:
                self._terminal_opener(command)
            else:
                open_desktop_terminal(
                    command,
                    preset=self.terminal_open_preset,
                    command_template=self.terminal_open_command,
                )
            self._opened_visible_terminals.add(session_id)
            self._terminal_open_last_error = None
            return True
        except Exception as exc:  # 打开窗口失败不应阻断会话启动。
            self._terminal_open_last_error = str(exc)
            return False

    def ensure_visible_window(self, session_id: str) -> bool:
        """确保该会话当前有可见窗口在 attach：若用户关掉了窗口（tmux 仍在跑但无人 attach），
        重新开一个窗口 attach 上去；已有窗口则不重复开。供「关窗后重新提问应自动再开窗」。"""
        if not self.auto_open_terminal:
            return False
        is_attached = getattr(self.backend, "is_attached", None)
        if is_attached is not None:
            try:
                if is_attached(session_id=session_id):
                    return False  # 已有窗口 attach，无需重开。
            except Exception:
                pass
        return self.open_terminal_window(session_id, force=True)

    def stop_session(
        self,
        *,
        session_id: str,
        trace_id: str,
        reason: str = "manual_stop",
    ) -> dict[str, object]:
        """停止会话终端：杀掉后端（tmux/PTY）进程并清理可见窗口标记。

        状态由后端直接反映（tmux 会话不在 = 未运行），故无需额外发事件；生命周期监控下一拍
        会补 ``terminal.exited``。返回操作摘要。
        """
        self.control.repository.get_session(session_id)
        stopper = getattr(self.backend, "stop", None) or getattr(
            self.backend, "terminate", None
        )
        stopped = False
        if stopper is not None:
            try:
                try:
                    stopper(session_id=session_id)
                except TypeError:
                    stopper(session_id)
                stopped = True
            except Exception as exc:
                self._terminal_open_last_error = str(exc)
        self._opened_visible_terminals.discard(session_id)
        self._terminal_started_monotonic.pop(session_id, None)
        return {"stopped": stopped, "reason": reason}

    def _resume_start_command(self, session_id: str) -> str:
        """重启/重开时的启动命令：会话此前真正启动过则用 resume 命令续上次对话，否则用基础命令。"""
        session = self.control.repository.get_session(session_id)
        base = self.agent_launch_config.profile_for(session.agent_type).command
        started_before = any(
            event.type == "terminal.started"
            for event in self.control.repository.list_events(
                session_id=session_id, limit=1_000_000
            )
        )
        if not started_before:
            return base
        return resume_command_for(session.agent_type, base)

    def open_or_resume_terminal(
        self, *, session_id: str, trace_id: str
    ) -> dict[str, object]:
        """控制台「打开终端」：tmux 仍在则重新 attach 开窗（= resume）；已停则带 resume 命令拉起 agent 再开窗。"""
        status = self.status(session_id=session_id, trace_id=trace_id)
        if status.started and status.running:
            opened = self.open_terminal_window(session_id, force=True)
            return {"action": "reattached", "opened": opened, "running": True}
        command = self._resume_start_command(session_id)
        generation = self.start_session(
            session_id=session_id,
            command=command,
            trace_id=trace_id,
            restart_reason="console_open",
            open_window=True,
        )
        return {
            "action": "started",
            "running": True,
            "generation": generation,
            "command": command,
        }

    def restart_terminal_with_resume(
        self, *, session_id: str, trace_id: str
    ) -> dict[str, object]:
        """控制台「重启终端」：先杀掉现有 tmux+agent，再带 resume 命令重新拉起并强制开窗。"""
        self.stop_session(
            session_id=session_id, trace_id=trace_id, reason="console_restart"
        )
        command = self._resume_start_command(session_id)
        generation = self.start_session(
            session_id=session_id,
            command=command,
            trace_id=trace_id,
            restart_reason="console_restart",
            open_window=True,
        )
        return {"action": "restarted", "generation": generation, "command": command}

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

    def detect_agent_adapter_capabilities(
        self,
        *,
        agent_types: list[AgentType] | None = None,
        timeout_seconds: float = 2.0,
    ) -> dict[str, dict[str, object]]:
        selected_agent_types = agent_types or list(AgentType)
        timeout = min(max(timeout_seconds, 0.1), 10.0)
        return {
            agent_type.value: self.agent_launch_config.adapter_capability_report(
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

    def claim_next_turn(
        self,
        *,
        actor: Actor,
        session_id: str,
        trace_id: str,
        expected_queue_version: str | None = None,
        submit_prompt: bool = False,
        owner_type: LeaseOwnerType = LeaseOwnerType.BOT,
        owner_id: str = "terminal-agent",
        ttl_seconds: int = 300,
        request_id: str | None = None,
        append_newline: bool = True,
        submit_newline: str = "\n",
    ) -> dict[str, object]:
        lease = None
        if submit_prompt:
            terminal_status = self.status(
                session_id=session_id,
                trace_id=f"{trace_id}:pre-submit-status",
            )
            if not terminal_status.started or not terminal_status.running:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    "终端会话尚未运行，不能提交 queued Turn。",
                    next_step="请先启动或恢复终端后再领取并提交 Turn。",
                    status_code=409,
                    details={
                        "session_id": session_id,
                        "started": terminal_status.started,
                        "running": terminal_status.running,
                    },
                )
            lease = self.control.acquire_lease(
                actor=actor,
                session_id=session_id,
                owner_type=owner_type,
                owner_id=owner_id,
                ttl_seconds=ttl_seconds,
                trace_id=f"{trace_id}:lease",
            )
        turn, queue_version = self.control.claim_next_turn(
            actor=actor,
            session_id=session_id,
            trace_id=trace_id,
            expected_queue_version=expected_queue_version,
        )
        submitted_request_id = None
        if submit_prompt and turn is not None and lease is not None:
            prompt = turn.prompt
            if append_newline and not prompt.endswith(submit_newline):
                prompt += submit_newline
            submitted_request_id = self.submit_input(
                session_id=session_id,
                epoch=lease.epoch,
                owner_type=lease.owner_type,
                owner_id=lease.owner_id,
                kind=TerminalInputKind.TEXT,
                data=prompt,
                trace_id=f"{trace_id}:input",
                request_id=request_id or f"turn-input:{turn.id}",
            )
        return {
            "queue_version": queue_version,
            "turn": turn.model_dump(mode="json") if turn else None,
            "lease": lease.model_dump(mode="json") if lease else None,
            "request_id": submitted_request_id,
        }

    def check_idle_turn_completions(
        self,
        *,
        now: float | None = None,
        trace_id: str = "turn-idle",
    ) -> list[dict[str, object]]:
        """对 hook-less agent（Codex / 通用终端）的运行中 Turn 做空闲完成判定。

        提交一轮后跟踪 PTY 输出游标：先记录基线，之后每拍比较；游标增长则刷新"最后
        变化"时间，连续静默 >= idle_complete_seconds 且距首次观察 >= idle_min_active_seconds
        时，落 turn.completed（幂等键 turn-idle-complete:<turn_id>）→ finish_turn → 会话回
        IDLE，交由 advance_queue 接力下一轮。Claude 等有结构化完成事件的 agent 不在此列。"""
        current = time.monotonic() if now is None else now
        completed: list[dict[str, object]] = []
        for session in self.control.repository.list_sessions():
            try:
                outcome = self._check_session_idle_completion(
                    session_id=session.id, now=current, trace_id=trace_id
                )
            except AgentBridgeError:
                outcome = None
            if outcome is not None:
                completed.append(outcome)
        return completed

    def _check_session_idle_completion(
        self,
        *,
        session_id: str,
        now: float,
        trace_id: str,
    ) -> dict[str, object] | None:
        session = self.control.repository.get_session(session_id)
        if session.agent_type not in self.idle_completion_agent_types:
            return None
        turn_id = session.active_turn_id
        if turn_id is None:
            self._turn_idle_watch.pop(session_id, None)
            return None
        turn = self.control.repository.get_turn(turn_id)
        if turn.status != TurnStatus.RUNNING:
            return None
        status = self.backend.status(session_id=session_id)
        if not status.started or not status.running:
            return None
        cursor = status.output_cursor
        watch = self._turn_idle_watch.get(session_id)
        if watch is None or watch.get("turn_id") != turn_id:
            self._turn_idle_watch[session_id] = {
                "turn_id": turn_id,
                "cursor": cursor,
                "last_change": now,
                "first_seen": now,
            }
            return None
        if cursor != watch.get("cursor"):
            watch["cursor"] = cursor
            watch["last_change"] = now
            return None
        idle_for = now - float(watch["last_change"])
        active_for = now - float(watch["first_seen"])
        if idle_for < self.idle_complete_seconds or active_for < self.idle_min_active_seconds:
            return None
        # 没有结构化输出的 agent（如 Codex）：从原生 TUI 抽取真实回答，作为 assistant.delta 先
        # 投递，再发 turn.completed——这样群里能看到真实答案而不是占位完成提示。
        answer = self._extract_terminal_answer(session_id, session.agent_type)
        if answer:
            self.control.ingest_session_event(
                session_id=session_id,
                event_type="assistant.delta",
                source=SemanticEventSource.TERMINAL_AGENT,
                trace_id=f"{trace_id}:idle-answer",
                turn_id=turn_id,
                idempotency_key=f"turn-idle-answer:{turn_id}",
                payload={"text": answer, "extracted_from": "terminal"},
            )
        self.control.ingest_session_event(
            session_id=session_id,
            event_type="turn.completed",
            source=SemanticEventSource.TERMINAL_AGENT,
            trace_id=f"{trace_id}:idle-complete",
            turn_id=turn_id,
            idempotency_key=f"turn-idle-complete:{turn_id}",
            payload={"completion": "idle_heuristic", "idle_seconds": round(idle_for, 3)},
        )
        self._turn_idle_watch.pop(session_id, None)
        return {"session_id": session_id, "turn_id": turn_id, "idle_seconds": idle_for}

    def _extract_terminal_answer(self, session_id: str, agent_type: AgentType) -> str:
        """从原生 TUI 抽取最后一段回答（目前支持 Codex）。失败返回空串。"""
        if agent_type != AgentType.CODEX:
            return ""
        try:
            snapshot = self.backend.snapshot(session_id=session_id)
        except Exception:
            return ""
        return extract_codex_answer(snapshot)

    @staticmethod
    def _advance_skip(session_id: str, reason: str) -> dict[str, object]:
        return {
            "session_id": session_id,
            "action": "skipped",
            "reason": reason,
            "turn_id": None,
            "request_id": None,
        }

    def advance_queue(
        self,
        *,
        session_id: str,
        actor: Actor | None = None,
        trace_id: str = "turn-runner",
        auto_start_terminal: bool = True,
        lease_ttl_seconds: int = 600,
    ) -> dict[str, object]:
        """持久会话推进器：当会话空闲、终端在跑、队列有待办且不是本地人工接管时，
        领取下一条排队任务并写入原生 TUI；否则返回 skipped 及原因。

        持久交互进程不会每轮退出，所以"上一轮完成"由语义通道（Claude Stop hook /
        Codex 空闲启发式）落成 turn.completed → finish_turn，把会话重新置为 IDLE，
        再由本方法接力提交下一轮，从而实现连续多轮执行。"""
        runner_actor = actor or Actor(id="system:turn-runner", roles={"maintainer"})
        session = self.control.repository.get_session(session_id)
        if session.status in {
            SessionStatus.CLOSED,
            SessionStatus.CLOSING,
            SessionStatus.ARCHIVED,
            SessionStatus.RECOVERING,
            SessionStatus.HUMAN_CONTROLLED,
            SessionStatus.ERROR,
        }:
            return self._advance_skip(session_id, f"session_status={session.status.value}")
        if session.active_turn_id is not None:
            # 自愈：挂着 active_turn 但终端已死（僵尸 turn）→ 当场让该 turn 失败、释放会话，
            # 不依赖"退出事件是否曾上报"，从而也能修复进程重启前遗留的僵尸会话。
            active_status = self.status(
                session_id=session_id, trace_id=f"{trace_id}:zombie-check"
            )
            if active_status.started and active_status.running:
                return self._advance_skip(session_id, "turn_active")
            self._fail_active_turn_after_terminal_loss(
                session_id=session_id,
                reason="terminal_not_running",
                trace_id=trace_id,
            )
            session = self.control.repository.get_session(session_id)
            if session.active_turn_id is not None:
                return self._advance_skip(session_id, "turn_active")
        if session.queue_paused:
            return self._advance_skip(session_id, "queue_paused")
        # 本地用户接管时，机器人退为观察者，绝不抢输入。
        lease = self.control.repository.current_lease(session_id)
        if lease is not None and lease.owner_type == LeaseOwnerType.HUMAN:
            return self._advance_skip(session_id, "human_control")
        turns, _queue_version, _queue_paused = self.control.list_turn_queue(
            actor=runner_actor,
            session_id=session_id,
        )
        if not turns:
            return self._advance_skip(session_id, "queue_empty")
        started_terminal = False
        status = self.status(session_id=session_id, trace_id=f"{trace_id}:status")
        if not status.started or not status.running:
            if not auto_start_terminal:
                return self._advance_skip(session_id, "terminal_not_running")
            self.start_session(session_id=session_id, trace_id=f"{trace_id}:start")
            started_terminal = True
        else:
            # 终端本就在跑（如用户关掉窗口后重新提问）：若当前没有可见窗口 attach，则重新开窗，
            # 让本轮任务有终端可见。start_session 路径已自带开窗，故仅在「已在跑」分支补这一步。
            self.ensure_visible_window(session_id)
        # 原生 TUI 启动后需要预热时间才能正确接收输入；预热未满时本轮先不提交，
        # 由后续监控拍提交（避免把任务打进尚未就绪的 TUI 而丢键/不提交）。
        started_at = self._terminal_started_monotonic.get(session_id)
        if (
            self.submit_warmup_seconds > 0
            and started_at is not None
            and (time.monotonic() - started_at) < self.submit_warmup_seconds
        ):
            return self._advance_skip(session_id, "terminal_warming_up")
        result = self.claim_next_turn(
            actor=runner_actor,
            session_id=session_id,
            trace_id=f"{trace_id}:claim",
            submit_prompt=True,
            ttl_seconds=lease_ttl_seconds,
            submit_newline="\r",
        )
        turn = result.get("turn")
        if not isinstance(turn, dict):
            return self._advance_skip(session_id, "claim_empty")
        return {
            "session_id": session_id,
            "action": "started_and_submitted" if started_terminal else "submitted",
            "reason": None,
            "turn_id": turn.get("id"),
            "request_id": result.get("request_id"),
        }

    def advance_pending_queues(
        self,
        *,
        actor: Actor | None = None,
        trace_id: str = "turn-runner",
        auto_start_terminal: bool = True,
        lease_ttl_seconds: int = 600,
    ) -> list[dict[str, object]]:
        """扫描全部会话，对空闲且有排队任务者各推进一轮；供生命周期监控/守护周期调用。

        返回只包含真正发生提交或非平凡跳过（如 human_control / queue_paused /
        terminal_not_running）的结果，过滤掉 queue_empty / turn_active 这类常态噪声。"""
        results: list[dict[str, object]] = []
        for session in self.control.repository.list_sessions():
            # 注意：不要在这里因 active_turn_id 提前跳过——advance_queue 需要机会对
            # "挂着 active_turn 但终端已死"的僵尸会话做自愈（让该 turn 失败、释放会话）。
            if session.status in {
                SessionStatus.CLOSED,
                SessionStatus.CLOSING,
                SessionStatus.ARCHIVED,
            }:
                continue
            outcome = self.advance_queue(
                session_id=session.id,
                actor=actor,
                trace_id=trace_id,
                auto_start_terminal=auto_start_terminal,
                lease_ttl_seconds=lease_ttl_seconds,
            )
            if outcome.get("action") != "skipped" or outcome.get("reason") not in {
                "queue_empty",
                "turn_active",
            }:
                results.append(outcome)
        return results

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

    def flush_pending_terminal_inputs(
        self, session_id: str, *, actor: Actor | None = None, trace_id: str = "terminal-inject"
    ) -> list[str]:
        """把"立刻追加"的待发输入打进运行中的终端（agent 下一步读取）。人工接管或终端未运行时
        回退重排、等下一拍。供生命周期监控每拍调用。"""
        inputs = self.control.repository.drain_terminal_inputs(session_id)
        if not inputs:
            return []
        lease = self.control.repository.current_lease(session_id)
        status = self.backend.status(session_id=session_id)
        if (
            (lease is not None and lease.owner_type == LeaseOwnerType.HUMAN)
            or not status.started
            or not status.running
        ):
            for text in inputs:
                self.control.repository.queue_terminal_input(session_id, text)
            return []
        runner_actor = actor or Actor(id="system:turn-runner", roles={"maintainer"})
        if lease is None or lease.owner_type != LeaseOwnerType.BOT:
            lease = self.control.acquire_lease(
                actor=runner_actor,
                session_id=session_id,
                owner_type=LeaseOwnerType.BOT,
                owner_id="terminal-agent",
                ttl_seconds=600,
                trace_id=f"{trace_id}:lease",
            )
        submitted: list[str] = []
        for text in inputs:
            data = text if text.endswith("\r") else text + "\r"
            try:
                request_id = self.submit_input(
                    session_id=session_id,
                    epoch=lease.epoch,
                    owner_type=lease.owner_type,
                    owner_id=lease.owner_id,
                    kind=TerminalInputKind.TEXT,
                    data=data,
                    trace_id=f"{trace_id}:input",
                    request_id=f"inject:{uuid4().hex}",
                )
                submitted.append(request_id)
            except AgentBridgeError:
                self.control.repository.queue_terminal_input(session_id, text)
        return submitted

    def _capture_terminal_title(self, session_id: str) -> None:
        """抓取 agent 在终端里设置的标题并写入会话（供列表/状态显示）。仅 tmux 等支持的后端有效。"""
        getter = getattr(self.backend, "pane_title", None)
        if getter is None:
            return
        try:
            title = getter(session_id=session_id)
        except Exception:
            return
        self.control.repository.set_terminal_title(session_id, title)

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
        try:
            self.flush_terminal_event_outbox()
        except Exception as exc:
            errors.append(f"terminal_event_outbox: {exc}")
        for session_id in session_ids:
            try:
                status = self.status(session_id=session_id, trace_id=trace_id)
                observed[session_id] = status
                self._capture_terminal_title(session_id)
                self.flush_pending_terminal_inputs(session_id, trace_id=f"{trace_id}:inject")
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
        if self.idle_turn_completion:
            try:
                self.check_idle_turn_completions(trace_id=f"{trace_id}:idle")
            except Exception as exc:
                errors.append(f"check_idle_turn_completions: {exc}")
        if self.auto_advance_queues:
            try:
                self.advance_pending_queues(trace_id=f"{trace_id}:advance")
            except Exception as exc:
                errors.append(f"advance_pending_queues: {exc}")
        with self._lifecycle_lock:
            self._lifecycle_last_error = "; ".join(errors) if errors else None
            self._lifecycle_last_observed_count = len(observed)
        return observed

    def recover_lifecycle_state_from_events(self) -> dict[str, int]:
        recovered_generations: dict[str, int] = {}
        recovered_exits: set[tuple[str, int]] = set()
        recovered_losses: set[tuple[str, int]] = set()
        recovered_auto_restart_blocks: set[tuple[str, int, str]] = set()
        latest_lost_session_ids: list[str] = []
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
                if (session.id, generation) in recovered_losses:
                    latest_lost_session_ids.append(session.id)

        with self._lifecycle_lock:
            for session_id, generation in recovered_generations.items():
                self._terminal_start_generations[session_id] = max(
                    generation,
                    self._terminal_start_generations.get(session_id, 0),
                )
            self._reported_terminal_exits.update(recovered_exits)
            self._reported_terminal_losses.update(recovered_losses)
            self._reported_auto_restart_blocks.update(recovered_auto_restart_blocks)
        for session_id in latest_lost_session_ids:
            self._enable_terminal_offline_protection_if_needed(
                session_id=session_id,
                trace_id="terminal-lifecycle-recovery:offline-protection",
            )
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
        event_outbox_status = self.terminal_event_outbox_status()
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
                "event_outbox": event_outbox_status,
            }

    def flush_terminal_event_outbox(self) -> int:
        if self.event_outbox is None:
            with self._lifecycle_lock:
                self._terminal_event_outbox_last_flush_count = 0
                self._terminal_event_outbox_last_flush_error = None
            return 0
        try:
            flushed = self.event_outbox.flush(
                self._submit_terminal_event_payload,
                is_transient_terminal_event_error,
            )
        except Exception as exc:
            with self._lifecycle_lock:
                self._terminal_event_outbox_last_flush_error = str(exc)
            raise
        with self._lifecycle_lock:
            self._terminal_event_outbox_last_flush_count = flushed
            self._terminal_event_outbox_last_flush_error = None
        return flushed

    def terminal_event_outbox_status(self) -> dict[str, object]:
        if self.event_outbox is None:
            return {
                "enabled": False,
                "path": None,
                "pending_count": 0,
                "read_error": None,
                "last_flush_count": 0,
                "last_flush_error": None,
            }
        pending_count: int | None
        read_error: str | None
        try:
            pending_count = len(self.event_outbox.read_entries())
            read_error = None
        except Exception as exc:
            pending_count = None
            read_error = str(exc)
        with self._lifecycle_lock:
            last_flush_count = self._terminal_event_outbox_last_flush_count
            last_flush_error = self._terminal_event_outbox_last_flush_error
        return {
            "enabled": True,
            "path": str(self.event_outbox.path),
            "pending_count": pending_count,
            "read_error": read_error,
            "last_flush_count": last_flush_count,
            "last_flush_error": last_flush_error,
        }

    def _emit_terminal_event(
        self,
        *,
        event_type: str,
        trace_id: str,
        project_id: str | None,
        session_id: str | None,
        payload: Mapping[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> object | None:
        request_payload: dict[str, object] = {
            "event_type": event_type,
            "source": SemanticEventSource.TERMINAL_AGENT.value,
            "trace_id": trace_id,
            "payload": dict(payload or {}),
        }
        if project_id is not None:
            request_payload["project_id"] = project_id
        if session_id is not None:
            request_payload["session_id"] = session_id
        if idempotency_key is not None:
            request_payload["idempotency_key"] = idempotency_key

        if self.event_outbox is None:
            return self._submit_terminal_event_payload(request_payload)

        try:
            self.flush_terminal_event_outbox()
            return self._submit_terminal_event_payload(request_payload)
        except Exception as exc:
            if not is_transient_terminal_event_error(exc):
                raise
            self.event_outbox.append(request_payload)
            return None

    def _submit_terminal_event_payload(
        self,
        request_payload: Mapping[str, object],
    ) -> object:
        payload = request_payload.get("payload") or {}
        if not isinstance(payload, Mapping):
            raise ValueError("terminal event payload must be an object")
        return self.control.emit_event(
            event_type=str(request_payload["event_type"]),
            source=SemanticEventSource(
                str(
                    request_payload.get("source")
                    or SemanticEventSource.TERMINAL_AGENT.value
                )
            ),
            trace_id=str(request_payload["trace_id"]),
            project_id=(
                str(request_payload["project_id"])
                if request_payload.get("project_id") is not None
                else None
            ),
            session_id=(
                str(request_payload["session_id"])
                if request_payload.get("session_id") is not None
                else None
            ),
            payload={str(key): value for key, value in payload.items()},
            idempotency_key=(
                str(request_payload["idempotency_key"])
                if request_payload.get("idempotency_key") is not None
                else None
            ),
        )

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
        self._emit_terminal_event(
            event_type="terminal.exited",
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
        self._fail_active_turn_after_terminal_loss(
            session_id=session.id,
            reason="terminal_exited",
            trace_id=trace_id,
        )

    def _fail_active_turn_after_terminal_loss(
        self, *, session_id: str, reason: str, trace_id: str
    ) -> None:
        """终端在某个 turn 运行中退出/丢失时，把该 turn 标记失败，释放会话避免僵尸占用。

        持久交互进程一旦死亡，进行中那一轮的模型上下文已不可恢复，必须让它失败、把会话
        交回 IDLE，后续任务才能继续；否则会话会一直停在 running、active_turn 永不清空。"""
        try:
            session = self.control.repository.get_session(session_id)
            turn_id = session.active_turn_id
            if not turn_id:
                return
            turn = self.control.repository.get_turn(turn_id)
            if turn.status != TurnStatus.RUNNING:
                return
            self.control.ingest_session_event(
                session_id=session_id,
                event_type="turn.failed",
                source=SemanticEventSource.TERMINAL_AGENT,
                trace_id=f"{trace_id}:turn-failed",
                turn_id=turn_id,
                idempotency_key=f"turn-failed-terminal:{turn_id}",
                payload={
                    "error": "终端在任务执行中退出，本轮无法继续。",
                    "reason": reason,
                },
            )
        except AgentBridgeError:
            pass

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
        self._emit_terminal_event(
            event_type="terminal.lost",
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
        self._enable_terminal_offline_protection_if_needed(
            session_id=session.id,
            trace_id=f"{trace_id}:offline-protection",
        )
        with self._lifecycle_lock:
            self._reported_terminal_losses.add(loss_key)
        self._fail_active_turn_after_terminal_loss(
            session_id=session.id,
            reason="terminal_lost",
            trace_id=trace_id,
        )

    def _terminal_lifecycle_actor(self) -> Actor:
        return Actor(id="terminal-agent", roles={"admin"})

    def _enable_terminal_offline_protection_if_needed(
        self,
        *,
        session_id: str,
        trace_id: str,
    ) -> None:
        session = self.control.repository.get_session(session_id)
        if session.status in {SessionStatus.CLOSED, SessionStatus.ARCHIVED}:
            return
        if session.status == SessionStatus.RECOVERING:
            return
        self.control.set_terminal_agent_offline_protection(
            actor=self._terminal_lifecycle_actor(),
            session_id=session_id,
            offline=True,
            trace_id=trace_id,
        )

    def _disable_terminal_offline_protection_if_needed(
        self,
        *,
        session_id: str,
        trace_id: str,
    ) -> None:
        session = self.control.repository.get_session(session_id)
        if session.status != SessionStatus.RECOVERING:
            return
        self.control.set_terminal_agent_offline_protection(
            actor=self._terminal_lifecycle_actor(),
            session_id=session_id,
            offline=False,
            trace_id=trace_id,
        )

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
        self._emit_terminal_event(
            event_type="terminal.auto_restart.skipped",
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
