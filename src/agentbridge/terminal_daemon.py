from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentbridge.api import create_repository_from_env, create_terminal_backend_from_env, env_float
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, AgentBridgeError, ErrorCode, LeaseOwnerType
from agentbridge.terminal_agent import TerminalAgentService, TerminalInputKind


@dataclass(frozen=True)
class LocalTerminalAgentConfig:
    socket_path: Path
    auth_token: str
    lifecycle_poll_interval_seconds: float = 1.0


class LocalTerminalAgentServer:
    def __init__(
        self,
        *,
        control: ControlPlane,
        terminal: TerminalAgentService,
        auth_token: str,
        lifecycle_monitor_enabled: bool = True,
        lifecycle_poll_interval_seconds: float = 1.0,
    ) -> None:
        if not auth_token:
            raise ValueError("auth_token must not be empty")
        self.control = control
        self.terminal = terminal
        self.auth_token = auth_token
        self.lifecycle_monitor_enabled = lifecycle_monitor_enabled
        self.lifecycle_poll_interval_seconds = max(float(lifecycle_poll_interval_seconds), 0.05)
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
        if self.lifecycle_monitor_enabled:
            self.terminal.start_lifecycle_monitor(
                interval_seconds=self.lifecycle_poll_interval_seconds
            )

    async def stop(self) -> None:
        self.terminal.stop_lifecycle_monitor()
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
            if not isinstance(token, str) or not hmac.compare_digest(token, self.auth_token):
                raise AgentBridgeError(
                    ErrorCode.PERMISSION_DENIED,
                    "本地 Terminal Agent token 无效。",
                    next_step="请使用当前本地 token 重新连接。",
                    status_code=403,
                )
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
        if not isinstance(token, str) or not hmac.compare_digest(token, self.auth_token):
            raise AgentBridgeError(
                ErrorCode.PERMISSION_DENIED,
                "本地 Terminal Agent token 无效。",
                next_step="请使用当前本地 token 重新连接。",
                status_code=403,
            )
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
        if action == "start_session":
            self.terminal.start_session(
                session_id=required_str(payload, "session_id"),
                command=str(payload.get("command") or "sh"),
                trace_id=str(payload.get("trace_id") or "local-terminal"),
            )
            return {"status": "started"}
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
                "请使用 health、start_session、acquire_human_lease、release_lease、"
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


def config_from_env() -> LocalTerminalAgentConfig:
    socket_path = Path(
        os.environ.get(
            "AGENTBRIDGE_TERMINAL_SOCKET",
            str(Path.home() / ".agentbridge" / "terminal-agent.sock"),
        )
    ).expanduser()
    token = os.environ.get("AGENTBRIDGE_LOCAL_TOKEN")
    token_file = os.environ.get("AGENTBRIDGE_LOCAL_TOKEN_FILE")
    if token is None and token_file:
        token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    if token is None:
        token = secrets.token_urlsafe(32)
        print(f"AGENTBRIDGE_LOCAL_TOKEN={token}", flush=True)
    return LocalTerminalAgentConfig(
        socket_path=socket_path,
        auth_token=token,
        lifecycle_poll_interval_seconds=env_float(
            "AGENTBRIDGE_TERMINAL_LIFECYCLE_POLL_INTERVAL_SECONDS",
            default=1.0,
        ),
    )


async def async_main() -> None:
    config = config_from_env()
    repository = create_repository_from_env()
    control = ControlPlane(repository=repository)
    terminal = TerminalAgentService(control, backend=create_terminal_backend_from_env())
    server = LocalTerminalAgentServer(
        control=control,
        terminal=terminal,
        auth_token=config.auth_token,
        lifecycle_poll_interval_seconds=config.lifecycle_poll_interval_seconds,
    )
    await server.serve_forever(config.socket_path)


def run() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass
