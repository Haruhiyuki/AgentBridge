from __future__ import annotations

import re
from dataclasses import dataclass

from agentbridge.agent_adapter_provider_schemas import provider_schema_snapshot_for
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
    "error": "turn.failed",
    "item/agentMessage/delta": "assistant.delta",
    "item/started": "tool.started",
    "item/completed": "tool.completed",
    "item/commandExecution/outputDelta": "tool.output.delta",
    "turn/diff/updated": "diff.updated",
    "turn/plan/updated": "plan.updated",
    "item/commandExecution/requestApproval": "approval.requested",
    "item/fileChange/requestApproval": "approval.requested",
    "item/tool/requestUserInput": "question.requested",
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
ADAPTER_TEXT_FIELDS = ("text", "delta", "message", "content", "output")
ADAPTER_PROMPT_FIELDS = ("prompt", "reason", "question", "description", "summary")
ADAPTER_TOOL_NAME_FIELDS = ("tool_name", "toolName", "tool", "name", "command")
ADAPTER_ITEM_TOOL_NAME_FIELDS = ("tool_name", "toolName", "name", "command")
ADAPTER_ITEM_ID_FIELDS = ("item_id", "itemId", "id", "request_id", "requestId")
ADAPTER_ERROR_FIELDS = ("error", "stderr")
ADAPTER_OPTION_FIELDS = ("options", "choices")
ADAPTER_RISK_FIELDS = ("risk_level", "riskLevel", "risk")

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
VERSION_NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)+")


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
        # Claude AskUserQuestion 的真实问题+选项藏在 tool_input.questions 里，优先提取。
        aq_prompt, aq_options = claude_ask_user_question(payload)
        normalized_payload["prompt"] = aq_prompt or prompt or text or adapter_event_type
        normalized_payload["risk_level"] = adapter_risk_level(payload)
        options = aq_options or adapter_options(payload)
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


def adapter_schema_snapshot_for(
    agent_type: AgentType,
    schema_version: str | None = None,
) -> dict[str, object]:
    normalized_schema_version = validate_adapter_schema_version(
        agent_type=agent_type,
        schema_version=schema_version or default_adapter_schema_version_for(agent_type),
    )
    event_map = adapter_event_type_map_for(agent_type)
    provider_schema_snapshot = provider_schema_snapshot_for(
        agent_type_value=agent_type.value,
        schema_version=normalized_schema_version,
    )
    provider_schema_coverage = adapter_provider_schema_coverage(
        event_map=event_map,
        provider_schema_snapshot=provider_schema_snapshot,
    )
    return {
        "protocol": AGENT_ADAPTER_HANDSHAKE_PROTOCOL,
        "agent_type": agent_type.value,
        "adapter": ADAPTER_NAME_BY_AGENT[agent_type],
        "schema_version": normalized_schema_version,
        "adapter_event_types": [
            {
                "adapter_event_type": adapter_event_type,
                "semantic_event_type": semantic_event_type,
                "interaction_request": semantic_event_type
                in ADAPTER_INTERACTION_REQUEST_TYPES,
            }
            for adapter_event_type, semantic_event_type in sorted(event_map.items())
        ],
        "semantic_event_types": sorted(set(event_map.values())),
        "interaction_request_semantic_types": sorted(
            set(event_map.values()).intersection(ADAPTER_INTERACTION_REQUEST_TYPES)
        ),
        "interaction_response_semantic_types": sorted(ADAPTER_INTERACTION_RESPONSE_TYPES),
        "payload_extractors": {
            "text_fields": list(ADAPTER_TEXT_FIELDS),
            "prompt_fields": list(ADAPTER_PROMPT_FIELDS),
            "tool_name_fields": list(ADAPTER_TOOL_NAME_FIELDS),
            "item_tool_name_fields": list(ADAPTER_ITEM_TOOL_NAME_FIELDS),
            "adapter_item_id_fields": list(ADAPTER_ITEM_ID_FIELDS),
            "error_fields": list(ADAPTER_ERROR_FIELDS),
            "option_fields": list(ADAPTER_OPTION_FIELDS),
            "risk_fields": list(ADAPTER_RISK_FIELDS),
        },
        "normalization": {
            "raw_event_policy": "preserve_under_raw_event",
            "schema_version_required": True,
            "session_agent_type_match_required": True,
            "unknown_adapter_event_type": "reject",
            "interaction_idempotency": "adapter_event_idempotency_key",
        },
        "response_contract": {
            "poll_endpoint": "/api/v1/sessions/{session_id}/agent-adapter/responses",
            "matching_keys": [
                "request_event_id",
                "interaction_id",
                "adapter_item_id",
            ],
            "ready_decisions": [
                "answered",
                "approved",
                "denied",
                "cancelled",
                "expired",
            ],
            "pending_decision": "pending",
        },
        "response_application": adapter_response_application_for(agent_type),
        "compatibility": adapter_schema_compatibility_for(
            agent_type,
            normalized_schema_version,
        ),
        "provider_schema_snapshot": provider_schema_snapshot,
        "provider_schema_coverage": provider_schema_coverage,
    }


def adapter_provider_schema_coverage(
    *,
    event_map: dict[str, str],
    provider_schema_snapshot: dict[str, object] | None,
) -> dict[str, object] | None:
    if provider_schema_snapshot is None:
        return None
    provider_methods: set[str] = set()
    for section_name in (
        "server_requests",
        "server_notifications",
        "hook_events",
        "tool_matchers",
    ):
        section = provider_schema_snapshot.get(section_name)
        if isinstance(section, dict):
            provider_methods.update(str(method) for method in section)
    legacy_aliases = provider_schema_snapshot.get("legacy_aliases")
    legacy_methods = set(str(method) for method in legacy_aliases) if isinstance(
        legacy_aliases, dict
    ) else set()
    verified = sorted(method for method in event_map if method in provider_methods)
    legacy = sorted(method for method in event_map if method in legacy_methods)
    unverified = sorted(
        method for method in event_map if method not in provider_methods | legacy_methods
    )
    return {
        "generated_by": provider_schema_snapshot.get("captured_from"),
        "verified_adapter_event_types": verified,
        "legacy_alias_event_types": legacy,
        "unverified_adapter_event_types": unverified,
        "all_adapter_event_types_provider_verified": not legacy and not unverified,
    }


def adapter_schema_behavior_matrix_for(agent_type: AgentType) -> dict[str, object]:
    return {
        "protocol": AGENT_ADAPTER_HANDSHAKE_PROTOCOL,
        "agent_type": agent_type.value,
        "adapter": ADAPTER_NAME_BY_AGENT[agent_type],
        "default_schema_version": default_adapter_schema_version_for(agent_type),
        "supported_schema_versions": sorted(supported_adapter_schema_versions_for(agent_type)),
        "compatibility_matrices": [
            adapter_schema_compatibility_for(agent_type, schema_version)
            for schema_version in sorted(supported_adapter_schema_versions_for(agent_type))
        ],
        "schemas": [
            adapter_schema_snapshot_for(agent_type, schema_version)
            for schema_version in sorted(supported_adapter_schema_versions_for(agent_type))
        ],
    }


def all_adapter_schema_behavior_matrices() -> dict[str, object]:
    return {
        "protocol": AGENT_ADAPTER_HANDSHAKE_PROTOCOL,
        "agents": {
            agent_type.value: adapter_schema_behavior_matrix_for(agent_type)
            for agent_type in sorted(
                SUPPORTED_ADAPTER_SCHEMA_VERSIONS_BY_AGENT,
                key=lambda item: item.value,
            )
        },
    }


def adapter_schema_compatibility_for(
    agent_type: AgentType,
    schema_version: str,
) -> dict[str, object]:
    normalized_schema_version = validate_adapter_schema_version(
        agent_type=agent_type,
        schema_version=schema_version,
    )
    if agent_type == AgentType.CODEX:
        provider_schema_snapshot = provider_schema_snapshot_for(
            agent_type_value=agent_type.value,
            schema_version=normalized_schema_version,
        )
        return codex_app_server_compatibility_matrix(
            schema_version=normalized_schema_version,
            provider_schema_snapshot=provider_schema_snapshot,
        )
    if agent_type == AgentType.CLAUDE:
        provider_schema_snapshot = provider_schema_snapshot_for(
            agent_type_value=agent_type.value,
            schema_version=normalized_schema_version,
        )
        return claude_hooks_compatibility_matrix(
            schema_version=normalized_schema_version,
            provider_schema_snapshot=provider_schema_snapshot,
        )
    raise unsupported_agent_error(agent_type)


def codex_app_server_compatibility_matrix(
    *,
    schema_version: str,
    provider_schema_snapshot: dict[str, object] | None,
) -> dict[str, object]:
    captured_from = (
        provider_schema_snapshot.get("captured_from")
        if isinstance(provider_schema_snapshot, dict)
        else None
    )
    provider_version = (
        captured_from.get("codex_cli_version")
        if isinstance(captured_from, dict)
        else None
    )
    verified_provider_versions: list[dict[str, object]] = []
    if isinstance(provider_version, str) and provider_version.strip():
        verified_provider_versions.append(
            {
                "provider_version_text": provider_version,
                "provider_version": version_number_from_text(provider_version),
                "captured_at": provider_schema_snapshot.get("captured_at"),
                "evidence": {
                    "command": captured_from.get("command")
                    if isinstance(captured_from, dict)
                    else None,
                    "root_bundle_sha256": nested_string(
                        provider_schema_snapshot,
                        "bundle",
                        "root_bundle_sha256",
                    ),
                    "jsonrpc_schema_sha256": nested_string(
                        provider_schema_snapshot,
                        "schema_hashes",
                        "JSONRPCMessage.json",
                    ),
                },
            }
        )
    return {
        "agent_type": AgentType.CODEX.value,
        "adapter": ADAPTER_NAME_BY_AGENT[AgentType.CODEX],
        "schema_version": schema_version,
        "provider": "openai.codex_app_server",
        "verification_status": (
            "provider_snapshot_verified"
            if verified_provider_versions
            else "schema_contract_only"
        ),
        "version_policy": "warn_when_unverified",
        "provider_version_matrix": {
            "observed_version_source": "version_probe.version_text",
            "verified_provider_versions": verified_provider_versions,
        },
        "notes": [
            (
                "Schema behavior is generated from the captured provider JSON schema "
                "bundle when available."
            ),
            (
                "Unknown provider versions stay usable behind the schema handshake but "
                "are marked unverified in capability detection."
            ),
        ],
    }


def claude_hooks_compatibility_matrix(
    *,
    schema_version: str,
    provider_schema_snapshot: dict[str, object] | None,
) -> dict[str, object]:
    captured_from = (
        provider_schema_snapshot.get("captured_from")
        if isinstance(provider_schema_snapshot, dict)
        else None
    )
    provider_version = (
        captured_from.get("claude_code_version")
        if isinstance(captured_from, dict)
        else None
    )
    verified_provider_versions: list[dict[str, object]] = []
    if isinstance(provider_version, str) and provider_version.strip():
        verified_provider_versions.append(
            {
                "provider_version_text": provider_version,
                "provider_version": version_number_from_text(provider_version),
                "captured_at": provider_schema_snapshot.get("captured_at"),
                "evidence": {
                    "command": captured_from.get("command")
                    if isinstance(captured_from, dict)
                    else None,
                    "documentation_source": captured_from.get("documentation_source")
                    if isinstance(captured_from, dict)
                    else None,
                    "documentation_checked_at": captured_from.get(
                        "documentation_checked_at"
                    )
                    if isinstance(captured_from, dict)
                    else None,
                },
            }
        )
    return {
        "agent_type": AgentType.CLAUDE.value,
        "adapter": ADAPTER_NAME_BY_AGENT[AgentType.CLAUDE],
        "schema_version": schema_version,
        "provider": "anthropic.claude_code_hooks",
        "verification_status": (
            "provider_snapshot_verified"
            if verified_provider_versions
            else "schema_contract_only"
        ),
        "version_policy": "warn_when_unverified",
        "provider_version_matrix": {
            "observed_version_source": "version_probe.version_text",
            "verified_provider_versions": verified_provider_versions,
        },
        "notes": [
            (
                "Claude Hook behavior is generated from the captured provider hook "
                "reference and local Claude Code version probe when available."
            ),
            (
                "Unknown provider versions stay usable behind the schema handshake but "
                "are marked unverified in capability detection."
            ),
        ],
    }


def adapter_provider_version_verification(
    *,
    agent_type: AgentType,
    schema_version: str | None,
    provider_version_text: str | None,
) -> dict[str, object]:
    if not schema_version:
        return {
            "status": "unknown",
            "reason": "schema_version_missing",
            "provider_version_text": provider_version_text,
        }
    if not adapter_schema_version_supported(
        agent_type=agent_type,
        schema_version=schema_version,
    ):
        return {
            "status": "unknown",
            "reason": "schema_version_unsupported",
            "schema_version": schema_version,
            "provider_version_text": provider_version_text,
            "supported_schema_versions": sorted(
                supported_adapter_schema_versions_for(agent_type)
            ),
        }
    compatibility = adapter_schema_compatibility_for(agent_type, schema_version)
    provider_version_matrix = compatibility.get("provider_version_matrix")
    verified_versions = []
    if isinstance(provider_version_matrix, dict):
        raw_versions = provider_version_matrix.get("verified_provider_versions")
        if isinstance(raw_versions, list):
            verified_versions = [
                item for item in raw_versions if isinstance(item, dict)
            ]
    observed_text = provider_version_text.strip() if provider_version_text else ""
    observed_version = version_number_from_text(observed_text)
    if not observed_text:
        return {
            "status": "unknown",
            "reason": "provider_version_missing",
            "provider_version_text": None,
            "provider_version": None,
            "compatibility": compatibility,
        }
    for item in verified_versions:
        if provider_version_matches(observed_text, observed_version, item):
            return {
                "status": "verified",
                "reason": "provider_version_in_matrix",
                "provider_version_text": observed_text,
                "provider_version": observed_version,
                "matched_provider_version": item,
                "compatibility": compatibility,
            }
    return {
        "status": "unverified",
        "reason": (
            "provider_version_not_in_matrix"
            if verified_versions
            else "no_verified_provider_versions"
        ),
        "provider_version_text": observed_text,
        "provider_version": observed_version,
        "verified_provider_versions": verified_versions,
        "compatibility": compatibility,
    }


def provider_version_matches(
    observed_text: str,
    observed_version: str | None,
    verified_version: dict[str, object],
) -> bool:
    verified_text = verified_version.get("provider_version_text")
    if isinstance(verified_text, str) and observed_text == verified_text:
        return True
    verified_number = verified_version.get("provider_version")
    return (
        observed_version is not None
        and isinstance(verified_number, str)
        and observed_version == verified_number
    )


def version_number_from_text(value: str | None) -> str | None:
    if not value:
        return None
    match = VERSION_NUMBER_PATTERN.search(value)
    return match.group(0) if match else None


def nested_string(payload: dict[str, object] | None, *keys: str) -> str | None:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) else None


def adapter_event_type_map_for(agent_type: AgentType) -> dict[str, str]:
    if agent_type == AgentType.CLAUDE:
        return CLAUDE_EVENT_TYPE_MAP
    if agent_type == AgentType.CODEX:
        return CODEX_EVENT_TYPE_MAP
    raise unsupported_agent_error(agent_type)


def adapter_response_application_for(agent_type: AgentType) -> dict[str, object]:
    if agent_type == AgentType.CLAUDE:
        return {
            "format": "claude.hooks.command_stdout.v1",
            "approval_events": ["PermissionRequest", "PreToolUse"],
            "question_events": [
                "AskUserQuestion",
                "QuestionRequested",
                "PlanRequested",
            ],
            "approval_output": "hookSpecificOutput",
            "question_output": "hookSpecificOutput.updatedInput",
        }
    if agent_type == AgentType.CODEX:
        return {
            "format": "codex.app_server.agentbridge_action.v1",
            "json_rpc_response_format": "codex.app_server.json_rpc_response.v1",
            "approval_actions": [
                "approval_decision",
                "approval_pending",
            ],
            "question_actions": ["user_input_response"],
            "terminal_actions": [
                "interaction_cancelled",
                "interaction_expired",
            ],
            "json_rpc_result_path": "result.agentbridge",
            "json_rpc_method": None,
        }
    raise unsupported_agent_error(agent_type)


def adapter_semantic_event_type(*, agent_type: AgentType, adapter_event_type: str) -> str:
    event_type = adapter_event_type_map_for(agent_type).get(adapter_event_type)
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
    for key in ADAPTER_TEXT_FIELDS:
        value = payload.get(key)
        text = string_or_joined_text(value)
        if text:
            return text
    return None


def adapter_prompt(payload: dict[str, object]) -> str | None:
    for key in ADAPTER_PROMPT_FIELDS:
        value = payload.get(key)
        text = string_or_joined_text(value)
        if text:
            return text
    tool_name = adapter_tool_name(payload)
    if tool_name:
        return f"{tool_name} requires approval"
    return None


def adapter_tool_name(payload: dict[str, object]) -> str | None:
    for key in ADAPTER_TOOL_NAME_FIELDS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    item = payload.get("item")
    if isinstance(item, dict):
        for key in ADAPTER_ITEM_TOOL_NAME_FIELDS:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def adapter_item_id(payload: dict[str, object]) -> str | None:
    for key in ADAPTER_ITEM_ID_FIELDS:
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
    for key in ADAPTER_ERROR_FIELDS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def adapter_options(payload: dict[str, object]) -> list[str]:
    value = None
    for key in ADAPTER_OPTION_FIELDS:
        value = payload.get(key)
        if value is not None:
            break
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


def claude_ask_user_question(
    payload: dict[str, object],
) -> tuple[str | None, list[str]]:
    """从 Claude ``AskUserQuestion`` 工具的 ``tool_input.questions`` 提取问题文本与选项。

    AskUserQuestion 的真实内容嵌在 ``tool_input.questions[*].{question,header,options[*].label}``，
    顶层没有 prompt/options 字段，故通用提取拿不到（曾只显示占位符 "AskUserQuestion requires approval"）。
    返回 ``(prompt, options)``：单问题时 prompt 为问题文本、options 为选项标签（可被「/ab answer 编号」用）；
    多问题时把所有问题+选项铺成一段富文本作 prompt、options 留空（让用户自由作答）。
    """
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None, []
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not questions:
        return None, []

    def _opt_label_desc(opt: object) -> tuple[str, str]:
        if isinstance(opt, dict):
            return str(opt.get("label") or "").strip(), str(opt.get("description") or "").strip()
        if isinstance(opt, str):
            return opt.strip(), ""
        return "", ""

    valid = [q for q in questions if isinstance(q, dict)]
    if len(valid) == 1:
        q = valid[0]
        header = str(q.get("header") or "").strip()
        qtext = str(q.get("question") or "").strip()
        prompt = (f"[{header}] " if header else "") + qtext
        if q.get("multiSelect") and "多选" not in prompt:
            prompt += "（可多选）"
        options = [
            label for label, _ in (_opt_label_desc(o) for o in (q.get("options") or [])) if label
        ]
        return prompt or None, options

    # 多问题：铺成富文本，每题下用字母列出选项，便于「题号+选项字母」分题作答。
    lines: list[str] = []
    any_multi = False
    for qi, q in enumerate(valid, 1):
        header = str(q.get("header") or "").strip()
        qtext = str(q.get("question") or "").strip()
        title = f"{qi}) " + (f"[{header}] " if header else "") + qtext
        if q.get("multiSelect"):
            any_multi = True
            if "多选" not in title:
                title += "（可多选）"
        lines.append(title)
        for oi, opt in enumerate(q.get("options") or []):
            label, desc = _opt_label_desc(opt)
            if label:
                letter = chr(ord("A") + oi) if oi < 26 else str(oi + 1)
                lines.append(f"   {letter}. {label}" + (f" — {desc}" if desc else ""))
    example = " ".join(f"{i}A" for i in range(1, len(valid) + 1))
    hint = f"逐题作答：题号+选项字母，如 {example}"
    if any_multi:
        hint += "；可多选的题字母连写，如 1AC"
    lines.append(hint)
    return ("\n".join(lines) or None), []


def _askuserquestion_option_count(question: object) -> int:
    if not isinstance(question, dict):
        return 0
    return sum(
        1
        for opt in (question.get("options") or [])
        if (str(opt.get("label") or "").strip() if isinstance(opt, dict) else str(opt).strip())
    )


def parse_askuserquestion_selection(
    answer: str, questions: list[object]
) -> dict[int, list[int]]:
    """把用户的作答串解析成 ``{题号(0基): [选项下标(0基)…]}``。

    支持 ``1A 2B 3C``（题号+选项字母）、``1AC``（同题多选连写）、单题裸 ``A``/``AC``/数字。
    字母 A→0、B→1…；数字 1→0、2→1…。越界/重复忽略。供「驱动原生选择器」按下标算按键用。
    """
    valid = [q for q in questions if isinstance(q, dict)]
    counts = [_askuserquestion_option_count(q) for q in valid]
    selection: dict[int, list[int]] = {}

    def resolve(selector: str, option_count: int) -> list[int]:
        indices: list[int] = []
        for char in selector:
            index: int | None = None
            if char.isalpha():
                index = ord(char.upper()) - ord("A")
            elif char.isdigit():
                index = int(char) - 1
            if index is not None and 0 <= index < option_count and index not in indices:
                indices.append(index)
        return indices

    for token in answer.replace(",", " ").replace("，", " ").split():
        match = re.match(r"^(\d+)([A-Za-z0-9]+)$", token)
        if match:
            qnum = int(match.group(1))
            if 1 <= qnum <= len(valid):
                indices = resolve(match.group(2), counts[qnum - 1])
                if indices:
                    bucket = selection.setdefault(qnum - 1, [])
                    bucket.extend(i for i in indices if i not in bucket)
                continue
        if len(valid) == 1:
            indices = resolve(token, counts[0])
            if indices:
                bucket = selection.setdefault(0, [])
                bucket.extend(i for i in indices if i not in bucket)
    return selection


def askuserquestion_keystrokes(
    questions: list[object], selection: dict[int, list[int]]
) -> list[str]:
    """把每题选中的选项下标，算成驱动原生 AskUserQuestion 选择器的按键名序列。

    协议（实测）：选择器出现时光标在每题第一项。
    - 单选题：``Down × idx`` 移到目标项后 ``Enter``——Enter 选中并自动跳到下一题；
    - 多选题：自上而下逐项，选中的按 ``Space`` 勾选、每项之后 ``Down``；过完所有项再 ``Down``
      到「Next」按 ``Enter`` 提交本题；
    - 全部题目答完后再补一个 ``Enter`` 作总提交。
    返回 ``Up/Down/Enter/Space`` 键名列表（与 PTY_KEY_SEQUENCES 对应），按 KEY 输入逐个写入终端。
    """
    valid = [q for q in questions if isinstance(q, dict)]
    keys: list[str] = []
    for qi, question in enumerate(valid):
        count = _askuserquestion_option_count(question)
        chosen = [i for i in selection.get(qi, []) if 0 <= i < count]
        if question.get("multiSelect"):
            for i in range(count):
                if i in chosen:
                    keys.append("Space")
                if i < count - 1:
                    keys.append("Down")
            keys.append("Down")  # 移到「Next」提交按钮
            keys.append("Enter")  # 提交本题（多选不自动跳，需显式提交）
        else:
            target = chosen[0] if chosen else 0
            keys.extend(["Down"] * target)
            keys.append("Enter")  # 选中并自动跳下一题
    keys.append("Enter")  # 全部答完后的总提交
    return keys


def adapter_risk_level(payload: dict[str, object]) -> str:
    value = None
    for key in ADAPTER_RISK_FIELDS:
        value = payload.get(key)
        if value is not None:
            break
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
