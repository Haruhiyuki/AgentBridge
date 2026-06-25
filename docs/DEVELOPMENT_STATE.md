# AgentBridge Development State

Last updated: 2026-06-25

## Product Understanding

AgentBridge is a local-first collaboration control layer for native programming-agent CLIs. The product must preserve a real terminal session on the user's machine, expose bot-friendly semantic events, and enforce a single-writer lease so group chat users, a web admin surface, and the local human cannot corrupt the same interactive CLI state.

The authoritative design input is `AgentBridge_项目总设计文档_v0.2_多项目多会话指令版.md`.

## Implementation Status

Current milestone: M0 backend foundation.

Implemented in this slice:

- Python/FastAPI project skeleton.
- In-memory Control Plane domain model and repository.
- Project, Workspace, Session, Turn, Interaction, WriterLease, ChatContext, CommandInvocation, and AuditEvent models.
- `/agent` text command parsing and execution for:
  - `project list/info/use/create`
  - `session list/new/use/info/close`
  - `ask` / `send`
  - `control status/takeover/release`
  - `health`
- Idempotent command execution by idempotency key.
- Optimistic locking for active project/session pointers.
- Writer lease epoch handling with local human preemption over bot control.
- Audit hash chain for command and domain state changes.
- Ordered semantic event streams for project/session state changes.
- REST event replay through `GET /api/v1/sessions/{id}/events`.
- Idempotent Terminal Agent event ingestion through `POST /api/v1/sessions/{id}/events`.
- SQLAlchemy-backed repository enabled with `AGENTBRIDGE_DATABASE_URL`.
- Alembic initial migration for projects, workspaces, chat contexts, bindings, sessions, turns, interactions, writer leases, command idempotency records, audit events, and semantic events.
- Recovery tests proving persisted control-plane state survives repository re-instantiation.
- Terminal Agent input gateway with fake and tmux backends.
- Local Terminal Agent daemon using JSONL over a Unix socket with token authentication.
- Local daemon actions for `health`, `start_session`, `acquire_human_lease`, `release_lease`, `submit_input`, and `snapshot`.
- Local Console Client command `agentbridge-console`.
- Console Client acquires a human writer lease on first input, caches the epoch, forwards text/paste/signal/resize through the daemon, and can release the lease on exit.
- Terminal input request idempotency now prevents duplicate backend writes for repeated request IDs.
- Terminal start/input/snapshot REST endpoints for MVP integration tests.
- Terminal input enforcement against current writer lease owner and epoch, with rejected/accepted semantic events.
- RenderDocument intermediate representation for bot-facing messages.
- OneBot/plain-text fallback renderer with code block preservation, action listing, and deterministic message splitting.
- Rendered event API through `GET /api/v1/sessions/{id}/rendered-events`.
- Bot Gateway delivery service using the renderer.
- In-memory Bot transport and idempotent delivery records keyed by platform, chat context, event, and message index.
- Delivery APIs through `POST /api/v1/bot-gateway/deliver-session-events` and `GET /api/v1/bot-gateway/deliveries`.
- Bot delivery records are now domain/repository state and persist through Alembic migration `0002_bot_delivery_records`.
- Recovery tests prove replay after repository re-instantiation skips already delivered Bot messages.
- OneBot V11 HTTP transport with group/private payload routing, bearer token support, and idempotency header.
- Bot transport selection through `AGENTBRIDGE_BOT_TRANSPORT=onebot.v11` and `AGENTBRIDGE_ONEBOT_HTTP_URL`.
- Focused unit/API tests for the above.

Not implemented yet:

- Raw TTY console mode, brokered PTY host, desktop terminal auto-launch, and terminal resize observation.
- NoneBot/OneBot adapter and renderer.
- Real Claude Code/Codex adapters.
- Admin Web UI.
- Full RBAC/ABAC policy editor and multi-person approval flows.
- WebSocket transport for Terminal Agent and Bot Gateway event delivery.
- Rich platform-specific renderer delivery state, message editing, and button/card support.
- NoneBot plugin wrapper around the OneBot transport and inbound command/action event handling.
- Normalized relational query layer for large audit/event searches; the current SQLAlchemy repository persists Pydantic payload snapshots with indexed routing columns.
- PostgreSQL-specific operational hardening, connection pooling policy, and migration deployment docs.

## Important Decisions

- The first backend slice uses an in-memory repository to make command, routing, lease, and API semantics testable before introducing persistence.
- Unknown ASCII-looking `/agent` management commands are rejected instead of being silently treated as prompts. Non-command free text still becomes `ask` to support the documented shortcut pattern.
- Semantic events are separate from audit records: events drive product state replay and Bot rendering, while audit records preserve security/accountability history.
- SQLAlchemy persistence is currently a single-process write-through snapshot repository. It is sufficient for restart recovery and contract tests, but multi-process production deployments need row-level updates and stronger transaction boundaries.
- Terminal input must pass through the AgentBridge gateway. Direct `tmux attach` remains outside the safety model because it bypasses writer leases.
- The local Terminal Agent socket is token-gated and chmodded to `0600`; production hardening still needs OS user checks, token rotation, and Windows named-pipe parity.
- The first Console Client is line-mode to validate the lease boundary. Raw mode and visible TUI passthrough remain separate work because they need careful terminal state restoration.
- Rendering is split into platform-neutral documents and platform renderers. The first renderer intentionally targets text fallback so unsupported Bot platforms still receive coherent output.
- Bot delivery idempotency is implemented before real platform integration so duplicate event replay cannot cause duplicate sends once a real transport is attached.
- Bot delivery records are persisted separately from semantic events so replay, delivery retries, and platform message IDs can evolve without mutating event history.
- OneBot outbound delivery is implemented as a transport contract first. Full NoneBot integration still needs inbound event parsing, lifecycle registration, and adapter-specific rate-limit handling.
- The original design document remains unchanged; this file is the rolling handoff/progress document for future sessions.

## Verification

Run:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
AGENTBRIDGE_DATABASE_URL=sqlite:////tmp/agentbridge-check.db uv run alembic upgrade head
```

## Next Development Backlog

1. Add tmux lifecycle supervision tests around the local daemon, including restart/reconnect behavior.
2. Upgrade the Console Client to raw TTY passthrough with safe terminal-state restoration and resize forwarding.
3. Add NoneBot plugin wrapper for OneBot V11 inbound messages, commands, and action callbacks.
4. Add delivery failure state, retry/backoff, and platform rate-limit handling.
5. Expand policy engine to explicit role bindings, approval quorum, and risk levels.
