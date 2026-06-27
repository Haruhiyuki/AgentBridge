# AgentBridge

> Let a group chat safely drive the coding agents on your own machine — and sit down to take over the same terminal whenever you want.

**English** ｜ [中文](./README.zh-CN.md)

**AgentBridge** connects the native CLI agents on your computer (Claude Code, Codex) to group chat: members dispatch work under **scoped permissions**, the agent runs in a **real, visible terminal on your machine**, and you can **seamlessly take over the same session** at any time — the bot instantly steps back to observer.

It resolves one core tension: **chat users, a web console, and a local human** all want to drive the same interactive CLI without clobbering each other's terminal state. AgentBridge coordinates them with a **single-writer lease + an ordered semantic event stream**, then layers on multi-project/session routing, `/agent` commands, approvals, and audit.

---

## What it looks like

1. Someone types `/ab ask fix the 500 on the login endpoint` in chat;
2. `claude` starts in a real terminal on your machine; its progress and answer **stream back to chat live**;
3. Before a risky action, the agent raises an **approval** in chat and only proceeds once granted;
4. You return to your desk and run `agentbridge-console <session>` to **take over** the same session by hand — the bot yields automatically.

## Highlights

- **Native CLI, real terminal** — Claude Code / Codex run in a visible real terminal on your machine, not a cloud sandbox or a throwaway subprocess.
- **Seamless human takeover** — one model, "native TUI inside a PTY", identical for both agents; a local human **unconditionally preempts** the bot's write access.
- **Persistent sessions + task queue** — a long-lived session auto-claims the next task into the TUI instead of respawning a process each time.
- **Single-writer lease** — only one writer touches the terminal at a time, with an epoch version; stale writes are rejected.
- **Platform-agnostic bot integration** — a generic inbound envelope + SSE outbound stream + a tiny SDK; a new platform adapter is typically **~100–150 lines** of glue (QQ / Telegram / Discord / Lark …).
- **Full governance** — RBAC + access policy, risk-tiered approvals, question/plan interactions, hash-chained audit, device identity & certificates — plus a built-in operations web console.

## Quick start

Requires **Python ≥ 3.12**; [`uv`](https://github.com/astral-sh/uv) recommended.

```bash
uv sync --extra dev                                           # install deps
uv run uvicorn agentbridge.api:create_app --factory --reload  # start the control plane (in-memory + fake terminal)
curl http://127.0.0.1:8000/api/v1/health                      # smoke: {"status":"ok",...}
```

- Interactive API docs: `http://127.0.0.1:8000/docs`
- Operations console: `http://127.0.0.1:8000/admin`
- The default in-memory store is wiped on exit; to actually drive a local agent, see “Connect your local agent”.

## Using it

Chat users drive everything through `/agent` (alias `/ab`); every command goes through **parse → permission/policy → execute → audit**:

| You want to… | Command |
|---|---|
| Dispatch a task | `/ab ask <task>`, `/ab send`, `/ab claude <task>` / `/ab codex <task>` |
| See the state | `/ab status` (project/session/control/queue at a glance), `/ab health` |
| Manage sessions/projects | `/ab session list/new/use/close`, `/ab project list/use/create` |
| Queue | `/ab queue list/move/clear/pause/resume` |
| Respond to interactions | `/ab answer <answer>`, `/ab approve <n>` / `/ab deny <n>` |
| Control | `/ab control status/takeover/release` |

> List commands print **1-based numbers**, so `/ab select session 2`, `/ab approve 1`, etc. work without copying UUIDs on text-only platforms.

**Human takeover** — run a local daemon and attach a console to the same session:

```bash
uv run agentbridge-terminal-agent                                 # local daemon (Unix socket, token auth)
uv run agentbridge-console <session-id> --start --raw --release   # raw TTY passthrough; detach with Ctrl-]
```

The console acquires the `human` writer lease first; the bot immediately drops to observer.

## Connect your local agent

To run an agent in a real terminal you need: (1) the `claude` / `codex` CLI installed; (2) a tmux/PTY backend with the lifecycle monitor on. The execution model is "native TUI inside a PTY", and turn-completion is signaled two ways:

- **Claude** — via **Claude Code Hooks** (idempotently merged into the workspace `.claude/settings.local.json` on session start), which POST structured events back.
- **Codex / generic TUI** — no hooks; a **PTY output-idle heartbeat** marks the turn complete.

Minimal switches (details in [`docs/`](./docs)):

```bash
export AGENTBRIDGE_TERMINAL_BACKEND=tmux                  # or pty / pty_host
export AGENTBRIDGE_TERMINAL_LIFECYCLE_MONITOR_ENABLED=true
export AGENTBRIDGE_TERMINAL_AUTO_ADVANCE_QUEUES=true      # auto-claim queued turns
export AGENTBRIDGE_CLAUDE_HOOK_DEPLOY=true                # Claude via hooks
```

## Integrate your bot

The server already encapsulates **identity resolution, project/session routing, authz, idempotency, command execution, and answer-merging**, so a platform adapter is thin — just three steps:

1. **Forward** the user message as a platform-agnostic envelope to `POST /api/v1/bot-gateway/inbound-events`, get back `result`;
2. **Reply** with `result.message`;
3. **Stream** — if `result` carries a `session_id`, connect the SSE stream `GET /api/v1/sessions/{id}/chat-events/stream` and relay each message until it closes itself.

→ Full guide [`docs/BOT_INTEGRATION.md`](./docs/BOT_INTEGRATION.md), zero-dependency reference [`examples/minimal_bot.py`](./examples/minimal_bot.py), Python client [`agentbridge.bot_client`](./src/agentbridge/bot_client.py). These endpoints are grouped under the `bot-integration` tag in OpenAPI.

## Core concepts

| Concept | Meaning |
|---|---|
| **Project** | Container of workspaces and sessions, with quotas (active sessions, running/queued turns, daily turns per user). |
| **Workspace** | A code directory bounded by `allowed_root`, with writability and max writer sessions. |
| **Session** | A long-lived agent instance (`claude` / `codex` / `generic_tui`) backed by one terminal. |
| **Turn** | One task request: queued → claimed → running → completed, emitting semantic events along the way. |
| **Writer Lease** | The terminal's single-writer lock with an `epoch`; human > bot/web preemption. |
| **Interaction** | A human-in-the-loop event: question / approval / plan checkpoint. |
| **Chat Context** | A group/DM space that can bind a project, set approval quorums, and grant roles. |

## Architecture

```
   Chat platforms (OneBot / NoneBot / Telegram / Discord / Lark …)
        │  inbound messages / buttons / slash commands
        ▼
┌──────────────────────────────────────────────────────────┐
│  Bot Gateway     render → idempotent delivery → retry → rate-limit │
│  /agent commands parse → permission/policy → execute → audit       │
│  Control Plane   projects / sessions / turn queue / lease / approvals │
│  Agent adapters  Claude Hooks · Codex app-server           │
│  Terminal Agent  PTY / tmux backend + lifecycle monitor + lease gate │
└──────────────────────────────────────────────────────────┘
        │  gated writes (validate lease epoch)
        ▼
   Native terminal on your machine (claude / codex TUI in a PTY)
        ▲
        └── a human takes over anytime via the Console Client (raw TTY)
```

| Component | Responsibility |
|---|---|
| **Control Plane** | Domain core: projects/workspaces/sessions/turns/interactions/leases/audit, optimistic locking & quotas |
| **Bot Gateway** | Semantic events → platform delivery; idempotency records / retry / rate-limit / edit & delete |
| **Commands** | `/agent` parsing & execution (alias, permission, risk, confirmation metadata) |
| **Terminal Agent / PTY Host** | Terminal input gateway, lifecycle monitor, queue advance; a standalone PTY host reconnects after restarts |
| **Agent adapters** | Claude Hooks / Codex app-server bridging; event normalization and response injection |
| **Policy / security** | Access policy engine, RBAC, device identity, certificates & mTLS |

## Going further (production)

Enable as needed — see [`docs/`](./docs) and the interactive API docs at `/docs`:

- **Persistence** — set `AGENTBRIDGE_DATABASE_URL` for the SQLAlchemy repository + Alembic migrations ([database deployment](./docs/operations/DATABASE_DEPLOYMENT.md)).
- **Terminal backends** — `fake` (default/tests), `tmux`, `pty`, `pty_host` (standalone host that reconnects the same terminal after an API restart, [service manager](./docs/operations/PTY_HOST_SERVICE_MANAGER.md)).
- **Security** — REST/WS/Admin tokens (`AGENTBRIDGE_*_TOKEN(_FILE)`, reread per request, fail-closed), RBAC + access-policy engine, device identity & certificates/mTLS ([certificate ops](./docs/operations/DEVICE_CERTIFICATE_OPERATIONS.md)).
- **Approvals & quotas** — risk tiers (low→critical), quorum overrides `chat-context > project > global`, managed via `/agent policy`.
- **Audit** — hash-chained records, `GET /api/v1/audit` filter/export, signed archives with offline `agentbridge-audit-verify`.
- **Operations web** — panels under `/admin` for system health, projects/sessions, interactions, audit, access policy, terminal lifecycle, device identities, bot delivery.
- **Release gating** — `agentbridge-release` (preflight), `agentbridge-readiness` (report), `agentbridge-acceptance` (evidence); see [release candidate](./docs/operations/RELEASE_CANDIDATE.md).

## CLI tools

| Command | Purpose |
|---|---|
| `agentbridge-api` | Start the FastAPI control plane |
| `agentbridge-terminal-agent` | Local terminal daemon (Unix socket) |
| `agentbridge-pty-host` | Standalone PTY host (reconnects after restart) |
| `agentbridge-console` | Console client for human takeover |
| `agentbridge-adapter-client` | Claude Hook / Codex app-server bridge CLI |
| `agentbridge-readiness` / `-release` / `-acceptance` / `-audit-verify` | Readiness / release preflight / acceptance / audit verification |

## Development

```bash
uv sync --extra dev
uv run pytest            # test suite
uv run ruff check .      # lint (E/F/I/B/UP/N, line length 100)
```

Stack: Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Alembic · cryptography · uvicorn.
Current implementation status and direction: [`docs/DEVELOPMENT_STATE.md`](./docs/DEVELOPMENT_STATE.md).
