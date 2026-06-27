from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from agentbridge.agent_adapter_events import (
    ADAPTER_NAME_BY_AGENT,
    AGENT_ADAPTER_HANDSHAKE_PROTOCOL,
    adapter_schema_behavior_matrix_for,
    adapter_schema_snapshot_for,
    all_adapter_schema_behavior_matrices,
    default_adapter_schema_version_for,
    supported_adapter_schema_versions_for,
    validate_adapter_schema_version,
)
from agentbridge.domain import AgentBridgeError, AgentType

JsonTransport = Callable[
    [str, str, dict[str, str], dict[str, object] | None, float],
    dict[str, object],
]
CLAUDE_INTERACTION_HOOK_EVENTS = {
    "PermissionRequest",
    "AskUserQuestion",
    "QuestionRequested",
    "PlanRequested",
}
CLAUDE_QUESTION_TOOL_EVENT_BY_TOOL_NAME = {
    "AskUserQuestion": "AskUserQuestion",
    "QuestionRequested": "QuestionRequested",
    "ExitPlanMode": "PlanRequested",
}
CLAUDE_HOOK_SETTINGS_DEFAULT_EVENTS = (
    "SessionStart",
    "MessageDisplay",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PostToolUseFailure",
    "Stop",
    "StopFailure",
    "SessionEnd",
)
CLAUDE_HOOK_SETTINGS_OPTIONAL_EVENTS = ("FileChanged",)
CLAUDE_HOOK_SETTINGS_SUPPORTED_EVENTS = frozenset(
    CLAUDE_HOOK_SETTINGS_DEFAULT_EVENTS + CLAUDE_HOOK_SETTINGS_OPTIONAL_EVENTS
)
CLAUDE_HOOK_SETTINGS_DERIVED_EVENTS = {
    "AskUserQuestion",
    "QuestionRequested",
    "PlanRequested",
}
CLAUDE_HOOK_SETTINGS_TOOL_MATCHER_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
}
CLAUDE_HOOK_SETTINGS_BLOCKING_EVENTS = {
    "PreToolUse",
    "PermissionRequest",
}
CODEX_INTERACTION_APP_SERVER_EVENTS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/tool/requestUserInput",
    "tool/requestUserInput",
}
CODEX_QUESTION_APP_SERVER_EVENTS = {
    "item/tool/requestUserInput",
    "tool/requestUserInput",
}
CODEX_APP_SERVER_STREAM_OUTPUT_FORMATS = {"json-rpc", "action", "bridge-json"}
CODEX_APP_SERVER_RESTART_POLICIES = {"never", "on-failure", "always"}


class AgentAdapterClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload or {}


def is_transient_adapter_error(error: AgentAdapterClientError) -> bool:
    if error.status_code is None:
        return True
    return error.status_code in {408, 425, 429} or error.status_code >= 500


@dataclass(frozen=True)
class AgentAdapterClientConfig:
    base_url: str
    session_id: str
    api_token: str | None = None
    device_id: str | None = None
    device_key: str | None = None
    timeout_seconds: float = 10.0
    offline_outbox_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("base_url is required")
        if not self.session_id.strip():
            raise ValueError("session_id is required")
        if (self.device_id is None) != (self.device_key is None):
            raise ValueError("device_id and device_key must be provided together")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


class AgentAdapterEventOutbox:
    schema_version = "agentbridge.adapter_outbox.v1"

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

    def flush(self, sender: Callable[[dict[str, object]], dict[str, object]]) -> int:
        entries = self.read_entries()
        sent = 0
        for index, entry in enumerate(entries):
            payload = entry["payload"]
            try:
                sender(payload)
            except AgentAdapterClientError as exc:
                if is_transient_adapter_error(exc):
                    self.replace_entries(entries[index:])
                    raise AgentAdapterClientError(
                        "offline adapter outbox flush deferred",
                        payload={
                            "sent": sent,
                            "remaining": len(entries) - index,
                            "cause": exc.message,
                        },
                    ) from exc
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
                    raise AgentAdapterClientError(
                        f"offline adapter outbox line {line_number} is not valid JSON",
                        status_code=400,
                    ) from exc
                if not isinstance(decoded, dict):
                    raise AgentAdapterClientError(
                        f"offline adapter outbox line {line_number} must be an object",
                        status_code=400,
                    )
                payload = decoded.get("payload")
                if not isinstance(payload, dict):
                    raise AgentAdapterClientError(
                        f"offline adapter outbox line {line_number} must include payload",
                        status_code=400,
                    )
                entries.append({"payload": {str(key): value for key, value in payload.items()}})
        return entries

    def replace_entries(self, entries: list[dict[str, dict[str, object]]]) -> None:
        if not entries:
            with contextlib.suppress(FileNotFoundError):
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


class AgentAdapterControlClient:
    def __init__(
        self,
        config: AgentAdapterClientConfig,
        *,
        transport: JsonTransport | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or urllib_json_transport
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.offline_outbox = (
            AgentAdapterEventOutbox(config.offline_outbox_path)
            if config.offline_outbox_path is not None
            else None
        )

    def ingest_event(
        self,
        *,
        agent_type: AgentType,
        adapter_event_type: str,
        payload: Mapping[str, object],
        schema_version: str | None = None,
        trace_id: str = "agent-adapter-client",
        idempotency_key: str | None = None,
        turn_id: str | None = None,
        interaction_id: str | None = None,
    ) -> dict[str, object]:
        if not adapter_event_type.strip():
            raise ValueError("adapter_event_type is required")
        normalized_schema_version = validate_adapter_schema_version(
            agent_type=agent_type,
            schema_version=schema_version or default_adapter_schema_version_for(agent_type),
        )
        request_payload: dict[str, object] = {
            "agent_type": agent_type.value,
            "adapter_event_type": adapter_event_type,
            "trace_id": trace_id,
            "schema_version": normalized_schema_version,
            "payload": dict(payload),
        }
        optional_fields = {
            "idempotency_key": idempotency_key,
            "turn_id": turn_id,
            "interaction_id": interaction_id,
        }
        for key, value in optional_fields.items():
            if value is not None:
                request_payload[key] = value
        if self.offline_outbox is None:
            return self._post_adapter_event_payload(request_payload)
        try:
            self.flush_offline_events()
            return self._post_adapter_event_payload(request_payload)
        except AgentAdapterClientError as exc:
            if not is_transient_adapter_error(exc):
                raise
            queued_count = self.offline_outbox.append(request_payload)
            return {
                "offline_queued": True,
                "outbox_path": str(self.offline_outbox.path),
                "queued_count": queued_count,
                "agent_type": agent_type.value,
                "adapter_event_type": adapter_event_type,
                "trace_id": trace_id,
                "schema_version": normalized_schema_version,
                "idempotency_key": idempotency_key,
            }

    def flush_offline_events(self) -> int:
        if self.offline_outbox is None:
            return 0
        return self.offline_outbox.flush(self._post_adapter_event_payload)

    def poll_responses(
        self,
        *,
        after_seq: int | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        if after_seq is not None and after_seq < 0:
            raise ValueError("after_seq must be non-negative")
        if limit < 1:
            raise ValueError("limit must be positive")
        query: dict[str, str] = {"limit": str(limit)}
        if after_seq is not None:
            query["after_seq"] = str(after_seq)
        return self._request_json(
            "GET",
            f"{self._session_path('agent-adapter/responses')}?{urlencode(query)}",
            payload=None,
        )

    def wait_for_response(
        self,
        request_event: Mapping[str, object],
        *,
        timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 1.0,
        ready_only: bool = True,
        limit: int = 100,
    ) -> dict[str, object]:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        request_event_id = string_value(request_event.get("id"))
        request_seq = int_value(request_event.get("seq"))
        request_payload = request_event.get("payload")
        adapter_item_id = None
        if isinstance(request_payload, Mapping):
            adapter_item_id = string_value(request_payload.get("adapter_item_id"))
        interaction_id = string_value(request_event.get("interaction_id"))
        if request_event_id is None and adapter_item_id is None and interaction_id is None:
            raise ValueError("request_event must include id, interaction_id, or adapter_item_id")

        after_seq = request_seq
        deadline = self.clock() + timeout_seconds
        last_response: dict[str, object] | None = None
        while True:
            response_payload = self.poll_responses(after_seq=after_seq, limit=limit)
            responses = response_payload.get("responses")
            if not isinstance(responses, list):
                raise AgentAdapterClientError("poll response payload must include responses list")
            for response in responses:
                if not isinstance(response, dict):
                    continue
                response_seq = int_value(response.get("seq"))
                if response_seq is not None and (after_seq is None or response_seq > after_seq):
                    after_seq = response_seq
                if not adapter_response_matches_request(
                    response,
                    request_event_id=request_event_id,
                    interaction_id=interaction_id,
                    adapter_item_id=adapter_item_id,
                ):
                    continue
                last_response = {str(key): value for key, value in response.items()}
                if not ready_only or response.get("ready") is True:
                    return last_response

            now = self.clock()
            if now >= deadline:
                raise AgentAdapterClientError(
                    "timed out waiting for adapter response",
                    payload={
                        "request_event_id": request_event_id,
                        "request_seq": request_seq,
                        "interaction_id": interaction_id,
                        "adapter_item_id": adapter_item_id,
                        "timeout_seconds": timeout_seconds,
                        "last_response": last_response,
                    },
                )
            self.sleep(min(poll_interval_seconds, max(deadline - now, 0.0)))

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None,
    ) -> dict[str, object]:
        url = self.config.base_url.rstrip("/") + path
        return self.transport(
            method,
            url,
            self._headers(),
            payload,
            self.config.timeout_seconds,
        )

    def _post_adapter_event_payload(
        self,
        request_payload: dict[str, object],
    ) -> dict[str, object]:
        return self._request_json(
            "POST",
            self._session_path("agent-adapter/events"),
            payload=request_payload,
        )

    def _session_path(self, suffix: str) -> str:
        session_id = quote(self.config.session_id, safe="")
        return f"/api/v1/sessions/{session_id}/{suffix}"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.config.api_token:
            headers["Authorization"] = f"Bearer {self.config.api_token}"
        if self.config.device_id and self.config.device_key:
            headers["X-AgentBridge-Device-ID"] = self.config.device_id
            headers["X-AgentBridge-Device-Key"] = self.config.device_key
        return headers


@dataclass(frozen=True)
class NativeAgentAdapterClient:
    control_client: AgentAdapterControlClient
    agent_type: AgentType
    schema_version: str

    def emit(
        self,
        adapter_event_type: str,
        payload: Mapping[str, object],
        *,
        trace_id: str = "agent-adapter-client",
        idempotency_key: str | None = None,
        turn_id: str | None = None,
        interaction_id: str | None = None,
    ) -> dict[str, object]:
        return self.control_client.ingest_event(
            agent_type=self.agent_type,
            adapter_event_type=adapter_event_type,
            payload=payload,
            schema_version=self.schema_version,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            turn_id=turn_id,
            interaction_id=interaction_id,
        )

    def poll_responses(
        self,
        *,
        after_seq: int | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        return self.control_client.poll_responses(after_seq=after_seq, limit=limit)

    def emit_and_wait(
        self,
        adapter_event_type: str,
        payload: Mapping[str, object],
        *,
        trace_id: str = "agent-adapter-client",
        idempotency_key: str | None = None,
        turn_id: str | None = None,
        interaction_id: str | None = None,
        timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 1.0,
        ready_only: bool = True,
    ) -> dict[str, object]:
        event = self.emit(
            adapter_event_type,
            payload,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            turn_id=turn_id,
            interaction_id=interaction_id,
        )
        if event.get("offline_queued") is True:
            raise AgentAdapterClientError(
                "adapter event queued offline; response unavailable until Control Plane reconnects",
                payload=event,
            )
        response = self.control_client.wait_for_response(
            event,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            ready_only=ready_only,
        )
        return {"event": event, "response": response}


class ClaudeHookAdapterClient(NativeAgentAdapterClient):
    def __init__(
        self,
        control_client: AgentAdapterControlClient,
        *,
        schema_version: str | None = None,
    ) -> None:
        super().__init__(
            control_client=control_client,
            agent_type=AgentType.CLAUDE,
            schema_version=schema_version or default_adapter_schema_version_for(AgentType.CLAUDE),
        )


class CodexAppServerAdapterClient(NativeAgentAdapterClient):
    def __init__(
        self,
        control_client: AgentAdapterControlClient,
        *,
        schema_version: str | None = None,
    ) -> None:
        super().__init__(
            control_client=control_client,
            agent_type=AgentType.CODEX,
            schema_version=schema_version or default_adapter_schema_version_for(AgentType.CODEX),
        )


def adapter_client_for_agent(
    agent_type: AgentType,
    control_client: AgentAdapterControlClient,
    *,
    schema_version: str | None = None,
) -> NativeAgentAdapterClient:
    if agent_type == AgentType.CLAUDE:
        return ClaudeHookAdapterClient(control_client, schema_version=schema_version)
    if agent_type == AgentType.CODEX:
        return CodexAppServerAdapterClient(control_client, schema_version=schema_version)
    raise ValueError(f"structured adapter client is not supported for {agent_type.value}")


def handle_claude_hook_payload(
    *,
    control_client: AgentAdapterControlClient,
    hook_payload: Mapping[str, object],
    schema_version: str | None = None,
    trace_id: str = "claude-hook-adapter",
    wait_timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 1.0,
) -> dict[str, object]:
    adapter_event_type = claude_adapter_event_type_from_hook_payload(hook_payload)
    client = ClaudeHookAdapterClient(control_client, schema_version=schema_version)
    idempotency_key = claude_hook_idempotency_key(adapter_event_type, hook_payload)
    if adapter_event_type in CLAUDE_INTERACTION_HOOK_EVENTS:
        result = client.emit_and_wait(
            adapter_event_type,
            hook_payload,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            timeout_seconds=wait_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        native_response = format_adapter_response_for_agent(
            AgentType.CLAUDE,
            result["response"],
        )
        return {
            "adapter_event_type": adapter_event_type,
            "idempotency_key": idempotency_key,
            "event": result["event"],
            "response": result["response"],
            "native_response": native_response,
            "stdout_json": native_response["stdout_json"],
        }
    event = client.emit(
        adapter_event_type,
        hook_payload,
        trace_id=trace_id,
        idempotency_key=idempotency_key,
    )
    return {
        "adapter_event_type": adapter_event_type,
        "idempotency_key": idempotency_key,
        "event": event,
        "stdout_json": None,
    }


def claude_adapter_event_type_from_hook_payload(payload: Mapping[str, object]) -> str:
    hook_event_name = (
        string_value(payload.get("hook_event_name"))
        or string_value(payload.get("hookEventName"))
        or string_value(payload.get("event"))
    )
    if not hook_event_name:
        raise ValueError("Claude hook payload must include hook_event_name")
    if hook_event_name == "PreToolUse":
        tool_name = string_value(payload.get("tool_name")) or string_value(payload.get("toolName"))
        mapped_tool_event = CLAUDE_QUESTION_TOOL_EVENT_BY_TOOL_NAME.get(tool_name or "")
        if mapped_tool_event is not None:
            return mapped_tool_event
    return hook_event_name


def claude_hook_idempotency_key(
    adapter_event_type: str,
    payload: Mapping[str, object],
) -> str:
    for key in ("tool_use_id", "toolUseId", "request_id", "requestId", "event_id", "eventId"):
        value = string_value(payload.get(key))
        if value:
            return f"claude-hook:{adapter_event_type}:{value}"
    session_id = string_value(payload.get("session_id")) or string_value(payload.get("sessionId"))
    canonical_payload = json.dumps(
        {str(key): value for key, value in payload.items()},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()[:24]
    if session_id:
        return f"claude-hook:{adapter_event_type}:{session_id}:{digest}"
    return f"claude-hook:{adapter_event_type}:{digest}"


def claude_hook_failure_stdout_json(
    hook_payload: Mapping[str, object],
    error: str,
) -> dict[str, object]:
    adapter_event_type = claude_adapter_event_type_from_hook_payload(hook_payload)
    response = {
        "decision": "denied",
        "reason": f"AgentBridge adapter failed closed: {error}",
        "adapter_event_type": adapter_event_type,
        "request_payload": {"raw_event": dict(hook_payload)},
    }
    return claude_hook_stdout_json(response)


def handle_codex_app_server_message(
    *,
    control_client: AgentAdapterControlClient,
    message: Mapping[str, object],
    schema_version: str | None = None,
    trace_id: str = "codex-app-server-adapter",
    wait_timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 1.0,
) -> dict[str, object]:
    adapter_event_type = codex_app_server_event_type_from_message(message)
    payload = codex_app_server_event_payload_from_message(message)
    client = CodexAppServerAdapterClient(control_client, schema_version=schema_version)
    idempotency_key = codex_app_server_idempotency_key(adapter_event_type, message)
    if adapter_event_type in CODEX_INTERACTION_APP_SERVER_EVENTS:
        result = client.emit_and_wait(
            adapter_event_type,
            payload,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            timeout_seconds=wait_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        native_response = format_adapter_response_for_agent(
            AgentType.CODEX,
            result["response"],
        )
        json_rpc_response = codex_app_server_json_rpc_response(
            result["response"],
            request_id=codex_app_server_message_id(message),
        )
        return {
            "adapter_event_type": adapter_event_type,
            "idempotency_key": idempotency_key,
            "event": result["event"],
            "response": result["response"],
            "native_response": native_response,
            "action": native_response["payload"],
            "json_rpc_response": json_rpc_response,
        }
    event = client.emit(
        adapter_event_type,
        payload,
        trace_id=trace_id,
        idempotency_key=idempotency_key,
    )
    return {
        "adapter_event_type": adapter_event_type,
        "idempotency_key": idempotency_key,
        "event": event,
        "action": None,
        "json_rpc_response": None,
    }


def bridge_codex_app_server_jsonl_stream(
    *,
    control_client: AgentAdapterControlClient,
    input_file: TextIO,
    output_file: TextIO,
    error_file: TextIO | None = None,
    schema_version: str | None = None,
    trace_id: str = "codex-app-server-adapter",
    wait_timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 1.0,
    output_format: str = "json-rpc",
    strict: bool = False,
) -> dict[str, object]:
    if output_format not in CODEX_APP_SERVER_STREAM_OUTPUT_FORMATS:
        raise ValueError(f"unsupported Codex app-server stream output_format: {output_format}")
    processed = 0
    skipped = 0
    emitted = 0
    errors = 0
    for line_number, raw_line in enumerate(input_file, start=1):
        if not raw_line.strip():
            skipped += 1
            continue
        try:
            decoded = json.loads(raw_line)
            if not isinstance(decoded, dict):
                raise ValueError("Codex app-server JSONL message must be a JSON object")
            message = {str(key): value for key, value in decoded.items()}
        except (json.JSONDecodeError, ValueError) as exc:
            errors += 1
            if strict:
                raise ValueError(
                    f"Codex app-server JSONL line {line_number} is invalid: {exc}"
                ) from exc
            write_codex_app_server_stream_error(
                error_file,
                line_number=line_number,
                error=str(exc),
            )
            continue
        if "method" not in message:
            skipped += 1
            continue
        processed += 1
        payload = codex_app_server_stream_message_output(
            control_client=control_client,
            message=message,
            schema_version=schema_version,
            trace_id=trace_id,
            wait_timeout_seconds=wait_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            output_format=output_format,
            strict=strict,
            error_file=error_file,
            line_number=line_number,
        )
        if payload is None:
            continue
        write_json_line(output_file, payload)
        emitted += 1
    return {
        "processed": processed,
        "skipped": skipped,
        "emitted": emitted,
        "errors": errors,
    }


class TextStreamRouter:
    def __init__(
        self,
        input_file: TextIO,
        *,
        close_output_on_input_eof: bool,
    ) -> None:
        self.input_file = input_file
        self.close_output_on_input_eof = close_output_on_input_eof
        self._condition = threading.Condition()
        self._output_file: TextIO | None = None
        self._output_lock: threading.Lock | None = None
        self._input_closed = False
        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.write_errors = 0

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout=timeout)

    def stop(self) -> None:
        with self._condition:
            self._stopped = True
            self._condition.notify_all()

    def attach(
        self,
        output_file: TextIO,
        *,
        lock: threading.Lock | None = None,
    ) -> None:
        should_close = False
        with self._condition:
            if self._stopped or (
                self._input_closed and self.close_output_on_input_eof
            ):
                should_close = True
            else:
                self._output_file = output_file
                self._output_lock = lock
                self._condition.notify_all()
        if should_close:
            close_text_output(output_file, lock=lock)

    def detach(
        self,
        output_file: TextIO,
        *,
        close_output: bool = False,
    ) -> None:
        output_lock: threading.Lock | None = None
        with self._condition:
            if self._output_file is output_file:
                output_lock = self._output_lock
                self._output_file = None
                self._output_lock = None
                self._condition.notify_all()
        if close_output:
            close_text_output(output_file, lock=output_lock)

    def close_current(self) -> None:
        output_file: TextIO | None
        output_lock: threading.Lock | None
        with self._condition:
            output_file = self._output_file
            output_lock = self._output_lock
            self._output_file = None
            self._output_lock = None
            self._condition.notify_all()
        if output_file is not None:
            close_text_output(output_file, lock=output_lock)

    def _run(self) -> None:
        try:
            for line in self.input_file:
                with self._condition:
                    if self._stopped:
                        break
                self._write_when_attached(line)
        finally:
            with self._condition:
                self._input_closed = True
                output_file = self._output_file
                output_lock = self._output_lock
                if self.close_output_on_input_eof:
                    self._output_file = None
                    self._output_lock = None
                self._condition.notify_all()
            if self.close_output_on_input_eof and output_file is not None:
                close_text_output(output_file, lock=output_lock)

    def _write_when_attached(self, line: str) -> None:
        while True:
            with self._condition:
                while self._output_file is None and not self._stopped:
                    self._condition.wait()
                if self._stopped:
                    return
                output_file = self._output_file
                output_lock = self._output_lock
            try:
                write_text(output_file, line, lock=output_lock)
                return
            except (BrokenPipeError, OSError, ValueError):
                with self._condition:
                    if self._output_file is output_file:
                        self._output_file = None
                        self._output_lock = None
                        self.write_errors += 1
                        self._condition.notify_all()


class CodexAppServerProxyHealthWriter:
    def __init__(self, output_file: TextIO | None) -> None:
        self.output_file = output_file
        self._lock = threading.Lock()
        self.events_written = 0
        self.write_errors = 0

    def write(self, status: str, **fields: object) -> None:
        if self.output_file is None:
            return
        payload: dict[str, object] = {
            "type": "agentbridge.codex_app_server_proxy.health",
            "status": status,
            "timestamp_unix": time.time(),
        }
        for key, value in fields.items():
            if value is not None:
                payload[key] = value
        with self._lock:
            try:
                write_json_line(self.output_file, payload)
            except (BrokenPipeError, OSError, ValueError):
                self.write_errors += 1
            else:
                self.events_written += 1


def bridge_codex_app_server_stdio_proxy(
    *,
    control_client: AgentAdapterControlClient,
    command_args: list[str],
    upstream_input: TextIO,
    downstream_output: TextIO,
    bridge_output: TextIO | None = None,
    error_file: TextIO | None = None,
    schema_version: str | None = None,
    trace_id: str = "codex-app-server-adapter",
    wait_timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 1.0,
    bridge_output_format: str = "json-rpc",
    inject_responses: bool = False,
    forward_injected_requests: bool = False,
    restart_policy: str = "never",
    max_restarts: int = 0,
    restart_delay_seconds: float = 1.0,
    restart_min_uptime_seconds: float = 0.0,
    health_output: TextIO | None = None,
    health_interval_seconds: float = 0.0,
    strict: bool = False,
) -> dict[str, object]:
    if not command_args:
        raise ValueError("Codex app-server proxy command_args must not be empty")
    if bridge_output_format not in CODEX_APP_SERVER_STREAM_OUTPUT_FORMATS:
        raise ValueError(
            f"unsupported Codex app-server bridge_output_format: {bridge_output_format}"
        )
    if restart_policy not in CODEX_APP_SERVER_RESTART_POLICIES:
        raise ValueError(f"unsupported Codex app-server restart_policy: {restart_policy}")
    if max_restarts < 0:
        raise ValueError("Codex app-server proxy max_restarts must be non-negative")
    if restart_delay_seconds < 0:
        raise ValueError(
            "Codex app-server proxy restart_delay_seconds must be non-negative"
        )
    if restart_min_uptime_seconds < 0:
        raise ValueError(
            "Codex app-server proxy restart_min_uptime_seconds must be non-negative"
        )
    if health_interval_seconds < 0:
        raise ValueError(
            "Codex app-server proxy health_interval_seconds must be non-negative"
        )
    health_writer = CodexAppServerProxyHealthWriter(health_output)
    stdin_router = TextStreamRouter(
        upstream_input,
        close_output_on_input_eof=not inject_responses,
    )
    stdin_router.start()
    attempts = 0
    restarts = 0
    unhealthy_exits = 0
    processed = 0
    skipped = 0
    emitted = 0
    injected = 0
    suppressed = 0
    errors = 0
    return_code = 0
    try:
        while True:
            attempts += 1
            started_at = time.monotonic()
            attempt_summary = bridge_codex_app_server_stdio_proxy_attempt(
                control_client=control_client,
                command_args=command_args,
                stdin_router=stdin_router,
                downstream_output=downstream_output,
                bridge_output=bridge_output,
                error_file=error_file,
                schema_version=schema_version,
                trace_id=trace_id,
                wait_timeout_seconds=wait_timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                bridge_output_format=bridge_output_format,
                inject_responses=inject_responses,
                forward_injected_requests=forward_injected_requests,
                restart_policy=restart_policy,
                attempt=attempts,
                health_writer=health_writer,
                health_interval_seconds=health_interval_seconds,
                strict=strict,
            )
            uptime_seconds = time.monotonic() - started_at
            return_code = int(attempt_summary["return_code"] or 0)
            processed += int(attempt_summary["processed"])
            skipped += int(attempt_summary["skipped"])
            emitted += int(attempt_summary["emitted"])
            injected += int(attempt_summary["injected"])
            suppressed += int(attempt_summary["suppressed"])
            errors += int(attempt_summary["errors"])
            if (
                restart_min_uptime_seconds > 0
                and uptime_seconds < restart_min_uptime_seconds
            ):
                unhealthy_exits += 1
            if not should_restart_codex_app_server_proxy(
                restart_policy=restart_policy,
                return_code=return_code,
            ):
                break
            if restarts >= max_restarts:
                break
            restarts += 1
            health_writer.write(
                "restarting",
                attempt=attempts,
                restart=restarts,
                max_restarts=max_restarts,
                restart_policy=restart_policy,
                return_code=return_code,
                delay_seconds=restart_delay_seconds,
            )
            if restart_delay_seconds > 0:
                time.sleep(restart_delay_seconds)
    finally:
        stdin_router.close_current()
        stdin_router.stop()
        stdin_router.join(timeout=1.0)
        health_writer.write(
            "stopped",
            attempts=attempts,
            restarts=restarts,
            restart_policy=restart_policy,
            return_code=return_code,
        )
    summary = {
        "command": command_args,
        "return_code": return_code,
        "attempts": attempts,
        "restarts": restarts,
        "restart_policy": restart_policy,
        "unhealthy_exits": unhealthy_exits,
        "stdin_write_errors": stdin_router.write_errors,
        "processed": processed,
        "skipped": skipped,
        "emitted": emitted,
        "injected": injected,
        "suppressed": suppressed,
        "errors": errors,
    }
    if health_output is not None:
        summary["health_events"] = health_writer.events_written
        summary["health_write_errors"] = health_writer.write_errors
    return summary


def bridge_codex_app_server_stdio_proxy_attempt(
    *,
    control_client: AgentAdapterControlClient,
    command_args: list[str],
    stdin_router: TextStreamRouter,
    downstream_output: TextIO,
    bridge_output: TextIO | None,
    error_file: TextIO | None,
    schema_version: str | None,
    trace_id: str,
    wait_timeout_seconds: float,
    poll_interval_seconds: float,
    bridge_output_format: str,
    inject_responses: bool,
    forward_injected_requests: bool,
    restart_policy: str,
    attempt: int,
    health_writer: CodexAppServerProxyHealthWriter,
    health_interval_seconds: float,
    strict: bool,
) -> dict[str, object]:
    process = subprocess.Popen(
        command_args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise RuntimeError("Codex app-server proxy failed to open process pipes")
    started_at = time.monotonic()
    health_writer.write(
        "started",
        attempt=attempt,
        pid=process.pid,
        restart_policy=restart_policy,
    )
    health_stop_event, health_thread = start_codex_app_server_proxy_health_probe(
        process=process,
        health_writer=health_writer,
        interval_seconds=health_interval_seconds,
        attempt=attempt,
        started_at=started_at,
    )
    stdin_lock = threading.Lock()
    stdin_router.attach(process.stdin, lock=stdin_lock)
    stderr_thread = threading.Thread(
        target=forward_text_stream,
        args=(process.stderr, error_file),
        daemon=True,
    )
    stderr_thread.start()

    processed = 0
    skipped = 0
    emitted = 0
    injected = 0
    suppressed = 0
    errors = 0
    try:
        for line_number, raw_line in enumerate(process.stdout, start=1):
            if not raw_line.strip():
                downstream_output.write(raw_line)
                downstream_output.flush()
                skipped += 1
                continue
            try:
                decoded = json.loads(raw_line)
                if not isinstance(decoded, dict):
                    raise ValueError(
                        "Codex app-server JSONL message must be a JSON object"
                    )
                message = {str(key): value for key, value in decoded.items()}
            except (json.JSONDecodeError, ValueError) as exc:
                downstream_output.write(raw_line)
                downstream_output.flush()
                errors += 1
                if strict:
                    process.terminate()
                    raise ValueError(
                        f"Codex app-server stdout line {line_number} is invalid: {exc}"
                    ) from exc
                write_codex_app_server_stream_error(
                    error_file,
                    line_number=line_number,
                    error=str(exc),
                )
                continue
            if "method" not in message:
                downstream_output.write(raw_line)
                downstream_output.flush()
                skipped += 1
                continue
            adapter_event_type = string_value(message.get("method")) or ""
            request_id = codex_app_server_message_id(message)
            should_suppress = (
                inject_responses
                and not forward_injected_requests
                and adapter_event_type in CODEX_INTERACTION_APP_SERVER_EVENTS
                and request_id is not None
            )
            if should_suppress:
                suppressed += 1
            else:
                downstream_output.write(raw_line)
                downstream_output.flush()
            processed += 1
            payloads = codex_app_server_stream_message_payloads(
                control_client=control_client,
                message=message,
                schema_version=schema_version,
                trace_id=trace_id,
                wait_timeout_seconds=wait_timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                strict=strict,
                error_file=error_file,
                line_number=line_number,
            )
            payload = payloads.get(bridge_output_format)
            if payload is not None and bridge_output is not None:
                write_json_line(bridge_output, payload)
                emitted += 1
            injection_payload = payloads.get("json-rpc")
            if inject_responses and injection_payload is not None:
                try:
                    write_json_line(process.stdin, injection_payload, lock=stdin_lock)
                except (BrokenPipeError, OSError, ValueError) as exc:
                    errors += 1
                    if strict:
                        process.terminate()
                        message = (
                            f"Codex app-server stdout line {line_number} "
                            f"response injection failed: {exc}"
                        )
                        raise RuntimeError(message) from exc
                    if should_suppress:
                        downstream_output.write(raw_line)
                        downstream_output.flush()
                        suppressed -= 1
                    write_codex_app_server_stream_error(
                        error_file,
                        line_number=line_number,
                        error=f"response injection failed: {exc}",
                    )
                else:
                    injected += 1
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        stop_codex_app_server_proxy_health_probe(health_stop_event, health_thread)
        health_writer.write(
            "terminated",
            attempt=attempt,
            pid=process.pid,
            return_code=process.returncode,
            uptime_seconds=round(time.monotonic() - started_at, 3),
        )
        raise
    finally:
        stdin_router.detach(process.stdin, close_output=True)
    return_code = process.wait()
    stop_codex_app_server_proxy_health_probe(health_stop_event, health_thread)
    health_writer.write(
        "exited",
        attempt=attempt,
        pid=process.pid,
        return_code=return_code,
        uptime_seconds=round(time.monotonic() - started_at, 3),
    )
    stderr_thread.join(timeout=1.0)
    return {
        "pid": process.pid,
        "return_code": return_code,
        "processed": processed,
        "skipped": skipped,
        "emitted": emitted,
        "injected": injected,
        "suppressed": suppressed,
        "errors": errors,
    }


def should_restart_codex_app_server_proxy(
    *,
    restart_policy: str,
    return_code: int,
) -> bool:
    if restart_policy == "always":
        return True
    if restart_policy == "on-failure":
        return return_code != 0
    return False


def start_codex_app_server_proxy_health_probe(
    *,
    process: subprocess.Popen[str],
    health_writer: CodexAppServerProxyHealthWriter,
    interval_seconds: float,
    attempt: int,
    started_at: float,
) -> tuple[threading.Event | None, threading.Thread | None]:
    if health_writer.output_file is None or interval_seconds <= 0:
        return None, None
    stop_event = threading.Event()

    def run() -> None:
        while not stop_event.wait(interval_seconds):
            if process.poll() is not None:
                return
            health_writer.write(
                "running",
                attempt=attempt,
                pid=process.pid,
                uptime_seconds=round(time.monotonic() - started_at, 3),
            )

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return stop_event, thread


def stop_codex_app_server_proxy_health_probe(
    stop_event: threading.Event | None,
    thread: threading.Thread | None,
) -> None:
    if stop_event is None or thread is None:
        return
    stop_event.set()
    thread.join(timeout=1.0)


def forward_text_stream(
    input_file: TextIO,
    output_file: TextIO | None,
    *,
    close_output: bool = False,
    lock: threading.Lock | None = None,
) -> None:
    try:
        for line in input_file:
            if output_file is None:
                continue
            try:
                write_text(output_file, line, lock=lock)
            except (BrokenPipeError, OSError, ValueError):
                return
    finally:
        if close_output and output_file is not None:
            close_text_output(output_file, lock=lock)


def codex_app_server_stream_message_output(
    *,
    control_client: AgentAdapterControlClient,
    message: Mapping[str, object],
    schema_version: str | None,
    trace_id: str,
    wait_timeout_seconds: float,
    poll_interval_seconds: float,
    output_format: str,
    strict: bool,
    error_file: TextIO | None,
    line_number: int,
) -> dict[str, object] | None:
    payloads = codex_app_server_stream_message_payloads(
        control_client=control_client,
        message=message,
        schema_version=schema_version,
        trace_id=trace_id,
        wait_timeout_seconds=wait_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        strict=strict,
        error_file=error_file,
        line_number=line_number,
    )
    return payloads.get(output_format)


def codex_app_server_stream_message_payloads(
    *,
    control_client: AgentAdapterControlClient,
    message: Mapping[str, object],
    schema_version: str | None,
    trace_id: str,
    wait_timeout_seconds: float,
    poll_interval_seconds: float,
    strict: bool,
    error_file: TextIO | None,
    line_number: int,
) -> dict[str, dict[str, object] | None]:
    try:
        result = handle_codex_app_server_message(
            control_client=control_client,
            message=message,
            schema_version=schema_version,
            trace_id=trace_id,
            wait_timeout_seconds=wait_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    except (AgentAdapterClientError, AgentBridgeError, OSError, ValueError) as exc:
        adapter_event_type = string_value(message.get("method")) or ""
        if adapter_event_type in CODEX_INTERACTION_APP_SERVER_EVENTS:
            action = codex_app_server_failure_action_payload(
                adapter_event_type=adapter_event_type,
                message=message,
                error=str(exc),
            )
            request_id = codex_app_server_message_id(message)
            bridge_payload = {
                "adapter_event_type": adapter_event_type,
                "action": action,
                "error": str(exc),
            }
            json_rpc_payload = (
                {
                    "id": request_id,
                    "result": {"agentbridge": action},
                }
                if request_id is not None
                else None
            )
            return {
                "bridge-json": bridge_payload,
                "json-rpc": json_rpc_payload,
                "action": action,
            }
        if strict:
            raise
        write_codex_app_server_stream_error(
            error_file,
            line_number=line_number,
            error=str(exc),
        )
        return {"bridge-json": None, "json-rpc": None, "action": None}
    json_rpc_payload = result.get("json_rpc_response")
    action_payload = result.get("action")
    return {
        "bridge-json": result,
        "json-rpc": json_rpc_payload if isinstance(json_rpc_payload, dict) else None,
        "action": action_payload if isinstance(action_payload, dict) else None,
    }


def write_codex_app_server_stream_error(
    error_file: TextIO | None,
    *,
    line_number: int,
    error: str,
) -> None:
    if error_file is None:
        return
    print(
        f"codex app-server stream line {line_number} failed open: {error}",
        file=error_file,
    )


def write_json_line(
    output_file: TextIO,
    payload: Mapping[str, object],
    *,
    lock: threading.Lock | None = None,
) -> None:
    write_text(
        output_file,
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        lock=lock,
    )


def write_text(
    output_file: TextIO,
    text: str,
    *,
    lock: threading.Lock | None = None,
) -> None:
    if lock is None:
        output_file.write(text)
        output_file.flush()
        return
    with lock:
        output_file.write(text)
        output_file.flush()


def close_text_output(
    output_file: TextIO,
    *,
    lock: threading.Lock | None = None,
) -> None:
    try:
        if lock is None:
            output_file.close()
            return
        with lock:
            output_file.close()
    except OSError:
        return


def codex_app_server_event_type_from_message(message: Mapping[str, object]) -> str:
    method = string_value(message.get("method"))
    if method is None:
        raise ValueError("Codex app-server message must include method")
    return method


def codex_app_server_event_payload_from_message(
    message: Mapping[str, object],
) -> dict[str, object]:
    params = message.get("params")
    if params is None:
        payload: dict[str, object] = {}
    elif isinstance(params, Mapping):
        payload = {str(key): value for key, value in params.items()}
    else:
        payload = {"params": params}
    method = codex_app_server_event_type_from_message(message)
    payload.setdefault("json_rpc_method", method)
    request_id = codex_app_server_message_id(message)
    if request_id is not None:
        payload.setdefault("json_rpc_id", request_id)
    return payload


def codex_app_server_message_id(message: Mapping[str, object]) -> str | int | float | None:
    if "id" not in message:
        return None
    value = message.get("id")
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value if value else None
    if isinstance(value, int | float):
        return value
    return None


def codex_app_server_idempotency_key(
    adapter_event_type: str,
    message: Mapping[str, object],
) -> str:
    request_id = codex_app_server_message_id(message)
    if request_id is not None:
        return f"codex-app-server:{adapter_event_type}:rpc:{request_id}"
    params = message.get("params")
    if isinstance(params, Mapping):
        item_id = codex_app_server_item_id(params)
        if item_id:
            return f"codex-app-server:{adapter_event_type}:item:{item_id}"
    canonical_message = json.dumps(
        {str(key): value for key, value in message.items()},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical_message.encode("utf-8")).hexdigest()[:24]
    return f"codex-app-server:{adapter_event_type}:{digest}"


def codex_app_server_item_id(params: Mapping[str, object]) -> str | None:
    for key in ("item_id", "itemId", "id", "request_id", "requestId"):
        value = string_value(params.get(key))
        if value:
            return value
    item = params.get("item")
    if isinstance(item, Mapping):
        return string_value(item.get("id"))
    return None


def codex_app_server_failure_action_payload(
    *,
    adapter_event_type: str,
    message: Mapping[str, object],
    error: str,
) -> dict[str, object]:
    decision = (
        "cancelled" if adapter_event_type in CODEX_QUESTION_APP_SERVER_EVENTS else "denied"
    )
    response = {
        "decision": decision,
        "approve": False if decision == "denied" else None,
        "answer": None,
        "reason": f"AgentBridge adapter failed closed: {error}",
        "adapter_event_type": adapter_event_type,
        "request_payload": {
            "raw_event": codex_app_server_event_payload_from_message(message),
        },
    }
    return codex_app_server_action_payload(response)


def claude_hook_settings_fragment(
    *,
    hook_command: str = "agentbridge-adapter-client",
    api_url: str | None = None,
    session_id: str | None = None,
    api_token: str | None = None,
    api_token_file: Path | None = None,
    device_id: str | None = None,
    device_key: str | None = None,
    device_key_file: Path | None = None,
    schema_version: str | None = None,
    trace_id: str = "claude-hook-adapter",
    timeout_seconds: float = 10.0,
    wait_timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 1.0,
    hook_timeout_seconds: float | None = None,
    events: list[str] | tuple[str, ...] | None = None,
    tool_matcher: str = "*",
    file_watch_patterns: list[str] | tuple[str, ...] | None = None,
    strict: bool = False,
    include_secret_values: bool = False,
) -> dict[str, object]:
    configured_events = claude_hook_settings_events(
        events=events,
        file_watch_patterns=file_watch_patterns,
    )
    hooks: dict[str, object] = {}
    for event in configured_events:
        handler = claude_hook_settings_handler(
            event,
            hook_command=hook_command,
            api_url=api_url,
            session_id=session_id,
            api_token=api_token,
            api_token_file=api_token_file,
            device_id=device_id,
            device_key=device_key,
            device_key_file=device_key_file,
            schema_version=schema_version,
            trace_id=trace_id,
            timeout_seconds=timeout_seconds,
            wait_timeout_seconds=wait_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            hook_timeout_seconds=hook_timeout_seconds,
            strict=strict,
            include_secret_values=include_secret_values,
        )
        group: dict[str, object] = {"hooks": [handler]}
        matcher = claude_hook_settings_matcher(
            event,
            tool_matcher=tool_matcher,
            file_watch_patterns=file_watch_patterns,
        )
        if matcher is not None:
            group["matcher"] = matcher
        hooks[event] = [group]
    return {"hooks": hooks}


def claude_hook_settings_events(
    *,
    events: list[str] | tuple[str, ...] | None,
    file_watch_patterns: list[str] | tuple[str, ...] | None,
) -> tuple[str, ...]:
    configured = list(events or CLAUDE_HOOK_SETTINGS_DEFAULT_EVENTS)
    if file_watch_patterns and "FileChanged" not in configured:
        configured.append("FileChanged")
    deduplicated: list[str] = []
    for event in configured:
        if event in CLAUDE_HOOK_SETTINGS_DERIVED_EVENTS:
            raise ValueError(f"{event} is derived from PreToolUse; configure PreToolUse instead")
        if event not in CLAUDE_HOOK_SETTINGS_SUPPORTED_EVENTS:
            raise ValueError(f"unsupported Claude hook event for AgentBridge config: {event}")
        if event == "FileChanged" and not file_watch_patterns:
            raise ValueError("FileChanged hook config requires at least one --file-watch matcher")
        if event not in deduplicated:
            deduplicated.append(event)
    return tuple(deduplicated)


def claude_hook_settings_matcher(
    event: str,
    *,
    tool_matcher: str,
    file_watch_patterns: list[str] | tuple[str, ...] | None,
) -> str | None:
    if event == "FileChanged":
        patterns = [pattern.strip() for pattern in (file_watch_patterns or []) if pattern.strip()]
        if not patterns:
            raise ValueError("FileChanged hook config requires at least one --file-watch matcher")
        return "|".join(patterns)
    if event in CLAUDE_HOOK_SETTINGS_TOOL_MATCHER_EVENTS:
        return tool_matcher
    return None


def claude_hook_settings_handler(
    event: str,
    *,
    hook_command: str,
    api_url: str | None,
    session_id: str | None,
    api_token: str | None,
    api_token_file: Path | None,
    device_id: str | None,
    device_key: str | None,
    device_key_file: Path | None,
    schema_version: str | None,
    trace_id: str,
    timeout_seconds: float,
    wait_timeout_seconds: float,
    poll_interval_seconds: float,
    hook_timeout_seconds: float | None,
    strict: bool,
    include_secret_values: bool,
) -> dict[str, object]:
    args = claude_hook_command_args(
        api_url=api_url,
        session_id=session_id,
        api_token=api_token,
        api_token_file=api_token_file,
        device_id=device_id,
        device_key=device_key,
        device_key_file=device_key_file,
        schema_version=schema_version,
        trace_id=trace_id,
        timeout_seconds=timeout_seconds,
        wait_timeout_seconds=wait_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        strict=strict,
        include_secret_values=include_secret_values,
    )
    return {
        "type": "command",
        "command": hook_command,
        "args": args,
        "timeout": claude_hook_timeout_for_event(
            event,
            timeout_seconds=timeout_seconds,
            wait_timeout_seconds=wait_timeout_seconds,
            hook_timeout_seconds=hook_timeout_seconds,
        ),
    }


def claude_hook_command_args(
    *,
    api_url: str | None,
    session_id: str | None,
    api_token: str | None,
    api_token_file: Path | None,
    device_id: str | None,
    device_key: str | None,
    device_key_file: Path | None,
    schema_version: str | None,
    trace_id: str,
    timeout_seconds: float,
    wait_timeout_seconds: float,
    poll_interval_seconds: float,
    strict: bool,
    include_secret_values: bool,
) -> list[str]:
    args = ["claude-hook"]
    append_optional_cli_arg(args, "--api-url", api_url)
    append_optional_cli_arg(args, "--session-id", session_id)
    append_secret_cli_args(
        args,
        value=api_token,
        file_value=api_token_file,
        value_flag="--api-token",
        file_flag="--api-token-file",
        include_secret_values=include_secret_values,
    )
    append_optional_cli_arg(args, "--device-id", device_id)
    append_secret_cli_args(
        args,
        value=device_key,
        file_value=device_key_file,
        value_flag="--device-key",
        file_flag="--device-key-file",
        include_secret_values=include_secret_values,
    )
    append_optional_cli_arg(args, "--schema-version", schema_version)
    append_optional_cli_arg(args, "--trace-id", trace_id)
    args.extend(["--timeout-seconds", cli_number(timeout_seconds)])
    args.extend(["--wait-timeout-seconds", cli_number(wait_timeout_seconds)])
    args.extend(["--poll-interval-seconds", cli_number(poll_interval_seconds)])
    if strict:
        args.append("--strict")
    return args


def append_optional_cli_arg(args: list[str], flag: str, value: str | None) -> None:
    if value:
        args.extend([flag, value])


def append_secret_cli_args(
    args: list[str],
    *,
    value: str | None,
    file_value: Path | None,
    value_flag: str,
    file_flag: str,
    include_secret_values: bool,
) -> None:
    if value and not include_secret_values:
        raise ValueError(
            f"refusing to embed {value_flag}; use {file_flag} or --include-secret-values"
        )
    if value:
        args.extend([value_flag, value])
    if file_value is not None:
        args.extend([file_flag, str(file_value)])


def claude_hook_timeout_for_event(
    event: str,
    *,
    timeout_seconds: float,
    wait_timeout_seconds: float,
    hook_timeout_seconds: float | None,
) -> float | int:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if wait_timeout_seconds <= 0:
        raise ValueError("wait_timeout_seconds must be positive")
    if hook_timeout_seconds is not None:
        if hook_timeout_seconds <= 0:
            raise ValueError("hook_timeout_seconds must be positive")
        return json_number(hook_timeout_seconds)
    if event in CLAUDE_HOOK_SETTINGS_BLOCKING_EVENTS:
        return json_number(wait_timeout_seconds + timeout_seconds + 5.0)
    return json_number(timeout_seconds + 5.0)


def cli_number(value: float) -> str:
    number = json_number(value)
    return str(number)


def json_number(value: float) -> float | int:
    return int(value) if float(value).is_integer() else value


def write_claude_hook_settings_file(
    settings_path: Path,
    fragment: Mapping[str, object],
) -> dict[str, object]:
    existing = load_claude_settings_file(settings_path)
    merged = merge_claude_hook_settings(existing, fragment)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return merged


def load_claude_settings_file(settings_path: Path) -> dict[str, object]:
    if not settings_path.exists():
        return {}
    raw = settings_path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("Claude settings file must contain a JSON object")
    return {str(key): value for key, value in decoded.items()}


def merge_claude_hook_settings(
    existing_settings: Mapping[str, object],
    fragment: Mapping[str, object],
) -> dict[str, object]:
    fragment_hooks = fragment.get("hooks")
    if not isinstance(fragment_hooks, Mapping):
        raise ValueError("Claude hook settings fragment must include hooks object")
    merged = {str(key): value for key, value in existing_settings.items()}
    existing_hooks = merged.get("hooks")
    if existing_hooks is None:
        hooks: dict[str, object] = {}
    elif isinstance(existing_hooks, Mapping):
        hooks = {str(key): value for key, value in existing_hooks.items()}
    else:
        raise ValueError("Claude settings hooks must be a JSON object")
    for event, generated_groups in fragment_hooks.items():
        if not isinstance(generated_groups, list):
            raise ValueError("Claude hook event groups must be arrays")
        current_groups = hooks.get(str(event), [])
        if not isinstance(current_groups, list):
            raise ValueError(f"Claude hook groups for {event} must be an array")
        hooks[str(event)] = cleaned_claude_hook_groups(current_groups) + generated_groups
    merged["hooks"] = hooks
    return merged


def cleaned_claude_hook_groups(groups: list[object]) -> list[object]:
    cleaned: list[object] = []
    for group in groups:
        if not isinstance(group, dict):
            cleaned.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            cleaned.append(group)
            continue
        retained = [
            handler for handler in handlers if not is_agentbridge_claude_hook_handler(handler)
        ]
        if not retained:
            continue
        updated_group = dict(group)
        updated_group["hooks"] = retained
        cleaned.append(updated_group)
    return cleaned


def is_agentbridge_claude_hook_handler(handler: object) -> bool:
    if not isinstance(handler, dict):
        return False
    args = handler.get("args")
    return (
        handler.get("type") == "command"
        and isinstance(args, list)
        and bool(args)
        and args[0] == "claude-hook"
    )


def adapter_response_matches_request(
    response: Mapping[str, object],
    *,
    request_event_id: str | None = None,
    interaction_id: str | None = None,
    adapter_item_id: str | None = None,
) -> bool:
    if request_event_id is not None and response.get("request_event_id") == request_event_id:
        return True
    if interaction_id is not None and response.get("interaction_id") == interaction_id:
        return True
    return adapter_item_id is not None and response.get("adapter_item_id") == adapter_item_id


def format_adapter_response_for_agent(
    agent_type: AgentType,
    response: Mapping[str, object],
) -> dict[str, object]:
    if agent_type == AgentType.CLAUDE:
        stdout_json = claude_hook_stdout_json(response)
        return {
            "agent_type": agent_type.value,
            "adapter": ADAPTER_NAME_BY_AGENT[agent_type],
            "format": "claude.hooks.command_stdout.v1",
            "exit_code": 0,
            "stdout_json": stdout_json,
            "response": dict(response),
        }
    if agent_type == AgentType.CODEX:
        action_payload = codex_app_server_action_payload(response)
        return {
            "agent_type": agent_type.value,
            "adapter": ADAPTER_NAME_BY_AGENT[agent_type],
            "format": "codex.app_server.agentbridge_action.v1",
            "action": action_payload["action"],
            "payload": action_payload,
            "response": dict(response),
        }
    raise ValueError(f"native response format is not supported for {agent_type.value}")


def claude_hook_stdout_json(response: Mapping[str, object]) -> dict[str, object]:
    adapter_event_type = string_value(response.get("adapter_event_type")) or ""
    decision = string_value(response.get("decision")) or ""
    reason = string_value(response.get("reason")) or default_response_reason(decision)
    if adapter_event_type == "PermissionRequest":
        behavior = "allow" if decision == "approved" else "deny"
        decision_payload: dict[str, object] = {"behavior": behavior}
        if behavior == "deny":
            decision_payload["message"] = reason
        return {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": decision_payload,
            }
        }
    if adapter_event_type in {"AskUserQuestion", "QuestionRequested", "PlanRequested"}:
        if decision == "answered":
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "Answered through AgentBridge.",
                    "updatedInput": claude_updated_input_from_response(response),
                }
            }
        return claude_pre_tool_use_decision("deny", reason)
    if decision in {"approved", "denied", "cancelled", "expired"}:
        permission_decision = "allow" if decision == "approved" else "deny"
        return claude_pre_tool_use_decision(permission_decision, reason)
    return {
        "agentbridgeResponse": {
            "decision": decision or "unknown",
            "response": dict(response),
        }
    }


def claude_pre_tool_use_decision(
    permission_decision: str,
    reason: str,
) -> dict[str, object]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": permission_decision,
            "permissionDecisionReason": reason,
        }
    }


def claude_updated_input_from_response(response: Mapping[str, object]) -> dict[str, object]:
    answer = string_value(response.get("answer")) or ""
    raw_event = raw_request_event(response)
    tool_input = raw_event.get("tool_input")
    if isinstance(tool_input, dict):
        updated_input = {str(key): value for key, value in tool_input.items()}
    else:
        updated_input = {str(key): value for key, value in raw_event.items()}
    questions = updated_input.get("questions")
    if isinstance(questions, list) and questions:
        answers = ask_user_question_answers(answer, questions)
        if answers:
            updated_input["answers"] = answers
            return updated_input
    updated_input["answer"] = answer
    return updated_input


def ask_user_question_answers(answer: str, questions: list[object]) -> dict[str, str]:
    """把用户的作答串按题映射到各题选中的「选项标签」，回注给 AskUserQuestion。

    之前的实现把同一条作答串原样塞给每个问题（三题都得到同一个答案），且不把「2」之类
    的编号映射到真实选项，导致 Claude 收到无效答案、行为异常。这里按题解析：
    - ``1A 2B 3C``：题号 + 选项字母，分别作答；
    - ``1AC``：同一题多选连写（用于 multiSelect）；
    - 单题时可裸写 ``A`` / ``AC`` / 直接选项文字；
    每个选择都解析成该题 ``options[*].label`` 真实标签（多选用「, 」连接）。解析不到的题
    回退用原始作答串，避免空答。返回 ``{问题文本: 选中标签}``，键与 AskUserQuestion 一致。
    """
    valid = [q for q in questions if isinstance(q, dict)]
    per_question_labels: list[list[str]] = []
    for question in valid:
        labels: list[str] = []
        for option in question.get("options") or []:
            if isinstance(option, dict):
                labels.append(str(option.get("label") or "").strip())
            else:
                labels.append(str(option).strip())
        per_question_labels.append(labels)

    selections = _parse_question_selections(answer, per_question_labels)

    answers: dict[str, str] = {}
    for index, question in enumerate(valid):
        question_text = question_text_from_item(question)
        if not question_text:
            continue
        chosen = selections.get(index)
        answers[question_text] = ", ".join(chosen) if chosen else answer
    return answers


def _parse_question_selections(
    answer: str, per_question_labels: list[list[str]]
) -> dict[int, list[str]]:
    """解析作答串 → ``{题号(0基): [选中标签…]}``。无法定位的 token 忽略。"""
    num_questions = len(per_question_labels)
    selections: dict[int, list[str]] = {}
    if num_questions == 0:
        return selections
    tokens = answer.replace(",", " ").replace("，", " ").split()
    for token in tokens:
        match = re.match(r"^(\d+)([A-Za-z0-9]+)$", token)
        if match:
            question_number = int(match.group(1))
            if 1 <= question_number <= num_questions:
                labels = _resolve_option_selectors(
                    match.group(2), per_question_labels[question_number - 1]
                )
                if labels:
                    bucket = selections.setdefault(question_number - 1, [])
                    for label in labels:
                        if label not in bucket:
                            bucket.append(label)
                    continue
        # 单题：允许裸写选项字母/编号/文字，无需题号前缀。
        if num_questions == 1:
            labels = _resolve_option_selectors(token, per_question_labels[0])
            if labels:
                bucket = selections.setdefault(0, [])
                for label in labels:
                    if label not in bucket:
                        bucket.append(label)
    return selections


def _resolve_option_selectors(selector: str, labels: list[str]) -> list[str]:
    """把 ``A`` / ``AC`` / ``2`` / 选项文字解析成该题真实选项标签列表。"""
    selector = selector.strip()
    if not selector or not labels:
        return []
    # 整体直接当作选项文字匹配（用户直接打了选项标签）。
    for label in labels:
        if label and label == selector:
            return [label]
    resolved: list[str] = []
    for char in selector:
        index: int | None = None
        if char.isalpha():
            index = ord(char.upper()) - ord("A")
        elif char.isdigit():
            index = int(char) - 1
        if index is not None and 0 <= index < len(labels) and labels[index]:
            label = labels[index]
            if label not in resolved:
                resolved.append(label)
    return resolved


def question_text_from_item(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "question", "prompt", "label"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
    return None


def codex_app_server_action_payload(response: Mapping[str, object]) -> dict[str, object]:
    decision = string_value(response.get("decision")) or "unknown"
    action = {
        "approved": "approval_decision",
        "denied": "approval_decision",
        "pending": "approval_pending",
        "answered": "user_input_response",
        "cancelled": "interaction_cancelled",
        "expired": "interaction_expired",
    }.get(decision, "interaction_response")
    return {
        "action": action,
        "decision": decision,
        "approve": response.get("approve"),
        "answer": response.get("answer"),
        "reason": response.get("reason"),
        "adapter_item_id": response.get("adapter_item_id"),
        "interaction_id": response.get("interaction_id"),
        "request_event_id": response.get("request_event_id"),
        "request_seq": response.get("request_seq"),
        "payload": response.get("payload"),
    }


def codex_app_server_json_rpc_response(
    response: Mapping[str, object],
    *,
    request_id: str | int | float | None = None,
) -> dict[str, object] | None:
    resolved_request_id = request_id
    if resolved_request_id is None:
        resolved_request_id = codex_app_server_response_json_rpc_id(response)
    if resolved_request_id is None:
        return None
    return {
        "id": resolved_request_id,
        "result": {
            "agentbridge": codex_app_server_action_payload(response),
        },
    }


def codex_app_server_response_json_rpc_id(
    response: Mapping[str, object],
) -> str | int | float | None:
    raw_event = raw_request_event(response)
    value = raw_event.get("json_rpc_id")
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value if value else None
    if isinstance(value, int | float):
        return value
    return None


def raw_request_event(response: Mapping[str, object]) -> dict[str, object]:
    request_payload = response.get("request_payload")
    if not isinstance(request_payload, dict):
        return {}
    raw_event = request_payload.get("raw_event")
    if not isinstance(raw_event, dict):
        return {}
    return {str(key): value for key, value in raw_event.items()}


def default_response_reason(decision: str) -> str:
    if decision == "approved":
        return "Approved through AgentBridge."
    if decision == "answered":
        return "Answered through AgentBridge."
    if decision == "cancelled":
        return "Interaction cancelled through AgentBridge."
    if decision == "expired":
        return "Interaction expired before AgentBridge received a response."
    return "Denied through AgentBridge."


def string_value(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def int_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError:
            return None
    return None


def handshake_payload_for_agent(
    agent_type: AgentType,
    *,
    schema_version: str | None = None,
    capabilities: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, object]:
    normalized_schema_version = validate_adapter_schema_version(
        agent_type=agent_type,
        schema_version=schema_version or default_adapter_schema_version_for(agent_type),
    )
    return {
        "protocol": AGENT_ADAPTER_HANDSHAKE_PROTOCOL,
        "compatible": True,
        "agent_type": agent_type.value,
        "schema_version": normalized_schema_version,
        "supported_schema_versions": sorted(supported_adapter_schema_versions_for(agent_type)),
        "schema_snapshot": adapter_schema_snapshot_for(agent_type, normalized_schema_version),
        "capabilities": capabilities or default_adapter_capabilities(agent_type),
        "warnings": warnings or [],
    }


def default_adapter_capabilities(agent_type: AgentType) -> list[str]:
    if agent_type == AgentType.CLAUDE:
        return [
            "agentbridge.event_ingest",
            "agentbridge.response_poll",
            "claude.hooks",
            "claude.hooks.provider_snapshot",
        ]
    if agent_type == AgentType.CODEX:
        return [
            "agentbridge.event_ingest",
            "agentbridge.response_poll",
            "codex.app_server",
            "codex.app_server.json_rpc",
            "codex.app_server.jsonl_stream",
            "codex.app_server.provider_schema_snapshot",
            "codex.app_server.stdio_proxy",
        ]
    raise ValueError(f"structured adapter capabilities are not supported for {agent_type.value}")


def urllib_json_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, object] | None,
    timeout_seconds: float,
) -> dict[str, object]:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_payload = decode_error_payload(exc)
        message = str(
            error_payload.get("message")
            or error_payload.get("detail")
            or f"HTTP {exc.code}"
        )
        raise AgentAdapterClientError(
            message,
            status_code=exc.code,
            payload=error_payload,
        ) from exc
    except URLError as exc:
        raise AgentAdapterClientError(str(exc.reason)) from exc
    if not response_body.strip():
        return {}
    try:
        decoded = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise AgentAdapterClientError("response body is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise AgentAdapterClientError("response body must be a JSON object")
    return decoded


def decode_error_payload(exc: HTTPError) -> dict[str, object]:
    try:
        raw_body = exc.read().decode("utf-8")
    except OSError:
        return {}
    if not raw_body.strip():
        return {}
    try:
        decoded = json.loads(raw_body)
    except json.JSONDecodeError:
        return {"message": raw_body}
    if isinstance(decoded, dict):
        return decoded
    return {"message": raw_body}


def build_config_from_args(args: argparse.Namespace) -> AgentAdapterClientConfig:
    base_url = args.api_url or os.environ.get("AGENTBRIDGE_API_URL") or "http://127.0.0.1:8000"
    session_id = args.session_id or os.environ.get("AGENTBRIDGE_SESSION_ID") or ""
    offline_outbox_value = (
        getattr(args, "offline_outbox", None)
        or os.environ.get("AGENTBRIDGE_ADAPTER_OFFLINE_OUTBOX")
    )
    api_token = secret_value(
        args.api_token,
        args.api_token_file,
        "AGENTBRIDGE_API_TOKEN",
        "AGENTBRIDGE_API_TOKEN_FILE",
    )
    device_key = secret_value(
        args.device_key,
        args.device_key_file,
        "AGENTBRIDGE_DEVICE_KEY",
        "AGENTBRIDGE_DEVICE_KEY_FILE",
    )
    device_id = args.device_id or os.environ.get("AGENTBRIDGE_DEVICE_ID")
    return AgentAdapterClientConfig(
        base_url=base_url,
        session_id=session_id,
        api_token=api_token,
        device_id=device_id,
        device_key=device_key,
        timeout_seconds=args.timeout_seconds,
        offline_outbox_path=Path(offline_outbox_value) if offline_outbox_value else None,
    )


def secret_value(
    explicit_value: str | None,
    explicit_file: Path | None,
    env_name: str,
    env_file_name: str,
) -> str | None:
    if explicit_value:
        return explicit_value
    if explicit_file is not None:
        return explicit_file.read_text(encoding="utf-8").strip()
    env_value = os.environ.get(env_name)
    if env_value:
        return env_value
    env_file = os.environ.get(env_file_name)
    if env_file:
        return Path(env_file).read_text(encoding="utf-8").strip()
    return None


def load_payload_from_args(args: argparse.Namespace) -> dict[str, object]:
    if args.payload_json is not None:
        decoded = json.loads(args.payload_json)
    elif args.payload_file is not None:
        raw = sys.stdin.read() if str(args.payload_file) == "-" else args.payload_file.read_text()
        decoded = json.loads(raw)
    else:
        decoded = {}
    if not isinstance(decoded, dict):
        raise ValueError("payload must be a JSON object")
    return {str(key): value for key, value in decoded.items()}


def load_response_from_args(args: argparse.Namespace) -> dict[str, object]:
    if args.response_json is not None:
        decoded = json.loads(args.response_json)
    else:
        raw = sys.stdin.read() if str(args.response_file) == "-" else args.response_file.read_text()
        decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("response must be a JSON object")
    return {str(key): value for key, value in decoded.items()}


def load_hook_payload_from_args(args: argparse.Namespace) -> dict[str, object]:
    if args.input_file is not None:
        raw = sys.stdin.read() if str(args.input_file) == "-" else args.input_file.read_text()
    else:
        raw = sys.stdin.read()
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("hook payload must be a JSON object")
    return {str(key): value for key, value in decoded.items()}


def load_codex_app_server_message_from_args(args: argparse.Namespace) -> dict[str, object]:
    if args.input_file is not None:
        raw = sys.stdin.read() if str(args.input_file) == "-" else args.input_file.read_text()
    else:
        raw = sys.stdin.read()
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("Codex app-server message must be a JSON object")
    return {str(key): value for key, value in decoded.items()}


def open_text_input_from_args(args: argparse.Namespace) -> tuple[TextIO, bool]:
    input_file = getattr(args, "input_file", None)
    if input_file is None or str(input_file) == "-":
        return sys.stdin, False
    return input_file.open("r", encoding="utf-8"), True


def open_text_output_path(path: Path | None) -> tuple[TextIO | None, bool]:
    if path is None:
        return None, False
    if str(path) == "-":
        return sys.stderr, False
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", encoding="utf-8"), True


def parse_agent_type(value: str) -> AgentType:
    try:
        return AgentType(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"unsupported agent type: {value}") from exc


def add_auth_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-url", help="AgentBridge API base URL")
    parser.add_argument("--session-id", help="AgentBridge Session ID")
    parser.add_argument("--api-token", help="API bearer token")
    parser.add_argument("--api-token-file", type=Path, help="File containing API bearer token")
    parser.add_argument("--device-id", help="Managed/static device ID")
    parser.add_argument("--device-key", help="Managed/static device key")
    parser.add_argument("--device-key-file", type=Path, help="File containing device key")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument(
        "--offline-outbox",
        type=Path,
        help="JSONL file for caching adapter events while Control Plane is unavailable",
    )


def add_emit_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--agent",
        type=parse_agent_type,
        required=True,
        choices=[AgentType.CLAUDE, AgentType.CODEX],
    )
    parser.add_argument("--schema-version")
    parser.add_argument("--event-type", required=True)
    parser.add_argument("--trace-id", default="agent-adapter-client")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--turn-id")
    parser.add_argument("--interaction-id")
    payload = parser.add_mutually_exclusive_group()
    payload.add_argument("--payload-json")
    payload.add_argument("--payload-file", type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bridge native Agent adapters to AgentBridge.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    emit = subparsers.add_parser("emit", help="Post one native adapter event")
    add_auth_arguments(emit)
    add_emit_arguments(emit)

    emit_and_wait = subparsers.add_parser(
        "emit-and-wait",
        help="Post one adapter event and wait for its interaction response",
    )
    add_auth_arguments(emit_and_wait)
    add_emit_arguments(emit_and_wait)
    emit_and_wait.add_argument("--wait-timeout-seconds", type=float, default=300.0)
    emit_and_wait.add_argument("--poll-interval-seconds", type=float, default=1.0)
    emit_and_wait.add_argument(
        "--include-pending",
        action="store_true",
        help="Return the first matching response, even if it is not ready",
    )
    emit_and_wait.add_argument(
        "--native-response",
        action="store_true",
        help="Print the formatted native adapter response instead of the event/response pair",
    )
    emit_and_wait.add_argument(
        "--native-stdout-json",
        action="store_true",
        help="Print only the native stdout JSON payload when the selected format provides one",
    )

    poll = subparsers.add_parser(
        "poll-responses",
        help="Poll response frames for adapter interactions",
    )
    add_auth_arguments(poll)
    poll.add_argument("--after-seq", type=int)
    poll.add_argument("--limit", type=int, default=100)

    flush_outbox = subparsers.add_parser(
        "flush-outbox",
        help="Flush cached adapter events from the configured offline outbox",
    )
    add_auth_arguments(flush_outbox)

    handshake = subparsers.add_parser(
        "handshake",
        help="Print a structured adapter handshake payload",
    )
    handshake.add_argument(
        "--agent",
        type=parse_agent_type,
        required=True,
        choices=[AgentType.CLAUDE, AgentType.CODEX],
    )
    handshake.add_argument("--schema-version")
    handshake.add_argument("--capability", action="append", dest="capabilities")
    handshake.add_argument("--warning", action="append", dest="warnings")

    schemas = subparsers.add_parser(
        "schemas",
        help="Print supported adapter schema snapshots and behavior matrices",
    )
    schemas.add_argument(
        "--agent",
        type=parse_agent_type,
        choices=[AgentType.CLAUDE, AgentType.CODEX],
    )
    schemas.add_argument("--schema-version")

    format_response = subparsers.add_parser(
        "format-response",
        help="Format an AgentBridge adapter response for a native adapter shim",
    )
    format_response.add_argument(
        "--agent",
        type=parse_agent_type,
        required=True,
        choices=[AgentType.CLAUDE, AgentType.CODEX],
    )
    response_input = format_response.add_mutually_exclusive_group(required=True)
    response_input.add_argument("--response-json")
    response_input.add_argument("--response-file", type=Path)
    format_response.add_argument(
        "--stdout-json",
        action="store_true",
        help="Print only the native stdout JSON payload when the selected format provides one",
    )

    claude_hook = subparsers.add_parser(
        "claude-hook",
        help="Run as a Claude Code command hook bridge for the current AgentBridge session",
    )
    add_auth_arguments(claude_hook)
    claude_hook.add_argument("--schema-version")
    claude_hook.add_argument("--input-file", type=Path)
    claude_hook.add_argument("--trace-id", default="claude-hook-adapter")
    claude_hook.add_argument("--wait-timeout-seconds", type=float, default=300.0)
    claude_hook.add_argument("--poll-interval-seconds", type=float, default=1.0)
    claude_hook.add_argument(
        "--json",
        action="store_true",
        help="Print the AgentBridge bridge result instead of Claude hook stdout JSON",
    )
    claude_hook.add_argument(
        "--strict",
        action="store_true",
        help="Return an error on non-interaction hook delivery failures instead of failing open",
    )

    claude_hooks_config = subparsers.add_parser(
        "claude-hooks-config",
        help="Generate or merge a Claude Code settings fragment for AgentBridge hooks",
    )
    add_auth_arguments(claude_hooks_config)
    claude_hooks_config.add_argument(
        "--hook-command",
        default="agentbridge-adapter-client",
        help="Executable to use in Claude Code command-hook exec form",
    )
    claude_hooks_config.add_argument("--schema-version")
    claude_hooks_config.add_argument("--trace-id", default="claude-hook-adapter")
    claude_hooks_config.add_argument("--wait-timeout-seconds", type=float, default=300.0)
    claude_hooks_config.add_argument("--poll-interval-seconds", type=float, default=1.0)
    claude_hooks_config.add_argument(
        "--hook-timeout-seconds",
        type=float,
        help="Override generated Claude hook handler timeout for every event",
    )
    claude_hooks_config.add_argument(
        "--event",
        action="append",
        dest="events",
        help="Claude hook event to configure; defaults to AgentBridge-supported v1 events",
    )
    claude_hooks_config.add_argument(
        "--tool-matcher",
        default="*",
        help="Matcher for Claude tool events such as PreToolUse and PermissionRequest",
    )
    claude_hooks_config.add_argument(
        "--file-watch",
        action="append",
        dest="file_watch_patterns",
        help="FileChanged matcher to include; can be passed more than once",
    )
    claude_hooks_config.add_argument(
        "--strict",
        action="store_true",
        help="Generate hook command args that fail closed for observer delivery errors",
    )
    claude_hooks_config.add_argument(
        "--include-secret-values",
        action="store_true",
        help="Allow direct --api-token/--device-key values to be embedded in settings JSON",
    )
    claude_hooks_config.add_argument(
        "--write-file",
        type=Path,
        help="Merge the generated hooks into a Claude settings JSON file",
    )

    codex_app_server_event = subparsers.add_parser(
        "codex-app-server-event",
        help="Bridge one Codex app-server JSON-RPC message into AgentBridge",
    )
    add_auth_arguments(codex_app_server_event)
    codex_app_server_event.add_argument("--schema-version")
    codex_app_server_event.add_argument("--input-file", type=Path)
    codex_app_server_event.add_argument("--trace-id", default="codex-app-server-adapter")
    codex_app_server_event.add_argument("--wait-timeout-seconds", type=float, default=300.0)
    codex_app_server_event.add_argument("--poll-interval-seconds", type=float, default=1.0)
    codex_app_server_event.add_argument(
        "--json",
        action="store_true",
        help="Print the AgentBridge bridge result instead of the native action payload",
    )
    codex_app_server_event.add_argument(
        "--json-rpc-response",
        action="store_true",
        help="Print a JSON-RPC result response when the input message includes an id",
    )
    codex_app_server_event.add_argument(
        "--strict",
        action="store_true",
        help="Return an error on non-interaction delivery failures instead of failing open",
    )

    codex_app_server_stream = subparsers.add_parser(
        "codex-app-server-stream",
        help="Bridge a Codex app-server JSONL stream into AgentBridge",
    )
    add_auth_arguments(codex_app_server_stream)
    codex_app_server_stream.add_argument("--schema-version")
    codex_app_server_stream.add_argument("--input-file", type=Path)
    codex_app_server_stream.add_argument("--trace-id", default="codex-app-server-adapter")
    codex_app_server_stream.add_argument("--wait-timeout-seconds", type=float, default=300.0)
    codex_app_server_stream.add_argument("--poll-interval-seconds", type=float, default=1.0)
    codex_app_server_stream.add_argument(
        "--output-format",
        choices=sorted(CODEX_APP_SERVER_STREAM_OUTPUT_FORMATS),
        default="json-rpc",
        help="JSONL payload to emit for interaction responses",
    )
    codex_app_server_stream.add_argument(
        "--strict",
        action="store_true",
        help="Return an error on invalid lines or non-interaction delivery failures",
    )

    codex_app_server_proxy = subparsers.add_parser(
        "codex-app-server-proxy",
        help="Run a Codex app-server stdio subprocess while collecting JSONL events",
    )
    add_auth_arguments(codex_app_server_proxy)
    codex_app_server_proxy.add_argument("--schema-version")
    codex_app_server_proxy.add_argument("--trace-id", default="codex-app-server-adapter")
    codex_app_server_proxy.add_argument("--wait-timeout-seconds", type=float, default=300.0)
    codex_app_server_proxy.add_argument("--poll-interval-seconds", type=float, default=1.0)
    codex_app_server_proxy.add_argument(
        "--bridge-output-format",
        choices=sorted(CODEX_APP_SERVER_STREAM_OUTPUT_FORMATS),
        default="json-rpc",
        help="JSONL side-channel payload to emit for AgentBridge interaction responses",
    )
    codex_app_server_proxy.add_argument(
        "--bridge-output-file",
        type=Path,
        help="Append AgentBridge response JSONL to this side-channel file; '-' writes stderr",
    )
    codex_app_server_proxy.add_argument(
        "--inject-responses",
        action="store_true",
        help="Write AgentBridge JSON-RPC interaction responses back to the child stdin",
    )
    codex_app_server_proxy.add_argument(
        "--forward-injected-requests",
        action="store_true",
        help=(
            "Forward interaction requests to downstream stdout even when "
            "AgentBridge injects a response"
        ),
    )
    codex_app_server_proxy.add_argument(
        "--restart-policy",
        choices=sorted(CODEX_APP_SERVER_RESTART_POLICIES),
        default="never",
        help="Bounded child process restart policy for the stdio proxy",
    )
    codex_app_server_proxy.add_argument(
        "--max-restarts",
        type=int,
        default=0,
        help="Maximum child process restarts when restart policy allows it",
    )
    codex_app_server_proxy.add_argument(
        "--restart-delay-seconds",
        type=float,
        default=1.0,
        help="Delay between child process restart attempts",
    )
    codex_app_server_proxy.add_argument(
        "--restart-min-uptime-seconds",
        type=float,
        default=0.0,
        help="Count child exits before this uptime as unhealthy in the proxy summary",
    )
    codex_app_server_proxy.add_argument(
        "--health-output-file",
        type=Path,
        help="Append Codex app-server proxy health JSONL to this file; '-' writes stderr",
    )
    codex_app_server_proxy.add_argument(
        "--health-interval-seconds",
        type=float,
        default=0.0,
        help="Emit running health heartbeat JSONL at this interval; 0 disables heartbeats",
    )
    codex_app_server_proxy.add_argument(
        "--strict",
        action="store_true",
        help="Terminate on invalid child stdout lines or non-interaction delivery failures",
    )
    codex_app_server_proxy.add_argument(
        "app_server_command",
        nargs=argparse.REMAINDER,
        help="Command to launch after '--'; defaults to: codex app-server",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "handshake":
            payload = handshake_payload_for_agent(
                args.agent,
                schema_version=args.schema_version,
                capabilities=args.capabilities,
                warnings=args.warnings,
            )
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "schemas":
            if args.agent is None:
                payload = all_adapter_schema_behavior_matrices()
            elif args.schema_version:
                payload = adapter_schema_snapshot_for(args.agent, args.schema_version)
            else:
                payload = adapter_schema_behavior_matrix_for(args.agent)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "format-response":
            payload = format_adapter_response_for_agent(
                args.agent,
                load_response_from_args(args),
            )
            if args.stdout_json:
                stdout_json = payload.get("stdout_json")
                if not isinstance(stdout_json, dict):
                    raise ValueError("selected native response format has no stdout_json")
                print(json.dumps(stdout_json, ensure_ascii=False, sort_keys=True))
            else:
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "claude-hooks-config":
            fragment = claude_hook_settings_fragment(
                hook_command=args.hook_command,
                api_url=args.api_url,
                session_id=args.session_id,
                api_token=args.api_token,
                api_token_file=args.api_token_file,
                device_id=args.device_id,
                device_key=args.device_key,
                device_key_file=args.device_key_file,
                schema_version=args.schema_version,
                trace_id=args.trace_id,
                timeout_seconds=args.timeout_seconds,
                wait_timeout_seconds=args.wait_timeout_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                hook_timeout_seconds=args.hook_timeout_seconds,
                events=args.events,
                tool_matcher=args.tool_matcher,
                file_watch_patterns=args.file_watch_patterns,
                strict=args.strict,
                include_secret_values=args.include_secret_values,
            )
            payload: Mapping[str, object]
            if args.write_file is not None:
                payload = write_claude_hook_settings_file(args.write_file, fragment)
            else:
                payload = fragment
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "codex-app-server-event":
            message = load_codex_app_server_message_from_args(args)
            control_client = AgentAdapterControlClient(build_config_from_args(args))
            try:
                result = handle_codex_app_server_message(
                    control_client=control_client,
                    message=message,
                    schema_version=args.schema_version,
                    trace_id=args.trace_id,
                    wait_timeout_seconds=args.wait_timeout_seconds,
                    poll_interval_seconds=args.poll_interval_seconds,
                )
            except (AgentAdapterClientError, AgentBridgeError, OSError, ValueError) as exc:
                adapter_event_type = codex_app_server_event_type_from_message(message)
                if adapter_event_type in CODEX_INTERACTION_APP_SERVER_EVENTS:
                    action = codex_app_server_failure_action_payload(
                        adapter_event_type=adapter_event_type,
                        message=message,
                        error=str(exc),
                    )
                    request_id = codex_app_server_message_id(message)
                    if args.json_rpc_response and request_id is not None:
                        payload = {
                            "id": request_id,
                            "result": {"agentbridge": action},
                        }
                    else:
                        payload = action
                    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                    return 0
                if args.strict:
                    raise
                print(f"agent adapter client failed open: {exc}", file=sys.stderr)
                return 0
            if args.json:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
                return 0
            if args.json_rpc_response:
                json_rpc_response = result.get("json_rpc_response")
                if isinstance(json_rpc_response, dict):
                    print(json.dumps(json_rpc_response, ensure_ascii=False, sort_keys=True))
                return 0
            action = result.get("action")
            if isinstance(action, dict):
                print(json.dumps(action, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "codex-app-server-stream":
            input_file, should_close_input = open_text_input_from_args(args)
            control_client = AgentAdapterControlClient(build_config_from_args(args))
            try:
                bridge_codex_app_server_jsonl_stream(
                    control_client=control_client,
                    input_file=input_file,
                    output_file=sys.stdout,
                    error_file=sys.stderr,
                    schema_version=args.schema_version,
                    trace_id=args.trace_id,
                    wait_timeout_seconds=args.wait_timeout_seconds,
                    poll_interval_seconds=args.poll_interval_seconds,
                    output_format=args.output_format,
                    strict=args.strict,
                )
            finally:
                if should_close_input:
                    input_file.close()
            return 0
        if args.command == "codex-app-server-proxy":
            command_args = args.app_server_command or ["codex", "app-server"]
            if command_args and command_args[0] == "--":
                command_args = command_args[1:]
            bridge_output, should_close_bridge_output = open_text_output_path(
                args.bridge_output_file
            )
            health_output, should_close_health_output = open_text_output_path(
                args.health_output_file
            )
            control_client = AgentAdapterControlClient(build_config_from_args(args))
            try:
                summary = bridge_codex_app_server_stdio_proxy(
                    control_client=control_client,
                    command_args=command_args,
                    upstream_input=sys.stdin,
                    downstream_output=sys.stdout,
                    bridge_output=bridge_output,
                    error_file=sys.stderr,
                    schema_version=args.schema_version,
                    trace_id=args.trace_id,
                    wait_timeout_seconds=args.wait_timeout_seconds,
                    poll_interval_seconds=args.poll_interval_seconds,
                    bridge_output_format=args.bridge_output_format,
                    inject_responses=args.inject_responses,
                    forward_injected_requests=args.forward_injected_requests,
                    restart_policy=args.restart_policy,
                    max_restarts=args.max_restarts,
                    restart_delay_seconds=args.restart_delay_seconds,
                    restart_min_uptime_seconds=args.restart_min_uptime_seconds,
                    health_output=health_output,
                    health_interval_seconds=args.health_interval_seconds,
                    strict=args.strict,
                )
            finally:
                if should_close_bridge_output and bridge_output is not None:
                    bridge_output.close()
                if should_close_health_output and health_output is not None:
                    health_output.close()
            return int(summary["return_code"] or 0)
        if args.command == "claude-hook":
            hook_payload = load_hook_payload_from_args(args)
            control_client = AgentAdapterControlClient(build_config_from_args(args))
            try:
                result = handle_claude_hook_payload(
                    control_client=control_client,
                    hook_payload=hook_payload,
                    schema_version=args.schema_version,
                    trace_id=args.trace_id,
                    wait_timeout_seconds=args.wait_timeout_seconds,
                    poll_interval_seconds=args.poll_interval_seconds,
                )
            except (AgentAdapterClientError, AgentBridgeError, OSError, ValueError) as exc:
                adapter_event_type = claude_adapter_event_type_from_hook_payload(hook_payload)
                if adapter_event_type in CLAUDE_INTERACTION_HOOK_EVENTS:
                    payload = claude_hook_failure_stdout_json(hook_payload, str(exc))
                    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                    return 0
                if args.strict:
                    raise
                print(f"agent adapter client failed open: {exc}", file=sys.stderr)
                return 0
            if args.json:
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
                return 0
            stdout_json = result.get("stdout_json")
            if isinstance(stdout_json, dict):
                print(json.dumps(stdout_json, ensure_ascii=False, sort_keys=True))
            return 0

        control_client = AgentAdapterControlClient(build_config_from_args(args))
        if args.command == "emit":
            adapter_client = adapter_client_for_agent(
                args.agent,
                control_client,
                schema_version=args.schema_version,
            )
            result = adapter_client.emit(
                args.event_type,
                load_payload_from_args(args),
                trace_id=args.trace_id,
                idempotency_key=args.idempotency_key,
                turn_id=args.turn_id,
                interaction_id=args.interaction_id,
            )
        elif args.command == "emit-and-wait":
            adapter_client = adapter_client_for_agent(
                args.agent,
                control_client,
                schema_version=args.schema_version,
            )
            result = adapter_client.emit_and_wait(
                args.event_type,
                load_payload_from_args(args),
                trace_id=args.trace_id,
                idempotency_key=args.idempotency_key,
                turn_id=args.turn_id,
                interaction_id=args.interaction_id,
                timeout_seconds=args.wait_timeout_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                ready_only=not args.include_pending,
            )
            if args.native_response or args.native_stdout_json:
                result = format_adapter_response_for_agent(args.agent, result["response"])
                if args.native_stdout_json:
                    stdout_json = result.get("stdout_json")
                    if not isinstance(stdout_json, dict):
                        raise ValueError("selected native response format has no stdout_json")
                    result = stdout_json
        elif args.command == "poll-responses":
            result = control_client.poll_responses(
                after_seq=args.after_seq,
                limit=args.limit,
            )
        elif args.command == "flush-outbox":
            result = {
                "flushed": control_client.flush_offline_events(),
                "outbox_path": (
                    str(control_client.offline_outbox.path)
                    if control_client.offline_outbox is not None
                    else None
                ),
            }
        else:
            parser.error("unknown command")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (AgentAdapterClientError, AgentBridgeError, OSError, ValueError) as exc:
        print(f"agent adapter client failed: {exc}", file=sys.stderr)
        return 1


def run() -> None:
    raise SystemExit(main())
