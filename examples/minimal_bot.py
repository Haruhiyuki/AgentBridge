"""AgentBridge 的最小、零依赖、平台无关 bot 接入参考实现。

把任意聊天平台接入 AgentBridge，适配器其实只需要三步：

  1. 把收到的用户消息/命令，按「平台无关信封」转发给服务端通用入口；
  2. 把同步返回的 ``result.message`` 发回给用户；
  3. 若该命令启动了 agent 工作（result 里带 session_id），就流式拉取该会话的回复直到本轮结束。

服务端已经把「身份解析→路由→鉴权→幂等→命令执行→回答合并」全部封装好了，所以平台适配器
可以很薄。本文件只用 Python 标准库（urllib），把 HTTP 契约显式写出来，便于任何语言照搬。
真正接平台时，把 ``demo()`` 里的"假平台循环"换成你平台的收发即可。

对照：当前 QQ bot 手写了 ~1100 行（自己做角色映射、provision、流式轮询循环）；用下面这套
通用契约，平台胶水通常 100~150 行就够。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable


class AgentBridgeBotClient:
    """面向 bot 适配器的最小客户端：一次 inbound + 一个流式循环。"""

    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        platform: str,
        bot_instance_id: str,
    ) -> None:
        self.base = base_url.rstrip("/") + "/api/v1"
        self.token = token
        self.platform = platform
        self.bot_instance_id = bot_instance_id

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read() or b"{}")

    # —— 步骤 1+2：把用户命令转发给通用入口，拿回要发的文本 ——
    def send_command(
        self,
        *,
        user_id: str,
        text: str,
        channel_id: str | None = None,
        private: bool = False,
        roles: Iterable[str] = ("member",),
        message_id: str | None = None,
    ) -> dict:
        """转发一条用户命令，返回服务端的执行结果 dict。

        - 群聊传 ``channel_id``；私聊传 ``private=True``。
        - ``roles`` 是该用户的角色（鉴权用）。身份→角色的映射由适配器决定（见 README 说明），
          服务端按 ``f"{platform}:{user_id}"`` 记录 actor 身份。
        返回的 dict 含 ``result``（``{title, message, data, ...}``，把 ``message`` 发回用户即可）；
        若命令启动了一轮 agent 工作，``result`` 里还会带 ``session_id`` / ``turn_id``。
        """
        envelope = {
            "event_type": "bot.command.received",
            "platform": self.platform,
            "bot_instance_id": self.bot_instance_id,
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
            envelope["idempotency_key"] = f"{self.platform}:{message_id}"
            envelope["message_id"] = str(message_id)
        return self._request("POST", "/bot-gateway/inbound-events", envelope)

    # —— 步骤 3：流式拉取一轮（含排队多轮）的 agent 回复，直到真正结束 ——
    def stream_session_replies(
        self,
        session_id: str,
        on_message: Callable[[str], None],
        *,
        poll_seconds: float = 3.0,
        idle_grace_seconds: float = 8.0,
        max_seconds: float = 1800.0,
    ) -> None:
        """轮询服务端已合并、可直接发送的聊天消息，逐条交给 ``on_message`` 发到平台。

        服务端的 ``/chat-events`` 已经做好了过滤管道噪声、合并分片、去重、限频，bot 只管转发
        ``messages[].text``。结束判据用服务端权威的 ``active`` 标志（仍有活动/排队 turn 即为
        True），并留一个空闲宽限窗，接住「后台命令跑完后才迟到的尾段回答」。
        """
        deadline = time.monotonic() + max_seconds
        after_seq = self._request("GET", f"/sessions/{session_id}/chat-events")["cursor"]
        idle_deadline: float | None = None
        while time.monotonic() < deadline:
            time.sleep(poll_seconds)
            data = self._request(
                "GET", f"/sessions/{session_id}/chat-events?after_seq={after_seq}"
            )
            after_seq = data.get("cursor", after_seq)
            for message in data.get("messages") or []:
                if message.get("text"):
                    on_message(message["text"])
            if (data.get("messages")) or data.get("active", True):
                idle_deadline = None  # 还在动 → 重置宽限
            else:
                now = time.monotonic()
                if idle_deadline is None:
                    idle_deadline = now + idle_grace_seconds
                elif now >= idle_deadline:
                    return  # 已空闲且过了宽限 → 本轮真正结束


def _dig(value: object, key: str) -> object | None:
    """在嵌套 dict/list 里深度找第一个该 key 的非空值（result 里的 session_id 可能有嵌套）。"""
    if isinstance(value, dict):
        if value.get(key) is not None:
            return value[key]
        for sub in value.values():
            found = _dig(sub, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _dig(item, key)
            if found is not None:
                return found
    return None


def demo() -> None:
    """把这段「假平台循环」换成你平台的真实收发，就是一个完整 bot 适配器。"""
    client = AgentBridgeBotClient(
        "http://127.0.0.1:8000",
        token="<your-api-token>",
        platform="demo",
        bot_instance_id="demo-bot",
    )

    def post_to_platform(text: str) -> None:
        print("→ 发给用户:", text)

    # 模拟收到一条群消息。
    incoming_text, user_id, channel_id, message_id = "/agent health", "u-1", "c-1", "m-1"

    result = client.send_command(
        user_id=user_id, channel_id=channel_id, text=incoming_text,
        roles=("admin",), message_id=message_id,
    ).get("result") or {}
    if result.get("message"):
        post_to_platform(result["message"])

    # 若命令启动了 agent 工作，流式把回答/提问转发回平台。
    session_id = _dig(result, "session_id")
    if session_id:
        client.stream_session_replies(str(session_id), post_to_platform)


if __name__ == "__main__":
    demo()
