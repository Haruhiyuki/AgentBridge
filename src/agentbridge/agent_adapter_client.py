from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
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


@dataclass(frozen=True)
class AgentAdapterClientConfig:
    base_url: str
    session_id: str
    api_token: str | None = None
    device_id: str | None = None
    device_key: str | None = None
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("base_url is required")
        if not self.session_id.strip():
            raise ValueError("session_id is required")
        if (self.device_id is None) != (self.device_key is None):
            raise ValueError("device_id and device_key must be provided together")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


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
        return self._request_json(
            "POST",
            self._session_path("agent-adapter/events"),
            payload=request_payload,
        )

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
        answers: dict[str, str] = {}
        for question in questions:
            question_text = question_text_from_item(question)
            if question_text:
                answers[question_text] = answer
        if answers:
            updated_input["answers"] = answers
            return updated_input
    updated_input["answer"] = answer
    return updated_input


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
        ]
    if agent_type == AgentType.CODEX:
        return [
            "agentbridge.event_ingest",
            "agentbridge.response_poll",
            "codex.app_server",
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
        else:
            parser.error("unknown command")
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (AgentAdapterClientError, AgentBridgeError, OSError, ValueError) as exc:
        print(f"agent adapter client failed: {exc}", file=sys.stderr)
        return 1


def run() -> None:
    raise SystemExit(main())
