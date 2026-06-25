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
- Session semantic event WebSocket stream through `/api/v1/sessions/{id}/events/ws`, with `after_seq` replay and live tailing.
- Bot-facing rendered event WebSocket stream through `/api/v1/sessions/{id}/rendered-events/ws`, returning render documents plus OneBot/plain-text messages.
- Optional `AGENTBRIDGE_WS_TOKEN` authentication for session event, rendered event, and terminal command WebSocket routes.
- SQLAlchemy-backed repository enabled with `AGENTBRIDGE_DATABASE_URL`.
- Alembic initial migration for projects, workspaces, chat contexts, bindings, sessions, turns, interactions, writer leases, command idempotency records, audit events, and semantic events.
- Recovery tests proving persisted control-plane state survives repository re-instantiation.
- Terminal Agent input gateway with fake and tmux backends.
- Local Terminal Agent daemon using JSONL over a Unix socket with token authentication.
- Local daemon actions for `health`, `start_session`, `acquire_human_lease`, `release_lease`, `submit_input`, and `snapshot`.
- Local Terminal Agent client waits briefly for Unix socket recovery, allowing console requests to survive short daemon restart windows.
- Lifecycle tests cover local daemon socket restart/reconnect behavior and tmux backend reuse of existing sessions after Agent restart.
- Local Console Client command `agentbridge-console`.
- Console Client acquires a human writer lease on first input, caches the epoch, forwards text/paste/signal/resize through the daemon, and can release the lease on exit.
- Terminal input request idempotency now prevents duplicate backend writes for repeated request IDs.
- Terminal start/input/snapshot REST endpoints for MVP integration tests.
- Terminal command WebSocket through `/api/v1/sessions/{id}/terminal/ws`, supporting `health`, `start_session`, `acquire_lease`, `release_lease`, `submit_input`, and `snapshot`.
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
- OneBot V11 inbound event adapter for group/private text messages and reply segments.
- OneBot inbound API through `POST /api/v1/onebot/events`, converting `/agent` and `/ab` messages into the existing command execution flow.
- Optional NoneBot wrapper module that normalizes NoneBot/OneBot-style message events into the existing command execution flow without adding a hard NoneBot dependency.
- NoneBot callback/action payloads containing command strings can execute through the same audited `/agent` command path.
- Bot delivery failure records with attempt count, last error, next retry time, and exponential backoff.
- Retry API through `POST /api/v1/bot-gateway/retry-failed-deliveries`.
- Alembic migration `0003_bot_delivery_retry_metadata` adds retry metadata columns.
- Tests cover initial failure, due retry, and retry after repository restart.
- Background Bot delivery retry worker with environment-controlled startup.
- Retry worker status and bounded run-once APIs through `/api/v1/bot-gateway/retry-worker`.
- Retry worker scheduling honors each record's `next_retry_at` and caps retry throughput by configured batch size and interval.
- Platform-scoped Bot delivery rate-limit policies configured through `AGENTBRIDGE_BOT_RATE_LIMITS`.
- Rate-limited Bot messages are stored as `retrying` records with `next_retry_at` without consuming send attempts.
- Rate-limit policy API through `GET /api/v1/bot-gateway/rate-limits`.
- Bot Gateway now treats platform limit responses with `retry_after_seconds` as adaptive `retrying` deliveries and schedules `next_retry_at` from the observed delay.
- OneBot HTTP outbound parses HTTP 429/`Retry-After` and reset headers into adaptive Bot Gateway retry metadata.
- Control Plane interaction APIs for questions and approvals.
- REST interaction routes: create, list, show, answer, and vote.
- `/agent approvals`, `/agent approval show`, `/agent answer`, `/agent approve`, and `/agent deny`.
- Basic approval quorum handling with `pending`, `partially_approved`, and `resolved` states.
- Approval request and vote semantic events with Bot-rendered plain-text actions.
- Interaction expiration through `expires_at` or API `ttl_seconds`, with `interaction.expired` events.
- Interaction cancellation through `POST /api/v1/interactions/{id}/cancel` and `/agent approval cancel`.
- Expired and cancelled interactions are persisted and cannot be answered or approved afterward.
- Risk-aware approval policy for low/medium/high/critical interactions.
- `AGENTBRIDGE_APPROVAL_QUORUMS` can override default risk quorum.
- High and critical approvals require `approval.dangerous`, exposed through the `dangerous_approver` role.
- Approval interactions store requester, risk level, required quorum, and policy snapshot.
- Project and chat-context scoped approval quorum overrides with `chat_context > project > global` precedence.
- Approval policy management REST APIs through `GET/PUT /api/v1/projects/{id}/approval-policy` and `GET/PUT /api/v1/chat-contexts/{id}/approval-policy`.
- `/agent policy show` and `/agent policy set <risk> <quorum>` for group/project approval quorum management.
- Alembic migration `0005_approval_policy_overrides` persists approval policy overrides.
- Explicit chat-context role bindings for group users.
- Effective actor roles now merge request/default roles with persisted group role bindings before permission checks.
- `/agent role list/grant/revoke` commands for maintainers/admins.
- Role binding REST APIs through `GET /api/v1/chat-contexts/{id}/roles`, `POST /api/v1/chat-contexts/{id}/roles/grant`, and `POST /api/v1/chat-contexts/{id}/roles/revoke`.
- Alembic migration `0004_group_role_bindings` persists role bindings.
- OneBot inbound permissions can now start from default `member` roles and rely on group bindings for `operator` capabilities.
- Focused unit/API tests for the above.

Not implemented yet:

- Raw TTY console mode, brokered PTY host, desktop terminal auto-launch, and terminal resize observation.
- Richer OneBot renderer/action adapter and native NoneBot lifecycle registration helpers.
- Real Claude Code/Codex adapters.
- Admin Web UI.
- General ABAC policy editor for action/resource/attribute rules beyond approval quorum overrides.
- Production WebSocket hardening with mTLS/device keys and Bot Gateway subscriber fan-out beyond the current session event streams.
- Rich platform-specific renderer delivery state, message editing, and button/card support.
- Native action/callback support for platforms that expose buttons or interactions.
- Broader platform-specific delivery state, including message edits, deletes, acknowledgement tracking, and per-adapter action callbacks.
- Normalized relational query layer for large audit/event searches; the current SQLAlchemy repository persists Pydantic payload snapshots with indexed routing columns.
- PostgreSQL-specific operational hardening, connection pooling policy, and migration deployment docs.

## Important Decisions

- The first backend slice uses an in-memory repository to make command, routing, lease, and API semantics testable before introducing persistence.
- Unknown ASCII-looking `/agent` management commands are rejected instead of being silently treated as prompts. Non-command free text still becomes `ask` to support the documented shortcut pattern.
- Semantic events are separate from audit records: events drive product state replay and Bot rendering, while audit records preserve security/accountability history.
- SQLAlchemy persistence is currently a single-process write-through snapshot repository. It is sufficient for restart recovery and contract tests, but multi-process production deployments need row-level updates and stronger transaction boundaries.
- Terminal input must pass through the AgentBridge gateway. Direct `tmux attach` remains outside the safety model because it bypasses writer leases.
- The local Terminal Agent socket is token-gated and chmodded to `0600`; production hardening still needs OS user checks, token rotation, and Windows named-pipe parity.
- Local console/daemon clients open a fresh socket per request and retry connection for short restart windows. Long offline periods still need explicit user-facing reconnect state in raw TTY mode.
- The first Console Client is line-mode to validate the lease boundary. Raw mode and visible TUI passthrough remain separate work because they need careful terminal state restoration.
- The tmux backend treats an existing `agentbridge_<session-id>` session as resumable state after Agent restart, matching the design's MVP recovery path.
- Rendering is split into platform-neutral documents and platform renderers. The first renderer intentionally targets text fallback so unsupported Bot platforms still receive coherent output.
- Bot delivery idempotency is implemented before real platform integration so duplicate event replay cannot cause duplicate sends once a real transport is attached.
- Bot delivery records are persisted separately from semantic events so replay, delivery retries, and platform message IDs can evolve without mutating event history.
- OneBot outbound delivery is implemented as a transport contract first. The NoneBot wrapper is optional and dependency-free; full NoneBot integration still needs lifecycle registration, richer message components, and adapter-specific delivery capabilities.
- Delivery retry state is stored on delivery records, not events, so the immutable semantic event stream remains replayable while platform delivery can fail and recover independently.
- The retry worker reuses the Bot Gateway retry path instead of writing records directly. This keeps manual retry, background retry, and future scheduler behavior consistent.
- Platform rate-limit policies intentionally schedule unsent records as `retrying` instead of sleeping inside request handlers. This keeps API calls bounded and leaves actual waiting to the retry worker.
- Observed platform rate-limit responses are also stored as `retrying`, but keep the incremented attempt count because the platform was actually contacted.
- Interaction commands now route through the same command parser and audit chain as project/session commands. Approval voting is permission-gated by `approval.vote`; answering questions is gated by `session.send`.
- Interaction expiry is a terminal state and never auto-approves. Reads and interaction actions opportunistically advance due interactions to `expired` so pending lists do not show stale approval requests.
- Approval policy snapshots are copied onto each approval interaction so later policy changes do not rewrite historical approval requirements.
- Approval quorum overrides are intentionally scoped and snapshotted at interaction creation. Chat-context overrides win over project overrides, and explicit per-interaction `required_votes` remains the strongest override.
- OneBot inbound support currently executes text commands. The optional NoneBot wrapper can also map callback/action payloads that carry a command string through the same command execution path.
- Group role bindings are scoped to a chat context and actor ID. This keeps OneBot user permissions local to the group/private context while still allowing command/API callers to carry bootstrap roles.
- WebSocket session streams are read-side transports over immutable semantic events. They use `after_seq` cursors for replay/reconnect and do not mutate Bot delivery records.
- `AGENTBRIDGE_WS_TOKEN` is the current MVP WebSocket gate for local/browser clients. It is intentionally simpler than the design's production mTLS/device-key model, which remains future hardening work.
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

1. Upgrade the Console Client to raw TTY passthrough with safe terminal-state restoration and resize forwarding.
2. Expand policy engine to general ABAC action/resource/attribute rules and policy simulation.
3. Replace the MVP WebSocket token gate with mTLS/device-key auth and add production Bot Gateway subscriber fan-out.
4. Add optional real-tmux integration smoke tests gated on tmux availability.
5. Add richer platform delivery state for edits/deletes/acknowledgements.
6. Add native NoneBot matcher setup helpers once a NoneBot dependency boundary is selected.
