from __future__ import annotations

import json
import os
import secrets
import socket
import socketserver
from dataclasses import dataclass
from pathlib import Path
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
    max_output_chars: int = DEFAULT_PTY_OUTPUT_LIMIT_CHARS
    host_state_path: Path | None = None


class PtyHostTerminalBackend:
    def __init__(
        self,
        *,
        socket_path: Path,
        auth_token: str = "",
        timeout_seconds: float = 2.0,
    ) -> None:
        self.socket_path = socket_path.expanduser()
        self.auth_token = auth_token
        self.timeout_seconds = timeout_seconds

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

    def _request(self, action: str, payload: dict[str, object]) -> dict[str, Any]:
        request = {
            "token": self.auth_token,
            "action": action,
            "payload": payload,
        }
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout_seconds)
                sock.connect(str(self.socket_path))
                file = sock.makefile("rwb")
                file.write(json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n")
                file.flush()
                line = file.readline()
        except OSError as exc:
            raise AgentBridgeError(
                ErrorCode.PLATFORM_CAPABILITY_MISSING,
                "PTY Host 不可用。",
                next_step="请启动 agentbridge-pty-host，或切换终端后端。",
                status_code=503,
                details={"reason": str(exc), "socket_path": str(self.socket_path)},
            ) from exc
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


class PtyHostServer(socketserver.ThreadingUnixStreamServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        *,
        socket_path: Path,
        auth_token: str = "",
        backend: PtyTerminalBackend | None = None,
    ) -> None:
        self.socket_path = socket_path.expanduser()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            if not self.socket_path.is_socket():
                raise RuntimeError(f"Refusing to replace non-socket path: {self.socket_path}")
            self.socket_path.unlink()
        self.auth_token = auth_token
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
        if self.server.auth_token and token != self.server.auth_token:
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


def config_from_env() -> PtyHostConfig:
    socket_path = Path(
        os.environ.get(
            "AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET",
            str(Path.home() / ".agentbridge" / "pty-host.sock"),
        )
    ).expanduser()
    token = os.environ.get("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN", "")
    if not token and os.environ.get("AGENTBRIDGE_TERMINAL_PTY_HOST_REQUIRE_TOKEN"):
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
