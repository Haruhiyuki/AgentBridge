<div align="center">

# AgentBridge

**Run the coding agents on _your own machine_ from a group chat — and grab the keyboard anytime.**

Claude Code & Codex, in a real terminal you control. Chat dispatches the work and watches it stream back live; you sit down and take over the same session whenever you want.

[![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-early%20access-orange)](#status)
[![Bot integration](https://img.shields.io/badge/docs-integrate%20a%20bot-2ea44f)](./docs/BOT_INTEGRATION.md)
&nbsp;·&nbsp; [中文 README](./README.zh-CN.md)

</div>

---

Most agent platforms run in a cloud sandbox or spawn a throwaway subprocess. **AgentBridge runs the real CLI on your machine, in a terminal you can see and grab.** Chat can drive it, your teammates can pile on tasks, the web console can manage it — but you're never locked out of your own tools, and nobody clobbers anybody else's terminal.

The hard part is letting **chat users, a web console, and a local human** share one interactive CLI without chaos. AgentBridge solves it with a **single-writer lease + an ordered semantic event stream**, then layers on multi-project routing, `/agent` commands, approvals, and audit.

## What it looks like

```text
  chat ›  /ab ask add a 5-req/min rate limit to the login route
   bot ›  ⏳ claude picked it up — streaming…
   bot ›  ⏺ editing auth/login.py · added a SlidingWindow limiter
   bot ›  🔐 approval needed: run the test suite?   reply: /ab approve 1
  chat ›  /ab approve 1
   bot ›  ✅ 24 passed. Done.

  # back at your desk, take over the very same session by hand:
  $ agentbridge-console ses_3f9c --raw     # the bot drops to observer instantly
```

## Highlights

- ⌨️ **Native CLI, real terminal** — Claude Code / Codex run in a visible terminal on your box, not a cloud sandbox or a one-shot subprocess.
- 🤝 **Seamless human takeover** — one model, _native TUI inside a PTY_; a local human **unconditionally preempts** the bot's write access.
- ♻️ **Persistent sessions + queue** — a long-lived session auto-claims the next task into the TUI instead of respawning a process each time.
- 🔒 **Single-writer lease** — only one writer touches the terminal at a time, epoch-versioned; stale writes are rejected.
- 🔌 **Platform-agnostic bots** — generic inbound envelope + SSE outbound stream + a tiny SDK; a new platform adapter is **~100–150 lines** (QQ / Telegram / Discord / Lark …).
- 🛡️ **Governance built in** — RBAC + access policy, risk-tiered approvals, question/plan interactions, hash-chained audit, device identity & mTLS — plus an operations web console.

## Quick start

Requires **Python ≥ 3.12**; [`uv`](https://github.com/astral-sh/uv) recommended.

```bash
uv sync --extra dev                                           # install
uv run uvicorn agentbridge.api:create_app --factory --reload  # run (in-memory store + fake terminal)
curl http://127.0.0.1:8000/api/v1/health                      # → {"status":"ok",...}
```

Then open **`/docs`** for the interactive API and **`/admin`** for the operations console. The default in-memory store is wiped on exit — to drive a real agent, see [Connect your local agent](#connect-your-local-agent).

## Using it

Everything is driven through `/agent` (alias `/ab`); every command runs **parse → permission/policy → execute → audit**.

| You want to… | Command |
|---|---|
| Dispatch a task | `/ab ask <task>` · `/ab send` · `/ab claude <task>` / `/ab codex <task>` |
| See the state | `/ab status` (project · session · control · queue at a glance) · `/ab health` |
| Manage sessions/projects | `/ab session list/new/use/close` · `/ab project list/use/create` |
| Queue | `/ab queue list/move/clear/pause/resume` |
| Respond to interactions | `/ab answer <answer>` · `/ab approve <n>` / `/ab deny <n>` |
| Control | `/ab control status/takeover/release` |

> List commands print **1-based numbers**, so `/ab approve 1` and `/ab select session 2` work without copying UUIDs on text-only platforms.

**Take over by hand** — run a local daemon, then attach a console to the same session:

```bash
uv run agentbridge-terminal-agent                                 # local daemon (Unix socket, token auth)
uv run agentbridge-console <session-id> --start --raw --release   # raw TTY passthrough; detach with Ctrl-]
```

The console grabs the `human` writer lease first; the bot immediately drops to observer.

## Connect your local agent

To run an agent in a real terminal you need the `claude` / `codex` CLI installed and a tmux/PTY backend with the lifecycle monitor on. The execution model is _native TUI inside a PTY_, and turn-completion is signaled two ways:

- **Claude** — via **Claude Code Hooks** (idempotently merged into the workspace `.claude/settings.local.json` on session start), which POST structured events back.
- **Codex / generic TUI** — no hooks; a **PTY output-idle heartbeat** marks the turn complete.

```bash
export AGENTBRIDGE_TERMINAL_BACKEND=tmux                  # or pty / pty_host
export AGENTBRIDGE_TERMINAL_LIFECYCLE_MONITOR_ENABLED=true
export AGENTBRIDGE_TERMINAL_AUTO_ADVANCE_QUEUES=true      # auto-claim queued turns
export AGENTBRIDGE_CLAUDE_HOOK_DEPLOY=true                # Claude via hooks
```

## Integrate your bot

The server already owns **identity, routing, authz, idempotency, command execution, and answer-merging**, so a platform adapter is thin — three steps:

1. **Forward** the message as a platform-agnostic envelope → `POST /api/v1/bot-gateway/inbound-events`, get back `result`.
2. **Reply** with `result.message`.
3. **Stream** — if `result` carries a `session_id`, connect `GET /api/v1/sessions/{id}/chat-events/stream` (SSE) and relay each message until it closes itself.

→ Guide: [`docs/BOT_INTEGRATION.md`](./docs/BOT_INTEGRATION.md) · zero-dep example: [`examples/minimal_bot.py`](./examples/minimal_bot.py) · Python client: [`agentbridge.bot_client`](./src/agentbridge/bot_client.py). These endpoints are tagged `bot-integration` in OpenAPI.

## Core concepts

| Concept | Meaning |
|---|---|
| **Project** | Container of workspaces & sessions, with quotas (active sessions, running/queued turns, daily turns per user). |
| **Workspace** | A code directory bounded by `allowed_root`, with writability and max writer sessions. |
| **Session** | A long-lived agent instance (`claude` / `codex` / `generic_tui`) backed by one terminal. |
| **Turn** | One task: queued → claimed → running → completed, emitting semantic events along the way. |
| **Writer Lease** | The terminal's single-writer lock with an `epoch`; human > bot/web preemption. |
| **Interaction** | A human-in-the-loop event: question / approval / plan checkpoint. |
| **Chat Context** | A group/DM that can bind a project, set approval quorums, and grant roles. |

## Architecture

```
   Chat platforms (OneBot / NoneBot / Telegram / Discord / Lark …)
        │  inbound messages · buttons · slash commands
        ▼
┌──────────────────────────────────────────────────────────────┐
│  Bot Gateway      render → idempotent delivery → retry → rate-limit │
│  /agent commands  parse → permission/policy → execute → audit      │
│  Control Plane    projects · sessions · turn queue · lease · approvals │
│  Agent adapters   Claude Hooks  ·  Codex app-server            │
│  Terminal Agent   PTY / tmux backend + lifecycle monitor + lease gate │
└──────────────────────────────────────────────────────────────┘
        │  gated writes (validate lease epoch)
        ▼
   Native terminal on your machine (claude / codex TUI in a PTY)
        ▲
        └── a human takes over anytime via the Console Client (raw TTY)
```

| Component | Responsibility |
|---|---|
| **Control Plane** | Domain core: projects/workspaces/sessions/turns/interactions/leases/audit, optimistic locking & quotas |
| **Bot Gateway** | Semantic events → platform delivery; idempotency records, retry, rate-limit, edit & delete |
| **Commands** | `/agent` parsing & execution (alias, permission, risk, confirmation metadata) |
| **Terminal Agent / PTY Host** | Terminal input gateway, lifecycle monitor, queue advance; a standalone PTY host reconnects after restarts |
| **Agent adapters** | Claude Hooks / Codex app-server bridging; event normalization and response injection |
| **Policy / security** | Access-policy engine, RBAC, device identity, certificates & mTLS |

## Going further

Enable as needed — see [`docs/`](./docs) and the interactive API at `/docs`:

| Topic | Where to start |
|---|---|
| **Persistence** (SQLAlchemy + Alembic) | set `AGENTBRIDGE_DATABASE_URL` · [database deployment](./docs/operations/DATABASE_DEPLOYMENT.md) |
| **Terminal backends** (`fake`/`tmux`/`pty`/`pty_host`) | standalone host reconnects a terminal across API restarts · [service manager](./docs/operations/PTY_HOST_SERVICE_MANAGER.md) |
| **Security** | REST/WS/Admin tokens (reread per request, fail-closed), RBAC + access policy, device certs/mTLS · [certificate ops](./docs/operations/DEVICE_CERTIFICATE_OPERATIONS.md) |
| **Approvals & quotas** | risk tiers (low→critical), quorum overrides `chat-context > project > global` |
| **Audit** | hash-chained records, filter/export, signed archives + offline `agentbridge-audit-verify` |
| **Operations web** | panels under `/admin` for health, sessions, interactions, audit, policy, devices, delivery |
| **Release gating** | `agentbridge-release` · `agentbridge-readiness` · `agentbridge-acceptance` · [release candidate](./docs/operations/RELEASE_CANDIDATE.md) |

## CLI tools

| Command | Purpose |
|---|---|
| `agentbridge-api` | Start the FastAPI control plane |
| `agentbridge-terminal-agent` | Local terminal daemon (Unix socket) |
| `agentbridge-pty-host` | Standalone PTY host (reconnects after restart) |
| `agentbridge-console` | Console client for human takeover |
| `agentbridge-adapter-client` | Claude Hook / Codex app-server bridge CLI |
| `agentbridge-readiness` / `-release` / `-acceptance` / `-audit-verify` | Readiness · release preflight · acceptance · audit verification |

## Development

```bash
uv sync --extra dev
uv run pytest            # test suite
uv run ruff check .      # lint (E/F/I/B/UP/N, line length 100)
```

Stack: Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Alembic · cryptography · uvicorn.

## Status

Early access — backend foundation. Unified model: native TUI in a PTY, Claude via hooks, Codex via PTY idle heartbeat, with persistent sessions and a humane session/agent/project command layer. Current direction lives in [`docs/DEVELOPMENT_STATE.md`](./docs/DEVELOPMENT_STATE.md).
