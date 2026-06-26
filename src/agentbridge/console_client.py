from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import shutil
import signal
import sys
import termios
import tty
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentbridge.domain import AgentBridgeError, ErrorCode, LeaseOwnerType
from agentbridge.terminal_agent import TerminalInputKind, TerminalOutputChunk
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


RAW_EXIT_BYTE = b"\x1d"
RAW_SIGNAL_BYTES = {
    0x03: "interrupt",
    0x04: "eof",
}
TERMINAL_REPAINT_PREFIX = "\x1b[H\x1b[2J"


class RawTerminalMode:
    def __init__(
        self,
        fd: int,
        *,
        termios_module: Any = termios,
        tty_module: Any = tty,
    ) -> None:
        self.fd = fd
        self.termios_module = termios_module
        self.tty_module = tty_module
        self._saved_attrs: list[Any] | None = None

    def __enter__(self) -> RawTerminalMode:
        self._saved_attrs = self.termios_module.tcgetattr(self.fd)
        self.tty_module.setraw(self.fd)
        return self

    def __exit__(self, *_: object) -> None:
        if self._saved_attrs is not None:
            self.termios_module.tcsetattr(
                self.fd,
                self.termios_module.TCSADRAIN,
                self._saved_attrs,
            )
            self._saved_attrs = None


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

    async def start_session(self, command: str | None = None) -> None:
        payload = {
            "session_id": self.session_id,
            "trace_id": self._trace_id("start"),
        }
        if command is not None:
            payload["command"] = command
        await self._request_ok("start_session", payload)

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

    async def read_output(self, after_cursor: int = 0) -> TerminalOutputChunk:
        response = await self._request_ok(
            "read_output",
            {"session_id": self.session_id, "after_cursor": after_cursor},
        )
        return TerminalOutputChunk(
            cursor=int(response.get("cursor") or 0),
            data=str(response.get("data") or ""),
            snapshot=str(response.get("snapshot") or ""),
            reset=bool(response.get("reset")),
        )

    async def stream_output(
        self,
        *,
        after_cursor: int = 0,
        poll_interval_seconds: float = 0.25,
    ):
        async for frame in self.daemon.stream_output(
            {
                "session_id": self.session_id,
                "after_cursor": after_cursor,
                "poll_interval_seconds": poll_interval_seconds,
            }
        ):
            if frame.get("ok") is not True:
                raise ConsoleClientError(frame)
            if frame.get("type") != "terminal.output":
                continue
            data = frame.get("data") or {}
            if not isinstance(data, dict):
                continue
            yield TerminalOutputChunk(
                cursor=int(data.get("cursor") or 0),
                data=str(data.get("data") or ""),
                snapshot=str(data.get("snapshot") or ""),
                reset=bool(data.get("reset")),
            )

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
    parser.add_argument(
        "--command",
        help="Command used with --start; defaults to the session agent launch command",
    )
    parser.add_argument("--send", help="Send one text payload and exit")
    parser.add_argument("--paste", help="Send one paste payload and exit")
    parser.add_argument("--snapshot", action="store_true", help="Print snapshot and exit")
    parser.add_argument("--release", action="store_true", help="Release lease on exit")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Run raw TTY passthrough mode; Ctrl-] exits the console",
    )
    parser.add_argument(
        "--no-follow-output",
        action="store_true",
        help="Disable snapshot polling output while running --raw",
    )
    parser.add_argument(
        "--output-poll-interval",
        type=float,
        default=0.25,
        help="Seconds between terminal snapshot polls in raw mode",
    )
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
        if args.raw:
            await run_raw_mode(
                client,
                follow_output=not args.no_follow_output,
                output_poll_interval_seconds=args.output_poll_interval,
            )
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


def decode_raw_input(chunk: bytes) -> str:
    return chunk.decode("utf-8", errors="replace")


def terminal_snapshot_update(previous: str, current: str) -> str:
    if current == previous:
        return ""
    if previous and current.startswith(previous):
        return current[len(previous) :]
    return f"{TERMINAL_REPAINT_PREFIX}{current}"


async def follow_terminal_output(
    client: LocalConsoleClient,
    *,
    output_file: Any | None = None,
    poll_interval_seconds: float = 0.25,
    stop_event: asyncio.Event | None = None,
    max_iterations: int | None = None,
) -> None:
    output_file = output_file or sys.stdout
    previous = ""
    cursor = 0
    iterations = 0
    poll_interval_seconds = max(poll_interval_seconds, 0.01)
    if hasattr(client, "stream_output") and max_iterations is None:
        async for chunk in client.stream_output(
            after_cursor=cursor,
            poll_interval_seconds=poll_interval_seconds,
        ):
            current = chunk.snapshot
            cursor = chunk.cursor
            update = (
                f"{TERMINAL_REPAINT_PREFIX}{chunk.snapshot}"
                if chunk.reset
                else chunk.data
            )
            if update:
                output_file.write(update)
                output_file.flush()
            previous = current
            if stop_event is not None and stop_event.is_set():
                return
        return

    while stop_event is None or not stop_event.is_set():
        if hasattr(client, "read_output"):
            chunk = await client.read_output(cursor)
            current = chunk.snapshot
            cursor = chunk.cursor
            update = (
                f"{TERMINAL_REPAINT_PREFIX}{chunk.snapshot}"
                if chunk.reset
                else chunk.data
            )
        else:
            current = await client.snapshot()
            update = terminal_snapshot_update(previous, current)
            cursor = len(current)
        if update:
            output_file.write(update)
            output_file.flush()
        previous = current
        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return
        await asyncio.sleep(poll_interval_seconds)


async def forward_raw_input(client: LocalConsoleClient, chunk: bytes) -> None:
    buffered = bytearray()
    for byte in chunk:
        signal_name = RAW_SIGNAL_BYTES.get(byte)
        if signal_name is None:
            buffered.append(byte)
            continue
        if buffered:
            await client.send_text(decode_raw_input(bytes(buffered)))
            buffered.clear()
        await client.send_signal(signal_name)
    if buffered:
        await client.send_text(decode_raw_input(bytes(buffered)))


async def forward_terminal_size(
    client: LocalConsoleClient,
    *,
    size_provider: Callable[[], os.terminal_size] = shutil.get_terminal_size,
) -> None:
    size = size_provider()
    await client.resize(cols=size.columns, rows=size.lines)


async def run_raw_mode(
    client: LocalConsoleClient,
    *,
    input_file: Any | None = None,
    output_file: Any | None = None,
    read_size: int = 4096,
    exit_byte: bytes = RAW_EXIT_BYTE,
    size_provider: Callable[[], os.terminal_size] = shutil.get_terminal_size,
    raw_mode_factory: Callable[[int], Any] = RawTerminalMode,
    follow_output: bool = True,
    output_poll_interval_seconds: float = 0.25,
) -> None:
    input_file = input_file or sys.stdin.buffer
    if hasattr(input_file, "isatty") and not input_file.isatty():
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "raw 模式需要连接到本地 TTY。",
            next_step="请在交互式终端中运行，或使用 --send/--paste 执行非交互输入。",
        )
    fd = input_file.fileno()
    loop = asyncio.get_running_loop()
    pending_resize_tasks: set[asyncio.Task[None]] = set()
    stop_output = asyncio.Event()
    output_task: asyncio.Task[None] | None = None

    def schedule_resize() -> None:
        task = asyncio.create_task(
            forward_terminal_size(client, size_provider=size_provider)
        )
        pending_resize_tasks.add(task)
        task.add_done_callback(pending_resize_tasks.discard)

    remove_resize_handler: Callable[[], object] | None = None
    with raw_mode_factory(fd):
        await forward_terminal_size(client, size_provider=size_provider)
        if follow_output:
            output_task = asyncio.create_task(
                follow_terminal_output(
                    client,
                    output_file=output_file,
                    poll_interval_seconds=output_poll_interval_seconds,
                    stop_event=stop_output,
                )
            )
        try:
            loop.add_signal_handler(signal.SIGWINCH, schedule_resize)

            def remove_signal_handler() -> object:
                return loop.remove_signal_handler(signal.SIGWINCH)

            remove_resize_handler = remove_signal_handler
        except (NotImplementedError, RuntimeError, ValueError):
            remove_resize_handler = None
        try:
            while True:
                chunk = await loop.run_in_executor(None, os.read, fd, read_size)
                if not chunk:
                    return
                if exit_byte and exit_byte in chunk:
                    chunk = chunk.split(exit_byte, 1)[0]
                    if chunk:
                        await forward_raw_input(client, chunk)
                    return
                await forward_raw_input(client, chunk)
        finally:
            stop_output.set()
            if output_task is not None:
                output_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await output_task
            if remove_resize_handler is not None:
                remove_resize_handler()
            if pending_resize_tasks:
                await asyncio.gather(*pending_resize_tasks, return_exceptions=True)


def run() -> None:
    try:
        raise SystemExit(asyncio.run(async_main()))
    except ConsoleClientError as exc:
        error = exc.response.get("error", {})
        print(error.get("message") or str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
