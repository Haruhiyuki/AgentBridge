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
- Focused unit/API tests for the above.

Not implemented yet:

- Terminal Agent process, tmux/PTY broker, or visible terminal launch.
- NoneBot/OneBot adapter and renderer.
- Real Claude Code/Codex adapters.
- Admin Web UI.
- Full RBAC/ABAC policy editor and multi-person approval flows.
- WebSocket transport for Terminal Agent and Bot Gateway event delivery.
- Normalized relational query layer for large audit/event searches; the current SQLAlchemy repository persists Pydantic payload snapshots with indexed routing columns.
- PostgreSQL-specific operational hardening, connection pooling policy, and migration deployment docs.

## Important Decisions

- The first backend slice uses an in-memory repository to make command, routing, lease, and API semantics testable before introducing persistence.
- Unknown ASCII-looking `/agent` management commands are rejected instead of being silently treated as prompts. Non-command free text still becomes `ask` to support the documented shortcut pattern.
- Semantic events are separate from audit records: events drive product state replay and Bot rendering, while audit records preserve security/accountability history.
- SQLAlchemy persistence is currently a single-process write-through snapshot repository. It is sufficient for restart recovery and contract tests, but multi-process production deployments need row-level updates and stronger transaction boundaries.
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

1. Refine the repository boundary so SQLAlchemy writes are row-level instead of full snapshots.
2. Implement Terminal Agent MVP using tmux control mode and a fake CLI fixture.
3. Add Bot Gateway event subscription and renderer intermediate representation.
4. Add plain-text/OneBot V11 rendering with golden snapshots.
5. Expand policy engine to explicit role bindings, approval quorum, and risk levels.
