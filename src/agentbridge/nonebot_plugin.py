from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from datetime import datetime
from typing import Any

from agentbridge.bot_command_registration import (
    bot_command_registration_manifest,
    emit_bot_command_registration_result,
)
from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import AgentBridgeError, BotPlatform, ChatContext, ErrorCode
from agentbridge.onebot import (
    OneBotInboundAdapter,
    command_text_from_action_payload,
    ensure_onebot_success,
    execute_onebot_inbound_command,
    onebot_raw_message_id,
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

    def register_matchers(self, matchers: Any) -> list[Any]:
        return [
            self.register_matcher(matcher)
            for matcher in nonebot_matcher_values(matchers)
        ]

    def register_command_registration_startup(
        self,
        driver: Any,
        registrar: Any,
        *,
        platform: str = "onebot.v11",
        scope: str | None = None,
        channel_id: str | None = None,
        thread_id: str | None = None,
        registration_id: str | None = None,
    ) -> Any:
        return register_nonebot_startup_handler(
            driver,
            self.as_command_registration_startup_handler(
                registrar,
                platform=platform,
                scope=scope,
                channel_id=channel_id,
                thread_id=thread_id,
                registration_id=registration_id,
            ),
        )

    def as_command_registration_startup_handler(
        self,
        registrar: Any,
        *,
        platform: str = "onebot.v11",
        scope: str | None = None,
        channel_id: str | None = None,
        thread_id: str | None = None,
        registration_id: str | None = None,
    ):
        async def handler() -> dict[str, Any]:
            manifest = self.command_registration_manifest(platform=platform)
            default_commands = command_entries_from_manifest(manifest)
            try:
                result = await call_command_registration_registrar(
                    registrar,
                    manifest,
                )
            except Exception as exc:
                self.record_command_registration_result(
                    status="failed",
                    platform=platform,
                    scope=scope,
                    channel_id=channel_id,
                    thread_id=thread_id,
                    registration_id=registration_id,
                    commands=default_commands,
                    error=str(exc),
                    payload={"exception_type": type(exc).__name__},
                )
                raise

            normalized = normalize_command_registration_result(
                result,
                default_commands=default_commands,
            )
            return self.record_command_registration_result(
                status=str(normalized["status"]),
                platform=platform,
                scope=string_or_default(normalized.get("scope"), scope),
                channel_id=string_or_default(normalized.get("channel_id"), channel_id),
                thread_id=string_or_default(normalized.get("thread_id"), thread_id),
                registration_id=string_or_default(
                    normalized.get("registration_id"),
                    registration_id,
                ),
                commands=normalized["commands"],
                error=string_or_default(normalized.get("error"), None),
                payload=normalized["payload"],
                idempotency_key=string_or_default(
                    normalized.get("idempotency_key"),
                    None,
                ),
                trace_id=string_or_default(normalized.get("trace_id"), None),
            )

        return handler

    def command_registration_manifest(
        self,
        *,
        platform: str = "onebot.v11",
    ) -> dict[str, object]:
        return bot_command_registration_manifest(platform=platform)

    def record_command_registration_result(
        self,
        *,
        status: str,
        platform: str = "onebot.v11",
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
    ) -> dict[str, Any]:
        command_items = commands
        if command_items is None:
            manifest = self.command_registration_manifest(platform=platform)
            native_entries = manifest.get("native_entries", [])
            command_items = [
                dict(item) for item in native_entries if isinstance(item, dict)
            ]
        event = emit_bot_command_registration_result(
            self.control,
            platform=platform,
            status=status,
            bot_instance_id=self.adapter.bot_instance_id,
            adapter="nonebot",
            scope=scope,
            channel_id=channel_id,
            thread_id=thread_id,
            registration_id=registration_id,
            commands=command_items,
            error=error,
            payload=payload,
            occurred_at=occurred_at,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
        )
        return {"event": event.model_dump(mode="json")}


class NoneBotTransport:
    """Dependency-free Bot Gateway transport for a NoneBot-like bot object."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    def send_text(
        self,
        *,
        platform: BotPlatform,
        chat_context_id: str,
        chat_context: ChatContext,
        text: str,
        idempotency_key: str,
    ) -> str:
        ensure_nonebot_platform_supported(platform)
        target = nonebot_target_from_chat_context(chat_context)
        response = call_nonebot_text_sender(
            self.bot,
            target=target,
            chat_context=chat_context,
            text=text,
            idempotency_key=idempotency_key,
        )
        return nonebot_platform_message_id(response, platform=platform)

    def delete_message(
        self,
        *,
        platform: BotPlatform,
        chat_context_id: str,
        chat_context: ChatContext,
        platform_message_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        ensure_nonebot_platform_supported(platform)
        raw_message_id = onebot_raw_message_id(platform_message_id)
        response = call_nonebot_delete_message(
            self.bot,
            chat_context=chat_context,
            raw_message_id=raw_message_id,
            platform_message_id=platform_message_id,
            idempotency_key=idempotency_key,
        )
        if isinstance(response, dict):
            ensure_onebot_success(response, action_label="删除")
        return {
            "platform_message_id": platform_message_id,
            "chat_context_id": chat_context_id,
            "chat_space_id": chat_context.chat_space_id,
            "response": response,
        }


def ensure_nonebot_platform_supported(platform: BotPlatform) -> None:
    if platform != BotPlatform.ONEBOT_V11:
        raise AgentBridgeError(
            ErrorCode.PLATFORM_CAPABILITY_MISSING,
            f"NoneBot transport 不支持平台：{platform.value}",
            next_step="请使用 onebot.v11 平台或选择其他 Bot transport。",
        )


def nonebot_target_from_chat_context(chat_context: ChatContext) -> dict[str, str]:
    target = {
        "chat_context_id": chat_context.id,
        "bot_instance_id": chat_context.bot_instance_id,
        "platform": chat_context.platform,
        "chat_space_id": chat_context.chat_space_id,
    }
    if chat_context.thread_id:
        target["thread_id"] = chat_context.thread_id
    if chat_context.user_id:
        target["scope"] = "private"
        target["user_id"] = chat_context.user_id
    else:
        target["scope"] = "group"
        target["group_id"] = chat_context.chat_space_id
        target["channel_id"] = chat_context.chat_space_id
    return target


def call_nonebot_text_sender(
    bot: Any,
    *,
    target: dict[str, str],
    chat_context: ChatContext,
    text: str,
    idempotency_key: str,
) -> Any:
    send_to = getattr(bot, "send_to", None)
    if callable(send_to):
        return resolve_maybe_awaitable(
            call_with_signature_fallbacks(
                send_to,
                attempts=[
                    (
                        (),
                        {
                            "target": target,
                            "message": text,
                            "idempotency_key": idempotency_key,
                        },
                    ),
                    ((target, text), {}),
                ],
                capability="NoneBot send_to",
            )
        )

    call_api = getattr(bot, "call_api", None)
    if callable(call_api):
        action, payload = nonebot_onebot_text_payload(chat_context, text)
        return resolve_maybe_awaitable(call_api(action, **payload))

    send = getattr(bot, "send", None)
    if callable(send):
        return resolve_maybe_awaitable(
            call_with_signature_fallbacks(
                send,
                attempts=[
                    ((), {"target": target, "message": text}),
                    ((target, text), {}),
                    ((text,), {}),
                ],
                capability="NoneBot send",
            )
        )

    raise AgentBridgeError(
        ErrorCode.PLATFORM_CAPABILITY_MISSING,
        "NoneBot bot 对象缺少可用的发送能力。",
        next_step="请提供带 send_to()、send() 或 call_api() 的 bot 对象。",
        details={"bot_type": type(bot).__name__},
    )


def call_nonebot_delete_message(
    bot: Any,
    *,
    chat_context: ChatContext,
    raw_message_id: int | str,
    platform_message_id: str,
    idempotency_key: str,
) -> Any:
    delete_message = getattr(bot, "delete_message", None)
    if callable(delete_message):
        return resolve_maybe_awaitable(
            call_with_signature_fallbacks(
                delete_message,
                attempts=[
                    (
                        (),
                        {
                            "message_id": raw_message_id,
                            "platform_message_id": platform_message_id,
                            "idempotency_key": idempotency_key,
                        },
                    ),
                    ((raw_message_id,), {}),
                ],
                capability="NoneBot delete_message",
            )
        )

    call_api = getattr(bot, "call_api", None)
    if callable(call_api):
        return resolve_maybe_awaitable(
            call_api("delete_msg", message_id=raw_message_id)
        )

    raise AgentBridgeError(
        ErrorCode.PLATFORM_CAPABILITY_MISSING,
        "NoneBot bot 对象缺少可用的删除能力。",
        next_step="请提供带 delete_message() 或 call_api() 的 bot 对象。",
        details={
            "bot_type": type(bot).__name__,
            "chat_context_id": chat_context.id,
            "platform_message_id": platform_message_id,
        },
    )


def call_with_signature_fallbacks(
    func: Any,
    *,
    attempts: list[tuple[tuple[Any, ...], dict[str, Any]]],
    capability: str,
) -> Any:
    errors: list[str] = []
    for args, kwargs in attempts:
        try:
            return func(*args, **kwargs)
        except TypeError as exc:
            errors.append(str(exc))
    raise AgentBridgeError(
        ErrorCode.PLATFORM_CAPABILITY_MISSING,
        f"{capability} 调用签名不兼容。",
        next_step="请提供兼容 AgentBridge transport 调用约定的适配函数。",
        details={"errors": errors},
    )


def resolve_maybe_awaitable(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(value)
    if inspect.iscoroutine(value):
        value.close()
    raise AgentBridgeError(
        ErrorCode.PLATFORM_CAPABILITY_MISSING,
        "同步 Bot Gateway delivery 不能在已运行事件循环中等待 NoneBot 异步调用。",
        next_step=(
            "请从同步工作线程调用 BotGatewayService，或使用 Bot Gateway WebSocket "
            "订阅并在 NoneBot 事件循环内发送。"
        ),
    )


def nonebot_onebot_text_payload(
    chat_context: ChatContext,
    text: str,
) -> tuple[str, dict[str, Any]]:
    if chat_context.user_id:
        return "send_private_msg", {"user_id": chat_context.user_id, "message": text}
    return "send_group_msg", {"group_id": chat_context.chat_space_id, "message": text}


def nonebot_platform_message_id(response: Any, *, platform: BotPlatform) -> str:
    if platform == BotPlatform.ONEBOT_V11 and isinstance(response, dict):
        ensure_onebot_success(response, action_label="发送")
    message_id = message_id_from_nonebot_response(response)
    prefix = "onebot" if platform == BotPlatform.ONEBOT_V11 else "nonebot"
    if message_id is None:
        return f"{prefix}:unknown"
    value = str(message_id)
    if value.startswith(("onebot:", "nonebot:")):
        return value
    return f"{prefix}:{value}"


def message_id_from_nonebot_response(response: Any) -> Any:
    if isinstance(response, dict):
        containers = [response]
        for key in ("data", "result"):
            value = response.get(key)
            if isinstance(value, dict):
                containers.append(value)
        for container in containers:
            for key in ("message_id", "messageId", "id"):
                value = container.get(key)
                if value is not None:
                    return value
        return None
    for attr in ("message_id", "messageId", "id"):
        value = getattr(response, attr, None)
        if value is not None:
            return value
    if isinstance(response, (str, int)):
        return response
    return None


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


def register_nonebot_matchers(
    matchers: Any,
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
    plugin.register_matchers(matchers)
    return plugin


def register_nonebot_command_registration(
    driver: Any,
    registrar: Any,
    *,
    control: ControlPlane,
    command_service: CommandService | None = None,
    bot_instance_id: str = "nonebot",
    default_roles: set[str] | None = None,
    command_prefixes: tuple[str, ...] = ("/agent", "/ab"),
    platform: str = "onebot.v11",
    scope: str | None = None,
    channel_id: str | None = None,
    thread_id: str | None = None,
    registration_id: str | None = None,
) -> NoneBotAgentBridgePlugin:
    plugin = NoneBotAgentBridgePlugin(
        control=control,
        command_service=command_service,
        bot_instance_id=bot_instance_id,
        default_roles=default_roles,
        command_prefixes=command_prefixes,
    )
    plugin.register_command_registration_startup(
        driver,
        registrar,
        platform=platform,
        scope=scope,
        channel_id=channel_id,
        thread_id=thread_id,
        registration_id=registration_id,
    )
    return plugin


def register_nonebot_lifecycle(
    *,
    control: ControlPlane,
    matchers: Any | None = None,
    driver: Any | None = None,
    command_registrar: Any | None = None,
    command_service: CommandService | None = None,
    bot_instance_id: str = "nonebot",
    default_roles: set[str] | None = None,
    command_prefixes: tuple[str, ...] = ("/agent", "/ab"),
    platform: str = "onebot.v11",
    scope: str | None = None,
    channel_id: str | None = None,
    thread_id: str | None = None,
    registration_id: str | None = None,
) -> NoneBotAgentBridgePlugin:
    plugin = NoneBotAgentBridgePlugin(
        control=control,
        command_service=command_service,
        bot_instance_id=bot_instance_id,
        default_roles=default_roles,
        command_prefixes=command_prefixes,
    )
    if matchers is not None:
        plugin.register_matchers(matchers)
    if driver is not None or command_registrar is not None:
        if driver is None or command_registrar is None:
            raise TypeError(
                "NoneBot lifecycle command registration requires both driver and "
                "command_registrar"
            )
        plugin.register_command_registration_startup(
            driver,
            command_registrar,
            platform=platform,
            scope=scope,
            channel_id=channel_id,
            thread_id=thread_id,
            registration_id=registration_id,
        )
    return plugin


def nonebot_matcher_values(matchers: Any) -> list[Any]:
    if callable(getattr(matchers, "handle", None)):
        return [matchers]
    if isinstance(matchers, dict):
        return list(matchers.values())
    try:
        return list(matchers)
    except TypeError:
        return [matchers]


def register_nonebot_handler(matcher: Any, handler: Any) -> Any:
    handle = getattr(matcher, "handle", None)
    if not callable(handle):
        raise TypeError("NoneBot matcher must expose a callable handle() decorator")
    decorator = handle()
    if not callable(decorator):
        raise TypeError("NoneBot matcher handle() must return a decorator")
    return decorator(handler)


def register_nonebot_startup_handler(driver: Any, handler: Any) -> Any:
    on_startup = getattr(driver, "on_startup", None)
    if not callable(on_startup):
        raise TypeError("NoneBot driver must expose a callable on_startup() decorator")
    decorator = on_startup()
    if not callable(decorator):
        raise TypeError("NoneBot driver on_startup() must return a decorator")
    return decorator(handler)


async def call_command_registration_registrar(
    registrar: Any,
    manifest: dict[str, object],
) -> Any:
    if not callable(registrar):
        raise TypeError("NoneBot command registrar must be callable")
    result = registrar(manifest)
    if inspect.isawaitable(result):
        return await result
    return result


def normalize_command_registration_result(
    result: Any,
    *,
    default_commands: list[dict[str, object]],
) -> dict[str, Any]:
    if result is None:
        return {
            "status": "succeeded",
            "commands": default_commands,
            "payload": {},
        }
    if isinstance(result, dict):
        normalized = dict(result)
        normalized.setdefault("status", "succeeded")
        normalized["commands"] = command_entries_from_value(
            normalized.get("commands"),
            default_commands=default_commands,
        )
        payload = normalized.get("payload")
        normalized["payload"] = payload if isinstance(payload, dict) else {}
        return normalized
    return {
        "status": "succeeded",
        "commands": default_commands,
        "payload": {"result": str(result)},
    }


def command_entries_from_manifest(manifest: dict[str, object]) -> list[dict[str, object]]:
    return command_entries_from_value(manifest.get("native_entries"), default_commands=[])


def command_entries_from_value(
    value: Any,
    *,
    default_commands: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return default_commands
    commands = [dict(item) for item in value if isinstance(item, dict)]
    return commands or default_commands


def string_or_default(value: Any, default: str | None) -> str | None:
    if value is None:
        return default
    return str(value)


def nonebot_event_to_onebot_event(event: Any) -> dict[str, Any]:
    source = event_mapping(event)
    command_text = command_text_from_action_payload(source) or None
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
