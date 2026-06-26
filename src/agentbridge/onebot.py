from __future__ import annotations

import hashlib
import json
import re
import shlex
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agentbridge.domain import Actor, AgentBridgeError, BotPlatform, ChatContext, ErrorCode

MODAL_COMMAND_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_-]*)\}")


class HTTPPoster(Protocol):
    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]: ...


class UrllibHTTPPoster:
    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"content-type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            retry_after_seconds = retry_after_seconds_from_headers(exc.headers)
            details: dict[str, Any] = {"status_code": exc.code, "reason": str(exc)}
            if retry_after_seconds is not None:
                details["retry_after_seconds"] = retry_after_seconds
            message = (
                "OneBot HTTP 发送被平台限流。"
                if retry_after_seconds is not None
                else "OneBot HTTP 发送失败。"
            )
            raise AgentBridgeError(
                ErrorCode.QUOTA_EXCEEDED
                if exc.code == 429 or retry_after_seconds is not None
                else ErrorCode.RESOURCE_CONFLICT,
                message,
                next_step=(
                    "AgentBridge 将按平台 Retry-After 延迟重试。"
                    if retry_after_seconds is not None
                    else "请检查 OneBot HTTP 地址、访问令牌和网络连通性。"
                ),
                status_code=429 if exc.code == 429 else 502,
                details=details,
            ) from exc
        except urllib.error.URLError as exc:
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "OneBot HTTP 发送失败。",
                next_step="请检查 OneBot HTTP 地址、访问令牌和网络连通性。",
                status_code=502,
                details={"reason": str(exc)},
            ) from exc


@dataclass(frozen=True)
class OneBotV11HTTPTransport:
    endpoint: str
    access_token: str | None = None
    poster: HTTPPoster | None = None

    def send_text(
        self,
        *,
        platform: BotPlatform,
        chat_context_id: str,
        chat_context: ChatContext,
        text: str,
        idempotency_key: str,
    ) -> str:
        if platform != BotPlatform.ONEBOT_V11:
            raise AgentBridgeError(
                ErrorCode.PLATFORM_CAPABILITY_MISSING,
                f"OneBot transport 不支持平台：{platform.value}",
                next_step="请使用 onebot.v11 平台或选择其他 Bot transport。",
            )
        action, payload = onebot_text_payload(chat_context, text)
        response = (self.poster or UrllibHTTPPoster()).post_json(
            self._url(action),
            payload,
            self._headers(idempotency_key),
        )
        return onebot_message_id(response)

    def edit_text(
        self,
        *,
        platform: BotPlatform,
        chat_context_id: str,
        chat_context: ChatContext,
        platform_message_id: str,
        text: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if platform != BotPlatform.ONEBOT_V11:
            raise AgentBridgeError(
                ErrorCode.PLATFORM_CAPABILITY_MISSING,
                f"OneBot transport 不支持平台：{platform.value}",
                next_step="请使用 onebot.v11 平台或选择其他 Bot transport。",
            )
        raise AgentBridgeError(
            ErrorCode.PLATFORM_CAPABILITY_MISSING,
            "OneBot V11 不支持标准原生消息编辑。",
            next_step="请使用平台特定扩展 transport，或发送新消息并删除旧消息。",
            details={
                "chat_context_id": chat_context_id,
                "chat_space_id": chat_context.chat_space_id,
                "platform_message_id": platform_message_id,
                "idempotency_key": idempotency_key,
                "text": text,
            },
        )

    def delete_message(
        self,
        *,
        platform: BotPlatform,
        chat_context_id: str,
        chat_context: ChatContext,
        platform_message_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if platform != BotPlatform.ONEBOT_V11:
            raise AgentBridgeError(
                ErrorCode.PLATFORM_CAPABILITY_MISSING,
                f"OneBot transport 不支持平台：{platform.value}",
                next_step="请使用 onebot.v11 平台或选择其他 Bot transport。",
            )
        message_id = onebot_raw_message_id(platform_message_id)
        response = (self.poster or UrllibHTTPPoster()).post_json(
            self._url("delete_msg"),
            {"message_id": message_id},
            self._headers(idempotency_key),
        )
        ensure_onebot_success(response, action_label="删除")
        return {
            "platform_message_id": platform_message_id,
            "chat_context_id": chat_context_id,
            "chat_space_id": chat_context.chat_space_id,
            "response": response,
        }

    def _url(self, action: str) -> str:
        return f"{self.endpoint.rstrip('/')}/{action}"

    def _headers(self, idempotency_key: str) -> dict[str, str]:
        headers = {"x-agentbridge-idempotency-key": idempotency_key}
        if self.access_token:
            headers["authorization"] = f"Bearer {self.access_token}"
        return headers


def onebot_text_payload(chat_context: ChatContext, text: str) -> tuple[str, dict[str, Any]]:
    if chat_context.user_id:
        return "send_private_msg", {"user_id": chat_context.user_id, "message": text}
    return "send_group_msg", {"group_id": chat_context.chat_space_id, "message": text}


def onebot_message_id(response: dict[str, Any]) -> str:
    ensure_onebot_success(response, action_label="发送")
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    message_id = data.get("message_id") if data else response.get("message_id")
    if message_id is None:
        return "onebot:unknown"
    return f"onebot:{message_id}"


def ensure_onebot_success(response: dict[str, Any], *, action_label: str) -> None:
    retcode = response.get("retcode")
    if retcode not in {0, "0", None}:
        raise AgentBridgeError(
            ErrorCode.RESOURCE_CONFLICT,
            f"OneBot 返回{action_label}失败。",
            next_step="请检查 OneBot 返回码和 Bot 连接状态。",
            status_code=502,
            details={"response": response},
        )


def onebot_raw_message_id(platform_message_id: str) -> int | str:
    value = platform_message_id.removeprefix("onebot:")
    try:
        return int(value)
    except ValueError:
        return value


def retry_after_seconds_from_headers(headers: Any) -> float | None:
    for key in ("retry-after", "Retry-After", "x-ratelimit-reset-after"):
        value = headers.get(key) if headers is not None else None
        seconds = retry_after_seconds_from_value(value)
        if seconds is not None:
            return seconds
    reset_at = headers.get("x-ratelimit-reset") if headers is not None else None
    if reset_at is None:
        reset_at = headers.get("X-RateLimit-Reset") if headers is not None else None
    try:
        return max(float(reset_at) - datetime.now(UTC).timestamp(), 0.0)
    except (TypeError, ValueError):
        return None


def retry_after_seconds_from_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        pass
    try:
        retry_at = parsedate_to_datetime(str(value))
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max((retry_at - datetime.now(UTC)).total_seconds(), 0.0)


class OneBotInboundCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str
    actor: Actor
    bot_instance_id: str
    platform: BotPlatform = BotPlatform.ONEBOT_V11
    chat_space_id: str
    user_id: str | None = None
    thread_id: str | None = None
    idempotency_key: str
    trace_id: str
    reply_message_id: str | None = None
    original_event: dict[str, Any] = Field(default_factory=dict)


class OneBotInboundAdapter:
    def __init__(
        self,
        *,
        bot_instance_id: str,
        default_roles: set[str] | None = None,
        command_prefixes: tuple[str, ...] = ("/agent", "/ab"),
    ) -> None:
        self.bot_instance_id = bot_instance_id
        self.default_roles = default_roles or {"member"}
        self.command_prefixes = command_prefixes

    def command_from_event(self, event: dict[str, Any]) -> OneBotInboundCommand | None:
        raw_text = self._command_text(event)
        if not self._is_command(raw_text):
            return None
        message_type = self._message_type(event)
        user_id = self._string_field(event, "user_id")
        if user_id is None:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "OneBot 命令事件缺少字段：user_id",
                next_step="按钮回调和消息事件都必须携带点击者或发送者 user_id。",
            )
        if message_type == "group":
            chat_space_id = self._required_string_field(event, "group_id")
            command_user_id = None
        elif message_type == "private":
            chat_space_id = f"private:{self._required_string_field(event, 'user_id')}"
            command_user_id = user_id
        else:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                f"不支持的 OneBot message_type：{message_type}",
                next_step="当前仅支持 group 和 private 消息。",
            )
        message_id = self._event_id(event)
        actor = Actor(id=f"onebot:{user_id}", roles=set(self.default_roles))
        return OneBotInboundCommand(
            raw_text=raw_text,
            actor=actor,
            bot_instance_id=self.bot_instance_id,
            chat_space_id=chat_space_id,
            user_id=command_user_id,
            thread_id=self._string_field(event, "thread_id"),
            idempotency_key=f"onebot:{message_id}",
            trace_id=f"onebot:{message_id}",
            reply_message_id=self._reply_message_id(event),
            original_event=event,
        )

    def _command_text(self, event: dict[str, Any]) -> str:
        if event.get("post_type") == "message":
            return self._extract_text(event).strip()
        return command_text_from_action_payload(event).strip()

    def _message_type(self, event: dict[str, Any]) -> str | None:
        message_type = self._string_field(event, "message_type")
        if message_type:
            return message_type
        if event.get("group_id") is not None:
            return "group"
        if event.get("user_id") is not None:
            return "private"
        return None

    def _is_command(self, text: str) -> bool:
        return any(
            text == prefix or text.startswith(prefix + " ")
            for prefix in self.command_prefixes
        )

    def _extract_text(self, event: dict[str, Any]) -> str:
        raw_message = event.get("raw_message")
        if isinstance(raw_message, str):
            return raw_message
        message = event.get("message")
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            parts: list[str] = []
            for segment in message:
                if not isinstance(segment, dict):
                    continue
                if segment.get("type") == "text":
                    data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
                    text = data.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return ""

    def _reply_message_id(self, event: dict[str, Any]) -> str | None:
        direct_reply = self._string_field(event, "reply_message_id")
        if direct_reply is not None:
            return direct_reply
        nested_reply = nested_string_field(event, "reply_message_id")
        if nested_reply is not None:
            return nested_reply
        message = event.get("message")
        if not isinstance(message, list):
            return None
        for segment in message:
            if not isinstance(segment, dict) or segment.get("type") != "reply":
                continue
            data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
            message_id = data.get("id")
            return str(message_id) if message_id is not None else None
        return None

    def _event_id(self, event: dict[str, Any]) -> str:
        for key in ("message_id", "event_id", "id"):
            value = self._string_field(event, key)
            if value is not None:
                return value
        for key in ("message_id", "event_id", "id"):
            value = nested_string_field(event, key)
            if value is not None:
                return value
        body = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
        return f"event:{hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]}"

    def _required_string_field(self, event: dict[str, Any], key: str) -> str:
        value = self._string_field(event, key)
        if value is None:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                f"OneBot 事件缺少字段：{key}",
                next_step="请检查 Bot Gateway 入站事件。",
            )
        return value

    @staticmethod
    def _string_field(event: dict[str, Any], key: str) -> str | None:
        value = event.get(key)
        return str(value) if value is not None else None


def command_text_from_action_payload(payload: dict[str, Any]) -> str:
    value = nested_command_text(payload)
    return value or ""


def nested_command_text(value: Any, *, depth: int = 0) -> str | None:
    if depth > 4:
        return None
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    modal_text = command_text_from_modal_payload(value)
    if modal_text:
        return modal_text
    for key in ("command", "raw_text", "callback_data", "value"):
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return text
    for key in ("data", "payload"):
        nested = nested_command_text(value.get(key), depth=depth + 1)
        if nested:
            return nested
    return None


def command_text_from_modal_payload(payload: dict[str, Any]) -> str | None:
    template = payload.get("command_template") or payload.get("commandTemplate")
    if not isinstance(template, str) or not template.strip():
        return None
    values = modal_payload_values(payload)
    placeholders = MODAL_COMMAND_PLACEHOLDER.findall(template)
    if not placeholders:
        return None
    command = template
    for placeholder in placeholders:
        if placeholder not in values:
            if len(placeholders) == 1:
                scalar = modal_payload_scalar_value(payload)
                if scalar is not None:
                    values[placeholder] = scalar
            if placeholder not in values and len(placeholders) == 1 and len(values) == 1:
                values[placeholder] = next(iter(values.values()))
            if placeholder not in values:
                return None
        command = command.replace(
            "{" + placeholder + "}",
            shlex.quote(values[placeholder]),
        )
    return command.strip() or None


def modal_payload_values(payload: dict[str, Any]) -> dict[str, str]:
    for key in ("values", "inputs", "input_values", "fields"):
        value = payload.get(key)
        if isinstance(value, dict):
            return {
                str(field): str(field_value)
                for field, field_value in value.items()
                if field_value is not None
            }
    return {}


def modal_payload_scalar_value(payload: dict[str, Any]) -> str | None:
    for key in ("selected_value", "selected", "selection", "value", "answer"):
        value = payload.get(key)
        if value is not None and not isinstance(value, (dict, list)):
            return str(value)
    return None


def nested_string_field(payload: dict[str, Any], key: str, *, depth: int = 0) -> str | None:
    if depth > 4:
        return None
    for nested_key in ("data", "payload"):
        value = payload.get(nested_key)
        if not isinstance(value, dict):
            continue
        field = value.get(key)
        if field is not None:
            return str(field)
        nested = nested_string_field(value, key, depth=depth + 1)
        if nested is not None:
            return nested
    return None


def execute_onebot_inbound_command(
    inbound: OneBotInboundCommand,
    *,
    command_service: Any,
    control: Any,
) -> dict[str, Any]:
    context = control.get_or_create_chat_context(
        bot_instance_id=inbound.bot_instance_id,
        platform=inbound.platform.value,
        chat_space_id=inbound.chat_space_id,
        thread_id=inbound.thread_id,
        user_id=inbound.user_id,
    )
    raw_text = command_text_with_reply_interaction(
        inbound.raw_text,
        reply_message_id=inbound.reply_message_id,
        chat_context_id=context.id,
        control=control,
    )
    invocation = command_service.parse(
        raw_text=raw_text,
        actor=inbound.actor,
        chat_context_id=context.id,
        idempotency_key=inbound.idempotency_key,
        trace_id=inbound.trace_id,
    )
    result = command_service.execute(invocation)
    return {
        "handled": True,
        "chat_context_id": context.id,
        "result": result.model_dump(mode="json"),
    }


def command_text_with_reply_interaction(
    raw_text: str,
    *,
    reply_message_id: str | None,
    chat_context_id: str,
    control: Any,
) -> str:
    if reply_message_id is None:
        return raw_text
    interaction_id = interaction_id_from_reply_message(
        reply_message_id=reply_message_id,
        chat_context_id=chat_context_id,
        control=control,
    )
    if interaction_id is None:
        return raw_text
    return inject_reply_interaction_id(raw_text, interaction_id=interaction_id)


def interaction_id_from_reply_message(
    *,
    reply_message_id: str,
    chat_context_id: str,
    control: Any,
) -> str | None:
    candidates = platform_message_id_candidates(reply_message_id)
    records = control.repository.list_bot_delivery_records(chat_context_id=chat_context_id)
    for record in reversed(records):
        if record.platform_message_id not in candidates:
            continue
        event = control.repository.get_semantic_event(record.event_id)
        if event is not None and event.interaction_id:
            return event.interaction_id
    return None


def platform_message_id_candidates(reply_message_id: str) -> set[str]:
    value = str(reply_message_id)
    candidates = {value}
    if value.startswith("onebot:"):
        candidates.add(value.removeprefix("onebot:"))
    else:
        candidates.add(f"onebot:{value}")
    return candidates


def inject_reply_interaction_id(raw_text: str, *, interaction_id: str) -> str:
    prefix, body = split_command_prefix(raw_text)
    if prefix is None or not body:
        return raw_text
    try:
        tokens = shlex.split(body)
    except ValueError:
        return raw_text
    if not tokens:
        return raw_text
    root = tokens[0]
    normalized_root = {
        "回答": "answer",
        "批准": "approve",
        "拒绝": "deny",
        "计划": "plan",
    }.get(root, root)
    args = tokens[1:]
    if normalized_root == "answer":
        if args and looks_like_interaction_id(args[0]):
            return raw_text
        return shlex.join([prefix, root, interaction_id, *args])
    if normalized_root == "approve":
        if args and looks_like_interaction_id(args[0]):
            return raw_text
        return shlex.join([prefix, root, interaction_id, *args])
    if normalized_root == "deny":
        if args and looks_like_interaction_id(args[0]):
            return raw_text
        return shlex.join([prefix, root, interaction_id, *args])
    if normalized_root == "plan" and args:
        plan_action = {
            "批准": "approve",
            "查看": "show",
            "显示": "show",
            "修改": "revise",
            "取消": "cancel",
            "拒绝": "cancel",
        }.get(args[0], args[0])
        if plan_action not in {"show", "approve", "revise", "revision", "cancel", "deny"}:
            return raw_text
        if len(args) >= 2 and looks_like_interaction_id(args[1]):
            return raw_text
        return shlex.join([prefix, root, args[0], interaction_id, *args[1:]])
    return raw_text


def split_command_prefix(raw_text: str) -> tuple[str | None, str]:
    stripped = raw_text.strip()
    if not stripped:
        return None, ""
    parts = stripped.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def looks_like_interaction_id(value: str) -> bool:
    return value.startswith("int_")
