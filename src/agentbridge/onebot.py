from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from agentbridge.domain import AgentBridgeError, BotPlatform, ChatContext, ErrorCode


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
