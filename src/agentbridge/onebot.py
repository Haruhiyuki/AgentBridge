from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from agentbridge.domain import Actor, AgentBridgeError, BotPlatform, ChatContext, ErrorCode


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
    retcode = response.get("retcode")
    if retcode not in {0, "0", None}:
        raise AgentBridgeError(
            ErrorCode.RESOURCE_CONFLICT,
            "OneBot 返回发送失败。",
            next_step="请检查 OneBot 返回码和 Bot 连接状态。",
            status_code=502,
            details={"response": response},
        )
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    message_id = data.get("message_id") if data else response.get("message_id")
    if message_id is None:
        return "onebot:unknown"
    return f"onebot:{message_id}"


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
        if event.get("post_type") != "message":
            return None
        raw_text = self._extract_text(event).strip()
        if not self._is_command(raw_text):
            return None
        message_type = event.get("message_type")
        user_id = self._string_field(event, "user_id")
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
        message_id = self._required_string_field(event, "message_id")
        actor = Actor(id=f"onebot:{user_id}", roles=set(self.default_roles))
        return OneBotInboundCommand(
            raw_text=raw_text,
            actor=actor,
            bot_instance_id=self.bot_instance_id,
            chat_space_id=chat_space_id,
            user_id=command_user_id,
            idempotency_key=f"onebot:{message_id}",
            trace_id=f"onebot:{message_id}",
            reply_message_id=self._reply_message_id(event),
            original_event=event,
        )

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
