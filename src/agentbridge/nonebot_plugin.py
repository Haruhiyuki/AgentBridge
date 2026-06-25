from __future__ import annotations

import hashlib
import json
from typing import Any

from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.onebot import (
    OneBotInboundAdapter,
    execute_onebot_inbound_command,
)

KNOWN_EVENT_FIELDS = (
    "post_type",
    "notice_type",
    "message_type",
    "group_id",
    "guild_id",
    "channel_id",
    "thread_id",
    "user_id",
    "message_id",
    "event_id",
    "id",
    "raw_message",
    "message",
    "reply_message_id",
    "data",
    "payload",
    "callback_data",
    "command",
    "self_id",
    "bot_id",
)


class NoneBotAgentBridgePlugin:
    """Optional NoneBot-facing adapter over the existing OneBot command bridge."""

    def __init__(
        self,
        *,
        control: ControlPlane,
        command_service: CommandService | None = None,
        bot_instance_id: str = "nonebot",
        default_roles: set[str] | None = None,
        command_prefixes: tuple[str, ...] = ("/agent", "/ab"),
    ) -> None:
        self.control = control
        self.command_service = command_service or CommandService(control)
        self.adapter = OneBotInboundAdapter(
            bot_instance_id=bot_instance_id,
            default_roles=default_roles,
            command_prefixes=command_prefixes,
        )

    def handle_event(self, event: Any) -> dict[str, Any]:
        onebot_event = nonebot_event_to_onebot_event(event)
        inbound = self.adapter.command_from_event(onebot_event)
        if inbound is None:
            return {"handled": False}
        return execute_onebot_inbound_command(
            inbound,
            command_service=self.command_service,
            control=self.control,
        )

    def as_async_handler(self):
        async def handler(event: Any) -> dict[str, Any]:
            return self.handle_event(event)

        return handler

    def register_matcher(self, matcher: Any) -> Any:
        return register_nonebot_handler(matcher, self.as_async_handler())


def register_nonebot_matcher(
    matcher: Any,
    *,
    control: ControlPlane,
    command_service: CommandService | None = None,
    bot_instance_id: str = "nonebot",
    default_roles: set[str] | None = None,
    command_prefixes: tuple[str, ...] = ("/agent", "/ab"),
) -> NoneBotAgentBridgePlugin:
    plugin = NoneBotAgentBridgePlugin(
        control=control,
        command_service=command_service,
        bot_instance_id=bot_instance_id,
        default_roles=default_roles,
        command_prefixes=command_prefixes,
    )
    plugin.register_matcher(matcher)
    return plugin


def register_nonebot_handler(matcher: Any, handler: Any) -> Any:
    handle = getattr(matcher, "handle", None)
    if not callable(handle):
        raise TypeError("NoneBot matcher must expose a callable handle() decorator")
    decorator = handle()
    if not callable(decorator):
        raise TypeError("NoneBot matcher handle() must return a decorator")
    return decorator(handler)


def nonebot_event_to_onebot_event(event: Any) -> dict[str, Any]:
    source = event_mapping(event)
    command_text = command_text_from_action_payload(source)
    plain_text = command_text or text_from_event(event, source)
    onebot_event = dict(source)
    if plain_text is not None:
        onebot_event["raw_message"] = plain_text
    if command_text is not None:
        onebot_event["post_type"] = "message"
    else:
        onebot_event["post_type"] = string_value(onebot_event.get("post_type")) or "message"

    if onebot_event.get("group_id") is None:
        group_id = onebot_event.get("channel_id") or onebot_event.get("guild_id")
        if group_id is not None:
            onebot_event["group_id"] = group_id
    message_type = string_value(onebot_event.get("message_type"))
    if not message_type:
        message_type = "group" if onebot_event.get("group_id") is not None else "private"
    onebot_event["message_type"] = message_type

    if onebot_event.get("user_id") is None:
        user_id = call_noarg(event, "get_user_id")
        if user_id is not None:
            onebot_event["user_id"] = user_id
    if onebot_event.get("message_id") is None:
        onebot_event["message_id"] = (
            onebot_event.get("event_id")
            or onebot_event.get("id")
            or deterministic_event_id(onebot_event)
        )
    return onebot_event


def event_mapping(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        payload = dict(event)
    elif hasattr(event, "model_dump"):
        payload = dict(event.model_dump())
    elif hasattr(event, "dict"):
        payload = dict(event.dict())
    else:
        payload = {}
    for key in KNOWN_EVENT_FIELDS:
        if key in payload:
            continue
        value = getattr(event, key, None)
        if value is not None:
            payload[key] = value
    plaintext = call_noarg(event, "get_plaintext")
    if plaintext is not None and "raw_message" not in payload:
        payload["raw_message"] = plaintext
    user_id = call_noarg(event, "get_user_id")
    if user_id is not None and "user_id" not in payload:
        payload["user_id"] = user_id
    message = call_noarg(event, "get_message")
    if message is not None and "message" not in payload:
        payload["message"] = message
    return payload


def command_text_from_action_payload(payload: dict[str, Any]) -> str | None:
    for key in ("command", "callback_data"):
        value = string_value(payload.get(key))
        if value:
            return value
    for key in ("data", "payload"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            for nested_key in ("command", "raw_text", "callback_data", "value"):
                value = string_value(nested.get(nested_key))
                if value:
                    return value
        value = string_value(nested)
        if value:
            return value
    return None


def text_from_event(event: Any, payload: dict[str, Any]) -> str | None:
    for key in ("raw_message", "message"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    plaintext = call_noarg(event, "get_plaintext")
    return string_value(plaintext)


def deterministic_event_id(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return f"nonebot:{hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]}"


def call_noarg(target: Any, name: str) -> Any:
    method = getattr(target, name, None)
    if not callable(method):
        return None
    try:
        return method()
    except TypeError:
        return None


def string_value(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
