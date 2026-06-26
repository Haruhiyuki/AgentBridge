from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import platform
import secrets
import shlex
import shutil
import socket
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentbridge.api import (
    create_repository_from_env,
    create_terminal_backend_from_env,
    env_bool,
    env_float,
    env_int,
    start_terminal_backend_supervision,
    stop_terminal_backend_supervision,
    terminal_auto_restart_command_allowlist_from_env,
)
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, AgentBridgeError, AgentType, ErrorCode, LeaseOwnerType
from agentbridge.terminal_agent import (
    TerminalAgentService,
    TerminalInputKind,
    TerminalLifecyclePolicy,
)


@dataclass(frozen=True)
class LocalTerminalAgentConfig:
    socket_path: Path
    auth_token: str
    auth_token_file: Path | None = None
    require_peer_user: bool = True
    lifecycle_poll_interval_seconds: float = 1.0
    terminal_auto_restart_on_lost: bool = False
    terminal_auto_restart_max_attempts: int = 1
    terminal_auto_restart_command_allowlist: tuple[str, ...] = ()
    desktop_auto_open_enabled: bool = False
    desktop_open_command: str | None = None
    desktop_open_preset: str | None = None


@dataclass(frozen=True)
class DesktopTerminalLaunchResult:
    launched: bool
    pid: int | None = None
    error: str | None = None

    def to_payload(self) -> dict[str, object]:
        return {"launched": self.launched, "pid": self.pid, "error": self.error}


@dataclass(frozen=True)
class DesktopTerminalOpenPreset:
    name: str
    executable: str
    argv_template: tuple[str, ...]
    macos_launcher_script: bool = False


MACOS_TERMINAL_APPLESCRIPT = (
    "on run argv\n"
    '  tell application "Terminal"\n'
    "    activate\n"
    "    do script quoted form of (item 1 of argv)\n"
    "  end tell\n"
    "end run"
)

DESKTOP_TERMINAL_OPEN_PRESETS: dict[str, DesktopTerminalOpenPreset] = {
    "macos-terminal": DesktopTerminalOpenPreset(
        name="macos-terminal",
        executable="osascript",
        argv_template=(
            "{terminal_executable}",
            "-e",
            MACOS_TERMINAL_APPLESCRIPT,
            "{launcher_script}",
        ),
        macos_launcher_script=True,
    ),
    "gnome-terminal": DesktopTerminalOpenPreset(
        name="gnome-terminal",
        executable="gnome-terminal",
        argv_template=(
            "{terminal_executable}",
            "--",
            "{console_command}",
            "{session_id}",
            "--socket",
            "{socket_path}",
            "--raw",
        ),
    ),
    "konsole": DesktopTerminalOpenPreset(
        name="konsole",
        executable="konsole",
        argv_template=(
            "{terminal_executable}",
            "-e",
            "{console_command}",
            "{session_id}",
            "--socket",
            "{socket_path}",
            "--raw",
        ),
    ),
    "wezterm": DesktopTerminalOpenPreset(
        name="wezterm",
        executable="wezterm",
        argv_template=(
            "{terminal_executable}",
            "start",
            "--",
            "{console_command}",
            "{session_id}",
            "--socket",
            "{socket_path}",
            "--raw",
        ),
    ),
    "alacritty": DesktopTerminalOpenPreset(
        name="alacritty",
        executable="alacritty",
        argv_template=(
            "{terminal_executable}",
            "-e",
            "{console_command}",
            "{session_id}",
            "--socket",
            "{socket_path}",
            "--raw",
        ),
    ),
    "kitty": DesktopTerminalOpenPreset(
        name="kitty",
        executable="kitty",
        argv_template=(
            "{terminal_executable}",
            "{console_command}",
            "{session_id}",
            "--socket",
            "{socket_path}",
            "--raw",
        ),
    ),
    "xterm": DesktopTerminalOpenPreset(
        name="xterm",
        executable="xterm",
        argv_template=(
            "{terminal_executable}",
            "-e",
            "{console_command}",
            "{session_id}",
            "--socket",
            "{socket_path}",
            "--raw",
        ),
    ),
}

AUTO_DESKTOP_TERMINAL_PRESETS: dict[str, tuple[str, ...]] = {
    "darwin": ("macos-terminal",),
    "linux": ("gnome-terminal", "konsole", "wezterm", "alacritty", "kitty", "xterm"),
}


def current_local_auth_token(*, auth_token: str, auth_token_file: Path | None) -> str:
    if auth_token_file is None:
        return auth_token

    token_path = auth_token_file.expanduser()
    try:
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AgentBridgeError(
            ErrorCode.PERMISSION_DENIED,
            "本地 Terminal Agent token 文件不可读。",
            next_step="请检查 AGENTBRIDGE_LOCAL_TOKEN_FILE 路径和权限。",
            status_code=403,
            details={"token_file": str(token_path), "reason": str(exc)},
        ) from exc
    if not token:
        raise AgentBridgeError(
            ErrorCode.PERMISSION_DENIED,
            "本地 Terminal Agent token 文件为空。",
            next_step="请写入非空 token 后重试。",
            status_code=403,
            details={"token_file": str(token_path)},
        )
    return token


class DesktopTerminalLauncher:
    def __init__(
        self,
        *,
        enabled: bool = False,
        command_template: str | None = None,
        open_preset: str | None = None,
        socket_path: Path | None = None,
        auth_token: str,
        auth_token_file: Path | None = None,
        launcher_script_dir: Path | None = None,
    ) -> None:
        self.enabled = enabled
        self.command_template = command_template
        self.open_preset = open_preset
        self.socket_path = socket_path
        self.auth_token = auth_token
        self.auth_token_file = auth_token_file
        self.launcher_script_dir = launcher_script_dir

    def launch(self, *, session_id: str) -> DesktopTerminalLaunchResult:
        if not self.enabled:
            return DesktopTerminalLaunchResult(launched=False)
        if self.socket_path is None:
            return DesktopTerminalLaunchResult(
                launched=False,
                error="Terminal Agent socket path is not available",
            )

        try:
            auth_token = self.current_auth_token()
            argv, launcher_script, error = self._build_argv(
                session_id=session_id,
                auth_token=auth_token,
            )
        except (OSError, AgentBridgeError) as exc:
            return DesktopTerminalLaunchResult(launched=False, error=str(exc))
        if error is not None:
            return DesktopTerminalLaunchResult(launched=False, error=error)
        if not argv:
            return DesktopTerminalLaunchResult(launched=False, error="open command is empty")

        env = dict(os.environ)
        env["AGENTBRIDGE_LOCAL_TOKEN"] = auth_token
        env["AGENTBRIDGE_TERMINAL_SOCKET"] = str(self.socket_path)
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
                env=env,
            )
        except OSError as exc:
            if launcher_script is not None:
                with contextlib.suppress(OSError):
                    launcher_script.unlink()
            return DesktopTerminalLaunchResult(launched=False, error=str(exc))
        return DesktopTerminalLaunchResult(launched=True, pid=process.pid)

    def current_auth_token(self) -> str:
        return current_local_auth_token(
            auth_token=self.auth_token,
            auth_token_file=self.auth_token_file,
        )

    def _build_argv(
        self,
        *,
        session_id: str,
        auth_token: str,
    ) -> tuple[list[str], Path | None, str | None]:
        if self.command_template:
            try:
                command = self.command_template.format(
                    session_id=session_id,
                    socket_path=str(self.socket_path),
                    console_command="agentbridge-console",
                )
                return shlex.split(command), None, None
            except (KeyError, ValueError) as exc:
                return [], None, str(exc)

        preset_name = (self.open_preset or "custom").strip().lower()
        if preset_name in {"", "custom"}:
            return (
                [],
                None,
                "AGENTBRIDGE_TERMINAL_OPEN_COMMAND or AGENTBRIDGE_TERMINAL_OPEN_PRESET "
                "is required when auto-open is enabled",
            )
        if preset_name == "auto":
            return self._build_auto_preset_argv(
                session_id=session_id,
                auth_token=auth_token,
            )
        return self._build_named_preset_argv(
            preset_name,
            session_id=session_id,
            auth_token=auth_token,
        )

    def _build_auto_preset_argv(
        self,
        *,
        session_id: str,
        auth_token: str,
    ) -> tuple[list[str], Path | None, str | None]:
        platform_name = platform.system().lower()
        preset_names = AUTO_DESKTOP_TERMINAL_PRESETS.get(platform_name, ())
        for preset_name in preset_names:
            argv, launcher_script, error = self._build_named_preset_argv(
                preset_name,
                session_id=session_id,
                auth_token=auth_token,
                missing_executable_is_error=False,
            )
            if error is None:
                return argv, launcher_script, None
        expected = ", ".join(preset_names or sorted(DESKTOP_TERMINAL_OPEN_PRESETS))
        return [], None, f"no supported desktop terminal preset found; expected one of: {expected}"

    def _build_named_preset_argv(
        self,
        preset_name: str,
        *,
        session_id: str,
        auth_token: str,
        missing_executable_is_error: bool = True,
    ) -> tuple[list[str], Path | None, str | None]:
        preset = DESKTOP_TERMINAL_OPEN_PRESETS.get(preset_name)
        if preset is None:
            return [], None, f"unknown desktop terminal open preset: {preset_name}"

        executable_path = shutil.which(preset.executable)
        if executable_path is None:
            if not missing_executable_is_error:
                return [], None, f"{preset.executable} not found"
            error = (
                f"desktop terminal open preset {preset.name!r} "
                f"requires {preset.executable!r} in PATH"
            )
            return (
                [],
                None,
                error,
            )

        launcher_script = None
        if preset.macos_launcher_script:
            launcher_script = self._write_macos_terminal_launcher_script(
                session_id=session_id,
                auth_token=auth_token,
            )

        values = {
            "terminal_executable": executable_path,
            "console_command": "agentbridge-console",
            "session_id": session_id,
            "socket_path": str(self.socket_path),
            "launcher_script": str(launcher_script) if launcher_script else "",
        }
        try:
            argv = [argument.format(**values) for argument in preset.argv_template]
        except KeyError as exc:
            if launcher_script is not None:
                with contextlib.suppress(OSError):
                    launcher_script.unlink()
            return [], None, str(exc)
        return argv, launcher_script, None

    def _write_macos_terminal_launcher_script(
        self,
        *,
        session_id: str,
        auth_token: str,
    ) -> Path:
        if self.socket_path is None:
            raise RuntimeError("Terminal Agent socket path is not available")
        script_dir = (
            self.launcher_script_dir
            if self.launcher_script_dir is not None
            else Path.home() / ".agentbridge" / "terminal-launchers"
        )
        script_dir = script_dir.expanduser()
        script_dir.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            script_dir.chmod(0o700)

        fd, raw_path = tempfile.mkstemp(
            prefix="agentbridge-console-",
            suffix=".sh",
            dir=script_dir,
            text=True,
        )
        script_path = Path(raw_path)
        console_command = " ".join(
            shlex.quote(argument)
            for argument in (
                "agentbridge-console",
                session_id,
                "--socket",
                str(self.socket_path),
                "--raw",
            )
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(
                    "#!/bin/sh\n"
                    "set -eu\n"
                    f"export AGENTBRIDGE_LOCAL_TOKEN={shlex.quote(auth_token)}\n"
                    f"export AGENTBRIDGE_TERMINAL_SOCKET={shlex.quote(str(self.socket_path))}\n"
                    'rm -f "$0"\n'
                    f"exec {console_command}\n"
                )
            script_path.chmod(0o700)
        except Exception:
            with contextlib.suppress(OSError):
                script_path.unlink()
            raise
        return script_path


class LocalTerminalAgentServer:
    def __init__(
        self,
        *,
        control: ControlPlane,
        terminal: TerminalAgentService,
        auth_token: str,
        auth_token_file: Path | None = None,
        require_peer_user: bool = True,
        allowed_peer_uid: int | None = None,
        lifecycle_monitor_enabled: bool = True,
        lifecycle_poll_interval_seconds: float = 1.0,
        desktop_launcher: DesktopTerminalLauncher | None = None,
    ) -> None:
        if not auth_token:
            raise ValueError("auth_token must not be empty")
        self.control = control
        self.terminal = terminal
        self.auth_token = auth_token
        self.auth_token_file = auth_token_file
        self.require_peer_user = require_peer_user
        if allowed_peer_uid is not None:
            self.allowed_peer_uid = allowed_peer_uid
        elif hasattr(os, "getuid"):
            self.allowed_peer_uid = os.getuid()
        else:
            self.allowed_peer_uid = None
        self.lifecycle_monitor_enabled = lifecycle_monitor_enabled
        self.lifecycle_poll_interval_seconds = max(float(lifecycle_poll_interval_seconds), 0.05)
        self.desktop_launcher = desktop_launcher or DesktopTerminalLauncher(
            auth_token=auth_token,
            auth_token_file=auth_token_file,
        )
        self._server: asyncio.AbstractServer | None = None
        self._socket_path: Path | None = None

    async def start(self, socket_path: Path) -> None:
        if not hasattr(asyncio, "start_unix_server"):
            raise RuntimeError("Unix socket terminal agent is not supported on this platform")
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists():
            if not socket_path.is_socket():
                raise RuntimeError(f"Refusing to replace non-socket path: {socket_path}")
            socket_path.unlink()
        self._server = await asyncio.start_unix_server(self._handle_client, path=str(socket_path))
        socket_path.chmod(0o600)
        self._socket_path = socket_path
        if (
            self.desktop_launcher.socket_path != socket_path
            or self.desktop_launcher.auth_token != self.auth_token
            or self.desktop_launcher.auth_token_file != self.auth_token_file
        ):
            self.desktop_launcher = DesktopTerminalLauncher(
                enabled=self.desktop_launcher.enabled,
                command_template=self.desktop_launcher.command_template,
                open_preset=self.desktop_launcher.open_preset,
                socket_path=socket_path,
                auth_token=self.auth_token,
                auth_token_file=self.auth_token_file,
                launcher_script_dir=self.desktop_launcher.launcher_script_dir,
            )
        start_terminal_backend_supervision(self.terminal)
        if self.lifecycle_monitor_enabled:
            self.terminal.start_lifecycle_monitor(
                interval_seconds=self.lifecycle_poll_interval_seconds
            )

    async def stop(self) -> None:
        self.terminal.stop_lifecycle_monitor()
        stop_terminal_backend_supervision(self.terminal)
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._socket_path and self._socket_path.exists():
            self._socket_path.unlink()
        self._socket_path = None

    async def serve_forever(self, socket_path: Path) -> None:
        await self.start(socket_path)
        if self._server is None:
            raise RuntimeError("server failed to start")
        async with self._server:
            await self._server.serve_forever()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            peer_error = self.peer_user_error(writer)
            if peer_error is not None:
                await self.write_response(
                    writer,
                    {"ok": False, "error": peer_error.to_payload()},
                )
                return
            while line := await reader.readline():
                try:
                    request = self.decode_request_line(line)
                except (TypeError, ValueError) as exc:
                    error = AgentBridgeError(
                        ErrorCode.COMMAND_ARGUMENT_INVALID,
                        "本地 Terminal Agent 请求格式无效。",
                        next_step="请发送包含 token、action 和 payload 的 JSON 行。",
                        details={"reason": str(exc)},
                    )
                    await self.write_response(writer, {"ok": False, "error": error.to_payload()})
                    continue
                if request.get("action") == "stream_output":
                    await self.stream_output(request, writer)
                    return
                response = self.handle_request_object(request)
                await self.write_response(writer, response)
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()

    def peer_user_error(self, writer: asyncio.StreamWriter) -> AgentBridgeError | None:
        if not self.require_peer_user:
            return None
        peer_uid = peer_uid_from_writer(writer)
        if self.allowed_peer_uid is None or peer_uid != self.allowed_peer_uid:
            return AgentBridgeError(
                ErrorCode.PERMISSION_DENIED,
                "本地 Terminal Agent 连接用户无权访问。",
                next_step="请使用同一 OS 用户连接本地 Terminal Agent，或关闭 peer 用户校验。",
                status_code=403,
                details={
                    "expected_uid": self.allowed_peer_uid,
                    "peer_uid": peer_uid,
                },
            )
        return None

    def current_auth_token(self) -> str:
        return current_local_auth_token(
            auth_token=self.auth_token,
            auth_token_file=self.auth_token_file,
        )

    def require_valid_token(self, token: object) -> None:
        if not isinstance(token, str) or not hmac.compare_digest(
            token,
            self.current_auth_token(),
        ):
            raise AgentBridgeError(
                ErrorCode.PERMISSION_DENIED,
                "本地 Terminal Agent token 无效。",
                next_step="请使用当前本地 token 重新连接。",
                status_code=403,
            )

    def handle_request_line(self, line: bytes) -> dict[str, Any]:
        try:
            request = self.decode_request_line(line)
            return self.handle_request_object(request)
        except AgentBridgeError as exc:
            return {"ok": False, "error": exc.to_payload()}
        except (TypeError, ValueError) as exc:
            error = AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "本地 Terminal Agent 请求格式无效。",
                next_step="请发送包含 token、action 和 payload 的 JSON 行。",
                details={"reason": str(exc)},
            )
            return {"ok": False, "error": error.to_payload()}

    def decode_request_line(self, line: bytes) -> dict[str, Any]:
        request = json.loads(line.decode("utf-8"))
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        return request

    def handle_request_object(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            data = self.handle_request(request)
            return {"ok": True, "data": data}
        except AgentBridgeError as exc:
            return {"ok": False, "error": exc.to_payload()}
        except (TypeError, ValueError) as exc:
            error = AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "本地 Terminal Agent 请求格式无效。",
                next_step="请发送包含 token、action 和 payload 的 JSON 行。",
                details={"reason": str(exc)},
            )
            return {"ok": False, "error": error.to_payload()}

    async def write_response(
        self,
        writer: asyncio.StreamWriter,
        response: dict[str, Any],
    ) -> None:
        writer.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")
        await writer.drain()

    async def stream_output(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            token = request.get("token")
            self.require_valid_token(token)
            payload = request.get("payload") or {}
            if not isinstance(payload, dict):
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "payload 必须是对象。",
                    next_step="请检查本地客户端请求格式。",
                )
            session_id = required_str(payload, "session_id")
            cursor = int(payload.get("after_cursor") or 0)
            poll_interval_seconds = max(float(payload.get("poll_interval_seconds") or 0.25), 0.01)
            idle_timeout_seconds = payload.get("idle_timeout_seconds")
            idle_timeout = (
                max(float(idle_timeout_seconds), 0.0)
                if idle_timeout_seconds is not None
                else None
            )
            max_frames = payload.get("max_frames")
            frame_limit = int(max_frames) if max_frames is not None else None
            sent_frames = 0
            loop = asyncio.get_running_loop()
            last_frame_at = loop.time()
            while frame_limit is None or sent_frames < frame_limit:
                chunk = self.terminal.read_output(
                    session_id=session_id,
                    after_cursor=cursor,
                )
                cursor = chunk.cursor
                if chunk.data or chunk.reset:
                    await self.write_response(
                        writer,
                        {
                            "ok": True,
                            "type": "terminal.output",
                            "data": {
                                "cursor": chunk.cursor,
                                "data": chunk.data,
                                "snapshot": chunk.snapshot,
                                "reset": chunk.reset,
                            },
                        },
                    )
                    sent_frames += 1
                    last_frame_at = loop.time()
                elif idle_timeout is not None and loop.time() - last_frame_at >= idle_timeout:
                    await self.write_response(
                        writer,
                        {
                            "ok": True,
                            "type": "terminal.output.idle_timeout",
                            "data": {"cursor": cursor},
                        },
                    )
                    return
                await asyncio.sleep(poll_interval_seconds)
        except AgentBridgeError as exc:
            await self.write_response(writer, {"ok": False, "error": exc.to_payload()})
        except (TypeError, ValueError) as exc:
            error = AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "本地 Terminal Agent 请求格式无效。",
                next_step="请发送包含 token、action 和 payload 的 JSON 行。",
                details={"reason": str(exc)},
            )
            await self.write_response(writer, {"ok": False, "error": error.to_payload()})

    def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        token = request.get("token")
        self.require_valid_token(token)
        action = request.get("action")
        payload = request.get("payload") or {}
        if not isinstance(payload, dict):
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "payload 必须是对象。",
                next_step="请检查本地客户端请求格式。",
            )
        if action == "health":
            return self.control.health()
        if action == "lifecycle_status":
            return self.terminal.lifecycle_monitor_status()
        if action == "run_lifecycle_monitor_once":
            observed = self.terminal.run_lifecycle_monitor_once(
                trace_id=str(payload.get("trace_id") or "local-terminal-lifecycle")
            )
            return {
                "monitor": self.terminal.lifecycle_monitor_status(),
                "observed": {
                    session_id: status.to_payload()
                    for session_id, status in observed.items()
                },
            }
        if action == "probe_agent_launch_profiles":
            return {
                "profiles": self.terminal.probe_agent_launch_versions(
                    agent_types=agent_types_from_payload(payload),
                    timeout_seconds=float(payload.get("timeout_seconds") or 2.0),
                )
            }
        if action == "detect_agent_adapters":
            return {
                "adapters": self.terminal.detect_agent_adapter_capabilities(
                    agent_types=agent_types_from_payload(payload),
                    timeout_seconds=float(payload.get("timeout_seconds") or 2.0),
                )
            }
        if action == "start_session":
            session_id = required_str(payload, "session_id")
            command = payload.get("command")
            if command is not None and not isinstance(command, str):
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "command 必须是字符串。",
                    next_step="请省略 command 以使用 Session 的 Agent 默认启动命令。",
                )
            self.terminal.start_session(
                session_id=session_id,
                command=command,
                trace_id=str(payload.get("trace_id") or "local-terminal"),
            )
            launch_result = self.desktop_launcher.launch(session_id=session_id)
            return {"status": "started", "desktop": launch_result.to_payload()}
        if action == "restart_session":
            session_id = required_str(payload, "session_id")
            command = payload.get("command")
            if command is not None and not isinstance(command, str):
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "command 必须是字符串。",
                    next_step="请省略 command 以复用上次启动命令，或提供字符串命令。",
                )
            result = self.terminal.restart_session(
                session_id=session_id,
                command=command,
                trace_id=str(payload.get("trace_id") or "local-terminal"),
            )
            launch_result = (
                self.desktop_launcher.launch(session_id=session_id)
                if result.restarted
                else DesktopTerminalLaunchResult(launched=False)
            )
            return {**result.to_payload(), "desktop": launch_result.to_payload()}
        if action == "acquire_human_lease":
            actor = actor_from_payload(payload)
            lease = self.control.acquire_lease(
                actor=actor,
                session_id=required_str(payload, "session_id"),
                owner_type=LeaseOwnerType.HUMAN,
                owner_id=required_str(payload, "owner_id"),
                ttl_seconds=int(payload.get("ttl_seconds") or 300),
                trace_id=str(payload.get("trace_id") or "local-terminal"),
            )
            return {"lease": lease.model_dump(mode="json")}
        if action == "release_lease":
            actor = actor_from_payload(payload)
            next_epoch = self.control.release_lease(
                actor=actor,
                session_id=required_str(payload, "session_id"),
                epoch=int(payload["epoch"]),
                trace_id=str(payload.get("trace_id") or "local-terminal"),
            )
            return {"next_epoch": next_epoch}
        if action == "claim_next_turn":
            actor = actor_from_payload(payload)
            expected_queue_version = payload.get("expected_queue_version")
            if expected_queue_version is not None and not isinstance(
                expected_queue_version, str
            ):
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "expected_queue_version 必须是字符串。",
                    next_step="请省略 expected_queue_version 或传入最新 queue_version。",
                )
            submit_prompt = payload.get("submit_prompt") or False
            if not isinstance(submit_prompt, bool):
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "submit_prompt 必须是布尔值。",
                    next_step="请传入 true/false，或省略该字段。",
                )
            owner_id = payload.get("owner_id") or "terminal-agent"
            if not isinstance(owner_id, str):
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "owner_id 必须是字符串。",
                    next_step="请提供写入者 ID，或省略以使用 terminal-agent。",
                )
            request_id = payload.get("request_id")
            if request_id is not None and not isinstance(request_id, str):
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "request_id 必须是字符串。",
                    next_step="请提供字符串 request_id，或省略以自动生成。",
                )
            append_newline = payload.get("append_newline")
            if append_newline is None:
                append_newline = True
            if not isinstance(append_newline, bool):
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "append_newline 必须是布尔值。",
                    next_step="请传入 true/false，或省略该字段。",
                )
            return self.terminal.claim_next_turn(
                actor=actor,
                session_id=required_str(payload, "session_id"),
                trace_id=str(payload.get("trace_id") or "local-terminal"),
                expected_queue_version=expected_queue_version,
                submit_prompt=submit_prompt,
                owner_type=LeaseOwnerType(
                    str(payload.get("owner_type") or LeaseOwnerType.BOT.value)
                ),
                owner_id=owner_id,
                ttl_seconds=int(payload.get("ttl_seconds") or 300),
                request_id=request_id,
                append_newline=append_newline,
            )
        if action == "submit_input":
            request_id = payload.get("request_id")
            request_id = self.terminal.submit_input(
                session_id=required_str(payload, "session_id"),
                epoch=int(payload["epoch"]),
                owner_type=LeaseOwnerType(str(payload["owner_type"])),
                owner_id=required_str(payload, "owner_id"),
                kind=TerminalInputKind(str(payload.get("type") or TerminalInputKind.TEXT.value)),
                data=str(payload.get("data") or ""),
                trace_id=str(payload.get("trace_id") or "local-terminal"),
                request_id=request_id if isinstance(request_id, str) else None,
                cols=int(payload["cols"]) if payload.get("cols") is not None else None,
                rows=int(payload["rows"]) if payload.get("rows") is not None else None,
            )
            return {"request_id": request_id}
        if action == "snapshot":
            session_id = required_str(payload, "session_id")
            return {"snapshot": self.terminal.snapshot(session_id=session_id)}
        if action == "status":
            session_id = required_str(payload, "session_id")
            return self.terminal.status(
                session_id=session_id,
                trace_id=str(payload.get("trace_id") or "local-terminal"),
            ).to_payload()
        if action == "read_output":
            chunk = self.terminal.read_output(
                session_id=required_str(payload, "session_id"),
                after_cursor=int(payload.get("after_cursor") or 0),
            )
            return {
                "cursor": chunk.cursor,
                "data": chunk.data,
                "snapshot": chunk.snapshot,
                "reset": chunk.reset,
            }
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"未知本地 Terminal Agent action：{action}",
            next_step=(
                "请使用 health、lifecycle_status、run_lifecycle_monitor_once、"
                "probe_agent_launch_profiles、detect_agent_adapters、start_session、"
                "restart_session、acquire_human_lease、release_lease、claim_next_turn、"
                "submit_input、snapshot、status、read_output 或 stream_output。"
            ),
        )


class LocalTerminalAgentClient:
    def __init__(
        self,
        socket_path: Path,
        auth_token: str,
        *,
        connect_timeout_seconds: float = 2.0,
        connect_retry_interval_seconds: float = 0.05,
    ) -> None:
        self.socket_path = socket_path
        self.auth_token = auth_token
        self.connect_timeout_seconds = max(connect_timeout_seconds, 0.0)
        self.connect_retry_interval_seconds = max(connect_retry_interval_seconds, 0.01)

    async def request(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        reader, writer = await self._connect()
        request = {"token": self.auth_token, "action": action, "payload": payload or {}}
        writer.write(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
        await writer.drain()
        line = await reader.readline()
        writer.close()
        await writer.wait_closed()
        if not line:
            raise RuntimeError("Terminal Agent closed the connection without a response")
        return json.loads(line.decode("utf-8"))

    async def _connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.connect_timeout_seconds
        while True:
            try:
                return await asyncio.open_unix_connection(str(self.socket_path))
            except OSError:
                if loop.time() >= deadline:
                    raise
                await asyncio.sleep(
                    min(self.connect_retry_interval_seconds, max(deadline - loop.time(), 0.0))
            )

    async def stream_output(
        self,
        payload: dict[str, Any],
    ):
        reader, writer = await self._connect()
        request = {"token": self.auth_token, "action": "stream_output", "payload": payload}
        writer.write(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
        await writer.drain()
        try:
            while line := await reader.readline():
                yield json.loads(line.decode("utf-8"))
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await writer.wait_closed()


def required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            f"缺少必需字段：{key}",
            next_step=f"请在 payload 中提供 {key}。",
        )
    return value


def actor_from_payload(payload: dict[str, Any]) -> Actor:
    actor_id = str(payload.get("actor_id") or payload.get("owner_id") or "local-user")
    roles_value = payload.get("roles") or ["maintainer"]
    if not isinstance(roles_value, list):
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "roles 必须是字符串数组。",
            next_step="请检查本地客户端请求格式。",
        )
    return Actor(id=actor_id, roles={str(role) for role in roles_value})


def agent_types_from_payload(payload: dict[str, Any]) -> list[AgentType] | None:
    value = payload.get("agent_types")
    if value is None:
        return None
    if not isinstance(value, list):
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "agent_types 必须是字符串数组。",
            next_step="请省略 agent_types，或提供 claude/codex/generic_tui 字符串数组。",
        )
    try:
        return [AgentType(str(item)) for item in value]
    except ValueError as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "agent_types 包含未知 Agent 类型。",
            next_step="请使用 claude、codex 或 generic_tui。",
        ) from exc


def peer_uid_from_writer(writer: asyncio.StreamWriter) -> int | None:
    return peer_uid_from_socket(writer.get_extra_info("socket"))


def peer_uid_from_socket(peer_socket: object) -> int | None:
    peer_uid = peer_uid_from_getpeereid(peer_socket)
    if peer_uid is not None:
        return peer_uid

    peer_uid = peer_uid_from_local_peercred(peer_socket)
    if peer_uid is not None:
        return peer_uid

    if hasattr(socket, "SO_PEERCRED"):
        credentials_size = struct.calcsize("3i")
        try:
            credentials = peer_socket.getsockopt(  # type: ignore[attr-defined]
                socket.SOL_SOCKET,
                socket.SO_PEERCRED,
                credentials_size,
            )
        except (AttributeError, OSError):
            fileno = getattr(peer_socket, "fileno", lambda: -1)()
            if fileno < 0:
                return None
            try:
                with socket.fromfd(fileno, socket.AF_UNIX, socket.SOCK_STREAM) as dup_socket:
                    credentials = dup_socket.getsockopt(
                        socket.SOL_SOCKET,
                        socket.SO_PEERCRED,
                        credentials_size,
                    )
            except OSError:
                return None
        _pid, uid, _gid = struct.unpack("3i", credentials)
        return int(uid)

    fileno = getattr(peer_socket, "fileno", lambda: -1)()
    if fileno < 0:
        return None
    try:
        with socket.fromfd(fileno, socket.AF_UNIX, socket.SOCK_STREAM) as dup_socket:
            return peer_uid_from_getpeereid(dup_socket)
    except OSError:
        return None
    return None


def peer_uid_from_local_peercred(peer_socket: object) -> int | None:
    if not hasattr(socket, "LOCAL_PEERCRED"):
        return None
    try:
        credentials = peer_socket.getsockopt(  # type: ignore[attr-defined]
            getattr(socket, "SOL_LOCAL", 0),
            socket.LOCAL_PEERCRED,
            struct.calcsize("2i"),
        )
    except (AttributeError, OSError):
        return None
    if len(credentials) < struct.calcsize("2i"):
        return None
    _version, uid = struct.unpack("2i", credentials[: struct.calcsize("2i")])
    return int(uid)


def peer_uid_from_getpeereid(peer_socket: object) -> int | None:
    getpeereid = getattr(peer_socket, "getpeereid", None)
    if not callable(getpeereid):
        return None
    try:
        uid, _gid = getpeereid()
        return int(uid)
    except OSError:
        return None


def config_from_env() -> LocalTerminalAgentConfig:
    socket_path = Path(
        os.environ.get(
            "AGENTBRIDGE_TERMINAL_SOCKET",
            str(Path.home() / ".agentbridge" / "terminal-agent.sock"),
        )
    ).expanduser()
    token = os.environ.get("AGENTBRIDGE_LOCAL_TOKEN")
    token_file = os.environ.get("AGENTBRIDGE_LOCAL_TOKEN_FILE")
    token_file_path = None
    if token is None and token_file:
        token_file_path = Path(token_file).expanduser()
        token = current_local_auth_token(
            auth_token="",
            auth_token_file=token_file_path,
        )
    if token is None:
        token = secrets.token_urlsafe(32)
        print(f"AGENTBRIDGE_LOCAL_TOKEN={token}", flush=True)
    return LocalTerminalAgentConfig(
        socket_path=socket_path,
        auth_token=token,
        auth_token_file=token_file_path,
        require_peer_user=env_bool("AGENTBRIDGE_LOCAL_REQUIRE_PEER_USER", default=True),
        lifecycle_poll_interval_seconds=env_float(
            "AGENTBRIDGE_TERMINAL_LIFECYCLE_POLL_INTERVAL_SECONDS",
            default=1.0,
        ),
        terminal_auto_restart_on_lost=env_bool(
            "AGENTBRIDGE_TERMINAL_AUTO_RESTART_ON_LOST",
            default=False,
        ),
        terminal_auto_restart_max_attempts=max(
            env_int("AGENTBRIDGE_TERMINAL_AUTO_RESTART_MAX_ATTEMPTS", default=1),
            0,
        ),
        terminal_auto_restart_command_allowlist=(
            terminal_auto_restart_command_allowlist_from_env()
        ),
        desktop_auto_open_enabled=env_bool("AGENTBRIDGE_TERMINAL_AUTO_OPEN", default=False),
        desktop_open_command=os.environ.get("AGENTBRIDGE_TERMINAL_OPEN_COMMAND"),
        desktop_open_preset=os.environ.get("AGENTBRIDGE_TERMINAL_OPEN_PRESET"),
    )


async def async_main() -> None:
    config = config_from_env()
    repository = create_repository_from_env()
    control = ControlPlane(repository=repository)
    terminal = TerminalAgentService(
        control,
        backend=create_terminal_backend_from_env(),
        lifecycle_policy=TerminalLifecyclePolicy(
            auto_restart_on_lost=config.terminal_auto_restart_on_lost,
            auto_restart_max_attempts=config.terminal_auto_restart_max_attempts,
            auto_restart_command_allowlist=config.terminal_auto_restart_command_allowlist,
        ),
    )
    server = LocalTerminalAgentServer(
        control=control,
        terminal=terminal,
        auth_token=config.auth_token,
        auth_token_file=config.auth_token_file,
        require_peer_user=config.require_peer_user,
        lifecycle_poll_interval_seconds=config.lifecycle_poll_interval_seconds,
        desktop_launcher=DesktopTerminalLauncher(
            enabled=config.desktop_auto_open_enabled,
            command_template=config.desktop_open_command,
            open_preset=config.desktop_open_preset,
            socket_path=config.socket_path,
            auth_token=config.auth_token,
            auth_token_file=config.auth_token_file,
        ),
    )
    await server.serve_forever(config.socket_path)


def run() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass
