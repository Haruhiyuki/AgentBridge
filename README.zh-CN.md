# AgentBridge

> 让群聊安全地驱动你本机的编程 Agent，而你随时能坐下来接手同一个终端。

[English](./README.md) ｜ **中文**

**AgentBridge** 把你电脑上的原生 CLI Agent（Claude Code、Codex）接到群聊里：群成员在**受控权限**下远程派活，Agent 在你本机一个**真实、可见的终端**里跑；你本人随时可以**无缝接管同一个会话**，机器人立刻退为旁观。

它解决的核心矛盾是——**群聊用户、Web 后台、本地真人**三方都想操作同一个交互式 CLI，却谁也不想被对方搞乱终端状态。AgentBridge 用 **单写者租约 + 有序语义事件流** 协调三方，外面再叠上多项目/多会话、`/agent` 指令、权限审批与审计。

---

## 它是什么样的

1. 群里发 `/ab ask 修复登录接口的 500`；
2. 你电脑上的 `claude` 在真实终端里开跑，过程与回答**实时**流回群里；
3. Agent 要执行高风险操作时，在群里发起**审批**，通过了才继续；
4. 你回到电脑前，一行 `agentbridge-console <session>` 就**接管**同一个会话继续手敲——机器人自动让位。

## 核心特性

- **本机原生 CLI，真实终端** — Claude Code / Codex 跑在你机器上一个可见的真实终端里，不是云沙箱、不是一次性子进程。
- **真人无缝接管** — 统一模型是「原生 TUI 跑在 PTY 中」，两种 Agent 接管体验一致；本地真人可**无条件抢占**机器人的写入权。
- **持久会话 + 任务队列** — 长驻会话自动接力下一个任务投进 TUI，而非每次重开进程。
- **单写者租约** — 任意时刻只有一个写者能写终端，带 epoch 版本号，过期写入直接拒绝。
- **平台无关的机器人接入** — 通用入站信封 + SSE 出站流 + 轻量 SDK，接一个新平台通常 **100~150 行**胶水即可（QQ / Telegram / Discord / 飞书 …）。
- **完整治理** — RBAC + 访问策略、风险分级审批、问题/计划交互、哈希链审计、设备身份与证书；外加内置运维 Web。

## 快速开始

环境：**Python ≥ 3.12**，推荐 [`uv`](https://github.com/astral-sh/uv)。

```bash
uv sync --extra dev                                           # 装依赖
uv run uvicorn agentbridge.api:create_app --factory --reload  # 起控制平面（内存存储 + fake 终端，适合先跑通）
curl http://127.0.0.1:8000/api/v1/health                      # 冒烟：应返回 {"status":"ok",...}
```

- API 文档（交互式）：`http://127.0.0.1:8000/docs`
- 运维后台：`http://127.0.0.1:8000/admin`
- 默认内存存储退出即清空；要真正驱动本机 Agent，见下面「连上你本机的 Agent」。

## 上手用

群聊用户通过 `/agent`（别名 `/ab`）驱动一切，每条命令都走 **解析 → 权限/策略 → 执行 → 审计**：

| 你想做的 | 命令 |
|---|---|
| 派一个任务 | `/ab ask <任务>`、`/ab send`、`/ab claude <任务>` / `/ab codex <任务>` |
| 看现状 | `/ab status`（项目/会话/控制权/队列一张卡）、`/ab health` |
| 管会话/项目 | `/ab session list/new/use/close`、`/ab project list/use/create` |
| 队列 | `/ab queue list/move/clear/pause/resume` |
| 回应交互 | `/ab answer <作答>`、`/ab approve <编号>` / `/ab deny <编号>` |
| 控制权 | `/ab control status/takeover/release` |

> 列表命令带**一基数编号**，可用 `/ab select session 2`、`/ab approve 1` 等编号选择，纯文本平台无需复制 UUID。

**真人接管**：本机起一个终端守护，用控制台接管同一个会话——

```bash
uv run agentbridge-terminal-agent                  # 本地守护（Unix socket，token 鉴权）
uv run agentbridge-console <session-id> --start --raw --release   # 原生 TTY 透传接管，Ctrl-] 脱离
```

控制台会先申请 `human` 写者租约，机器人随即退为旁观。

## 连上你本机的 Agent

让 Agent 真正在本机终端跑起来，需要：① 装好 `claude` / `codex` CLI；② 用 tmux/PTY 终端后端 + 生命周期监控启动。统一执行模型是「原生 TUI 跑在 PTY 中」，回合完成信号有两种来源：

- **Claude** — 通过 **Claude Code Hooks**（会话启动时幂等合并进工作区 `.claude/settings.local.json`），把结构化事件回传。
- **Codex / 通用 TUI** — 无 Hook，用 **PTY 输出静默心跳**判定回合完成。

最小开关（详见 [`docs/`](./docs)）：

```bash
export AGENTBRIDGE_TERMINAL_BACKEND=tmux                  # 或 pty / pty_host
export AGENTBRIDGE_TERMINAL_LIFECYCLE_MONITOR_ENABLED=true
export AGENTBRIDGE_TERMINAL_AUTO_ADVANCE_QUEUES=true      # 队列自动接力
export AGENTBRIDGE_CLAUDE_HOOK_DEPLOY=true                # Claude 走 Hooks
```

## 接入你的机器人

服务端已经把**身份解析、项目/会话路由、鉴权、幂等、命令执行、回答合并**全部封装好，所以平台适配器很薄——只需三步：

1. **转发**：把用户消息按平台无关信封 `POST /api/v1/bot-gateway/inbound-events`，拿回 `result`；
2. **回发**：把 `result.message` 发回用户；
3. **流式**：若 `result` 带 `session_id`，连 SSE 流 `GET /api/v1/sessions/{id}/chat-events/stream`，逐条转发到流自动关闭。

→ 完整指南 [`docs/BOT_INTEGRATION.md`](./docs/BOT_INTEGRATION.md)，零依赖参考 [`examples/minimal_bot.py`](./examples/minimal_bot.py)，Python 客户端 [`agentbridge.bot_client`](./src/agentbridge/bot_client.py)。OpenAPI 里这些端点归在 `bot-integration` 标签下。

## 核心概念

| 概念 | 含义 |
|---|---|
| **Project** | 工作区与会话的容器，带配额（活动会话数、运行/排队 Turn、每用户每日 Turn）。 |
| **Workspace** | 受 `allowed_root` 约束的代码目录，可配可写性与最大写会话数。 |
| **Session** | 一个长驻 Agent 实例（`claude` / `codex` / `generic_tui`），对应一个终端。 |
| **Turn** | 一次任务请求：入队 → 认领 → 运行 → 完成，沿途产生语义事件。 |
| **Writer Lease** | 终端的单写者锁，带 `epoch`；真人 > 机器人/Web 的优先级抢占。 |
| **Interaction** | 需要人参与的事件：问题 / 审批 / 计划检查点。 |
| **Chat Context** | 一个群/私聊空间，可绑项目、设审批配额、授角色。 |

## 架构

```
   群聊平台（OneBot / NoneBot / Telegram / Discord / 飞书 …）
        │  入站消息 / 按钮 / 斜杠命令
        ▼
┌─────────────────────────────────────────────────────────┐
│  Bot Gateway     渲染 → 幂等投递 → 重试 → 限流              │
│  /agent 指令      解析 → 权限/策略 → 执行 → 审计            │
│  Control Plane   项目 / 会话 / Turn 队列 / 写者租约 / 审批   │
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
| **Bot Gateway** | 语义事件 → 平台消息投递，幂等记录 / 重试 / 限流 / 编辑删除 |
| **指令系统** | `/agent` 命令解析与执行（别名、权限、风险、确认元数据） |
| **Terminal Agent / PTY Host** | 终端输入网关、生命周期监控、队列推进；独立 PTY 宿主重启后可重连 |
| **Agent 适配器** | Claude Hooks / Codex app-server 桥接，事件归一与回应回灌 |
| **策略/安全** | 访问策略引擎、RBAC、设备身份、证书与 mTLS |

## 进阶与生产

按需启用，详见 [`docs/`](./docs) 与交互式 API 文档 `/docs`：

- **持久化** — 设 `AGENTBRIDGE_DATABASE_URL` 启用 SQLAlchemy 仓储 + Alembic 迁移（[数据库部署](./docs/operations/DATABASE_DEPLOYMENT.md)）。
- **终端后端** — `fake`（默认/测试）、`tmux`、`pty`、`pty_host`（独立宿主，API 重启后重连同一终端，[服务化](./docs/operations/PTY_HOST_SERVICE_MANAGER.md)）。
- **安全** — REST/WS/Admin 三类 token（`AGENTBRIDGE_*_TOKEN(_FILE)`，按请求重读、fail-closed）、RBAC + 访问策略引擎、设备身份与证书/mTLS（[证书运维](./docs/operations/DEVICE_CERTIFICATE_OPERATIONS.md)）。
- **审批与配额** — 风险分级（low→critical）、配额覆盖 `聊天上下文 > 项目 > 全局`、`/agent policy` 管理。
- **审计** — 哈希链审计、`GET /api/v1/audit` 过滤/导出、签名归档与离线验签 `agentbridge-audit-verify`。
- **运维 Web** — `/admin` 下系统健康、项目会话、交互审批、审计、访问策略、终端生命周期、设备身份、机器人投递等面板。
- **发布门禁** — `agentbridge-release`（预检）、`agentbridge-readiness`（就绪报告）、`agentbridge-acceptance`（验收证据），见 [发布候选](./docs/operations/RELEASE_CANDIDATE.md)。

## 命令行工具

| 命令 | 作用 |
|---|---|
| `agentbridge-api` | 启动 FastAPI 控制平面 |
| `agentbridge-terminal-agent` | 本地终端守护（Unix socket） |
| `agentbridge-pty-host` | 独立 PTY 宿主（重启可重连） |
| `agentbridge-console` | 真人接管的控制台客户端 |
| `agentbridge-adapter-client` | Claude Hook / Codex app-server 桥接 CLI |
| `agentbridge-readiness` / `-release` / `-acceptance` / `-audit-verify` | 就绪 / 发布预检 / 验收 / 审计验签 |

## 开发

```bash
uv sync --extra dev
uv run pytest            # 测试套件
uv run ruff check .      # lint（E/F/I/B/UP/N，行宽 100）
```

技术栈：Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Alembic · cryptography · uvicorn。
当前实现状态与方向见 [`docs/DEVELOPMENT_STATE.md`](./docs/DEVELOPMENT_STATE.md)。
