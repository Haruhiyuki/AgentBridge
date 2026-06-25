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
- Filtered audit API through `GET /api/v1/audit`, backed by repository-level newest-first queries bounded by `limit` and supporting action, actor, trace, project, session, and interaction filters for operational review; SQLAlchemy uses indexed action/actor columns before payload-level filters.
- Ordered semantic event streams for project/session state changes.
- REST event replay through `GET /api/v1/sessions/{id}/events`.
- Cross-stream semantic event search through `GET /api/v1/events`, backed by repository-level newest-first queries bounded by `limit` and supporting project, session, turn, interaction, event type, source, and trace filters for operational investigation.
- Audit and semantic event search support a bounded `q` text filter over audit `details` and semantic event `payload`, exposed through REST and the Audit & Events Admin Web page.
- Idempotent Terminal Agent event ingestion through `POST /api/v1/sessions/{id}/events`.
- Session semantic event WebSocket stream through `/api/v1/sessions/{id}/events/ws`, with `after_seq` replay and live tailing.
- Bot-facing rendered event WebSocket stream through `/api/v1/sessions/{id}/rendered-events/ws`, returning render documents plus OneBot/plain-text messages.
- Optional `AGENTBRIDGE_API_TOKEN` authentication for REST API routes other than `/api/v1/health`, accepting bearer tokens or `X-AgentBridge-API-Token`, with unlocked Admin Web cookies accepted when `AGENTBRIDGE_ADMIN_TOKEN` is configured; if only `AGENTBRIDGE_API_TOKEN` is configured, it also gates and unlocks the built-in Admin Web pages.
- Optional `AGENTBRIDGE_WS_TOKEN` authentication for session event, rendered event, and terminal command WebSocket routes; same-origin browser sessions with an unlocked Admin Web cookie can connect to protected WebSocket streams without exposing the token to page JavaScript.
- Optional `AGENTBRIDGE_DEVICE_KEYS` JSON mapping for per-device HTTP and WebSocket keys, using device ID plus device key rather than one shared bearer token.
- SQLAlchemy-backed repository enabled with `AGENTBRIDGE_DATABASE_URL`.
- Alembic initial migration for projects, workspaces, chat contexts, bindings, sessions, turns, interactions, writer leases, command idempotency records, audit events, and semantic events.
- Recovery tests proving persisted control-plane state survives repository re-instantiation.
- Terminal Agent input gateway with fake, tmux, and stdlib PTY backends.
- PTY backend launches local commands with `pty.openpty` and `subprocess.Popen`, owns process-group cleanup, reads the PTY master fd on a background thread, and exposes cursor-based output chunks.
- PTY output retention is bounded by `AGENTBRIDGE_TERMINAL_PTY_OUTPUT_LIMIT_CHARS` (default `1000000`); stale cursors receive reset chunks with the retained tail instead of allowing unbounded memory growth.
- PTY backend can persist a local JSON host-state registry through `AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH`, recording session ID, cwd, command, host pid, child pid, process status, exit code, and output cursor metadata for future host supervision.
- Independent `agentbridge-pty-host` process that owns the stdlib PTY backend behind a chmod `0600` Unix socket, plus `AGENTBRIDGE_TERMINAL_BACKEND=pty_host` client backend for API/daemon processes to reconnect to host-owned PTYs after restart.
- `pty_host` client backend can auto-start `agentbridge-pty-host` with `AGENTBRIDGE_TERMINAL_PTY_HOST_AUTO_START=true`; it removes stale Unix socket files, waits for host health, and retries the original request once.
- `pty_host` supervisor can run an optional API/daemon-lifecycle watchdog with `AGENTBRIDGE_TERMINAL_PTY_HOST_WATCHDOG_ENABLED=true`, periodically checking host health and restarting `agentbridge-pty-host` after a crash.
- When PTY host watchdog and `AGENTBRIDGE_TERMINAL_AUTO_RESTART_ON_LOST=true` are both enabled, the lifecycle monitor can detect PTY sessions lost by host-process death, emit `terminal.lost`, and restart them from the latest persisted `terminal.started` command.
- Terminal lifecycle status now includes backend supervision state, exposing whether a PTY host watchdog is enabled/running and how many host restarts it has performed.
- PTY host service-manager deployment guide and templates for systemd user services and macOS launchd user agents live under `docs/operations/`, including env-file/token handling and recovery expectations.
- Local Terminal Agent daemon using JSONL over a Unix socket with token authentication.
- Local daemon actions for `health`, `lifecycle_status`, `run_lifecycle_monitor_once`, `start_session`, `restart_session`, `acquire_human_lease`, `release_lease`, `submit_input`, `snapshot`, `status`, cursor-based `read_output`, and multi-frame `stream_output`.
- Local Terminal Agent client waits briefly for Unix socket recovery, allowing console requests to survive short daemon restart windows.
- Lifecycle tests cover local daemon socket restart/reconnect behavior and tmux backend reuse of existing sessions after Agent restart, with an opt-in real-tmux smoke test gated by `AGENTBRIDGE_RUN_TMUX_TESTS=true`.
- Local Console Client command `agentbridge-console`.
- Console Client acquires a human writer lease on first input, caches the epoch, forwards text/paste/signal/resize through the daemon, and can release the lease on exit.
- Console Client raw TTY passthrough mode with safe terminal-state restoration, initial resize forwarding, `SIGWINCH` resize forwarding, Ctrl-C/Ctrl-D signal mapping, and Ctrl-] detach.
- Terminal backends expose cursor-based output chunks through `read_output(after_cursor)`, with fake/tmux implementations backed by current snapshot state and the PTY backend backed by its reader loop buffer.
- Console Client raw mode follows terminal output through daemon `stream_output` frames, appending cursor chunks and repainting when the backend reports a reset.
- Terminal status is exposed through REST, Terminal WebSocket, and local daemon actions; PTY status includes running/exited state, exit code, pid, and current output cursor.
- Terminal Agent emits `terminal.exited` once per observed terminal start generation when status or lifecycle polling first sees a backend has exited, and the renderer has an operator-visible fallback for that lifecycle event.
- Terminal lifecycle monitor can poll known started terminals in the background; the local Terminal Agent daemon enables it by default, and the FastAPI lifespan can enable it with `AGENTBRIDGE_TERMINAL_LIFECYCLE_MONITOR_ENABLED=true`.
- Terminal lifecycle monitor status and manual run-once operations are exposed through `GET /api/v1/terminal/lifecycle-monitor`, `POST /api/v1/terminal/lifecycle-monitor/run-once`, and local daemon lifecycle actions for operational inspection.
- Terminal lifecycle tracking is reconstructed from persisted semantic events at service startup, restoring known terminal start generations and already-reported exits after Control Plane or daemon process restarts.
- Recovered terminal generations whose backend state is missing emit `terminal.lost` once per generation, with persisted de-duplication and an operator-visible renderer fallback.
- Terminal restart can be requested through REST, Terminal WebSocket, and the local daemon; if no command is supplied, it reuses the latest persisted `terminal.started` command, records a new start generation, and avoids replacing an already-running backend.
- Terminal lifecycle policy can automatically restart lost recovered terminals with `AGENTBRIDGE_TERMINAL_AUTO_RESTART_ON_LOST=true`, capped by `AGENTBRIDGE_TERMINAL_AUTO_RESTART_MAX_ATTEMPTS` to prevent restart loops.
- Local Terminal Agent can open a visible desktop console after `start_session` through `AGENTBRIDGE_TERMINAL_AUTO_OPEN`, either with a custom `AGENTBRIDGE_TERMINAL_OPEN_COMMAND` template or built-in `AGENTBRIDGE_TERMINAL_OPEN_PRESET` values.
- Built-in desktop terminal presets support `auto`, `macos-terminal`, `gnome-terminal`, `konsole`, `wezterm`, `alacritty`, `kitty`, and `xterm`; token/socket state is kept out of launched argv and supplied through environment variables or a short-lived local launcher script for macOS Terminal.
- Terminal input request idempotency now prevents duplicate backend writes for repeated request IDs.
- Terminal start/input/snapshot/status REST endpoints for MVP integration tests.
- Terminal command WebSocket through `/api/v1/sessions/{id}/terminal/ws`, supporting `health`, `start_session`, `restart_session`, `acquire_lease`, `release_lease`, `submit_input`, `snapshot`, and `status`.
- Terminal input enforcement against current writer lease owner and epoch, with rejected/accepted semantic events.
- RenderDocument intermediate representation for bot-facing messages.
- OneBot/plain-text fallback renderer with code block preservation, action listing, and deterministic message splitting.
- Rendered event API through `GET /api/v1/sessions/{id}/rendered-events`.
- Bot Gateway delivery service using the renderer.
- In-memory Bot transport and idempotent delivery records keyed by platform, chat context, event, and message index.
- Delivery APIs through `POST /api/v1/bot-gateway/deliver-session-events` and `GET /api/v1/bot-gateway/deliveries`.
- Bot Gateway subscriber WebSocket through `/api/v1/bot-gateway/session-events/ws`, pushing `bot.render.create` frames with chat routing metadata and per-message idempotency keys.
- Bot render frames include platform-neutral action descriptors for button-capable adapters, with callback payloads that route back into audited `/agent` commands.
- Bot delivery result API through `POST /api/v1/bot-gateway/delivery-results`, tracking platform `acknowledge`, `edit`, and `delete` results by delivery idempotency key.
- Bot Gateway outbound mutation APIs through `POST /api/v1/bot-gateway/deliveries/edit` and `/delete`, calling the selected transport before updating platform delivery state.
- Bot delivery records are now domain/repository state and persist through Alembic migration `0002_bot_delivery_records`.
- Alembic migration `0006_bot_delivery_platform_state` persists platform delivery lifecycle columns.
- Recovery tests prove replay after repository re-instantiation skips already delivered Bot messages.
- OneBot V11 HTTP transport with group/private payload routing, `delete_msg`, bearer token support, and idempotency header.
- Bot transport selection through `AGENTBRIDGE_BOT_TRANSPORT=onebot.v11` and `AGENTBRIDGE_ONEBOT_HTTP_URL`.
- OneBot V11 inbound event adapter for group/private text messages and reply segments.
- OneBot inbound API through `POST /api/v1/onebot/events`, converting `/agent` and `/ab` messages into the existing command execution flow.
- Optional NoneBot wrapper module that normalizes NoneBot/OneBot-style message events into the existing command execution flow without adding a hard NoneBot dependency.
- Dependency-free NoneBot matcher registration helpers that attach the AgentBridge async handler through matcher `handle()` decorators.
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
- Persistent access policy rules with `allow`/`deny` effects over action, resource type/id, actor IDs, roles, and exact-match attributes.
- PolicyEngine evaluates enabled access policy rules before RBAC fallback; explicit deny rules win over allow rules.
- Access policy management REST APIs through `GET/POST/PUT /api/v1/access-policy/rules` and `POST /api/v1/access-policy/rules/{id}/delete`.
- Policy simulation REST API through `POST /api/v1/access-policy/simulate`, returning decision source, reason, required permission, roles, and matched rule ID.
- Built-in admin entrypoint at `/admin`, project/session operations dashboard at `/admin/projects`, interaction/approval operations dashboard at `/admin/interactions`, audit/event exploration dashboard at `/admin/audit`, access policy editor at `/admin/access-policy`, terminal lifecycle dashboard at `/admin/terminal-lifecycle`, and Bot delivery operations dashboard at `/admin/bot-delivery`; focused route coverage verifies the admin pages link to the underlying operational APIs.
- Audit/event Admin Web page exposes the cross-stream semantic event search API with project, session, type, source, trace, turn, interaction, and limit filters.
- Audit/event Admin Web page can live-tail a selected session's semantic event stream over `/api/v1/sessions/{id}/events/ws`, while retaining manual REST replay for bounded event history inspection.
- Optional `AGENTBRIDGE_ADMIN_TOKEN` browser gate for `/admin*` pages, accepting one-time `admin_token` query unlock, HttpOnly/SameSite cookie sessions, bearer tokens, or `X-AgentBridge-Admin-Token` headers.
- Alembic migration `0007_access_policy_rules` persists access policy rules.
- Alembic migration `0008_semantic_event_query_columns` adds indexed semantic event routing columns for source, trace, project, session, turn, and interaction search.
- Project, session, interaction, approval, group-role, policy-management, and terminal checks now pass resource type/id plus stable attributes into access policy evaluation.
- Terminal REST and WebSocket paths reuse Control Plane `terminal` resource checks, so session-specific terminal rules are enforced outside the main command flow as well.
- Focused unit/API tests for the above.

Not implemented yet:

- Remaining PTY host hardening: true process-preserving recovery across host-process death, cross-platform stale socket/pipe cleanup, and Windows ConPTY/Named Pipe parity.
- Richer OneBot renderer/action adapter and deeper native NoneBot lifecycle helpers beyond matcher registration.
- Real Claude Code/Codex adapters.
- Broader Admin Web UI beyond project/session operations, interaction/approval operations, audit/event live exploration, access policy, terminal lifecycle, and Bot delivery operations.
- Production API/WebSocket hardening with mTLS and database-managed device identities beyond the current environment-configured device keys.
- Platform-specific rich card/button transport adapters and outbound message edit extensions beyond standard OneBot V11.
- Native action/callback support for platforms that expose buttons or interactions.
- Broader per-adapter action callback state beyond command-carrying callbacks.
- Fully normalized relational query layer for arbitrary payload search and complex audit/event cross-field searches; audit and semantic event routing filters now use indexed columns where available, while the current `q` payload/details search is a bounded JSON text match.
- PostgreSQL-specific operational hardening, connection pooling policy, and migration deployment docs.

## Important Decisions

- The first backend slice uses an in-memory repository to make command, routing, lease, and API semantics testable before introducing persistence.
- Unknown ASCII-looking `/agent` management commands are rejected instead of being silently treated as prompts. Non-command free text still becomes `ask` to support the documented shortcut pattern.
- Semantic events are separate from audit records: events drive product state replay and Bot rendering, while audit records preserve security/accountability history.
- Audit listing is a repository concern. In-memory and SQLAlchemy repositories return bounded newest-first results; the SQLAlchemy path filters action/actor in the database and then applies project/session/interaction/trace payload filters until the requested limit is reached.
- Semantic event replay and semantic event search are separate repository contracts. Replay APIs preserve per-stream ascending `seq` behavior for clients, while `/api/v1/events` and `list_semantic_events` provide bounded newest-first operational search across streams using SQLAlchemy routing columns. The current `q` search inspects serialized JSON payload/details after indexed filters, which is useful for operations but does not replace a normalized payload query layer.
- SQLAlchemy persistence is currently a single-process write-through snapshot repository. It is sufficient for restart recovery and contract tests, but multi-process production deployments need row-level updates and stronger transaction boundaries.
- Terminal input must pass through the AgentBridge gateway. Direct `tmux attach` remains outside the safety model because it bypasses writer leases.
- The local Terminal Agent socket is token-gated and chmodded to `0600`; production hardening still needs OS user checks, token rotation, and Windows named-pipe parity.
- Local console/daemon clients open a fresh socket per request and retry connection for short restart windows. Long offline periods still need explicit user-facing reconnect state during raw TTY passthrough.
- Console raw mode is still an input/control passthrough over the Terminal Agent socket, not a full terminal emulator. Its daemon output stream consumes backend cursor chunks; with `AGENTBRIDGE_TERMINAL_BACKEND=pty` those chunks come from a stdlib PTY reader loop, while fake/tmux remain snapshot-derived.
- The stdlib PTY backend is opt-in for local experiments. Fake remains the default test backend, and tmux remains the resumable MVP backend after Agent process restarts.
- The PTY backend uses a bounded retained-output window rather than recording all terminal output. Cursor values are absolute within the session lifetime; when a reader falls behind the retained window, `read_output`/`stream_output` returns `reset=True` with the retained tail so consoles can repaint deterministically.
- `agentbridge-pty-host` is the first independent PTY host process. It keeps PTY sessions alive across API/daemon client restarts when the host process itself remains running. The `pty_host` client can auto-start the host and clean stale Unix sockets, and the optional watchdog can restart a crashed host process. A host crash still destroys the owned PTY sessions, but combining the watchdog with lost-terminal auto-restart now gives an explicit restart-based recovery path from the last persisted command. True process-preserving recovery after host death is not possible with the current stdlib PTY owner model.
- Terminal exit observation can be driven by the in-process lifecycle monitor, so clients do not have to call `status` to produce `terminal.exited`. The monitor recovers tracked terminal generations from semantic events and emits `terminal.lost` when a recovered generation has no observable backend session. `restart_session` can explicitly recover a lost/exited backend from the latest persisted command, and the opt-in auto-restart policy can do that for lost recovered terminals with a bounded attempt count. Service-manager deployment is now documented for the PTY host; production lifecycle supervision still needs a persistent scheduler if automatic policy execution must survive the supervising API/daemon process itself.
- The documented product-like PTY host topology is now OS service-manager first: run one `agentbridge-pty-host` per local user under systemd user services or macOS launchd, and let API/daemon clients connect over the token-gated Unix socket. In-process auto-start/watchdog remains useful as a local fallback but should not compete with an enabled service-manager unit.
- Automatic lost-terminal restart is disabled by default because restarting a native CLI can have side effects. Operators must opt in with `AGENTBRIDGE_TERMINAL_AUTO_RESTART_ON_LOST=true`, and `AGENTBRIDGE_TERMINAL_AUTO_RESTART_MAX_ATTEMPTS` bounds restart attempts per service process.
- Terminal lifecycle run-once is treated as an operational action because it can emit `terminal.exited`/`terminal.lost` and trigger opt-in auto-restarts. The REST endpoint requires `terminal.control`; read-only lifecycle status requires `audit.view`.
- Desktop terminal auto-open is opt-in. Custom command templates remain supported, and built-in presets cover macOS Terminal plus common Linux terminal emulators. The daemon keeps sensitive local token/socket state out of launched argv; macOS Terminal uses a mode-0700 short-lived launcher script because AppleScript cannot directly propagate the daemon's environment into the new shell.
- The tmux backend treats an existing `agentbridge_<session-id>` session as resumable state after Agent restart, matching the design's MVP recovery path.
- Rendering is split into platform-neutral documents and platform renderers. The first renderer intentionally targets text fallback so unsupported Bot platforms still receive coherent output.
- Bot delivery idempotency is implemented before real platform integration so duplicate event replay cannot cause duplicate sends once a real transport is attached.
- Bot delivery records are persisted separately from semantic events so replay, delivery retries, and platform message IDs can evolve without mutating event history.
- OneBot outbound delivery is implemented as a transport contract first. The NoneBot wrapper is optional and dependency-free; matcher registration helpers cover the common `matcher.handle()` setup path, while full NoneBot integration still needs broader lifecycle hooks, richer message components, and adapter-specific delivery capabilities.
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
- Access policy rules are stored separately from approval quorum overrides. Approval policy answers "how many votes"; access policy answers "who may do which action".
- Access policy evaluation is deny-first and then allow-before-RBAC. This makes temporary freezes explicit while preserving the existing role matrix as the default baseline.
- Access policy enforcement uses stable resource attributes only: IDs, status, project/session linkage, visibility, agent type, chat context, operation, risk level, and owner metadata. It intentionally avoids volatile or sensitive filesystem path values as policy attributes.
- The Admin Web UI is intentionally API-backed and build-free for now. It has a small `/admin` entrypoint, project/session operations for project inventory, workspace registration, session creation, and session closure, interaction/approval operations for listing, filtering, creating, answering, voting, and cancelling interactions, audit/event exploration for filtered audit records plus semantic event search/replay/live tail, access policy editing, terminal lifecycle inspection/run-once, and Bot delivery operations for records, retry worker status, due retry, and rate limits. `AGENTBRIDGE_ADMIN_TOKEN` gates the built-in browser pages with token unlock plus an HttpOnly/SameSite cookie while preserving zero-config local development when unset. The optional REST API token gate accepts that unlocked admin cookie so browser-admin API calls are covered by the same gate, protected WebSocket event streams accept the cookie for same-origin Admin pages, and `AGENTBRIDGE_API_TOKEN` can serve as the admin unlock token when no separate admin token is configured. Database-managed device identities and richer dashboards remain future work.
- Session creation is evaluated against the target project because the session resource ID does not exist yet. Terminal control is evaluated as `resource_type=terminal` with the session ID so terminal-specific rules do not have to overmatch ordinary session sends.
- WebSocket session streams are read-side transports over immutable semantic events. They use `after_seq` cursors for replay/reconnect and do not mutate Bot delivery records.
- Bot Gateway WebSocket subscriptions fan out render frames for external platform adapters, but do not store delivery records. Platform adapters report delivery acknowledgements, edits, and deletes explicitly through the delivery-result API keyed by message idempotency key.
- Bot delivery platform state is stored on delivery records, not semantic events. Edits/deletes update platform lifecycle metadata and latest delivery text without rewriting the immutable event stream.
- Bot Gateway edit/delete APIs call transport-native operations first, then record platform state. OneBot V11 supports deletion through `delete_msg`; message editing is a platform-specific extension and intentionally reports capability missing for the standard OneBot V11 transport.
- Render actions are emitted twice: as plain-text fallback commands and as structured button descriptors in Bot Gateway WebSocket frames. Platform adapters own conversion into native buttons/cards and should send callbacks carrying the descriptor payload command.
- `AGENTBRIDGE_API_TOKEN`, `AGENTBRIDGE_WS_TOKEN`, `AGENTBRIDGE_ADMIN_TOKEN`, and `AGENTBRIDGE_DEVICE_KEYS` are the current MVP gates for HTTP API clients, WebSocket clients, built-in admin pages, and per-device client keys. Browser Admin sessions use an HttpOnly cookie for REST calls and same-origin event WebSocket streams so the unlock token does not need to be readable from JavaScript. These gates are intentionally simpler than the design's production mTLS/device-key model, which remains future hardening work.
- The original design document remains unchanged; this file is the rolling handoff/progress document for future sessions.

## Verification

Run:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run python -m compileall -q src tests alembic
AGENTBRIDGE_DATABASE_URL=sqlite:////tmp/agentbridge-check.db uv run alembic upgrade head
```

## Next Development Backlog

1. Harden PTY host recovery beyond watchdog plus command restart, including cross-platform socket/pipe cleanup, Windows ConPTY/Named Pipe parity, and clearer operator policy for non-idempotent CLI restarts.
2. Expand the Admin Web UI beyond project/session operations, interaction/approval operations, audit/event live/search exploration, access policy, terminal lifecycle, and Bot delivery operations, including additional live dashboards and normalized payload search.
3. Replace the MVP HTTP API/WebSocket/admin/device-key gates with mTLS and managed device identity.
4. Add platform-specific rich card/button transport adapters and outbound edit extensions.
5. Add deeper native NoneBot lifecycle hooks once a stronger dependency boundary is selected.
