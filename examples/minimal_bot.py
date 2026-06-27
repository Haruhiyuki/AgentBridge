"""AgentBridge 平台无关 bot 接入的最小示例。

把任意聊天平台接入 AgentBridge，适配器只需三步——服务端已封装身份/路由/鉴权/幂等/命令执行/
回答合并，所以平台胶水通常 100~150 行即可：

  1. 转发：把用户消息/命令按平台无关信封发给通用入口 → 拿回 ``result``；
  2. 回发：把 ``result.message`` 发回用户；
  3. 流式：若命令启动了 agent 工作（result 带 session_id），用 SSE 流把回复逐条转发，流关即收尾。

本示例直接用官方轻量客户端 ``agentbridge.bot_client``（零额外依赖，仅标准库）。真正接平台时，
把下面的「假平台循环」换成你平台的收发即可。契约细节见 docs/BOT_INTEGRATION.md。

对照：当前 QQ bot 手写了 ~1100 行（自己做角色映射、provision、流式轮询循环）。
"""

from __future__ import annotations

from agentbridge.bot_client import AgentBridgeBotClient


def demo() -> None:
    client = AgentBridgeBotClient(
        "http://127.0.0.1:8000",
        token="<your-api-token>",
        platform="demo",
        bot_instance_id="demo-bot",
    )

    def post_to_platform(text: str) -> None:
        print("→ 发给用户:", text)

    # 模拟收到一条群消息（换成你平台的事件即可）。
    incoming_text, user_id, channel_id, message_id = "/agent health", "u-1", "c-1", "m-1"

    # 步骤 1+2：转发命令并把结果发回。
    response = client.send_command(
        user_id=user_id, channel_id=channel_id, text=incoming_text,
        roles=("admin",), message_id=message_id,
    )
    message = (response.get("result") or {}).get("message")
    if message:
        post_to_platform(message)

    # 步骤 3：若启动了 agent 工作，SSE 流式把回答/提问转发回平台（服务端会在本轮结束时自动关流）。
    session_id = AgentBridgeBotClient.session_id_of(response)
    if session_id:
        client.stream_replies(session_id, post_to_platform)


if __name__ == "__main__":
    demo()
