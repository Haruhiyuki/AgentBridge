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
- Local Terminal Agent daemon over a token-protected Unix socket.
- Local Console Client that acquires human lease on first input and forwards terminal input.
- RenderDocument intermediate representation with OneBot/plain-text fallback rendering.
- Bot Gateway delivery service with persistent idempotent delivery records, in-memory text transport, and OneBot V11 HTTP transport.
- Background Bot delivery retry worker with configurable interval and batch-size guardrails.
- Platform-scoped Bot delivery rate-limit policies that schedule unsent messages for retry.
- Chat-context scoped role bindings with `/agent role list/grant/revoke` and REST management APIs.
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

## Local Terminal Agent

Run the local Terminal Agent socket server:

```bash
export AGENTBRIDGE_LOCAL_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export AGENTBRIDGE_TERMINAL_SOCKET="$HOME/.agentbridge/terminal-agent.sock"
uv run agentbridge-terminal-agent
```

If `AGENTBRIDGE_LOCAL_TOKEN` is omitted, the daemon prints a generated token at startup. The socket file is created with mode `0600`. The JSONL socket protocol currently supports `health`, `start_session`, `acquire_human_lease`, `release_lease`, `submit_input`, and `snapshot`.

## Rendering

Semantic events can be mapped to platform-neutral render documents and plain-text fallback messages:

```bash
curl http://127.0.0.1:8000/api/v1/sessions/<session-id>/rendered-events
```

The current renderer targets reliable text fallback for OneBot-style platforms. Rich buttons/cards and platform-specific delivery state are planned for the Bot Gateway layer.

## Bot Gateway Delivery

Rendered session events can be delivered through the MVP Bot Gateway service:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/bot-gateway/deliver-session-events \
  -H 'content-type: application/json' \
  -d '{"session_id":"<session-id>","chat_context_id":"<chat-context-id>"}'
```

Delivery records are idempotent by platform, chat context, event, and message index, and can be persisted through the SQLAlchemy repository. Failed sends are recorded with attempt count, last error, and next retry time; `POST /api/v1/bot-gateway/retry-failed-deliveries` retries due failures.

The background retry worker is disabled by default. Enable it for long-running deployments:

```bash
export AGENTBRIDGE_BOT_RETRY_WORKER_ENABLED=true
export AGENTBRIDGE_BOT_RETRY_INTERVAL_SECONDS=30
export AGENTBRIDGE_BOT_RETRY_BATCH_SIZE=100
```

Configure platform-scoped rate limits as `<platform>=<capacity>/<window-seconds>`:

```bash
export AGENTBRIDGE_BOT_RATE_LIMITS="onebot.v11=20/60,plain_text=100/60"
```

When the limit is reached, AgentBridge records the delivery as `retrying` without sending it or incrementing the attempt count. The retry worker or run-once API sends it after `next_retry_at`.

Check worker state or run one bounded retry pass:

```bash
curl http://127.0.0.1:8000/api/v1/bot-gateway/rate-limits
curl http://127.0.0.1:8000/api/v1/bot-gateway/retry-worker
curl -X POST http://127.0.0.1:8000/api/v1/bot-gateway/retry-worker/run-once \
  -H 'content-type: application/json' \
  -d '{"limit":10}'
```

The current in-memory transport is intended for contract tests; real NoneBot inbound handling is the next integration layer.

To send through a OneBot V11 HTTP endpoint:

```bash
export AGENTBRIDGE_BOT_TRANSPORT=onebot.v11
export AGENTBRIDGE_ONEBOT_HTTP_URL=http://127.0.0.1:5700
export AGENTBRIDGE_ONEBOT_ACCESS_TOKEN=...
```

The OneBot transport maps chat contexts with `user_id` to `send_private_msg`; other contexts use `send_group_msg` with `chat_space_id` as `group_id`.

Inbound OneBot HTTP events can be posted to:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/onebot/events \
  -H 'content-type: application/json' \
  -d '{"event":{"post_type":"message","message_type":"group","group_id":10001,"user_id":20002,"message_id":30003,"raw_message":"/agent health"}}'
```

Only `/agent` and `/ab` text commands are executed. Non-command messages are ignored.

## Group Role Bindings

Command actors can carry bootstrap roles in the request, and AgentBridge can persist extra roles scoped to a chat context. Maintainers can grant a OneBot user operator permissions in the current group:

```text
/agent role grant onebot:20002 operator
/agent role list
/agent role revoke onebot:20002 operator
```

The same capability is available through:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/chat-contexts/<chat-context-id>/roles/grant \
  -H 'content-type: application/json' \
  -d '{"actor":{"id":"usr_1","roles":["maintainer"]},"target_actor_id":"onebot:20002","roles":["operator"]}'
```

## Console Client

Attach to a session through the local Terminal Agent socket:

```bash
export AGENTBRIDGE_LOCAL_TOKEN=...
export AGENTBRIDGE_TERMINAL_SOCKET="$HOME/.agentbridge/terminal-agent.sock"
uv run agentbridge-console <session-id> --start --command sh
```

The current console runs in line mode. Before forwarding the first line, it requests a `human` writer lease and then sends input with the returned epoch. Use `--send`, `--paste`, or `--snapshot` for scripted checks, and `--release` to release the lease on exit.

## API Smoke Test

```bash
curl http://127.0.0.1:8000/api/v1/health
```

The app uses in-memory storage by default, so data is reset when the process exits.
