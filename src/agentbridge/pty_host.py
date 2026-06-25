from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import socketserver
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any

from agentbridge.domain import AgentBridgeError, ErrorCode
from agentbridge.terminal_agent import (
    DEFAULT_PTY_OUTPUT_LIMIT_CHARS,
    PtyTerminalBackend,
    TerminalInputKind,
    TerminalOutputChunk,
    TerminalStatus,
)


@dataclass(frozen=True)
class PtyHostConfig:
    socket_path: Path
    auth_token: str = ""
    auth_token_file: Path | None = None
    max_output_chars: int = DEFAULT_PTY_OUTPUT_LIMIT_CHARS
    host_state_path: Path | None = None


@dataclass(frozen=True)
class PtyHostSupervisorConfig:
    socket_path: Path
    auth_token: str = ""
    auth_token_file: Path | None = None
    max_output_chars: int = DEFAULT_PTY_OUTPUT_LIMIT_CHARS
    host_state_path: Path | None = None
    start_command: tuple[str, ...] = ()
    startup_timeout_seconds: float = 3.0
    poll_interval_seconds: float = 0.05
    watchdog_enabled: bool = False
    watchdog_interval_seconds: float = 5.0


class PtyHostTerminalBackend:
    def __init__(
        self,
        *,
        socket_path: Path,
        auth_token: str = "",
        auth_token_file: Path | None = None,
        timeout_seconds: float = 2.0,
        supervisor: PtyHostSupervisor | None = None,
    ) -> None:
        self.socket_path = socket_path.expanduser()
        self.auth_token = auth_token
        self.auth_token_file = auth_token_file
        self.timeout_seconds = timeout_seconds
        self.supervisor = supervisor

    def start(self, *, session_id: str, cwd: str, command: str) -> None:
        self._request("start", {"session_id": session_id, "cwd": cwd, "command": command})

    def write(self, *, session_id: str, data: str, kind: TerminalInputKind) -> None:
        self._request(
            "write",
            {"session_id": session_id, "data": data, "kind": kind.value},
        )

    def signal(self, *, session_id: str, name: str) -> None:
        self._request("signal", {"session_id": session_id, "name": name})

    def resize(self, *, session_id: str, cols: int, rows: int) -> None:
        self._request("resize", {"session_id": session_id, "cols": cols, "rows": rows})

    def snapshot(self, *, session_id: str) -> str:
        data = self._request("snapshot", {"session_id": session_id})
        return str(data["snapshot"])

    def read_output(self, *, session_id: str, after_cursor: int = 0) -> TerminalOutputChunk:
        data = self._request(
            "read_output",
            {"session_id": session_id, "after_cursor": after_cursor},
        )
        return TerminalOutputChunk(
            cursor=int(data["cursor"]),
            data=str(data["data"]),
            snapshot=str(data["snapshot"]),
            reset=bool(data["reset"]),
        )

    def status(self, *, session_id: str) -> TerminalStatus:
        data = self._request("status", {"session_id": session_id})
        return TerminalStatus(
            started=bool(data["started"]),
            running=bool(data["running"]),
            exit_code=int(data["exit_code"]) if data.get("exit_code") is not None else None,
            pid=int(data["pid"]) if data.get("pid") is not None else None,
            output_cursor=int(data.get("output_cursor") or 0),
            output_base_cursor=int(data.get("output_base_cursor") or 0),
            output_retained_chars=int(data.get("output_retained_chars") or 0),
        )

    def terminate(self, *, session_id: str) -> None:
        self._request("terminate", {"session_id": session_id})

    def health(self) -> dict[str, object]:
        return self._request("health", {})

    def start_supervision(self) -> None:
        if self.supervisor is not None:
            self.supervisor.start_watchdog()

    def stop_supervision(self) -> None:
        if self.supervisor is not None:
            self.supervisor.stop_watchdog()

    def supervision_status(self) -> dict[str, object]:
        if self.supervisor is None:
            return {"enabled": False}
        return self.supervisor.status()

    def _request(self, action: str, payload: dict[str, object]) -> dict[str, Any]:
        request = {
            "token": self.current_auth_token(),
            "action": action,
            "payload": payload,
        }
        try:
            line = self._send_request(request)
        except OSError as exc:
            if self.supervisor is not None:
                self.supervisor.ensure_running()
                try:
                    line = self._send_request(request)
                except OSError as retry_exc:
                    raise self._host_unavailable_error(retry_exc) from retry_exc
            else:
                raise self._host_unavailable_error(exc) from exc
        if not line:
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "PTY Host 未返回响应。",
                next_step="请检查 PTY Host 进程状态后重试。",
                status_code=409,
            )
        response = json.loads(line.decode("utf-8"))
        if not isinstance(response, dict):
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "PTY Host 响应格式无效。",
                next_step="请检查 PTY Host 版本后重试。",
                status_code=409,
            )
        if response.get("ok") is True:
            data = response.get("data") or {}
            if not isinstance(data, dict):
                return {}
            return data
        error = response.get("error") or {}
        if not isinstance(error, dict):
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "PTY Host 请求失败。",
                next_step="请检查 PTY Host 日志后重试。",
                status_code=409,
            )
        code = ErrorCode(str(error.get("error_code") or ErrorCode.RESOURCE_CONFLICT.value))
        raise AgentBridgeError(
            code,
            str(error.get("message") or "PTY Host 请求失败。"),
            next_step=str(error.get("next_step") or "请检查 PTY Host 状态后重试。"),
            details=error.get("details") if isinstance(error.get("details"), dict) else None,
        )

    def _send_request(self, request: dict[str, object]) -> bytes:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout_seconds)
            sock.connect(str(self.socket_path))
            file = sock.makefile("rwb")
            file.write(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
            file.flush()
            return file.readline()

    def _host_unavailable_error(self, exc: OSError) -> AgentBridgeError:
        return AgentBridgeError(
            ErrorCode.PLATFORM_CAPABILITY_MISSING,
            "PTY Host 不可用。",
            next_step="请启动 agentbridge-pty-host，或切换终端后端。",
            status_code=503,
            details={"reason": str(exc), "socket_path": str(self.socket_path)},
        )

    def current_auth_token(self) -> str:
        token = pty_host_current_auth_token(
            auth_token=self.auth_token,
            auth_token_file=self.auth_token_file,
        )
        if token is None:
            return ""
        return token


class PtyHostSupervisor:
    def __init__(self, config: PtyHostSupervisorConfig) -> None:
        self.config = config
        self.process: subprocess.Popen[bytes] | None = None
        self.start_count = 0
        self.restart_count = 0
        self.last_error: str | None = None
        self._lock = RLock()
        self._watchdog_stop_event = Event()
        self._watchdog_thread: Thread | None = None

    def ensure_running(self) -> bool:
        with self._lock:
            healthy, health_error = self._check_health()
            if healthy:
                self.last_error = None
                return False
            self._raise_if_auth_failed(health_error)
            previous_process = self.process
            self._remove_stale_socket()
            self.process = subprocess.Popen(
                self.start_command(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
                env=self._host_env(),
            )
            self.start_count += 1
            if previous_process is not None:
                self.restart_count += 1
            deadline = time.monotonic() + self.config.startup_timeout_seconds
            while time.monotonic() < deadline:
                healthy, health_error = self._check_health()
                if healthy:
                    self.last_error = None
                    return True
                self._raise_if_auth_failed(health_error)
                if self.process.poll() is not None:
                    break
                time.sleep(max(self.config.poll_interval_seconds, 0.01))
            error = AgentBridgeError(
                ErrorCode.PLATFORM_CAPABILITY_MISSING,
                "PTY Host 启动后未就绪。",
                next_step="请检查 agentbridge-pty-host 是否能正常启动。",
                status_code=503,
                details={
                    "socket_path": str(self.config.socket_path.expanduser()),
                    "return_code": self.process.poll() if self.process else None,
                },
            )
            self.last_error = error.message
            raise error

    def start_watchdog(self) -> None:
        if not self.config.watchdog_enabled:
            return
        with self._lock:
            if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
                return
            self._watchdog_stop_event.clear()
            self._watchdog_thread = Thread(
                target=self._watchdog_loop,
                name="agentbridge-pty-host-watchdog",
                daemon=True,
            )
            self._watchdog_thread.start()

    def stop_watchdog(self) -> None:
        thread = self._watchdog_thread
        if thread is None:
            return
        self._watchdog_stop_event.set()
        thread.join(
            timeout=max(
                self.config.startup_timeout_seconds + self.config.watchdog_interval_seconds,
                1.0,
            )
        )
        if not thread.is_alive():
            self._watchdog_thread = None

    def status(self) -> dict[str, object]:
        process = self.process
        return {
            "enabled": True,
            "watchdog_enabled": self.config.watchdog_enabled,
            "watchdog_running": (
                self._watchdog_thread is not None and self._watchdog_thread.is_alive()
            ),
            "start_count": self.start_count,
            "restart_count": self.restart_count,
            "host_pid": process.pid if process is not None else None,
            "host_return_code": process.poll() if process is not None else None,
            "last_error": self.last_error,
        }

    def is_healthy(self) -> bool:
        healthy, _error = self._check_health()
        return healthy

    def _check_health(self) -> tuple[bool, AgentBridgeError | None]:
        try:
            client = PtyHostTerminalBackend(
                socket_path=self.config.socket_path,
                auth_token=self.config.auth_token,
                auth_token_file=self.config.auth_token_file,
                timeout_seconds=min(self.config.startup_timeout_seconds, 1.0),
            )
            return client.health().get("status") == "ok", None
        except AgentBridgeError as exc:
            return False, exc

    def _raise_if_auth_failed(self, exc: AgentBridgeError | None) -> None:
        if exc is None or exc.code != ErrorCode.PERMISSION_DENIED:
            return
        error = AgentBridgeError(
            ErrorCode.PERMISSION_DENIED,
            "PTY Host 已运行但 token 无效。",
            next_step=(
                "请检查 AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN 或 "
                "AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE 是否与正在运行的 "
                "agentbridge-pty-host 一致；当前不会清理该 socket。"
            ),
            status_code=403,
            details={"socket_path": str(self.config.socket_path.expanduser())},
        )
        self.last_error = error.message
        raise error from exc

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop_event.is_set():
            try:
                self.ensure_running()
            except AgentBridgeError as exc:
                self.last_error = exc.message
            self._watchdog_stop_event.wait(
                max(self.config.watchdog_interval_seconds, 0.05)
            )

    def start_command(self) -> list[str]:
        if self.config.start_command:
            return list(self.config.start_command)
        return [sys.executable, "-m", "agentbridge.pty_host"]

    def _host_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET"] = str(
            self.config.socket_path.expanduser()
        )
        if self.config.auth_token:
            env["AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN"] = self.config.auth_token
        else:
            env.pop("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN", None)
        if self.config.auth_token_file is not None:
            env["AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE"] = str(
                self.config.auth_token_file.expanduser()
            )
        else:
            env.pop("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE", None)
        env["AGENTBRIDGE_TERMINAL_PTY_OUTPUT_LIMIT_CHARS"] = str(
            self.config.max_output_chars
        )
        if self.config.host_state_path is not None:
            env["AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH"] = str(
                self.config.host_state_path.expanduser()
            )
        return env

    def _remove_stale_socket(self) -> None:
        socket_path = self.config.socket_path.expanduser()
        if not socket_path.exists():
            return
        if not socket_path.is_socket():
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "PTY Host socket 路径已被非 socket 文件占用。",
                next_step="请移除该路径或配置新的 AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET。",
                status_code=409,
                details={"socket_path": str(socket_path)},
            )
        socket_path.unlink()


class PtyHostServer(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        *,
        socket_path: Path,
        auth_token: str = "",
        auth_token_file: Path | None = None,
        backend: PtyTerminalBackend | None = None,
    ) -> None:
        self.socket_path = socket_path.expanduser()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            if not self.socket_path.is_socket():
                raise RuntimeError(f"Refusing to replace non-socket path: {self.socket_path}")
            self.socket_path.unlink()
        self.auth_token = auth_token
        self.auth_token_file = auth_token_file
        self.backend = backend or PtyTerminalBackend()
        super().__init__(str(self.socket_path), PtyHostRequestHandler)
        self.socket_path.chmod(0o600)

    def server_close(self) -> None:
        super().server_close()
        if self.socket_path.exists():
            self.socket_path.unlink()


class PtyHostRequestHandler(socketserver.StreamRequestHandler):
    server: PtyHostServer

    def handle(self) -> None:
        line = self.rfile.readline()
        try:
            request = json.loads(line.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            response = {"ok": True, "data": self.handle_request(request)}
        except AgentBridgeError as exc:
            response = {"ok": False, "error": exc.to_payload()}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            error = AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "PTY Host 请求格式无效。",
                next_step="请发送包含 token、action 和 payload 的 JSON 行。",
                details={"reason": str(exc)},
            )
            response = {"ok": False, "error": error.to_payload()}
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")

    def handle_request(self, request: dict[str, Any]) -> dict[str, object]:
        token = request.get("token")
        expected_tokens, token_configured = pty_host_auth_tokens(
            auth_token=self.server.auth_token,
            auth_token_file=self.server.auth_token_file,
        )
        if token_configured and not any(
            isinstance(token, str) and hmac.compare_digest(token, expected_token)
            for expected_token in expected_tokens
        ):
            raise AgentBridgeError(
                ErrorCode.PERMISSION_DENIED,
                "PTY Host token 无效。",
                next_step="请使用当前 PTY Host token 重新连接。",
                status_code=403,
            )
        action = str(request.get("action") or "")
        payload = request.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        backend = self.server.backend
        if action == "health":
            return {"status": "ok", "host_pid": os.getpid()}
        if action == "start":
            backend.start(
                session_id=required_str(payload, "session_id"),
                cwd=required_str(payload, "cwd"),
                command=required_str(payload, "command"),
            )
            return {"status": "started"}
        if action == "write":
            backend.write(
                session_id=required_str(payload, "session_id"),
                data=str(payload.get("data") or ""),
                kind=TerminalInputKind(str(payload.get("kind") or TerminalInputKind.TEXT.value)),
            )
            return {"status": "written"}
        if action == "signal":
            backend.signal(
                session_id=required_str(payload, "session_id"),
                name=required_str(payload, "name"),
            )
            return {"status": "signaled"}
        if action == "resize":
            backend.resize(
                session_id=required_str(payload, "session_id"),
                cols=int(payload["cols"]),
                rows=int(payload["rows"]),
            )
            return {"status": "resized"}
        if action == "snapshot":
            return {"snapshot": backend.snapshot(session_id=required_str(payload, "session_id"))}
        if action == "read_output":
            chunk = backend.read_output(
                session_id=required_str(payload, "session_id"),
                after_cursor=int(payload.get("after_cursor") or 0),
            )
            return {
                "cursor": chunk.cursor,
                "data": chunk.data,
                "snapshot": chunk.snapshot,
                "reset": chunk.reset,
            }
        if action == "status":
            return backend.status(
                session_id=required_str(payload, "session_id"),
            ).to_payload()
        if action == "terminate":
            backend.terminate(required_str(payload, "session_id"))
            return {"status": "terminated"}
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"未知 PTY Host action：{action}",
            next_step=(
                "请使用 health、start、write、signal、resize、snapshot、"
                "read_output、status 或 terminate。"
            ),
        )


def required_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            f"缺少必需字段：{key}",
            next_step=f"请在 payload 中提供 {key}。",
        )
    return value


def pty_host_auth_tokens(
    *,
    auth_token: str,
    auth_token_file: Path | None,
) -> tuple[list[str], bool]:
    tokens: list[str] = []
    configured = False
    if auth_token:
        configured = True
        tokens.append(auth_token)
    if auth_token_file is not None:
        configured = True
        file_token = pty_host_token_from_file(auth_token_file)
        if file_token:
            tokens.append(file_token)
    return tokens, configured


def pty_host_current_auth_token(
    *,
    auth_token: str,
    auth_token_file: Path | None,
) -> str | None:
    if auth_token_file is not None:
        file_token = pty_host_token_from_file(auth_token_file)
        if file_token:
            return file_token
    return auth_token or None


def pty_host_token_from_file(path: Path) -> str | None:
    try:
        return path.expanduser().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def config_from_env() -> PtyHostConfig:
    socket_path = Path(
        os.environ.get(
            "AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET",
            str(Path.home() / ".agentbridge" / "pty-host.sock"),
        )
    ).expanduser()
    token = os.environ.get("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN", "")
    token_file = os.environ.get("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE", "").strip()
    token_file_path = Path(token_file).expanduser() if token_file else None
    if (
        not token
        and token_file_path is None
        and os.environ.get("AGENTBRIDGE_TERMINAL_PTY_HOST_REQUIRE_TOKEN")
    ):
        token = secrets.token_urlsafe(32)
        print(f"AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN={token}", flush=True)
    max_output_chars = int(
        os.environ.get(
            "AGENTBRIDGE_TERMINAL_PTY_OUTPUT_LIMIT_CHARS",
            str(DEFAULT_PTY_OUTPUT_LIMIT_CHARS),
        )
    )
    host_state = os.environ.get("AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH")
    return PtyHostConfig(
        socket_path=socket_path,
        auth_token=token,
        auth_token_file=token_file_path,
        max_output_chars=max_output_chars,
        host_state_path=Path(host_state).expanduser() if host_state else None,
    )


def serve(config: PtyHostConfig) -> None:
    backend = PtyTerminalBackend(
        max_output_chars=config.max_output_chars,
        host_state_path=config.host_state_path,
    )
    server = PtyHostServer(
        socket_path=config.socket_path,
        auth_token=config.auth_token,
        auth_token_file=config.auth_token_file,
        backend=backend,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


def run() -> None:
    try:
        serve(config_from_env())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
