"""官方轻量 Python bot 客户端：把任意聊天平台接入 AgentBridge 的「三步契约」封装成一个类。

服务端已封装身份解析、项目/会话路由、鉴权、幂等、命令执行、回答合并/限频，所以平台适配器
只需：

  1. 把用户消息/命令按「平台无关信封」发给通用入口 → 拿回 ``result``；
  2. 把 ``result.message`` 发回用户；
  3. 若命令启动了 agent 工作（result 带 session_id），用 SSE 流把回复逐条转发，流关即收尾。

本模块零额外依赖（仅标准库 urllib），核心逻辑拆成纯函数便于测试。详见 docs/BOT_INTEGRATION.md。
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Iterable, Iterator


def build_inbound_envelope(
    *,
    platform: str,
    bot_instance_id: str,
    user_id: str,
    text: str,
    channel_id: str | None = None,
    private: bool = False,
    roles: Iterable[str] = ("member",),
    message_id: str | None = None,
    event_type: str = "bot.command.received",
) -> dict[str, object]:
    """构造通用入口（``/bot-gateway/inbound-events``）的平台无关请求体。"""
    envelope: dict[str, object] = {
        "event_type": event_type,
        "platform": platform,
        "bot_instance_id": bot_instance_id,
        "user_id": str(user_id),
        "command": text,
        "default_roles": list(roles),
    }
    if private:
        envelope["scope"] = "private"
    elif channel_id is not None:
        envelope["channel_id"] = str(channel_id)
    if message_id is not None:
        # 幂等键：同一条平台消息重发不会被重复处理。
        envelope["idempotency_key"] = f"{platform}:{message_id}"
        envelope["message_id"] = str(message_id)
    return envelope


def dig(value: object, key: str) -> object | None:
    """在嵌套 dict/list 里深度查找第一个该 key 的非空值（result 里的 session_id 可能嵌套）。"""
    if isinstance(value, dict):
        if value.get(key) is not None:
            return value[key]
        for sub in value.values():
            found = dig(sub, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = dig(item, key)
            if found is not None:
                return found
    return None


def iter_sse_frames(lines: Iterable[str]) -> Iterator[tuple[str, dict[str, object]]]:
    """解析 SSE 行流 → ``(event, data)`` 帧序列；``: `` 注释（保活）与空行忽略。

    本服务端每帧是单行 ``data:``，故按「event 行记录类型、data 行产出帧、空行重置」处理即可。
    """
    event = "message"
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line.startswith(":"):
            continue  # 注释/保活
        if line == "":
            event = "message"
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            body = line[len("data:") :].strip()
            try:
                data = json.loads(body) if body else {}
            except ValueError:
                data = {"raw": body}
            yield event, data if isinstance(data, dict) else {"value": data}


class AgentBridgeBotClient:
    """面向 bot 适配器的最小客户端：一次 inbound + 一个 SSE 流式收尾。"""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        platform: str,
        bot_instance_id: str,
    ) -> None:
        self.base = base_url.rstrip("/") + "/api/v1"
        self.token = token
        self.platform = platform
        self.bot_instance_id = bot_instance_id

    def _headers(self, *, json_body: bool) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if json_body:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _open(self, method: str, path: str, body: dict | None = None, *, timeout: float):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers=self._headers(json_body=body is not None),
        )
        return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310

    # —— 步骤 1+2：转发命令，拿回要发的文本 ——
    def send_command(
        self,
        *,
        user_id: str,
        text: str,
        channel_id: str | None = None,
        private: bool = False,
        roles: Iterable[str] = ("member",),
        message_id: str | None = None,
        timeout: float = 30.0,
    ) -> dict[str, object]:
        """转发一条用户命令，返回服务端响应 ``{handled, chat_context_id, result, event}``。

        ``result`` 含 ``{title, message, data, ...}``（把 ``message`` 发回用户）；若启动了一轮
        agent 工作，``result`` 里还带 ``session_id``/``turn_id``（用 ``session_id_of`` 取出）。
        """
        envelope = build_inbound_envelope(
            platform=self.platform, bot_instance_id=self.bot_instance_id,
            user_id=user_id, text=text, channel_id=channel_id, private=private,
            roles=roles, message_id=message_id,
        )
        with self._open("POST", "/bot-gateway/inbound-events", envelope, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")

    @staticmethod
    def session_id_of(response: dict[str, object]) -> str | None:
        """从命令响应里取出该轮的 session_id（没有则说明命令未启动 agent 工作）。"""
        found = dig(response, "session_id")
        return str(found) if found else None

    # —— 步骤 3：SSE 流式拉取一轮（含排队多轮）的回复，服务端会在本轮结束时自动关流 ——
    def stream_replies(
        self,
        session_id: str,
        on_message: Callable[[str], None],
        *,
        after_seq: int = 0,
        idle_grace_seconds: float = 8.0,
        poll_interval_seconds: float = 1.0,
        max_seconds: float = 1800.0,
    ) -> None:
        """连上会话的 SSE 出站流，把每条可发送消息交给 ``on_message``，流关即返回。

        服务端封装了「轮询 + 游标推进 + active 判定 + 空闲宽限」，bot 无需自己写循环。
        """
        path = (
            f"/sessions/{session_id}/chat-events/stream?after_seq={after_seq}"
            f"&idle_grace_seconds={idle_grace_seconds}"
            f"&poll_interval_seconds={poll_interval_seconds}&max_seconds={max_seconds}"
        )
        with self._open("GET", path, timeout=max_seconds + 30) as resp:
            lines = (raw.decode("utf-8", "replace") for raw in resp)
            for event, data in iter_sse_frames(lines):
                if event == "message" and data.get("text"):
                    on_message(str(data["text"]))
                elif event == "done":
                    return
