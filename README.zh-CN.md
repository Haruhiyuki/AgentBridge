<div align="center">

# AgentBridge

**从群聊驱动你_自己电脑上_的编程 Agent —— 而你随时能抢回键盘。**

Claude Code 与 Codex，跑在你掌控的真实终端里。群聊派活、实时看它干；你一坐下就能接管同一个会话。

[![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-early%20access-orange)](#项目状态)
[![Bot integration](https://img.shields.io/badge/docs-%E6%8E%A5%E5%85%A5%E4%B8%80%E4%B8%AA%20bot-2ea44f)](./docs/BOT_INTEGRATION.md)
&nbsp;·&nbsp; [English README](./README.md)

</div>

---

多数 Agent 平台跑在云端沙箱，或起一个用完即弃的子进程。**AgentBridge 让 Agent 跑在你本机一个看得见、抓得住的真实终端里**——群聊能驱动它、队友能堆任务、Web 后台能管它，但你永远不会被锁在自己的工具之外，三方也不会互相把终端搞乱。

难点在于：**群聊用户、Web 后台、本地真人**要共用同一个交互式 CLI 而不打架。AgentBridge 用 **单写者租约 + 有序语义事件流** 解决，外面再叠上多项目路由、`/agent` 指令、权限审批与审计。

## 它跑起来是什么样

```text
  群聊 ›  /ab ask 给登录接口加个每分钟 5 次的限流
  机器人 ›  ⏳ claude 已接手 —— 实时流式…
  机器人 ›  ⏺ 正在改 auth/login.py · 加了一个滑动窗口限流器
  机器人 ›  🔐 需要审批：跑一遍测试套件?   回复：/ab approve 1
  群聊 ›  /ab approve 1
  机器人 ›  ✅ 24 个测试通过。完成。

  # 回到电脑前，亲手接管同一个会话：
  $ agentbridge-console ses_3f9c --raw     # 机器人立刻退为旁观
```

## 核心特性

- ⌨️ **本机原生 CLI，真实终端** — Claude Code / Codex 跑在你机器上一个可见的真实终端里，不是云沙箱、不是一次性子进程。
- 🤝 **真人无缝接管** — 统一模型「原生 TUI 跑在 PTY 中」；本地真人**无条件抢占**机器人的写入权。
- ♻️ **持久会话 + 队列** — 长驻会话自动接力下一个任务投进 TUI，而非每次重开进程。
- 🔒 **单写者租约** — 任意时刻只有一个写者能写终端，带 epoch 版本号，过期写入直接拒绝。
- 🔌 **平台无关的机器人接入** — 通用入站信封 + SSE 出站流 + 轻量 SDK；接一个新平台通常 **100~150 行**胶水（QQ / Telegram / Discord / 飞书 …）。
- 🛡️ **治理内建** — RBAC + 访问策略、风险分级审批、问题/计划交互、哈希链审计、设备身份与 mTLS，外加内置运维 Web。

## 快速开始

环境：**Python ≥ 3.12**，推荐 [`uv`](https://github.com/astral-sh/uv)。

```bash
uv sync --extra dev                                           # 装依赖
uv run uvicorn agentbridge.api:create_app --factory --reload  # 起服务（内存存储 + fake 终端）
curl http://127.0.0.1:8000/api/v1/health                      # → {"status":"ok",...}
```

再打开 **`/docs`** 看交互式 API、**`/admin`** 进运维后台。默认内存存储退出即清空——要真正驱动 Agent，见 [连上你本机的 Agent](#连上你本机的-agent)。

## 上手用

一切都走 `/agent`（别名 `/ab`），每条命令都经 **解析 → 权限/策略 → 执行 → 审计**。

| 你想做的 | 命令 |
|---|---|
| 派一个任务 | `/ab ask <任务>` · `/ab send` · `/ab claude <任务>` / `/ab codex <任务>` |
| 看现状 | `/ab status`（项目 · 会话 · 控制权 · 队列一张卡）· `/ab health` |
| 管会话/项目 | `/ab session list/new/use/close` · `/ab project list/use/create` |
| 队列 | `/ab queue list/move/clear/pause/resume` |
| 回应交互 | `/ab answer <作答>` · `/ab approve <编号>` / `/ab deny <编号>` |
| 控制权 | `/ab control status/takeover/release` |

> 列表命令带**一基数编号**，纯文本平台用 `/ab approve 1`、`/ab select session 2` 即可，无需复制 UUID。

**亲手接管** —— 本机起一个守护，再用控制台接管同一个会话：

```bash
uv run agentbridge-terminal-agent                                 # 本地守护（Unix socket，token 鉴权）
uv run agentbridge-console <session-id> --start --raw --release   # 原生 TTY 透传，Ctrl-] 脱离
```

控制台先申请 `human` 写者租约，机器人随即退为旁观。

## 连上你本机的 Agent

要让 Agent 真正在终端跑起来：装好 `claude` / `codex` CLI，并用 tmux/PTY 后端 + 生命周期监控启动。统一模型是「原生 TUI 跑在 PTY 中」，回合完成信号有两种来源：

- **Claude** — 通过 **Claude Code Hooks**（会话启动时幂等合并进工作区 `.claude/settings.local.json`），把结构化事件回传。
- **Codex / 通用 TUI** — 无 Hook，用 **PTY 输出静默心跳**判定回合完成。

```bash
export AGENTBRIDGE_TERMINAL_BACKEND=tmux                  # 或 pty / pty_host
export AGENTBRIDGE_TERMINAL_LIFECYCLE_MONITOR_ENABLED=true
export AGENTBRIDGE_TERMINAL_AUTO_ADVANCE_QUEUES=true      # 队列自动接力
export AGENTBRIDGE_CLAUDE_HOOK_DEPLOY=true                # Claude 走 Hooks
```

## 接入你的机器人

服务端已包揽 **身份、路由、鉴权、幂等、命令执行、回答合并**，所以平台适配器很薄——三步：

1. **转发**：把消息按平台无关信封 `POST /api/v1/bot-gateway/inbound-events`，拿回 `result`。
2. **回发**：把 `result.message` 发回用户。
3. **流式**：若 `result` 带 `session_id`，连 SSE 流 `GET /api/v1/sessions/{id}/chat-events/stream`，逐条转发到流自动关闭。

→ 指南：[`docs/BOT_INTEGRATION.md`](./docs/BOT_INTEGRATION.md) · 零依赖示例：[`examples/minimal_bot.py`](./examples/minimal_bot.py) · Python 客户端：[`agentbridge.bot_client`](./src/agentbridge/bot_client.py)。这些端点在 OpenAPI 里归在 `bot-integration` 标签下。

## 核心概念

| 概念 | 含义 |
|---|---|
| **Project** | 工作区与会话的容器，带配额（活动会话数、运行/排队 Turn、每用户每日 Turn）。 |
| **Workspace** | 受 `allowed_root` 约束的代码目录，可配可写性与最大写会话数。 |
| **Session** | 一个长驻 Agent 实例（`claude` / `codex` / `generic_tui`），对应一个终端。 |
| **Turn** | 一次任务：入队 → 认领 → 运行 → 完成，沿途产生语义事件。 |
| **Writer Lease** | 终端的单写者锁，带 `epoch`；真人 > 机器人/Web 的优先级抢占。 |
| **Interaction** | 需要人参与的事件：问题 / 审批 / 计划检查点。 |
| **Chat Context** | 一个群/私聊空间，可绑项目、设审批配额、授角色。 |

## 架构

```
   群聊平台（OneBot / NoneBot / Telegram / Discord / 飞书 …）
        │  入站消息 · 按钮 · 斜杠命令
        ▼
┌─────────────────────────────────────────────────────────┐
│  Bot Gateway     渲染 → 幂等投递 → 重试 → 限流              │
│  /agent 指令      解析 → 权限/策略 → 执行 → 审计            │
│  Control Plane   项目 · 会话 · Turn 队列 · 写者租约 · 审批   │
│  Agent 适配器     Claude Hooks · Codex app-server         │
│  Terminal Agent  PTY / tmux 后端 + 生命周期监控 + 租约门禁   │
└─────────────────────────────────────────────────────────┘
        │  受控写入（校验租约 epoch）
        ▼
   本机原生终端（PTY 中运行 claude / codex 的 TUI）
        ▲
        └── 真人随时用 Console Client（原生 TTY 透传）接管
```

| 组件 | 职责 |
|---|---|
| **Control Plane** | 领域核心：项目/工作区/会话/Turn/交互/租约/审计，乐观锁与配额 |
| **Bot Gateway** | 语义事件 → 平台消息投递；幂等记录、重试、限流、编辑删除 |
| **指令系统** | `/agent` 命令解析与执行（别名、权限、风险、确认元数据） |
| **Terminal Agent / PTY Host** | 终端输入网关、生命周期监控、队列推进；独立 PTY 宿主重启后可重连 |
| **Agent 适配器** | Claude Hooks / Codex app-server 桥接，事件归一与回应回灌 |
| **策略/安全** | 访问策略引擎、RBAC、设备身份、证书与 mTLS |

## 进阶与生产

按需启用 —— 详见 [`docs/`](./docs) 与交互式 API `/docs`：

| 主题 | 从哪开始 |
|---|---|
| **持久化**（SQLAlchemy + Alembic） | 设 `AGENTBRIDGE_DATABASE_URL` · [数据库部署](./docs/operations/DATABASE_DEPLOYMENT.md) |
| **终端后端**（`fake`/`tmux`/`pty`/`pty_host`） | 独立宿主 API 重启后重连同一终端 · [服务化](./docs/operations/PTY_HOST_SERVICE_MANAGER.md) |
| **安全** | REST/WS/Admin token（按请求重读、fail-closed）、RBAC + 访问策略、设备证书/mTLS · [证书运维](./docs/operations/DEVICE_CERTIFICATE_OPERATIONS.md) |
| **审批与配额** | 风险分级（low→critical）、配额覆盖 `聊天上下文 > 项目 > 全局` |
| **审计** | 哈希链记录、过滤/导出、签名归档 + 离线 `agentbridge-audit-verify` |
| **运维 Web** | `/admin` 下健康、会话、交互、审计、策略、设备、投递面板 |
| **发布门禁** | `agentbridge-release` · `agentbridge-readiness` · `agentbridge-acceptance` · [发布候选](./docs/operations/RELEASE_CANDIDATE.md) |

## 命令行工具

| 命令 | 作用 |
|---|---|
| `agentbridge-api` | 启动 FastAPI 控制平面 |
| `agentbridge-terminal-agent` | 本地终端守护（Unix socket） |
| `agentbridge-pty-host` | 独立 PTY 宿主（重启可重连） |
| `agentbridge-console` | 真人接管的控制台客户端 |
| `agentbridge-adapter-client` | Claude Hook / Codex app-server 桥接 CLI |
| `agentbridge-readiness` / `-release` / `-acceptance` / `-audit-verify` | 就绪 · 发布预检 · 验收 · 审计验签 |

## 开发

```bash
uv sync --extra dev
uv run pytest            # 测试套件
uv run ruff check .      # lint（E/F/I/B/UP/N，行宽 100）
```

技术栈：Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Alembic · cryptography · uvicorn。

## 项目状态

早期可用 —— 后端基础。统一模型：原生 TUI 跑在 PTY 中，Claude 走 Hooks、Codex 用 PTY 静默心跳，配持久会话与人性化的会话/Agent/项目命令层。最新方向见 [`docs/DEVELOPMENT_STATE.md`](./docs/DEVELOPMENT_STATE.md)。
