from __future__ import annotations

from dataclasses import dataclass

from agentbridge.domain import (
    AgentBridgeError,
    AgentType,
    ErrorCode,
    SemanticEvent,
    SemanticEventSource,
)


@dataclass(frozen=True)
class NormalizedAgentAdapterEvent:
    event_type: str
    payload: dict[str, object]


CLAUDE_EVENT_TYPE_MAP: dict[str, str] = {
    "SessionStart": "agent.session.started",
    "MessageDisplay": "assistant.delta",
    "PreToolUse": "tool.started",
    "PostToolUse": "tool.completed",
    "PostToolUseFailure": "tool.failed",
    "FileChanged": "file_change.completed",
    "PermissionRequest": "approval.requested",
    "AskUserQuestion": "question.requested",
    "QuestionRequested": "question.requested",
    "PlanRequested": "plan.requested",
    "Stop": "turn.completed",
    "StopFailure": "turn.failed",
    "SessionEnd": "agent.session.ended",
}

CODEX_EVENT_TYPE_MAP: dict[str, str] = {
    "item/agentMessage/delta": "assistant.delta",
    "item/started": "tool.started",
    "item/completed": "tool.completed",
    "item/commandExecution/outputDelta": "tool.output.delta",
    "turn/diff/updated": "diff.updated",
    "turn/plan/updated": "plan.updated",
    "item/commandExecution/requestApproval": "approval.requested",
    "item/fileChange/requestApproval": "approval.requested",
    "tool/requestUserInput": "question.requested",
    "turn/completed": "turn.completed",
    "turn/failed": "turn.failed",
}

ADAPTER_NAME_BY_AGENT: dict[AgentType, str] = {
    AgentType.CLAUDE: "claude_hooks",
    AgentType.CODEX: "codex_app_server",
    AgentType.GENERIC_TUI: "generic_tui",
}

SUPPORTED_ADAPTER_SCHEMA_VERSIONS_BY_AGENT: dict[AgentType, set[str]] = {
    AgentType.CLAUDE: {"claude-hooks.v1"},
    AgentType.CODEX: {"codex-app-server.v1"},
}
AGENT_ADAPTER_HANDSHAKE_PROTOCOL = "agentbridge.adapter.v1"

ADAPTER_INTERACTION_REQUEST_TYPES = {
    "approval.requested",
    "question.requested",
    "plan.requested",
}

ADAPTER_INTERACTION_RESPONSE_TYPES = {
    "approval.voted",
    "interaction.answered",
    "interaction.cancelled",
    "interaction.expired",
}


def normalize_agent_adapter_event(
    *,
    agent_type: AgentType,
    adapter_event_type: str,
    payload: dict[str, object],
    schema_version: str | None = None,
) -> NormalizedAgentAdapterEvent:
    event_type = adapter_semantic_event_type(
        agent_type=agent_type,
        adapter_event_type=adapter_event_type,
    )
    normalized_payload: dict[str, object] = {
        "agent_type": agent_type.value,
        "adapter": ADAPTER_NAME_BY_AGENT[agent_type],
        "adapter_event_type": adapter_event_type,
        "schema_version": schema_version,
        "raw_event": payload,
    }
    text = adapter_text(payload)
    if text is not None:
        normalized_payload["text"] = text
    prompt = adapter_prompt(payload)
    if event_type in {"approval.requested", "question.requested", "plan.requested"}:
        normalized_payload["prompt"] = prompt or text or adapter_event_type
        normalized_payload["risk_level"] = adapter_risk_level(payload)
        options = adapter_options(payload)
        if options:
            normalized_payload["options"] = options
    tool_name = adapter_tool_name(payload)
    if tool_name is not None:
        normalized_payload["tool_name"] = tool_name
    item_id = adapter_item_id(payload)
    if item_id is not None:
        normalized_payload["adapter_item_id"] = item_id
    error = adapter_error(payload)
    if error is not None:
        normalized_payload["error"] = error
    return NormalizedAgentAdapterEvent(
        event_type=event_type,
        payload=normalized_payload,
    )


def validate_agent_adapter_event_context(
    *,
    session_agent_type: AgentType,
    agent_type: AgentType,
    schema_version: str | None,
) -> str:
    if agent_type != session_agent_type:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "Adapter agent_type 必须匹配 Session agent_type。",
            next_step="请确认 Adapter 正在向对应 Agent 类型的 Session 上报事件。",
            details={
                "session_agent_type": session_agent_type.value,
                "agent_type": agent_type.value,
            },
        )
    return validate_adapter_schema_version(
        agent_type=agent_type,
        schema_version=schema_version,
    )


def validate_adapter_schema_version(
    *,
    agent_type: AgentType,
    schema_version: str | None,
) -> str:
    supported_versions = supported_adapter_schema_versions_for(agent_type)
    normalized = schema_version.strip() if schema_version is not None else ""
    if not normalized:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "Adapter 事件必须声明 schema_version。",
            next_step="请使用已验证的 Adapter schema_version 后重试。",
            details={
                "agent_type": agent_type.value,
                "supported_schema_versions": sorted(supported_versions),
            },
        )
    if normalized not in supported_versions:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "Adapter schema_version 未通过兼容性门禁。",
            next_step="请升级 Adapter 或在版本矩阵中加入该 schema 后再启用。",
            details={
                "agent_type": agent_type.value,
                "schema_version": normalized,
                "supported_schema_versions": sorted(supported_versions),
            },
        )
    return normalized


def adapter_schema_version_supported(
    *,
    agent_type: AgentType,
    schema_version: str | None,
) -> bool:
    normalized = schema_version.strip() if schema_version is not None else ""
    return bool(normalized) and normalized in supported_adapter_schema_versions_for(agent_type)


def supported_adapter_schema_versions_for(agent_type: AgentType) -> set[str]:
    versions = SUPPORTED_ADAPTER_SCHEMA_VERSIONS_BY_AGENT.get(agent_type)
    if versions is None:
        raise unsupported_agent_error(agent_type)
    return versions


def default_adapter_schema_version_for(agent_type: AgentType) -> str:
    versions = sorted(supported_adapter_schema_versions_for(agent_type))
    if not versions:
        raise unsupported_agent_error(agent_type)
    return versions[-1]


def adapter_semantic_event_type(*, agent_type: AgentType, adapter_event_type: str) -> str:
    if agent_type == AgentType.CLAUDE:
        mapping = CLAUDE_EVENT_TYPE_MAP
    elif agent_type == AgentType.CODEX:
        mapping = CODEX_EVENT_TYPE_MAP
    else:
        raise unsupported_agent_error(agent_type)
    event_type = mapping.get(adapter_event_type)
    if event_type is None:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "未知 Agent Adapter 事件类型。",
            next_step="请确认 adapter_event_type 已加入 AgentBridge 的映射表。",
            details={
                "agent_type": agent_type.value,
                "adapter_event_type": adapter_event_type,
            },
        )
    return event_type


def adapter_text(payload: dict[str, object]) -> str | None:
    for key in ("text", "delta", "message", "content", "output"):
        value = payload.get(key)
        text = string_or_joined_text(value)
        if text:
            return text
    return None


def adapter_prompt(payload: dict[str, object]) -> str | None:
    for key in ("prompt", "reason", "question", "description", "summary"):
        value = payload.get(key)
        text = string_or_joined_text(value)
        if text:
            return text
    tool_name = adapter_tool_name(payload)
    if tool_name:
        return f"{tool_name} requires approval"
    return None


def adapter_tool_name(payload: dict[str, object]) -> str | None:
    for key in ("tool_name", "toolName", "tool", "name", "command"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    item = payload.get("item")
    if isinstance(item, dict):
        for key in ("tool_name", "toolName", "name", "command"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def adapter_item_id(payload: dict[str, object]) -> str | None:
    for key in ("item_id", "itemId", "id", "request_id", "requestId"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    item = payload.get("item")
    if isinstance(item, dict):
        value = item.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def adapter_error(payload: dict[str, object]) -> str | None:
    for key in ("error", "stderr"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def adapter_options(payload: dict[str, object]) -> list[str]:
    value = payload.get("options") or payload.get("choices")
    if not isinstance(value, list):
        return []
    options: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            options.append(item.strip())
        elif isinstance(item, dict):
            label = item.get("label") or item.get("text") or item.get("value")
            if isinstance(label, str) and label.strip():
                options.append(label.strip())
    return options


def adapter_risk_level(payload: dict[str, object]) -> str:
    value = payload.get("risk_level") or payload.get("riskLevel") or payload.get("risk")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "medium"


def string_or_joined_text(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        joined = "".join(parts).strip()
        return joined or None
    if isinstance(value, dict):
        text = value.get("text") or value.get("content")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def unsupported_agent_error(agent_type: AgentType) -> AgentBridgeError:
    return AgentBridgeError(
        ErrorCode.COMMAND_ARGUMENT_INVALID,
        "该 Agent 类型没有结构化 Adapter 事件映射。",
        next_step="请只为 claude 或 codex 会话上报结构化 adapter 事件。",
        details={"agent_type": agent_type.value},
    )


def adapter_response_frames_from_events(
    events: list[SemanticEvent],
    *,
    after_seq: int | None = None,
    limit: int = 100,
) -> list[dict[str, object]]:
    adapter_requests: dict[str, SemanticEvent] = {}
    for event in events:
        if (
            event.source == SemanticEventSource.AGENT_ADAPTER
            and event.type in ADAPTER_INTERACTION_REQUEST_TYPES
            and event.interaction_id
        ):
            adapter_requests.setdefault(event.interaction_id, event)

    frames: list[dict[str, object]] = []
    for event in events:
        if after_seq is not None and event.seq <= after_seq:
            continue
        if event.type not in ADAPTER_INTERACTION_RESPONSE_TYPES or not event.interaction_id:
            continue
        request_event = adapter_requests.get(event.interaction_id)
        if request_event is None:
            continue
        frames.append(adapter_response_frame(event, request_event))
        if len(frames) >= limit:
            break
    return frames


def adapter_response_frame(
    response_event: SemanticEvent,
    request_event: SemanticEvent,
) -> dict[str, object]:
    response_payload = response_event.payload
    request_payload = request_event.payload
    return {
        "seq": response_event.seq,
        "event_id": response_event.id,
        "type": response_event.type,
        "trace_id": response_event.trace_id,
        "interaction_id": response_event.interaction_id,
        "turn_id": response_event.turn_id,
        "ready": adapter_response_ready(response_event),
        "decision": adapter_response_decision(response_event),
        "status": response_payload.get("status"),
        "answer": response_payload.get("answer"),
        "approve": response_payload.get("approve"),
        "reason": response_payload.get("reason"),
        "adapter": request_payload.get("adapter"),
        "agent_type": request_payload.get("agent_type"),
        "adapter_event_type": request_payload.get("adapter_event_type"),
        "adapter_item_id": request_payload.get("adapter_item_id"),
        "request_event_id": request_event.id,
        "request_seq": request_event.seq,
        "request_payload": request_payload,
        "payload": response_payload,
    }


def adapter_response_ready(event: SemanticEvent) -> bool:
    if event.type in {
        "interaction.answered",
        "interaction.cancelled",
        "interaction.expired",
    }:
        return True
    if event.type == "approval.voted":
        return event.payload.get("status") == "resolved"
    return False


def adapter_response_decision(event: SemanticEvent) -> str:
    if event.type == "interaction.answered":
        return "answered"
    if event.type == "interaction.cancelled":
        return "cancelled"
    if event.type == "interaction.expired":
        return "expired"
    if event.type == "approval.voted":
        if event.payload.get("status") != "resolved":
            return "pending"
        return "approved" if event.payload.get("approve") else "denied"
    return "unknown"
