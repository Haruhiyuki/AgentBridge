# 接入一个聊天平台（Bot 适配器指南）

AgentBridge 把「身份解析 → 项目/会话路由 → 鉴权 → 幂等 → 命令执行 → 回答合并/限频」全部封装在
**服务端**。聊天平台适配器（bot）因此可以很薄——通常 **100~150 行平台胶水**即可，无需在 bot 里
自己做角色映射、项目/工作区 provision、或手写流式合并逻辑。

> - Python 适配器：直接用官方轻量客户端 [`agentbridge.bot_client`](../src/agentbridge/bot_client.py)
>   （零额外依赖），示例见 [`examples/minimal_bot.py`](../examples/minimal_bot.py)。
> - 其它语言：照下面的 HTTP 契约实现这三步即可。

## 适配器只需做三件事

1. **转发**：把收到的用户消息/命令，按下面的「平台无关信封」POST 给通用入口；
2. **回发**：把同步返回的 `result.message` 发回给用户；
3. **流式**（仅当命令启动了 agent 工作）：用返回里的 `session_id` 拉取该会话回复，逐条发回平台。

其余的——身份、路由、鉴权、幂等、命令解析、把 agent 分片合并成干净回答——都在服务端完成。
Bot **不**在本地执行任何管理操作。

## 1. 入站：通用入口（平台无关）

```
POST /api/v1/bot-gateway/inbound-events
Authorization: Bearer <api-token>
```

请求体（信封，字段平台无关）：

```jsonc
{
  "event_type": "bot.command.received",   // 或 bot.message.received / bot.slash_command.received …
  "platform": "discord",                  // 任意平台标识：discord / slack / telegram / onebot.v11 …
  "bot_instance_id": "discord-bot-1",
  "user_id": "u-12345",                   // 必填；服务端以 f"{platform}:{user_id}" 作为 actor 身份
  "channel_id": "c-67890",                // 群聊传 channel_id；私聊改传 "scope": "private"
  "command": "/agent health",             // 命令文本（或用 "text" 传普通消息）
  "default_roles": ["member"],            // 该用户的角色（见「身份与权限」）
  "idempotency_key": "discord:msg-99999", // 建议带：同一条平台消息重发不会被重复处理
  "message_id": "msg-99999"
}
```

同步响应：

```jsonc
{
  "handled": true,
  "chat_context_id": "ctx_…",   // 服务端按 (platform, channel_id/scope) 自动建/复用聊天上下文
  "result": {
    "title": "Health",
    "message": "Control Plane 正常。",  // ← 把这个发回用户
    "data": { "...": "..." },
    "session_id": "ses_…",   // 仅当命令启动了一轮 agent 工作时出现（嵌套，建议深度查找）
    "turn_id": "turn_…"
  }
}
```

> OneBot/QQ 也可以用历史端点 `POST /api/v1/onebot/events`（接收 OneBot v11 原生事件结构）。
> **新平台一律推荐用上面的通用入口**，无需构造任何平台特有的事件结构。

## 2. 出站：流式拉取一轮回复（推荐用 SSE）

若 `result` 里带 `session_id`，说明启动了 agent 工作，回答会随时间产生。

### 推荐：SSE 流（服务端自动关流）

```
GET /api/v1/sessions/{session_id}/chat-events/stream?after_seq=<cursor>
```

返回 `text/event-stream`。**服务端封装了「轮询 + 游标推进 + active 判定 + 空闲宽限」**，并在
本轮（含排队多轮）真正结束时**自动关流**——bot 连上、把每条 `message` 帧的 text 发回平台、流关
就收尾即可，无需自己写循环：

```text
event: message
data: {"seq": 43, "kind": "answer", "text": "…"}    ← 把 text 发给用户

: keep-alive                                          ← 保活注释行，忽略

event: done
data: {"cursor": 43, "reason": "completed"}           ← 本轮结束，流即将关闭
```

可选 query：`idle_grace_seconds`（默认 8）、`poll_interval_seconds`（默认 1）、`max_seconds`（默认 1800）。

> Python：`AgentBridgeBotClient.stream_replies(session_id, on_message)` 一行搞定。

### 简单替代：游标轮询

不便用 SSE 时，轮询 `GET /api/v1/sessions/{session_id}/chat-events?after_seq=<cursor>`，响应为
`{messages, cursor, active, queued_turns}`：以 `cursor` 推进 `after_seq`、逐条转发 `messages[].text`、
当 `active=false` 且追上游标并过了一个小空闲宽限后结束。（`deliver-session-events` 与 WebSocket
`*/rendered-events/ws`、`bot-gateway/session-events/ws` 是面向幂等投递/富交互的进阶机制。）

### 消息 `kind` 语义

`answer` 最终回答、`progress` 过程进度、`question`/`approval`/`plan` 交互请求（用户用
`/ab answer`、`/ab approve` 等回复）、`error` 失败。

## 3. 身份与权限

- 服务端以 `f"{platform}:{user_id}"` 作为 actor 身份。
- **身份 → 角色** 有两种模式，可并存：
  - **bot 传入**（默认）：把用户角色放进 `default_roles`。平台特有账号体系不必进服务端。
  - **服务端绑定**（可选，省去 bot 维护本地角色文件）：把身份绑定到角色后，该身份发来的入站
    事件由服务端解析角色，`default_roles` 可不传：
    ```
    PUT  /api/v1/identity-roles      {actor, platform, user_id, roles:["maintainer"]}
    GET  /api/v1/identity-roles
    POST /api/v1/identity-roles/delete  {actor, platform, user_id}
    ```
    存在绑定即覆盖 `default_roles`；不存在则沿用 `default_roles`。需设
    `AGENTBRIDGE_IDENTITY_ROLES_FILE` 持久化。
- 角色对应的**权限**（能做什么）完全由服务端 RBAC 拥有，可经 `GET /api/v1/roles`、
  `PUT /api/v1/roles/{name}` 管理。
- 三档内置角色：`member`（提问/查看）、`maintainer`（会话/agent 管理）、`admin`（全部，含治理）。

## 4. 命令一览

适配器把平台消息原样作为 `command` 转发即可，命令解析在服务端完成。常用：

- `/agent ask <内容>` / `send` / `continue`：给当前会话排一轮任务（会返回 `session_id` 供流式）。
- `/agent answer <作答>`：回答当前交互式提问（默认认准当前提问）。
- `/agent approve|deny <编号>`：处理审批。
- `/agent project ...` / `session ...` / `status` / `health`：项目/会话/状态管理。

（`/agent` 可配短别名如 `/ab`；适配器把别名归一到 `/agent` 再转发，或直接透传由服务端识别。）

## 参考与对照

- 最小参考（推荐起点）：[`examples/minimal_bot.py`](../examples/minimal_bot.py)
- NoneBot 适配器（含原生命令注册、消息编辑等高级能力）：`src/agentbridge/nonebot_plugin.py`
- 设计意图见总设计文档第 14 章「机器人接入与多协议适配」。
