from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from agentbridge.commands import command_registry_payload
from agentbridge.domain import (
    AgentBridgeError,
    ErrorCode,
    SemanticEvent,
    SemanticEventSource,
    utc_now,
)

BOT_COMMAND_REGISTRATION_RESULT_STATUSES = {
    "succeeded",
    "failed",
    "partial",
}


def bot_command_registration_manifest(platform: str | None = None) -> dict[str, object]:
    registry = command_registry_payload()
    specs = registry["specs"]
    native_entries = [
        {
            "name": bot_native_command_name(str(spec["name"])),
            "canonical_command": spec["name"],
            "summary": spec["summary"],
            "usage": spec["usage"],
            "required_permission": spec["required_permission"],
            "target_mode": spec["target_mode"],
            "risk": spec["risk"],
            "argument_schema": spec["argument_schema"],
        }
        for spec in specs
    ]
    normalized_platform = platform.strip() if platform and platform.strip() else None
    return {
        "schema_version": "bot.command_registration_manifest.v1",
        "platform": normalized_platform,
        "root_command": registry["root_command"],
        "aliases": registry["aliases"],
        "text_prefixes": registry["text_prefixes"],
        "command_registry_schema_version": registry["schema_version"],
        "command_specs": specs,
        "native_entries": native_entries,
    }


def emit_bot_command_registration_result(
    control: Any,
    *,
    platform: str,
    status: str,
    bot_instance_id: str = "bot-gateway",
    adapter: str | None = None,
    scope: str | None = None,
    channel_id: str | None = None,
    thread_id: str | None = None,
    registration_id: str | None = None,
    commands: list[dict[str, object]] | None = None,
    error: str | None = None,
    payload: dict[str, object] | None = None,
    occurred_at: datetime | None = None,
    idempotency_key: str | None = None,
    trace_id: str | None = None,
) -> SemanticEvent:
    command_items = commands or []
    adapter_payload = payload or {}
    normalized_status = normalized_bot_command_registration_status(status)
    result_idempotency_key = idempotency_key or bot_command_registration_idempotency_key(
        platform=platform,
        bot_instance_id=bot_instance_id,
        adapter=adapter,
        scope=scope,
        channel_id=channel_id,
        thread_id=thread_id,
        registration_id=registration_id,
        status=normalized_status,
        commands=command_items,
        error=error,
        payload=adapter_payload,
    )
    event_trace_id = trace_id or result_idempotency_key
    result_occurred_at = occurred_at or utc_now()
    return control.emit_event(
        event_type="bot.command_registration.result",
        source=SemanticEventSource.BOT_GATEWAY,
        trace_id=event_trace_id,
        payload={
            "bot_instance_id": bot_instance_id,
            "adapter": adapter,
            "platform": platform,
            "scope": scope,
            "channel_id": channel_id,
            "thread_id": thread_id,
            "registration_id": registration_id,
            "status": normalized_status,
            "command_count": len(command_items),
            "commands": command_items,
            "error": error,
            "payload": adapter_payload,
            "occurred_at": result_occurred_at.isoformat(),
        },
        idempotency_key=f"{result_idempotency_key}:bot.command_registration.result",
    )


def bot_native_command_name(canonical_command: str) -> str:
    return canonical_command.replace(".", "-").replace("_", "-")


def normalized_bot_command_registration_status(status: str) -> str:
    normalized = status.strip().lower()
    aliases = {
        "success": "succeeded",
        "ok": "succeeded",
        "error": "failed",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in BOT_COMMAND_REGISTRATION_RESULT_STATUSES:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "未知 Bot command registration result 状态。",
            next_step="请使用 succeeded、failed 或 partial。",
            status_code=400,
            details={"status": status},
        )
    return normalized


def bot_command_registration_idempotency_key(
    *,
    platform: str,
    bot_instance_id: str,
    adapter: str | None,
    scope: str | None,
    channel_id: str | None,
    thread_id: str | None,
    registration_id: str | None,
    status: str,
    commands: list[dict[str, object]],
    error: str | None,
    payload: dict[str, object],
) -> str:
    scope_key = bot_command_registration_scope_key(
        scope=scope,
        channel_id=channel_id,
        thread_id=thread_id,
    )
    result_id = registration_id or bot_command_registration_fingerprint(
        bot_instance_id=bot_instance_id,
        adapter=adapter,
        platform=platform,
        scope=scope,
        channel_id=channel_id,
        thread_id=thread_id,
        status=status,
        commands=commands,
        error=error,
        payload=payload,
    )
    return f"bot-command-registration:{platform}:{bot_instance_id}:{scope_key}:{result_id}"


def bot_command_registration_scope_key(
    *,
    scope: str | None,
    channel_id: str | None,
    thread_id: str | None,
) -> str:
    registration_scope = scope or "global"
    scope_id = channel_id or thread_id or "global"
    return f"{registration_scope}:{scope_id}"


def bot_command_registration_fingerprint(
    *,
    bot_instance_id: str,
    adapter: str | None,
    platform: str,
    scope: str | None,
    channel_id: str | None,
    thread_id: str | None,
    status: str,
    commands: list[dict[str, object]],
    error: str | None,
    payload: dict[str, object],
) -> str:
    body = {
        "bot_instance_id": bot_instance_id,
        "adapter": adapter,
        "platform": platform,
        "scope": scope,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "status": status,
        "commands": commands,
        "error": error,
        "payload": payload,
    }
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return f"result:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"
