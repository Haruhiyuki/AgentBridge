from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentbridge.domain import AgentBridgeError, ErrorCode, LeaseOwnerType
from agentbridge.terminal_agent import TerminalInputKind
from agentbridge.terminal_daemon import LocalTerminalAgentClient


class ConsoleClientError(RuntimeError):
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        error = response.get("error", {})
        super().__init__(str(error.get("message") or "Console client request failed"))


@dataclass
class ConsoleLease:
    session_id: str
    owner_id: str
    epoch: int


class LocalConsoleClient:
    """Local console client that acquires human control before forwarding input."""

    def __init__(
        self,
        *,
        daemon: LocalTerminalAgentClient,
        session_id: str,
        owner_id: str = "local-user",
        actor_id: str | None = None,
        roles: list[str] | None = None,
        trace_prefix: str = "console",
        ttl_seconds: int = 300,
    ) -> None:
        self.daemon = daemon
        self.session_id = session_id
        self.owner_id = owner_id
        self.actor_id = actor_id or owner_id
        self.roles = roles or ["maintainer"]
        self.trace_prefix = trace_prefix
        self.ttl_seconds = ttl_seconds
        self.lease: ConsoleLease | None = None

    async def start_session(self, command: str = "sh") -> None:
        await self._request_ok(
            "start_session",
            {
                "session_id": self.session_id,
                "command": command,
                "trace_id": self._trace_id("start"),
            },
        )

    async def acquire(self) -> ConsoleLease:
        if self.lease is not None:
            return self.lease
        response = await self._request_ok(
            "acquire_human_lease",
            {
                "session_id": self.session_id,
                "owner_id": self.owner_id,
                "actor_id": self.actor_id,
                "roles": self.roles,
                "ttl_seconds": self.ttl_seconds,
                "trace_id": self._trace_id("acquire"),
            },
        )
        lease = response["lease"]
        self.lease = ConsoleLease(
            session_id=self.session_id,
            owner_id=self.owner_id,
            epoch=int(lease["epoch"]),
        )
        return self.lease

    async def send_text(self, text: str, *, request_id: str | None = None) -> str:
        return await self.submit_input(
            TerminalInputKind.TEXT,
            text,
            request_id=request_id,
        )

    async def send_paste(self, text: str, *, request_id: str | None = None) -> str:
        return await self.submit_input(
            TerminalInputKind.PASTE,
            text,
            request_id=request_id,
        )

    async def send_signal(self, name: str, *, request_id: str | None = None) -> str:
        return await self.submit_input(
            TerminalInputKind.SIGNAL,
            name,
            request_id=request_id,
        )

    async def resize(self, *, cols: int, rows: int, request_id: str | None = None) -> str:
        lease = await self.acquire()
        request_id = request_id or self._request_id("resize")
        await self._request_ok(
            "submit_input",
            {
                "session_id": self.session_id,
                "epoch": lease.epoch,
                "owner_type": LeaseOwnerType.HUMAN.value,
                "owner_id": self.owner_id,
                "type": TerminalInputKind.RESIZE.value,
                "data": "",
                "cols": cols,
                "rows": rows,
                "request_id": request_id,
                "trace_id": self._trace_id("resize"),
            },
        )
        return request_id

    async def submit_input(
        self,
        kind: TerminalInputKind,
        data: str,
        *,
        request_id: str | None = None,
    ) -> str:
        lease = await self.acquire()
        request_id = request_id or self._request_id(kind.value)
        await self._request_ok(
            "submit_input",
            {
                "session_id": self.session_id,
                "epoch": lease.epoch,
                "owner_type": LeaseOwnerType.HUMAN.value,
                "owner_id": self.owner_id,
                "type": kind.value,
                "data": data,
                "request_id": request_id,
                "trace_id": self._trace_id("input"),
            },
        )
        return request_id

    async def snapshot(self) -> str:
        response = await self._request_ok("snapshot", {"session_id": self.session_id})
        return str(response["snapshot"])

    async def release(self) -> int | None:
        if self.lease is None:
            return None
        lease = self.lease
        response = await self._request_ok(
            "release_lease",
            {
                "session_id": self.session_id,
                "epoch": lease.epoch,
                "actor_id": self.actor_id,
                "roles": self.roles,
                "trace_id": self._trace_id("release"),
            },
        )
        self.lease = None
        return int(response["next_epoch"])

    async def _request_ok(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.daemon.request(action, payload)
        if response.get("ok") is True:
            data = response.get("data") or {}
            if not isinstance(data, dict):
                raise ConsoleClientError(
                    {
                        "ok": False,
                        "error": {
                            "message": "Terminal Agent returned non-object data",
                            "response": response,
                        },
                    }
                )
            return data
        raise ConsoleClientError(response)

    def _trace_id(self, operation: str) -> str:
        return f"{self.trace_prefix}-{operation}-{uuid4().hex[:8]}"

    def _request_id(self, operation: str) -> str:
        return f"cin_{operation}_{uuid4().hex[:12]}"


def token_from_env() -> str:
    token = os.environ.get("AGENTBRIDGE_LOCAL_TOKEN")
    token_file = os.environ.get("AGENTBRIDGE_LOCAL_TOKEN_FILE")
    if token:
        return token
    if token_file:
        return Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    raise AgentBridgeError(
        ErrorCode.COMMAND_ARGUMENT_INVALID,
        "缺少本地 Terminal Agent token。",
        next_step="请设置 AGENTBRIDGE_LOCAL_TOKEN 或 AGENTBRIDGE_LOCAL_TOKEN_FILE。",
    )


def socket_path_from_env() -> Path:
    return Path(
        os.environ.get(
            "AGENTBRIDGE_TERMINAL_SOCKET",
            str(Path.home() / ".agentbridge" / "terminal-agent.sock"),
        )
    ).expanduser()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Attach to an AgentBridge terminal session.")
    parser.add_argument("session_id", help="AgentBridge session ID")
    parser.add_argument("--socket", type=Path, default=socket_path_from_env())
    parser.add_argument("--token", default=None)
    parser.add_argument("--owner-id", default="local-user")
    parser.add_argument("--actor-id", default=None)
    parser.add_argument("--role", action="append", dest="roles", default=None)
    parser.add_argument("--start", action="store_true", help="Start the terminal before input")
    parser.add_argument("--command", default="sh", help="Command used with --start")
    parser.add_argument("--send", help="Send one text payload and exit")
    parser.add_argument("--paste", help="Send one paste payload and exit")
    parser.add_argument("--snapshot", action="store_true", help="Print snapshot and exit")
    parser.add_argument("--release", action="store_true", help="Release lease on exit")
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    token = args.token or token_from_env()
    daemon = LocalTerminalAgentClient(args.socket.expanduser(), token)
    client = LocalConsoleClient(
        daemon=daemon,
        session_id=args.session_id,
        owner_id=args.owner_id,
        actor_id=args.actor_id,
        roles=args.roles,
    )
    try:
        if args.start:
            await client.start_session(args.command)
        if args.send is not None:
            await client.send_text(args.send)
            return 0
        if args.paste is not None:
            await client.send_paste(args.paste)
            return 0
        if args.snapshot:
            print(await client.snapshot())
            return 0
        await run_line_mode(client)
        return 0
    finally:
        if args.release:
            await client.release()


async def run_line_mode(client: LocalConsoleClient) -> None:
    print("AgentBridge console line mode. Ctrl-D to exit.", file=sys.stderr)
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if line == "":
            return
        await client.send_text(line)


def run() -> None:
    try:
        raise SystemExit(asyncio.run(async_main()))
    except ConsoleClientError as exc:
        error = exc.response.get("error", {})
        print(error.get("message") or str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
