# AgentBridge

> 面向群聊机器人的本地编程 Agent 远程协作平台

[English README](./README.md) ｜ 中文说明（本文）

**AgentBridge** 让群聊成员（QQ / Telegram / Discord / 飞书 等）在**受控权限**下,远程协作驱动你**本机上的原生 CLI Agent**(Claude Code、Codex),同时保证你本人能随时坐到电脑前**无缝接管同一个真实终端会话**。

它要调和的核心矛盾是:**群聊用户、Web 后台、本地真人**三方想操作同一个交互式 CLI,却不能互相把对方的终端状态搞乱。AgentBridge 用一套 **单写者租约(single-writer lease)+ 有序语义事件流** 来协调三者,并在外层叠加多项目/多会话、`/agent` 指令、权限审批、审计与机器人渲染投递。

权威产品/架构设计见 [`AgentBridge_项目总设计文档_v0.2_多项目多会话指令版.md`](./AgentBridge_项目总设计文档_v0.2_多项目多会话指令版.md);当前实现状态见 [`docs/DEVELOPMENT_STATE.md`](./docs/DEVELOPMENT_STATE.md)。

---

## 目录

- [核心特性](#核心特性)
- [整体架构](#整体架构)
- [核心概念](#核心概念)
- [快速开始](#快速开始)
- [持久化](#持久化)
- [终端后端](#终端后端)
- [Agent 启动与适配器](#agent-启动与适配器)
- [本地终端守护与人工接管](#本地终端守护与人工接管)
- [机器人接入与消息渲染](#机器人接入与消息渲染)
- [`/agent` 指令系统](#agent-指令系统)
- [交互、审批与计划](#交互审批与计划)
- [权限与安全模型](#权限与安全模型)
- [后台管理 Web](#后台管理-web)
- [审计与事件](#审计与事件)
- [命令行工具一览](#命令行工具一览)
- [开发与测试](#开发与测试)
- [发布与验收](#发布与验收)
- [文档索引](#文档索引)
- [当前状态与路线](#当前状态与路线)

---

## 核心特性

- **本机原生 CLI,真实终端** — Claude Code / Codex 跑在本机一个**可见的真实终端**里,不是云端沙箱、不是一次性子进程。
- **人类无缝接管** — 统一执行模型是「原生 TUI 跑在 PTY 中」,两种 Agent 接管体验完全一致;本地真人可**无条件抢占**机器人的控制权。
- **持久交互式会话** — 长驻会话 + 任务队列,会话空闲且无人占用控制权时自动认领下一个任务投入 TUI,而非每次重开进程。
- **单写者租约** — 任意时刻只有一个写者(真人 / 机器人 / Web)能向终端写入,带 epoch 版本号,过期写入直接拒绝。
- **语义事件流** — 终端输出被归一成有序、可重放、幂等的语义事件(`assistant.delta` / `tool.*` / `approval.requested` / `turn.completed` …)。
- **多协议机器人接入** — 统一 RenderDocument 中间表示 → OneBot V11 / 纯文本渲染,带幂等投递、失败重试、限流。
- **完整治理面** — 多项目/多会话/配额、RBAC + 访问策略引擎、风险分级审批、问题/计划/审批交互、哈希链审计、设备身份 + 证书 + mTLS。
- **内置后台 Web** — 系统健康、项目会话、审批交互、审计事件、访问策略、终端生命周期、设备身份、机器人投递等运维面板。

## 整体架构

```
   群聊平台 (OneBot / NoneBot / Telegram / Discord / 飞书 …)
        │  入站消息 / 按钮回调 / 斜杠命令
        ▼
┌──────────────────────────────────────────────────────────┐
│  Bot Gateway      渲染 → 幂等投递 → 失败重试 → 平台限流       │
│  /agent 指令系统   解析 → 权限/策略 → 执行 → 审计             │
│  Control Plane    项目 / 会话 / Turn 队列 / 写者租约 / 审批     │
│  Agent 适配器      Claude Hooks  ·  Codex app-server         │
│  Terminal Agent   PTY / tmux 后端 + 生命周期监控 + 租约门禁     │
└──────────────────────────────────────────────────────────┘
        │  受控写入(校验租约 epoch)
        ▼
   本机原生终端(PTY 中运行 claude / codex 的 TUI)
        ▲
        └── 真人随时用 Console Client(原生 TTY 透传)接管
```

**组件职责:**

| 组件 | 职责 |
|---|---|
| **Control Plane**(`control_plane.py`) | 领域核心:项目/工作区/会话/Turn/交互/租约/聊天上下文/审计,乐观锁与配额 |
| **Bot Gateway**(`bot_gateway.py`) | 把语义事件渲染成平台消息并投递,管理幂等记录、重试、限流、编辑/删除 |
| **指令系统**(`commands.py`) | `/agent` 文本/回调命令解析与执行,带别名、权限、风险、确认元数据 |
| **Terminal Agent**(`terminal_agent.py` / `terminal_daemon.py`) | 终端输入网关、生命周期监控、队列推进、离线保护 |
| **PTY Host**(`pty_host.py`) | 独立进程持有 PTY,API/守护重启后可重连同一终端 |
| **Agent 适配器**(`agent_adapter_*.py`) | Claude Hooks / Codex app-server 桥接,事件归一与回应回灌 |
| **渲染器**(`renderer.py`) | RenderDocument → OneBot V11 / 纯文本,代码块保护与安全分片 |
| **策略/权限**(`policy.py`、`device_*.py`) | 访问策略引擎、RBAC、设备身份、证书与 mTLS |
| **持久化**(`persistence.py` / `storage.py`) | 内存仓储 与 SQLAlchemy 仓储,Alembic 迁移 |

## 核心概念

- **Project(项目)** — 一组工作区与会话的容器,带配额(最大活动会话数、运行中/排队 Turn 数、每用户每日 Turn 数)。
- **Workspace(工作区)** — 受 `allowed_root` 边界约束的代码目录,可配置是否可写、最大写会话数。
- **Session(会话)** — 一个长驻的 Agent 实例(`agent_type` = `claude` / `codex` / `generic_tui`),对应一个终端。
- **Turn(回合)** — 一次任务请求,进入会话队列,被认领→运行→完成,产生语义事件。
- **Writer Lease(写者租约)** — 终端的单写者锁,带 `epoch` 版本号;真人 > 机器人/Web 的优先级抢占。
- **Interaction(交互)** — 需要人参与的事件:问题(question)、审批(approval)、计划检查点(plan)。
- **语义事件 / 渲染事件** — 有序、可重放、幂等的内部事件流,及其面向机器人的渲染投影。
- **Chat Context(聊天上下文)** — 一个群/私聊空间,可绑定项目、设置审批配额、授予角色。

## 快速开始

环境要求:**Python ≥ 3.12**;推荐使用 [`uv`](https://github.com/astral-sh/uv)。

```bash
# 安装依赖(含开发依赖)
uv sync --extra dev

# 运行测试与静态检查
uv run pytest
uv run ruff check .

# 启动控制平面(默认内存存储 + fake 终端后端,适合本地契约测试)
uv run uvicorn agentbridge.api:create_app --factory --reload
```

冒烟测试:

```bash
curl http://127.0.0.1:8000/api/v1/health        # 极简、无鉴权
curl http://127.0.0.1:8000/api/v1/readiness      # 运维就绪报告(可被鉴权保护)
uv run agentbridge-readiness --format actions     # 列出降级项与下一步操作
```

没有 `uv` 时,直接用当前 Python 环境:

```bash
python3 -m pytest
python3 -m uvicorn agentbridge.api:create_app --factory --reload
```

> 默认内存存储会在进程退出时清空数据;`/api/v1/health` 故意保持极简且不鉴权。

## 持久化

默认内存存储。设置 `AGENTBRIDGE_DATABASE_URL` 启用 SQLAlchemy 仓储:

```bash
export AGENTBRIDGE_DATABASE_URL=sqlite:///./agentbridge.db
uv run alembic upgrade head
uv run uvicorn agentbridge.api:create_app --factory --reload
```

- 本地一次性开发可用 `AGENTBRIDGE_AUTO_CREATE_SCHEMA=true` 启动时建表;**生产请显式跑 Alembic 迁移**。
- 连接池调优:`AGENTBRIDGE_DATABASE_POOL_SIZE`、`_MAX_OVERFLOW`、`_POOL_TIMEOUT_SECONDS`、`_POOL_RECYCLE_SECONDS`、`_POOL_PRE_PING`。
- 同主机多写进程共享快照仓储时,用 `AGENTBRIDGE_DATABASE_WRITE_LOCK_PATH` 配置单写者锁。
- 详见 [`docs/operations/DATABASE_DEPLOYMENT.md`](./docs/operations/DATABASE_DEPLOYMENT.md)。

## 终端后端

通过 `AGENTBRIDGE_TERMINAL_BACKEND` 选择:

| 后端 | 说明 |
|---|---|
| `fake` | **默认**,内存模拟,用于契约测试 |
| `tmux` | 复用 `agentbridge_<session-id>` tmux 会话,MVP 重启可重连 |
| `pty` | 用 `pty.openpty` + `subprocess` 起真实 PTY,后台线程读取输出 |
| `pty_host` | 客户端后端,连接独立的 `agentbridge-pty-host` 进程持有的 PTY |

**独立 PTY Host(API/守护重启后重连同一终端):**

```bash
install -m 0700 -d "$HOME/.agentbridge"
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$HOME/.agentbridge/pty-host.token"
chmod 0600 "$HOME/.agentbridge/pty-host.token"
export AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET="$HOME/.agentbridge/pty-host.sock"
export AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE="$HOME/.agentbridge/pty-host.token"
uv run agentbridge-pty-host

# 客户端侧
export AGENTBRIDGE_TERMINAL_BACKEND=pty_host
export AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET="$HOME/.agentbridge/pty-host.sock"
export AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE="$HOME/.agentbridge/pty-host.token"
export AGENTBRIDGE_TERMINAL_PTY_HOST_AUTO_START=true       # 探活确认无监听者才接管 socket
export AGENTBRIDGE_TERMINAL_PTY_HOST_WATCHDOG_ENABLED=true  # 看门狗:崩溃后重启 host
```

**生命周期与自动恢复:**

- `AGENTBRIDGE_TERMINAL_LIFECYCLE_MONITOR_ENABLED=true` 开启后台终端生命周期轮询。
- `AGENTBRIDGE_TERMINAL_AUTO_RESTART_ON_LOST=true` + 命令白名单 `AGENTBRIDGE_TERMINAL_AUTO_RESTART_COMMAND_ALLOWLIST`,可在 `terminal.lost` 后自动重启(受 `_MAX_ATTEMPTS` 限制,防止重启风暴)。
- `AGENTBRIDGE_TERMINAL_PTY_OUTPUT_LIMIT_CHARS`(默认 100 万)限定 PTY 输出保留窗口;过期游标会收到带尾部内容的 reset 帧。
- `AGENTBRIDGE_TERMINAL_AUTO_OPEN` + 预设 `AGENTBRIDGE_TERMINAL_OPEN_PRESET`(`auto` / `macos-terminal` / `gnome-terminal` / `wezterm` / `kitty` / …)可在启动会话后打开可见桌面终端窗口。

服务化部署见 [`docs/operations/PTY_HOST_SERVICE_MANAGER.md`](./docs/operations/PTY_HOST_SERVICE_MANAGER.md)(systemd / launchd 模板)。

## Agent 启动与适配器

启动终端时若省略 `command`,AgentBridge 按会话 `agent_type` 解析启动命令:`claude` → `claude`,`codex` → `codex`,`generic_tui` → `sh`。可覆盖:

```bash
export AGENTBRIDGE_AGENT_CLAUDE_COMMAND="claude"
export AGENTBRIDGE_AGENT_CODEX_COMMAND="codex-agentbridge"
export AGENTBRIDGE_AGENT_GENERIC_TUI_COMMAND="sh"
```

**统一执行模型 = 原生 TUI 跑在 PTY 中**,回合完成信号两种来源:

- **Claude** — 来自 **Claude Code Hooks**:会话启动时把 AgentBridge 钩子幂等合并进 `<workspace>/.claude/settings.local.json`(开关 `AGENTBRIDGE_CLAUDE_HOOK_DEPLOY`),原生 `claude` TUI 通过 Hook 把结构化事件 POST 回来。
- **Codex / generic_tui** — 无 Hook,用 **PTY 输出静默心跳**:静默超过 `AGENTBRIDGE_CODEX_IDLE_COMPLETE_SECONDS` 即判定 `turn.completed`(开关 `AGENTBRIDGE_TERMINAL_IDLE_TURN_COMPLETION`)。

**队列自动推进**:`AGENTBRIDGE_TERMINAL_AUTO_ADVANCE_QUEUES` 开启后,会话空闲、终端在跑、队列有活、无人持租约时,自动认领下一个 Turn 投入 TUI;回合完成后推进下一个。

**结构化适配器**:外部 Claude Hook / Codex app-server 进程可通过 `POST /api/v1/sessions/{id}/agent-adapter/events` 上报事件(`claude-hooks.v1` / `codex-app-server.v1` schema 门禁)。打包的 `agentbridge-adapter-client` CLI 提供握手、schema 快照、事件提交、阻塞等待审批/问题、离线 outbox 等桥接能力,并含 `claude-hook` 命令钩子 shim、`codex-app-server-proxy` stdio 代理。详见英文 README 与 [`docs/operations/CODEX_APP_SERVER_PROXY_SERVICE_MANAGER.md`](./docs/operations/CODEX_APP_SERVER_PROXY_SERVICE_MANAGER.md)。

## 本地终端守护与人工接管

启动本地 Terminal Agent(Unix socket,token 鉴权,socket 权限 `0600`,默认校验同 OS 用户 peer UID):

```bash
export AGENTBRIDGE_LOCAL_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
# 或用文件,守护进程每次请求重读(支持热轮换):
# export AGENTBRIDGE_LOCAL_TOKEN_FILE="$HOME/.agentbridge/terminal-agent.token"
uv run agentbridge-terminal-agent
```

**Console Client(真人接管)**:

```bash
export AGENTBRIDGE_LOCAL_TOKEN=...
export AGENTBRIDGE_TERMINAL_SOCKET="$HOME/.agentbridge/terminal-agent.sock"

# 行模式
uv run agentbridge-console <session-id> --start

# 原生 TTY 透传模式(退出时恢复终端状态;Ctrl-] 脱离)
uv run agentbridge-console <session-id> --start --raw --release
```

控制台在转发输入前会申请 **`human` 写者租约**并带 epoch 发送;raw 模式跟随 `stream_output` 帧显示实时输出、转发 `SIGWINCH`、把 Ctrl-C/Ctrl-D 映射为终端信号。脚本化检查可用 `--send` / `--paste` / `--snapshot`。

## 机器人接入与消息渲染

> **接一个新平台?** 先看 [`docs/BOT_INTEGRATION.md`](docs/BOT_INTEGRATION.md) 与最小参考
> [`examples/minimal_bot.py`](examples/minimal_bot.py):服务端已封装身份/路由/鉴权/幂等/命令执行/
> 回答合并,适配器只需「转发信封 → 回发 result → 流式拉会话」三步,平台胶水通常 100~150 行即可。

- **传输选择**:`AGENTBRIDGE_BOT_TRANSPORT=onebot.v11` + `AGENTBRIDGE_ONEBOT_HTTP_URL`;默认内存传输用于测试。
- **渲染**:RenderDocument 中间表示 → OneBot V11 / 纯文本兜底,保护代码块、列出动作、汇总工具进度,并做长度安全的确定性分片(围栏代码块在每个分片中保持配平)。
- **入站**:`POST /api/v1/onebot/events`(`/agent`、`/ab` 消息或回调)、平台中立的 `POST /api/v1/bot-gateway/inbound-events`、可选 NoneBot 包装器;按钮/选择/模态回调会以点击者真实 `user_id` 重新进入 RBAC/策略校验。
- **投递可靠性**:幂等投递记录(按平台/聊天上下文/事件/消息序)、失败指数退避重试、后台重试 worker、平台级限流策略 `AGENTBRIDGE_BOT_RATE_LIMITS`、`Retry-After`/429 自适应退避。
- **出站编辑/删除**:`POST /api/v1/bot-gateway/deliveries/edit|delete`,可选 OneBot 编辑扩展 `AGENTBRIDGE_ONEBOT_EDIT_ACTION` / `_EDIT_MESSAGE_FIELD`。
- **命令注册清单**:`GET /api/v1/bot-gateway/command-registration-manifest` 由同一份结构化命令注册表生成,供原生斜杠命令/菜单适配器使用,避免重复维护命令元数据。

## `/agent` 指令系统

群聊用户通过 `/agent`(及别名 `/ab`)文本命令驱动一切,统一经过 **解析 → 权限/策略 → 执行 → 审计** 的命令路径。主要命令分组:

- **项目**:`project list/info/use/create/bind/bindings/default`
- **会话**:`session list/new/use/info/close`、`/agent claude|codex [task]`(切换/创建会话并排队任务)、`agents`
- **任务**:`ask` / `send`、`queue list/remove/clear/move/pause/resume`
- **控制权**:`control status/takeover/release`
- **状态**:`/agent status` 统一状态卡(项目/会话/Agent/控制租约/队列/活动 Turn/同级会话)、`health`
- **交互**:`answer`、`approve`/`deny`/`approvals`/`approval show/cancel`、`question show/list`、`plan show/list/approve/revise/cancel`
- **治理**:`role list/grant/revoke`、`policy show/set`
- **纯文本可达性**:列表命令渲染**一基数编号行**,`select project|session <number>`、`approve 1` 等编号选择器让纯文本用户无需复制 UUID;缺参错误返回命令专属恢复提示。
- **本地化**:含中文别名 `状态` / `切换` / `使用`,以及分组中文 `help`。

结构化命令注册表通过 `GET /api/v1/commands` 导出(含别名、用法、参数 JSON Schema、所需权限、目标模式、风险、确认、渲染元数据)。

## 交互、审批与计划

- **问题 / 审批 / 计划检查点** 三类交互,带 REST 路由(创建/列举/查看/回答/投票/取消)与对应 `/agent` 命令。
- **风险分级审批**:low/medium/high/critical,可配额(quorum)。`AGENTBRIDGE_APPROVAL_QUORUMS` 覆盖默认;high/critical 需要 `approval.dangerous`(`dangerous_approver` 角色)。
- **配额覆盖优先级**:`聊天上下文 > 项目 > 全局`,通过 REST 与 `/agent policy` 管理。
- **生命周期**:`expires_at` / `ttl_seconds` 过期(`interaction.expired`)、`POST /api/v1/interactions/{id}/cancel` 取消;过期或取消后不可再回答/审批。

## 权限与安全模型

多层防护,逐层收紧:

- **RBAC + 聊天上下文角色绑定**:有效角色 = 请求/默认角色 ∪ 持久化群角色绑定,经 `/agent role …` 与 REST 管理;OneBot 入站默认 `member`,靠群绑定获得 `operator` 能力。
- **访问策略引擎**:`allow`/`deny` 规则匹配 动作/资源类型与 ID/Actor/角色/精确属性;**显式 deny 优先**;RBAC 作为兜底;`POST /api/v1/access-policy/simulate` 模拟决策来源与匹配规则。
- **设备身份**:数据库管理的托管设备,PBKDF2 盐化密钥哈希、托管客户端证书指纹与生命周期、`certificate_health` 到期/续期摘要;支持 CSR 签发/续期(PEM CA 或外部 CA/KMS/HSM 命令)、撤销、审计化证书健康扫描。
- **细粒度 scope**:`project_read/manage`、`session_read/manage/send`、`interaction_read/manage`、`terminal_read/control`、`audit_read`、`bot_gateway_read/manage`、`policy_read/manage`、`device_manage`、`command_parse/execute` 等,并可叠加 `allowed_resource_ids` 资源白名单。
- **传输鉴权**:REST 用 `AGENTBRIDGE_API_TOKEN(_FILE)`(Bearer 或 `X-AgentBridge-API-Token`);WebSocket 用 `AGENTBRIDGE_WS_TOKEN(_FILE)`;Admin 用 `AGENTBRIDGE_ADMIN_TOKEN(_FILE)`;每设备密钥 `AGENTBRIDGE_DEVICE_KEYS`;可信代理 mTLS 指纹门禁 `AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS(_FILE)`。token 文件按请求/连接重读,失败默认关闭(fail closed)。
- **写入门禁**:终端输入必须携带当前写者租约的 `epoch`/owner;真人或更高优先级抢占后,过期的机器人/Web 输入被拒绝。终端掉线进入**离线保护**,撤销非真人租约、挂起队列。

证书运维见 [`docs/operations/DEVICE_CERTIFICATE_OPERATIONS.md`](./docs/operations/DEVICE_CERTIFICATE_OPERATIONS.md)。

## 后台管理 Web

内置无构建步骤的运维页面(可选 token/证书门禁):

| 路径 | 用途 |
|---|---|
| `/admin` | 后台入口 |
| `/admin/system` | 系统健康、就绪报告、各 worker 状态、平台能力 |
| `/admin/projects` | 项目/工作区/会话、活动 Turn、队列、待审批、租约,可导出验收证据 |
| `/admin/interactions` | 问题/审批/计划操作 |
| `/admin/audit` | 跨流语义事件搜索 + 选定会话事件实时跟随 |
| `/admin/access-policy` | 访问策略规则编辑 |
| `/admin/terminal-lifecycle` | 终端生命周期、启动就绪/版本探测/适配器能力、outbox 状态 |
| `/admin/device-identities` | 设备身份、密钥轮换、证书签发/续期/健康扫描 |
| `/admin/bot-delivery` | 投递记录、重试 worker、限流、编辑/删除、命令注册遥测 |

## 审计与事件

- **哈希链审计**:命令级与领域状态变更写入带哈希链的审计记录;`command.executed` / `command.failed`(含 `denied` 结局)。
- **审计查询/导出**:`GET /api/v1/audit` 按动作/Actor/Trace/项目/会话/交互/载荷文本/时间窗过滤;`/api/v1/audit/export` 导出 JSON/CSV 或签名归档(外部命令 / Ed25519·RSA-PSS·ECDSA / HMAC-SHA256);`agentbridge-audit-verify` 离线验签。
- **语义事件**:有序、可重放(`GET /api/v1/sessions/{id}/events` 带 `after_seq`)、可跨流搜索(`GET /api/v1/events`、`/events/rendered`)、WebSocket 实时跟随,消费者 ACK 偏移持久化。
- **离线 outbox**:终端生命周期事件可经 `AGENTBRIDGE_TERMINAL_EVENT_OUTBOX` JSONL 暂存,在控制平面短暂不可用时本地追加、恢复后按序刷新,保持至少一次投递语义。

审计归档签名见 [`docs/operations/AUDIT_ARCHIVE_SIGNING.md`](./docs/operations/AUDIT_ARCHIVE_SIGNING.md)。

## 命令行工具一览

| 命令 | 作用 |
|---|---|
| `agentbridge-api` | 启动 FastAPI 控制平面 |
| `agentbridge-terminal-agent` | 本地终端守护(Unix socket) |
| `agentbridge-pty-host` | 独立 PTY 宿主进程(重启后可重连) |
| `agentbridge-console` | 真人接管用的本地控制台客户端 |
| `agentbridge-adapter-client` | Claude Hook / Codex app-server 桥接客户端/CLI |
| `agentbridge-readiness` | 读取就绪报告,列出降级项与下一步操作 |
| `agentbridge-acceptance` | 管理 MVP 验收证据清单与打包 |
| `agentbridge-release` | 发布候选预检(本地源码/打包交接边界) |
| `agentbridge-audit-verify` | 离线审计归档验签 |

## 开发与测试

```bash
uv sync --extra dev
uv run pytest                 # 约 22 个测试文件,覆盖契约/恢复/生命周期
uv run ruff check .           # ruff:E/F/I/B/UP/N 规则,行宽 100
uv run alembic upgrade head   # 应用迁移
```

真实 tmux 冒烟测试默认关闭,需显式开启:

```bash
AGENTBRIDGE_RUN_TMUX_TESTS=true \
  uv run pytest tests/test_terminal_agent.py::test_real_tmux_backend_smoke_streams_output_and_reuses_session
```

技术栈:Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Alembic · cryptography · uvicorn。

## 发布与验收

```bash
# 发布候选预检:本地源码/打包交接边界(版本一致性、console 脚本、runbook、配置变量等)
uv run agentbridge-release --profile local --format actions   # 缺失项记为警告(开发冒烟)
uv run agentbridge-release --profile rc --format actions       # 缺失项视为发布阻塞

# 就绪门禁
uv run agentbridge-readiness --format actions --fail-on-warn
```

可用 `AGENTBRIDGE_ACCEPTANCE_EVIDENCE_FILE` 配置 `agentbridge.acceptance_evidence.v1` 清单收集人工 MVP 签收;`agentbridge-acceptance init/set-section/set-checklist/attach-artifact/attach-admin-export/bundle/verify-bundle` 管理证据,生成可移植 ZIP 供发布评审离线验证。一次可用交接应同时通过 `release --profile rc` 与 `readiness --fail-on-warn`,并附验证过的验收 bundle。详见 [`docs/operations/RELEASE_CANDIDATE.md`](./docs/operations/RELEASE_CANDIDATE.md) 与 [`docs/operations/MVP_ACCEPTANCE_RUNBOOK.md`](./docs/operations/MVP_ACCEPTANCE_RUNBOOK.md)。

## 文档索引

- [`AgentBridge_项目总设计文档_v0.2_多项目多会话指令版.md`](./AgentBridge_项目总设计文档_v0.2_多项目多会话指令版.md) — 权威产品/架构总设计
- [`docs/DEVELOPMENT_STATE.md`](./docs/DEVELOPMENT_STATE.md) — 当前实现状态与最新方向
- [`docs/PROJECT_DESIGN.md`](./docs/PROJECT_DESIGN.md) — 项目设计补充
- [`README.md`](./README.md) — 英文 README(含最详尽的配置/API 说明)
- `docs/operations/` — 运维手册:数据库部署、PTY Host / Codex 代理服务化、证书运维、审计签名、验收 runbook、发布候选

## 当前状态与路线

- **当前里程碑**:M0 后端基础。
- **最新方向(2026-06-27)**:放弃一次性 `codex exec` 路线,改为设计中的**持久交互式会话**模型;Claude 与 Codex 均为一等 Agent;统一执行模型为「原生 TUI 跑在 PTY 中」,Claude 用 Hooks、Codex 用 PTY 静默心跳判定回合完成;配套人性化的会话/Agent/项目切换命令层。
- **端到端实地验证**(群聊 → 流式 → 人工接管 → 续跑)需本机已装 `claude` / `codex` CLI,并以上述 opt-in 开关 + 生命周期监控启动服务。
- **后续规划**:生产级 PTY 监督、更丰富的机器人渲染、provider 原生密钥托管、更深度的 Claude Hook / Codex app-server 适配。

---

> 本中文 README 是英文 [`README.md`](./README.md) 的导览版;最详尽的逐项配置、REST/WebSocket 接口与边界说明以英文 README 为准。
