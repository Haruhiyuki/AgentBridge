# AgentBridge

AgentBridge is a local programming-agent collaboration platform for bot-driven group chat workflows. It keeps native CLI agents such as Claude Code and Codex running in a visible local terminal while a control plane exposes structured project, session, command, lease, interaction, and audit APIs.

The initial implementation follows the product design in `AgentBridge_项目总设计文档_v0.2_多项目多会话指令版.md`.

## Current Scope

This repository currently contains the first executable backend slice:

- Python/FastAPI Control Plane skeleton.
- Shared domain models for projects, workspaces, sessions, turns, interactions, writer leases, chat contexts, and audit events.
- In-memory repository suitable for contract tests and local MVP prototyping.
- `/agent` command parser and executor for project/session routing, turn enqueueing, lease control, and idempotent invocation handling.
- Ordered semantic event streams with replay and idempotent Terminal Agent event ingestion.
- Optional SQLAlchemy persistence with an Alembic-managed schema.
- Terminal input gateway with fake/tmux backends and writer-lease epoch enforcement.
- REST API routes aligned with the design document's service interface.

NoneBot integration, Admin Web, visible local console attachment, and real Claude/Codex adapters are planned next milestones.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run alembic upgrade head
uv run uvicorn agentbridge.api:create_app --factory --reload
```

Without `uv`, use the active Python environment:

```bash
python3 -m pytest
python3 -m uvicorn agentbridge.api:create_app --factory --reload
```

## Persistence

The app defaults to in-memory storage. Set `AGENTBRIDGE_DATABASE_URL` to enable the SQLAlchemy repository:

```bash
export AGENTBRIDGE_DATABASE_URL=sqlite:///./agentbridge.db
uv run alembic upgrade head
uv run uvicorn agentbridge.api:create_app --factory --reload
```

For local throwaway development, `AGENTBRIDGE_AUTO_CREATE_SCHEMA=true` can create tables on startup. Production deployments should run Alembic migrations explicitly.

## Terminal Backend

The API uses a fake terminal backend by default for local contract tests. To use tmux for MVP experiments:

```bash
export AGENTBRIDGE_TERMINAL_BACKEND=tmux
```

Terminal input is accepted only when the request carries the current writer lease `epoch`, owner type, and owner ID. Stale Bot/Web inputs are rejected after human or higher-priority control preempts the lease.

## API Smoke Test

```bash
curl http://127.0.0.1:8000/api/v1/health
```

The app uses in-memory storage by default, so data is reset when the process exits.
