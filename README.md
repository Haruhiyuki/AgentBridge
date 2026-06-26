# AgentBridge

AgentBridge is a local programming-agent collaboration platform for bot-driven group chat workflows. It keeps native CLI agents such as Claude Code and Codex running in a visible local terminal while a control plane exposes structured project, session, command, lease, interaction, and audit APIs.

The initial implementation follows the product design in `AgentBridge_项目总设计文档_v0.2_多项目多会话指令版.md`.

## Current Scope

This repository currently contains the first executable backend slice:

- Python/FastAPI Control Plane skeleton.
- Shared domain models for projects, workspaces, sessions, turns, interactions, writer leases, chat contexts, and audit events.
- In-memory repository suitable for contract tests and local MVP prototyping.
- `/agent` command parser and executor for project/session routing, turn enqueueing, queue list/remove/clear/move/pause/resume, lease control, and idempotent invocation handling.
- Project active-session, running-Turn, queued-Turn, and daily per-user Turn quotas plus Workspace write-capacity enforcement for multi-session safety.
- Ordered semantic event streams with replay and idempotent Terminal Agent event ingestion.
- Optional SQLAlchemy persistence with an Alembic-managed schema.
- Terminal input gateway with fake, tmux, and stdlib PTY backends plus writer-lease epoch enforcement.
- Local Terminal Agent daemon over a token-protected Unix socket, with token-file hot reload for local rotation.
- Optional token/device-key gated REST APIs, WebSocket streams, and Terminal command transport.
- Session agent launch profiles so omitted terminal start commands resolve from
  `agent_type` (`claude`, `codex`, or `generic_tui`) with operator overrides.
- Local Console Client with line-mode, scripted input, raw TTY passthrough, and cursor-based output observation through the lease gateway.
- RenderDocument intermediate representation with OneBot/plain-text fallback rendering.
- Bot Gateway delivery service with persistent idempotent delivery records, WebSocket render subscriptions, in-memory text transport, and OneBot V11 HTTP transport.
- Optional NoneBot wrapper that normalizes message and action callback events into the existing `/agent` command path.
- Background Bot delivery retry worker with configurable interval and batch-size guardrails.
- Platform-scoped Bot delivery rate-limit policies that schedule unsent messages for retry.
- Interaction and approval flow APIs with `/agent answer`, `/agent approve`, `/agent deny`, `/agent approvals`, `/agent question`, and `/agent plan`.
- Interaction expiration and cancellation lifecycle with audit and semantic events.
- Risk-aware approval policy with configurable quorum and dangerous approval roles.
- Project/chat-context approval quorum overrides through REST and `/agent policy`.
- Chat-context scoped role bindings with `/agent role list/grant/revoke` and REST management APIs.
- Persistent access policy allow/deny rules with action/resource/actor/role/attribute matching and a simulation API.
- Built-in Admin Web pages for system health, project/session operations with active Turn, queue, pending approval, and lease status, interaction/approval operations, audit/event exploration, access policy editing, terminal lifecycle inspection, device identity management, and Bot delivery operations including platform capabilities, with optional token-gated browser access.
- REST API routes aligned with the design document's service interface.

Production PTY supervision, richer Bot renderers, provider-native key custody, and deeper Claude Hook/Codex app-server adapters are planned next milestones.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run alembic upgrade head
uv run uvicorn agentbridge.api:create_app --factory --reload
```

Real tmux smoke coverage is opt-in so normal test runs do not require local tmux:

```bash
AGENTBRIDGE_RUN_TMUX_TESTS=true uv run pytest tests/test_terminal_agent.py::test_real_tmux_backend_smoke_streams_output_and_reuses_session
```

Without `uv`, use the active Python environment:

```bash
python3 -m pytest
python3 -m uvicorn agentbridge.api:create_app --factory --reload
```

## Persistence

The app defaults to in-memory storage. Set `AGENTBRIDGE_DATABASE_URL` to enable the
SQLAlchemy repository:

```bash
export AGENTBRIDGE_DATABASE_URL=sqlite:///./agentbridge.db
uv run alembic upgrade head
uv run uvicorn agentbridge.api:create_app --factory --reload
```

For local throwaway development, `AGENTBRIDGE_AUTO_CREATE_SCHEMA=true` can create tables
on startup. Production deployments should run Alembic migrations explicitly and tune the
SQLAlchemy pool with `AGENTBRIDGE_DATABASE_POOL_SIZE`,
`AGENTBRIDGE_DATABASE_MAX_OVERFLOW`, `AGENTBRIDGE_DATABASE_POOL_TIMEOUT_SECONDS`,
`AGENTBRIDGE_DATABASE_POOL_RECYCLE_SECONDS`, and
`AGENTBRIDGE_DATABASE_POOL_PRE_PING`. See
`docs/operations/DATABASE_DEPLOYMENT.md` for SQLite/PostgreSQL deployment notes and the
current single-process persistence boundary.

## Terminal Backend

The API uses a fake terminal backend by default for local contract tests. Use the stdlib PTY backend for a local process with a real PTY reader loop:

```bash
export AGENTBRIDGE_TERMINAL_BACKEND=pty
export AGENTBRIDGE_TERMINAL_PTY_OUTPUT_LIMIT_CHARS=1000000
export AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH="$HOME/.agentbridge/pty-host-state.json"
```

Run the PTY in an independent local host process when you want API/daemon restarts to reconnect to a still-owned PTY:

```bash
install -m 0700 -d "$HOME/.agentbridge"
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$HOME/.agentbridge/pty-host.token"
chmod 0600 "$HOME/.agentbridge/pty-host.token"
export AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET="$HOME/.agentbridge/pty-host.sock"
export AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE="$HOME/.agentbridge/pty-host.token"
uv run agentbridge-pty-host

export AGENTBRIDGE_TERMINAL_BACKEND=pty_host
export AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET="$HOME/.agentbridge/pty-host.sock"
export AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE="$HOME/.agentbridge/pty-host.token"
export AGENTBRIDGE_TERMINAL_PTY_HOST_AUTO_START=true
export AGENTBRIDGE_TERMINAL_PTY_HOST_WATCHDOG_ENABLED=true
```

Use tmux when you want the MVP restart path to reuse an existing `agentbridge_<session-id>` session:

```bash
export AGENTBRIDGE_TERMINAL_BACKEND=tmux
```

When a terminal start request omits `command`, AgentBridge resolves the launch command
from the Session `agent_type`: `claude` runs `claude`, `codex` runs `codex`, and
`generic_tui` runs `sh`. Override those defaults when your local CLI needs flags,
wrappers, or absolute paths:

```bash
export AGENTBRIDGE_AGENT_CLAUDE_COMMAND="claude"
export AGENTBRIDGE_AGENT_CODEX_COMMAND="codex-agentbridge"
export AGENTBRIDGE_AGENT_GENERIC_TUI_COMMAND="sh"
# Optional explicit version probes; Claude/Codex default to "<executable> --version".
# export AGENTBRIDGE_AGENT_CLAUDE_VERSION_COMMAND="claude --version"
# export AGENTBRIDGE_AGENT_CODEX_VERSION_COMMAND="codex-agentbridge --version"
# Optional structured adapter handshake probes. The command must print JSON using
# protocol "agentbridge.adapter.v1" and a string-array "capabilities" field.
# export AGENTBRIDGE_AGENT_CLAUDE_HANDSHAKE_COMMAND="agentbridge-adapter-client handshake --agent claude"
# export AGENTBRIDGE_AGENT_CODEX_HANDSHAKE_COMMAND="agentbridge-adapter-client handshake --agent codex"
```

`GET /api/v1/terminal/lifecycle-monitor`, the local daemon `lifecycle_status` action,
and the Terminal Lifecycle Admin page report these launch profiles, including the
resolved executable path when it is available on PATH or points to an executable file.
This readiness check does not execute the agent command. Operators with terminal-control
permission can explicitly run bounded version probes through
`POST /api/v1/terminal/agent-launch/probe`, the local daemon
`probe_agent_launch_profiles` action, or the Terminal Lifecycle Admin page. Version
probe output is captured with timeout and length limits. Structured adapter capability
detection is available through `POST /api/v1/terminal/agent-adapters/detect`, the local
daemon `detect_agent_adapters` action, and the Admin page. Claude/Codex remain gated as
not ready unless an explicit handshake command reports `agentbridge.adapter.v1`
compatibility; this keeps future Hook/app-server integration behind a schema gate instead
of enabling unknown protocol versions by default.

External Claude Hook or Codex app-server adapter processes can report structured events
through `POST /api/v1/sessions/{session_id}/agent-adapter/events`. The endpoint requires
the same managed-device `session_event_ingest` scope as direct session event ingestion and
requires the reported `agent_type` to match the Session `agent_type`. Adapter events must
declare a verified `schema_version`: currently `claude-hooks.v1` for Claude and
`codex-app-server.v1` for Codex. Known Claude/Codex event names are normalized into
AgentBridge semantic events such as `assistant.delta`, `tool.started`, `tool.completed`,
`approval.requested`, `question.requested`, `diff.updated`, `plan.updated`, and
`turn.completed`, preserving the raw adapter payload under `raw_event` for audit and
replay. Adapter approval, question, and plan request events also create pending
AgentBridge Interaction records, keyed by the event idempotency key when one is supplied.
Adapter processes can poll
`GET /api/v1/sessions/{session_id}/agent-adapter/responses` with `after_seq` to receive
answers, approval decisions, cancellations, and expirations for interactions they created.
The packaged `agentbridge-adapter-client` CLI and `agentbridge.agent_adapter_client`
module provide the same bridge for native adapter processes, including standard
handshake JSON with schema snapshots, Codex provider-captured schema projection,
API-token or device-key headers, event submission, response polling, blocking waits for
adapter-originated interactions, and local schema matrix inspection:

```bash
export AGENTBRIDGE_API_URL="http://127.0.0.1:8000"
export AGENTBRIDGE_SESSION_ID="<session-id>"
export AGENTBRIDGE_DEVICE_ID="<adapter-device-id>"
export AGENTBRIDGE_DEVICE_KEY="<adapter-device-key>"

agentbridge-adapter-client handshake --agent claude
agentbridge-adapter-client schemas --agent claude
agentbridge-adapter-client schemas --agent codex
agentbridge-adapter-client emit \
  --agent claude \
  --event-type MessageDisplay \
  --payload-json '{"text":"hello from Claude"}' \
  --idempotency-key "claude-hook-1"
agentbridge-adapter-client emit-and-wait \
  --agent codex \
  --event-type item/commandExecution/requestApproval \
  --payload-json '{"item":{"id":"cmd-1","command":"pytest"},"reason":"Run tests"}' \
  --idempotency-key "codex-approval-1" \
  --wait-timeout-seconds 300
printf '%s\n' '{"method":"item/commandExecution/requestApproval","id":42,"params":{"item":{"id":"cmd-1","command":"pytest"},"reason":"Run tests"}}' |
  agentbridge-adapter-client codex-app-server-event \
    --session-id "<session-id>" \
    --device-id "<adapter-device-id>" \
    --device-key-file "$HOME/.agentbridge/codex-adapter.key" \
    --json-rpc-response
agentbridge-adapter-client codex-app-server-stream \
  --input-file codex-app-server.jsonl \
  --session-id "<session-id>" \
  --device-id "<adapter-device-id>" \
  --device-key-file "$HOME/.agentbridge/codex-adapter.key"
agentbridge-adapter-client codex-app-server-proxy \
  --session-id "<session-id>" \
  --device-id "<adapter-device-id>" \
  --device-key-file "$HOME/.agentbridge/codex-adapter.key" \
  --inject-responses \
  --restart-policy on-failure \
  --max-restarts 3 \
  --health-output-file agentbridge-codex-health.jsonl \
  --health-interval-seconds 30 \
  --bridge-output-file agentbridge-codex-responses.jsonl \
  -- codex app-server
agentbridge-adapter-client format-response \
  --agent claude \
  --stdout-json \
  --response-json '{"decision":"approved","adapter_event_type":"PermissionRequest"}'
agentbridge-adapter-client claude-hook \
  --session-id "<session-id>" \
  --device-id "<adapter-device-id>" \
  --device-key "<adapter-device-key>"
agentbridge-adapter-client claude-hooks-config \
  --session-id "<session-id>" \
  --device-id "<adapter-device-id>" \
  --device-key-file "$HOME/.agentbridge/claude-adapter.key" \
  --write-file .claude/settings.local.json
agentbridge-adapter-client poll-responses --after-seq 0 --limit 50
```

Use `agentbridge-adapter-client claude-hook` as the command for Claude Code command
hooks. It reads the hook JSON from stdin, reports the event to AgentBridge, exits quietly
for observer events, and prints Claude Hook stdout JSON for blocking approval/question
events after `emit-and-wait` resolves. If AgentBridge cannot return an interaction
decision for a blocking event, the shim fails closed with a deny response.
Use `agentbridge-adapter-client claude-hooks-config` to generate the Claude Code
settings `hooks` fragment, or pass `--write-file` to merge it into `settings.json`,
`.claude/settings.json`, or `.claude/settings.local.json`. The generated hook uses
Claude's exec-form command handler (`command` plus `args`) so paths containing spaces are
not shell-split, and `--write-file` replaces prior AgentBridge `claude-hook` handlers
while preserving unrelated user hooks. Direct `--api-token` and `--device-key` values are
not embedded unless `--include-secret-values` is explicitly passed; prefer token/key
files for persistent Claude settings.
Use `agentbridge-adapter-client codex-app-server-event` to bridge one Codex
app-server JSON-RPC message. It reads the message from stdin or `--input-file`,
uses the `method` as the version-gated adapter event type, preserves `params`
under the raw adapter payload, exits quietly for observer notifications, and for
AgentBridge-managed approval/question events waits for a response before printing
the Codex action envelope. When the input message carries an `id`, pass
`--json-rpc-response` to wrap that action under a JSON-RPC `result.agentbridge`
response.
Use `agentbridge-adapter-client codex-app-server-stream` for app-server stdio
JSONL streams. It skips JSON-RPC responses without `method`, treats each
notification/request line with `method` as a schema-gated Codex adapter event,
continues past invalid lines in non-strict mode, and emits newline-delimited
interaction responses. The default stream output is JSON-RPC `result.agentbridge`;
use `--output-format action` for action envelopes or `--output-format bridge-json`
for per-message bridge diagnostics.
Use `agentbridge-adapter-client codex-app-server-proxy` as a stdio proxy in front
of a real `codex app-server` command. It forwards upstream stdin to the child
process and forwards child stdout back unchanged while teeing child JSONL
messages into AgentBridge. AgentBridge interaction responses are written only to
the optional `--bridge-output-file` side channel unless `--inject-responses` is
set. With response injection enabled, the proxy writes AgentBridge JSON-RPC
interaction responses back to the child stdin and suppresses the handled request
line from downstream stdout by default to avoid duplicate client responses. Pass
`--forward-injected-requests` to keep those request lines visible to the primary
client while still injecting AgentBridge responses. The proxy can also supervise
short child failures with a bounded restart budget: set
`--restart-policy on-failure` or `--restart-policy always`, `--max-restarts`,
and optionally `--restart-delay-seconds` plus `--restart-min-uptime-seconds` to
track unhealthy quick exits in the returned summary. Restarts reuse a single
upstream stdin router, so the proxy does not create competing readers for the
primary client. Set `--health-output-file` to emit proxy health JSONL events for
`started`, periodic `running`, `exited`, `restarting`, and `stopped` states;
`--health-interval-seconds` controls the running heartbeat interval. For product-like
deployment, use the wrapper and service-manager guidance in
`docs/operations/CODEX_APP_SERVER_PROXY_SERVICE_MANAGER.md`; because the proxy is a
stdio bridge, keep it in the same stdin/stdout path as the upstream Codex app-server
client rather than running it as a detached background service.

Terminal input is accepted only when the request carries the current writer lease `epoch`, owner type, and owner ID. Stale Bot/Web inputs are rejected after human or higher-priority control preempts the lease. Workspace creation through the API and Project/Session Admin UI can configure `is_writable` and `max_write_sessions`; `read_only` workspaces are normalized to non-writable with zero write slots, and writer lease acquisition enforces the resulting Workspace capacity across shared sessions. The PTY backend keeps a bounded cursor-addressable output window from the PTY master fd; stale readers receive a reset frame with the retained tail. When `AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH` is set, PTY start/status/termination updates an atomic JSON host-state registry containing session ID, cwd, command, host pid, child pid, status, exit code, and output cursor metadata for future host supervision. The `pty_host` backend talks to `agentbridge-pty-host` over a chmod `0600` Unix socket, so a restarted API/daemon process can recreate its backend client and continue reading/writing PTYs owned by the host process. Set `AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE` on both host and clients to reread the shared PTY Host token for each request, allowing rotation without restarting either side; an unreadable or empty token file keeps a configured token gate closed when there is no static fallback token. With `AGENTBRIDGE_TERMINAL_PTY_HOST_AUTO_START=true`, the client backend removes a Unix socket only when health probing proves there is no listener, starts `agentbridge-pty-host`, waits for health, and retries the request once; if health reaches a live host but token auth fails, times out, or returns a protocol error, it preserves the socket and reports the error instead of starting a competing host. With `AGENTBRIDGE_TERMINAL_PTY_HOST_WATCHDOG_ENABLED=true`, API and daemon lifespans start a background watchdog that keeps the host healthy and restarts it after a crash; `AGENTBRIDGE_TERMINAL_PTY_HOST_WATCHDOG_INTERVAL_SECONDS` controls the poll interval. Combine the watchdog with `AGENTBRIDGE_TERMINAL_AUTO_RESTART_ON_LOST=true` and a command allowlist to have the lifecycle monitor mark host-crash-lost PTY sessions as `terminal.lost` and restart them only when the latest persisted command is approved for replay. For service-manager deployments, use the systemd/launchd guide and templates in `docs/operations/PTY_HOST_SERVICE_MANAGER.md`. Fake and tmux remain test/MVP backends.

## Local Terminal Agent

Run the local Terminal Agent socket server:

```bash
export AGENTBRIDGE_LOCAL_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
# Or store the token in a file; the daemon rereads it for each request.
# export AGENTBRIDGE_LOCAL_TOKEN_FILE="$HOME/.agentbridge/terminal-agent.token"
export AGENTBRIDGE_TERMINAL_SOCKET="$HOME/.agentbridge/terminal-agent.sock"
export AGENTBRIDGE_LOCAL_REQUIRE_PEER_USER=true
export AGENTBRIDGE_TERMINAL_LIFECYCLE_POLL_INTERVAL_SECONDS=1
export AGENTBRIDGE_TERMINAL_AUTO_RESTART_ON_LOST=false
export AGENTBRIDGE_TERMINAL_AUTO_RESTART_MAX_ATTEMPTS=1
# Required before automatic lost-terminal restart will replay a command.
# Use explicit shell-style patterns such as "codex*,claude*" or "*" to allow all.
# export AGENTBRIDGE_TERMINAL_AUTO_RESTART_COMMAND_ALLOWLIST="codex*,claude*"
# Optional: open a visible console with a built-in preset or custom command.
export AGENTBRIDGE_TERMINAL_AUTO_OPEN=true
export AGENTBRIDGE_TERMINAL_OPEN_PRESET=auto
# Or use a command template when the built-in presets do not fit your terminal.
# export AGENTBRIDGE_TERMINAL_OPEN_COMMAND='your-terminal-emulator -- agentbridge-console {session_id} --socket {socket_path} --raw'
uv run agentbridge-terminal-agent
```

If `AGENTBRIDGE_LOCAL_TOKEN` is omitted, the daemon prints a generated token at startup.
When `AGENTBRIDGE_LOCAL_TOKEN_FILE` is used instead, the daemon and auto-open launcher
reread the file for each request/launch, so operators can rotate the local token without
restarting the daemon. The socket file is created with mode `0600`, and Unix-domain
connections must come from the same OS user by default. Set
`AGENTBRIDGE_LOCAL_REQUIRE_PEER_USER=false` only for platforms that cannot expose peer
credentials. The JSONL socket protocol currently
supports `health`, `lifecycle_status`, `run_lifecycle_monitor_once`, `start_session`,
`probe_agent_launch_profiles`, `restart_session`, `acquire_human_lease`,
`release_lease`, `submit_input`, `snapshot`, `status`, cursor-based `read_output`, and
multi-frame `stream_output`.

Local clients open a fresh connection per request and wait briefly for the Unix socket to reappear, so short daemon restarts do not immediately fail console operations. With the PTY backend, the daemon owns a local child process, streams PTY output through cursor frames, and runs a lightweight lifecycle monitor that emits `terminal.exited` when a started terminal exits. When using persistent storage, the monitor reconstructs known started terminal generations from semantic events after a process restart; if a recovered generation has no observable backend session, it emits `terminal.lost` once so operators see that the local PTY state must be restarted. REST, WebSocket, and local daemon clients can call `restart_session` without a command to reuse the latest persisted `terminal.started` command, or pass an explicit command override. `AGENTBRIDGE_TERMINAL_AUTO_RESTART_ON_LOST=true` lets the lifecycle monitor perform that restart automatically only when the latest command matches `AGENTBRIDGE_TERMINAL_AUTO_RESTART_COMMAND_ALLOWLIST`, a comma-separated list of shell-style patterns. Leave the allowlist empty to block automatic command replay, or set `*` to allow all commands after an explicit operator risk review. Restarts are bounded by `AGENTBRIDGE_TERMINAL_AUTO_RESTART_MAX_ATTEMPTS` to avoid restart loops; blocked restarts emit `terminal.auto_restart.skipped`, and `lifecycle_monitor_status()` reports attempts, blocks, allowlist patterns, and backend supervision state such as PTY host watchdog restart counts. With `AGENTBRIDGE_TERMINAL_AUTO_OPEN=true`, the daemon opens a visible local console after `start_session` or a successful `restart_session`. `AGENTBRIDGE_TERMINAL_OPEN_PRESET` supports `auto`, `macos-terminal`, `gnome-terminal`, `konsole`, `wezterm`, `alacritty`, `kitty`, and `xterm`; the custom `AGENTBRIDGE_TERMINAL_OPEN_COMMAND` template remains available and takes precedence when set. `{session_id}`, `{socket_path}`, and `{console_command}` placeholders are available for custom templates, while sensitive local token/socket state is passed through environment variables instead of argv. With the tmux backend, restarting the Agent process reuses an existing `agentbridge_<session-id>` tmux session instead of creating a duplicate.

When the FastAPI process directly owns terminal backends, set `AGENTBRIDGE_TERMINAL_LIFECYCLE_MONITOR_ENABLED=true` to run the same lifecycle monitor from the API lifespan. Operators can inspect lifecycle and backend-supervision state with `GET /api/v1/terminal/lifecycle-monitor`, and can trigger one bounded scan with `POST /api/v1/terminal/lifecycle-monitor/run-once`.

## Rendering

Semantic events can be mapped to platform-neutral render documents and plain-text fallback messages:

```bash
curl http://127.0.0.1:8000/api/v1/sessions/<session-id>/rendered-events
curl 'http://127.0.0.1:8000/api/v1/events/rendered?event_type=device_identity.certificates_scanned'
```

The cross-stream rendered event API accepts the same operational filters as
`GET /api/v1/events`, so operator notifications such as certificate health scans can
be rendered without a session-specific stream. The current renderer targets reliable
text fallback for OneBot-style platforms. Rich buttons/cards and platform-specific
delivery state are planned for the Bot Gateway layer.

## Event Streaming

Session semantic events can be replayed and live-tailed over WebSocket:

```bash
wscat -c 'ws://127.0.0.1:8000/api/v1/sessions/<session-id>/events/ws?after_seq=42'
```

Use the rendered stream when a Bot-facing client wants the same event as a render document plus OneBot/plain-text fallback messages:

```bash
wscat -c 'ws://127.0.0.1:8000/api/v1/sessions/<session-id>/rendered-events/ws?after_seq=42'
```

Both streams emit replayed events first, then poll for new events. `after_seq`, `limit`, `poll_interval_seconds`, and `idle_timeout_seconds` are accepted as query parameters.

Set `AGENTBRIDGE_WS_TOKEN` or `AGENTBRIDGE_WS_TOKEN_FILE` to require WebSocket clients
to pass either `?token=...` or `Authorization: Bearer ...`. Token files are reread for
each WebSocket connection so operators can rotate shared WebSocket tokens without
restarting AgentBridge. A browser session unlocked through Admin Web can also use its
HttpOnly admin cookie for same-origin WebSocket streams. If neither variable is set and
no device keys or client certificate fingerprints are configured, WebSocket routes stay
open for local MVP development.

Set `AGENTBRIDGE_API_TOKEN` or `AGENTBRIDGE_API_TOKEN_FILE` to require REST API clients
to pass either `Authorization: Bearer <token>` or `X-AgentBridge-API-Token: <token>` for
`/api/*` routes other than `/api/v1/health`. If `AGENTBRIDGE_ADMIN_TOKEN` or
`AGENTBRIDGE_ADMIN_TOKEN_FILE` is configured, that same token and the unlocked Admin Web
cookie can also authorize REST API calls so the built-in admin pages continue to work
after browser unlock. If only `AGENTBRIDGE_API_TOKEN` or `AGENTBRIDGE_API_TOKEN_FILE` is
configured, it also gates and unlocks the built-in admin pages. Token files are reread
for each HTTP request, and a configured but unreadable/empty token file does not disable
the gate; if it is the only token source, token auth fails closed. If no token
variable/file is set and no device keys or client certificate fingerprints are
configured, REST routes stay open for local MVP development.

For per-device keys without adding database state, set `AGENTBRIDGE_DEVICE_KEYS` to a
JSON object mapping device IDs to secrets. REST clients present
`X-AgentBridge-Device-ID` plus `X-AgentBridge-Device-Key`; WebSocket clients present
`device_id` plus `device_key` query parameters.

For persisted device identities, create or rotate a key through
`POST /api/v1/device-identities` with `device_id`, optional `display_name`, and an
optional caller-supplied `device_key`. `allowed_scopes` can narrow the key or managed
certificate fingerprint to one or more scopes: `http_api`, `audit_read`,
`bot_gateway_read`, `bot_gateway_manage`, `onebot_event_ingest`, `command_parse`,
`command_execute`, `device_manage`, `policy_read`, `policy_manage`, `group_role_read`,
`group_role_manage`, `chat_context_manage`, `project_read`, `project_manage`,
`session_read`, `session_manage`, `session_send`, `session_event_ingest`,
`interaction_read`, `interaction_manage`, `terminal_read`, `terminal_control`,
`session_events_ws`, `rendered_events_ws`, `terminal_ws`, and
`bot_gateway_ws`;
omitting it grants all current scopes.
`allowed_resource_ids` can further restrict a managed device key or managed
certificate fingerprint to specific project, session, interaction, chat-context, or
device-identity IDs when those IDs are visible in the REST path/query string, supported
top-level JSON body fields, or the WebSocket route. Omit it or pass an empty list for
all resources; pass `"*"` for an explicit wildcard. If the allowlist is non-empty and a
request has no visible resource ID, the managed credential is rejected. The auth layer
only inspects bounded JSON bodies for stable top-level resource fields; it does not
perform endpoint-specific command parsing before authentication.
`AGENTBRIDGE_DEVICE_AUTH_RESOURCE_BODY_LIMIT_BYTES` controls the JSON body inspection
limit and defaults to `1048576`.
Managed device credentials need `audit_read` to call audit, audit export, and event history HTTP read APIs,
`bot_gateway_read` to call Bot Gateway HTTP read APIs, `bot_gateway_manage` to call
Bot Gateway HTTP mutation APIs,
`onebot_event_ingest` to receive OneBot inbound events, `command_parse` to call
`/api/v1/commands/parse`, `command_execute` to call `/api/v1/commands/execute`,
`device_manage` to call `/api/v1/device-identities` and its child routes,
`policy_read` to call policy read and simulation routes, `policy_manage` to create,
update, or delete access/approval policies,
`group_role_read` to list chat-context role bindings, `group_role_manage` to grant or
revoke chat-context roles,
`chat_context_manage` to create chat contexts or update their active project/session
pointers, `project_read` to list projects, show project details, or list workspaces,
`project_manage` to create projects, add workspaces, or bind projects to
chat spaces, `session_read` to list or show sessions, queued Turns, or current writer leases,
`session_manage` to create or close sessions, acquire or release writer leases,
or clear/reorder/pause/resume queues, `session_send` to enqueue session turns or remove queued Turns,
`session_event_ingest` to ingest Terminal Agent session events,
`interaction_read` to list or show interactions,
`interaction_manage` to create interactions or answer, cancel, and vote on them,
`terminal_read` to call terminal snapshot, terminal status,
or lifecycle status HTTP routes, and `terminal_control` to call terminal start,
restart, input, or lifecycle run-once HTTP routes. Command execution, direct turn enqueue, and
OneBot inbound commands still evaluate the effective actor through RBAC and access
policy.
`certificate_fingerprints` can bind one or more
proxy-verified client certificate fingerprints to the same device identity. If a new
identity has no certificate fingerprints and `device_key` is omitted, the server returns
a generated key once in the creation response. If certificate fingerprints are provided,
the identity can be certificate-only and no fallback key is generated unless
`device_key` is explicitly supplied. Existing keys are preserved unless a non-empty
`device_key` is supplied to rotate them. Stored keys are salted PBKDF2 hashes,
successful managed-device key or certificate authentication updates `last_used_at`, and
list/revoke responses never include raw keys, hashes, or salts. Once any managed device
identity exists, REST and WebSocket routes stay gated even if all managed devices are
later revoked; use an admin/API token or global client-certificate fingerprint to create
a new active device key and regain device-key access.
Managed certificate fingerprints can be rotated without replacing the full device
identity through
`POST /api/v1/device-identities/{device_id}/certificate-fingerprints/rotate` with
`add_fingerprints` and/or `remove_fingerprints`. The rotation preserves the device key,
allowed scopes, and resource allowlist, audits the added/removed fingerprints, and
rejects removing the last credential from a certificate-only device. Device identity
responses include `certificate_records`, retaining per-fingerprint source, issuance
metadata where available, and removal timestamps for operational audit. Responses also
include `certificate_health`, which reports `ok`, `expiring`, `expired`, `unknown`,
`none`, or `revoked` based on active certificate records, plus `renewal_status`,
`renewal_due_at`, `renewal_due_count`, and `renewal_overdue_count` for active
CA-issued certificates; known expired managed certificates are rejected during
client-certificate fingerprint authentication.
Operators can run
`POST /api/v1/device-identities/certificates/scan` to produce an audited certificate
health scan and a `device_identity.certificates_scanned` semantic event for expiring,
expired, or metadata-incomplete managed certificates. Set
`AGENTBRIDGE_DEVICE_CERT_SCAN_WORKER_ENABLED=true` to run the same scan in the
background at `AGENTBRIDGE_DEVICE_CERT_SCAN_INTERVAL_SECONDS` intervals; the worker
also reports renewal-due and renewal-overdue counts using
`AGENTBRIDGE_DEVICE_CERT_EXPIRY_WARNING_DAYS` as the renewal planning window. Worker
status is exposed through `/api/v1/device-identities/certificates/scan-worker`. Scan
events render as operator-readable summaries through `GET /api/v1/events/rendered`.
Set `AGENTBRIDGE_DEVICE_CERT_SCAN_NOTIFY_CHAT_CONTEXT_IDS` to a comma-separated list
of chat context IDs to have the scan worker automatically deliver action-required scan
events through Bot Gateway; `AGENTBRIDGE_DEVICE_CERT_SCAN_NOTIFY_PLATFORM` defaults to
`onebot.v11`, and `AGENTBRIDGE_DEVICE_CERT_SCAN_NOTIFY_ONLY_ACTION_REQUIRED` defaults
to `true`.
Set `AGENTBRIDGE_DEVICE_CERT_ISSUER_COMMAND` to issue CSR-based device certificates
through an external CA/KMS/HSM command, or set `AGENTBRIDGE_DEVICE_CERT_CA_CERT_FILE`
and `AGENTBRIDGE_DEVICE_CERT_CA_KEY_FILE` for the local PEM CA path. Both modes enable
`POST /api/v1/device-identities/{device_id}/certificates/issue`. The CSR Common Name
must exactly match `device_id`; issued certificates must include the client-auth EKU,
return the public certificate PEM once, store the SHA-256 fingerprint plus serial
number, subject, issuer, and validity window on the managed identity, and preserve
existing key/scope/resource settings. Use
`POST /api/v1/device-identities/{device_id}/certificates/renew` with a replacement CSR
to issue a new managed certificate and retire the active CA-issued certificate
fingerprint records for that device while preserving their removal metadata.
`AGENTBRIDGE_DEVICE_CERT_CA_KEY_PASSWORD`
or `AGENTBRIDGE_DEVICE_CERT_CA_KEY_PASSWORD_FILE` can unlock encrypted CA keys, and
`AGENTBRIDGE_DEVICE_CERT_DEFAULT_VALIDITY_DAYS` defaults new certificates to 30 days
when the request omits `validity_days`. `AGENTBRIDGE_DEVICE_CERT_EXPIRY_WARNING_DAYS`
sets the certificate health expiring window and defaults to 14 days.
See `docs/operations/DEVICE_CERTIFICATE_OPERATIONS.md` for the renewal scheduler,
external issuer command, cutover, TLS proxy, and CA key custody runbook.

For deployments behind a TLS-terminating reverse proxy that verifies client
certificates, set `AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS` or
`AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS_FILE` to an allowlist of SHA-256 certificate
fingerprints. The proxy must strip any incoming
`X-AgentBridge-Client-Cert-Fingerprint` header from untrusted clients, verify the mTLS
client certificate, and then set that header for AgentBridge. Fingerprints may be
colon-separated hex and are reread from the file for each HTTP request or WebSocket
connection. `AGENTBRIDGE_CLIENT_CERT_FINGERPRINT_HEADER` can override the trusted header
name. Configuring this allowlist gates REST APIs, WebSocket routes, and `/admin` pages;
an unreadable or empty fingerprint file fails closed. For finer transport scoping and
revocation, store fingerprints on managed device identities instead of the global
allowlist.

Audit records can be queried through `GET /api/v1/audit` with optional `actor_id`,
`action`, `project_id`, `session_id`, `interaction_id`, `trace_id`, `q`,
`details_field`, `details_value`, `created_from`, `created_to`, and `limit` filters.
`q` performs a case-insensitive contains match over audit `details`; `details_field`
supports dot-separated JSON object paths with an optional exact `details_value`;
`created_from` and `created_to` accept ISO-8601 datetimes. Results are bounded and
returned newest first for operational review.
The same filters can be exported through `GET /api/v1/audit/export?format=json`
or `format=csv`. Set `AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND` to sign
`format=archive` exports through an external KMS/HSM/Vault command that receives the
canonical archive JSON on stdin and returns a JSON signature. If no external command is
configured, `AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_PRIVATE_KEY_FILE` enables asymmetric
Ed25519, RSA, or ECDSA PEM signing; `AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY` or
`AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_FILE` remains the HMAC-SHA256 fallback.
`AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_ID` labels the signing key, and external or
asymmetric signatures can include verifier key metadata such as `public_key_sha256`.
Use `agentbridge-audit-verify` for offline HMAC/public-key archive verification. See
`docs/operations/AUDIT_ARCHIVE_SIGNING.md` for signer precedence, external signer I/O,
and verification guidance.

Semantic events can be searched across streams through `GET /api/v1/events` with
optional `project_id`, `session_id`, `turn_id`, `interaction_id`, `event_type`,
`source`, `trace_id`, `q`, `payload_field`, `payload_value`, `created_from`,
`created_to`, and `limit` filters. `q` performs a case-insensitive contains match over
event `payload`; `payload_field` supports dot-separated JSON object paths with an
optional exact `payload_value`; `created_from` and `created_to` accept ISO-8601
datetimes. This search endpoint returns bounded newest-first results for operational
investigation; use the session replay endpoints when a client needs stream-order replay
from `after_seq`.

## Terminal WebSocket

Browser/native clients can send terminal control frames through:

```bash
wscat -c 'ws://127.0.0.1:8000/api/v1/sessions/<session-id>/terminal/ws?token=<token>'
```

Request frames are JSON objects with `id`, `type`, and `payload`. The server replies with `terminal.result` or `terminal.error`:

```json
{"id":"start","type":"start_session","payload":{"actor":{"id":"usr_1","roles":["maintainer"]}}}
```

Supported actions are `health`, `start_session`, `restart_session`, `acquire_lease`, `release_lease`, `submit_input`, `snapshot`, and `status`. `start_session` accepts an optional `command` override; when omitted, it uses the Session agent launch profile. `submit_input` uses the same writer lease `epoch`, owner type, owner ID, and request-idempotency checks as the REST terminal input endpoint. `status` reports whether the terminal backend has started, whether it is still running, process exit metadata when available, and the current output cursor.

## Bot Gateway Delivery

Rendered session events can be delivered through the MVP Bot Gateway service:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/bot-gateway/deliver-session-events \
  -H 'content-type: application/json' \
  -d '{"session_id":"<session-id>","chat_context_id":"<chat-context-id>"}'
```

Filtered cross-stream operator events can be delivered to a chat context through the
same Bot Gateway delivery records. At least one event filter is required to avoid
accidental broad fan-out:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/bot-gateway/deliver-events \
  -H 'content-type: application/json' \
  -d '{"chat_context_id":"<chat-context-id>","event_type":"device_identity.certificates_scanned","trace_id":"<scan-trace-id>"}'
```

Delivery records are idempotent by platform, chat context, event, and message index, and can be persisted through the SQLAlchemy repository. Failed sends are recorded with attempt count, last error, and next retry time; `POST /api/v1/bot-gateway/retry-failed-deliveries` retries due failures.

External Bot Gateway subscribers can also receive Bot-facing render frames without mutating delivery records:

```bash
wscat -c 'ws://127.0.0.1:8000/api/v1/bot-gateway/session-events/ws?session_id=<session-id>&chat_context_id=<chat-context-id>&after_seq=42'
wscat -c 'ws://127.0.0.1:8000/api/v1/bot-gateway/notifications/ws?chat_context_id=<chat-context-id>&after_seq=42'
```

Ordinary render frames use `type: "bot.render.create"` and include the semantic event, render document, target chat context, platform, per-message idempotency keys, and platform-neutral `actions` for button-, select-, or modal-capable adapters. Cross-stream deliveries also emit idempotent `bot.notification` events for the notification WebSocket. Button descriptors carry a label, style, command, `callback_data`, and payload that OneBot/NoneBot callback handling can map back into the audited `/agent` command path. Select and modal descriptors add `command_template` plus input or option metadata; submitted payload values are rendered into the command template before the same command execution, RBAC, and access-policy checks run. Successful platform interaction submissions emit an idempotent `bot.interaction.ack` semantic event and Bot Gateway WebSocket frame on the affected session stream. Set `AGENTBRIDGE_WS_TOKEN` or `AGENTBRIDGE_WS_TOKEN_FILE` to protect these subscription endpoints in the same way as the other WebSocket routes.

Platform adapters can report delivery lifecycle results back to AgentBridge:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/bot-gateway/delivery-results \
  -H 'content-type: application/json' \
  -d '{"idempotency_key":"<message-key>","action":"acknowledge","platform_message_id":"<platform-message-id>"}'
```

Supported result actions are `acknowledge`, `edit`, and `delete`. These update the delivery record's `platform_state`, timestamps, optional platform payload, edit revision, and latest text without mutating the immutable semantic event.

AgentBridge can also initiate platform-native delivery mutations when the selected transport supports them:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/bot-gateway/deliveries/edit \
  -H 'content-type: application/json' \
  -d '{"idempotency_key":"<message-key>","text":"updated text"}'

curl -X POST http://127.0.0.1:8000/api/v1/bot-gateway/deliveries/delete \
  -H 'content-type: application/json' \
  -d '{"idempotency_key":"<message-key>"}'
```

The in-memory transport supports edit/delete for contract tests. OneBot V11 supports native `delete_msg`; standard OneBot V11 message editing is not available and returns a capability error unless a platform-specific transport adds that extension. Successful edit/delete result updates also emit `bot.render.update` or `bot.render.delete` semantic events for Bot Gateway WebSocket subscribers.

Bot clients and platform adapters can discover the conservative standard capability contract before choosing render or mutation behavior:

```bash
curl 'http://127.0.0.1:8000/api/v1/bot-gateway/capabilities?platform=onebot.v11'
```

The response uses the design-document `BotCapability` fields (`markdown`, `codeBlock`, `editMessage`, `buttons`, `selectMenu`, `modalInput`, `thread`, `reply`, `reaction`, `fileUpload`, `maxTextLength`, and `rateLimitProfile`) plus `platform` and `deleteMessage`. Platform-neutral action descriptors in WebSocket render frames are still adapter hints; they are not reported as native OneBot V11 button/select/modal support by this endpoint.

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

If a platform transport returns a rate-limit response with `Retry-After` or related reset headers, AgentBridge records the attempted send as `retrying` and schedules `next_retry_at` from the observed platform delay instead of using generic failure backoff.

Check worker state or run one bounded retry pass:

```bash
curl http://127.0.0.1:8000/api/v1/bot-gateway/capabilities
curl http://127.0.0.1:8000/api/v1/bot-gateway/rate-limits
curl http://127.0.0.1:8000/api/v1/bot-gateway/retry-worker
curl -X POST http://127.0.0.1:8000/api/v1/bot-gateway/retry-worker/run-once \
  -H 'content-type: application/json' \
  -d '{"limit":10}'
```

The current in-memory transport is intended for contract tests.

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

Platform adapters can also use the normalized Bot Gateway upstream endpoint for
message, command, slash command, action, select, modal, and attachment events:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/bot-gateway/inbound-events \
  -H 'content-type: application/json' \
  -d '{"event_type":"bot.slash_command.received","platform":"discord","channel_id":"chan-1","user_id":"usr-1","event_id":"slash-1","command":"health"}'
```

Adapters that register native slash commands or command menus can report platform
registration results as immutable Bot Gateway semantic events:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/bot-gateway/command-registration-results \
  -H 'content-type: application/json' \
  -d '{"platform":"discord","scope":"guild","channel_id":"guild-1","registration_id":"commands-v3","status":"succeeded","commands":[{"name":"agent"}]}'
```

The OneBot endpoint also accepts action callback payloads that contain a rendered action
descriptor command, including nested `data.payload.command` or `payload.command` shapes:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/onebot/events \
  -H 'content-type: application/json' \
  -d '{"default_roles":["approver"],"event":{"post_type":"notice","notice_type":"button_clicked","group_id":10001,"user_id":20002,"event_id":"callback-1","payload":{"command":"/agent approve <interaction-id> once"}}}'
```

Only `/agent` and `/ab` commands are executed. Non-command messages are ignored, and
callback events must carry the clicking `user_id` so the command actor can be
re-authorized through RBAC/access policy instead of trusting the button payload.
Successful action, select, and modal submissions return an `ack_event` and append the
same `bot.interaction.ack` event idempotently, keyed by the platform event id.
Inbound OneBot events are also recorded as Bot Gateway upstream semantic events,
including `bot.message.received`, `bot.command.received`, `bot.action.clicked`,
`bot.selection.submitted`, and `bot.modal.submitted`; ignored non-command messages are
still captured as received events for operational review.
Managed device credentials need `onebot_event_ingest` to call this endpoint.

For text-only fallback, users may reply to a Bot-rendered question or approval message
and omit the Interaction ID:

```text
/agent answer staging
/agent approve once
/agent deny missing context
```

AgentBridge resolves the replied platform message through Bot delivery records back to
the semantic event's `interaction_id`, then executes the expanded command as the
replying `onebot:<user_id>` actor.

For NoneBot deployments, register a matcher from application setup code:

```python
from agentbridge.control_plane import ControlPlane
from agentbridge.nonebot_plugin import register_nonebot_matcher

control = ControlPlane()
agentbridge = register_nonebot_matcher(
    matcher,
    control=control,
    bot_instance_id="nonebot-main",
    default_roles={"operator"},
)
```

The wrapper has no hard NoneBot dependency. The helper only expects a matcher object
with a `handle()` decorator; if you need manual wiring, `NoneBotAgentBridgePlugin`
still exposes `as_async_handler()` and `register_matcher()`. It accepts
NoneBot/OneBot-style event objects, executes `/agent` and `/ab` text commands, and
maps callback/action payloads containing a descriptor command through the same audited
command path.

## Interactions And Approvals

Agents can request questions or approvals against a session:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/sessions/<session-id>/interactions \
  -H 'content-type: application/json' \
  -d '{"actor":{"id":"agent","roles":["operator"]},"type":"approval","risk_level":"high","prompt":"Run destructive command?","ttl_seconds":300}'
```

Default approval quorum is risk-aware: `low=1`, `medium=1`, `high=1`, `critical=2`. Override it at startup:

```bash
export AGENTBRIDGE_APPROVAL_QUORUMS="high=2,critical=3"
```

High and critical approvals require a user with `dangerous_approver` or `admin`; normal `approver` users can approve low and medium requests.

Maintainers can override approval quorum per current chat context or project:

```text
/agent policy show
/agent policy set critical 3
/agent policy show --project backend
/agent policy set high 2 --project backend
```

The same capability is available through `PUT /api/v1/chat-contexts/<chat-context-id>/approval-policy` and `PUT /api/v1/projects/<project-id>/approval-policy` with `quorum_by_risk` values such as `{"critical":3}`. Chat-context overrides take precedence over project overrides; explicit `required_votes` on a single interaction still wins for that interaction.

Users can inspect and resolve them through commands:

```text
/agent approvals
/agent approval show <interaction-id>
/agent question show <interaction-id>
/agent answer <interaction-id> Use expand-contract migration
/agent approve <interaction-id> once
/agent deny <interaction-id> too risky
/agent approval cancel <interaction-id> superseded
/agent plan list
/agent plan show <interaction-id>
/agent plan approve <interaction-id>
/agent plan revise <interaction-id> Use expand-contract migration first
/agent plan cancel <interaction-id> obsolete
```

On OneBot-style text-only platforms, replying to the rendered question, approval, or
plan message lets users omit `<interaction-id>` for `/agent answer`, `/agent approve`,
`/agent deny`, and `/agent plan show/approve/revise/cancel`.
Bot-rendered plan requests expose callback-safe approve/show/cancel actions, a modal
descriptor for revision feedback, and the explicit
`/agent plan revise <interaction-id> <feedback>` text fallback.
Bot-rendered questions with explicit options expose a select descriptor and still include
the plain `/agent answer <interaction-id> <answer>` fallback.

REST callers can use `GET /api/v1/interactions`,
`POST /api/v1/sessions/{id}/interactions`, `POST /api/v1/interactions/{id}/answer`,
`POST /api/v1/interactions/{id}/vote`, and
`POST /api/v1/interactions/{id}/cancel`. Managed device credentials need
`interaction_read` for the GET APIs and `interaction_manage` for the write APIs.
Expired interactions move to `expired` and
cannot be approved later; approval request events render with plain-text approve/deny
actions for Bot delivery.

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

## Access Policy Rules

Maintainers can manage explicit access policy rules through REST. Rules match an action such as `session.send`, optional resource type/id, actor IDs, roles, and exact-match attributes. Matching deny rules override allow rules and the existing role permissions; matching allow rules can grant actions before the engine falls back to RBAC.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/access-policy/rules \
  -H 'content-type: application/json' \
  -d '{"actor":{"id":"usr_1","roles":["maintainer"]},"effect":"deny","action":"session.send","roles":["operator"],"description":"freeze operator sends"}'

curl http://127.0.0.1:8000/api/v1/access-policy/rules
```

Project, session, interaction, approval, group-role, policy-management, and terminal Control Plane checks now pass resource context into the policy engine. Session operations use `resource_type: "session"` with the session ID, terminal control uses `resource_type: "terminal"` with the session ID, and session creation is evaluated against the target project because the session ID does not exist yet.

Use simulation before enabling a risky rule:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/access-policy/simulate \
  -H 'content-type: application/json' \
  -d '{"actor":{"id":"usr_1","roles":["maintainer"]},"target_actor":{"id":"usr_operator","roles":["operator"]},"action":"terminal.control","resource_type":"session","attributes":{"risk":"low"}}'
```

Rules are persisted by Alembic migration `0007_access_policy_rules`. The REST and WebSocket terminal paths reuse the same resource-aware checks, so a rule can allow or deny a specific session's terminal without changing the global role matrix.

The built-in admin entrypoint is available at `http://127.0.0.1:8000/admin`.
It links to the project/session operations dashboard, interaction/approval dashboard,
audit/event explorer, access policy editor, system health dashboard, terminal lifecycle
dashboard, device identity dashboard, and Bot delivery operations dashboard. The system
health page summarizes `/api/v1/health`, terminal lifecycle monitor status, Bot retry
worker status, Bot platform capabilities, Bot rate-limit policies, and managed-device
endpoint reachability. The
project/session page lists projects, adds workspaces, creates sessions, closes selected
sessions, and surfaces active Turn, queue, pending approval, and lease status through
the same REST APIs used by external clients. The interaction
page lists and filters questions/approvals, creates new interactions, answers questions,
votes on approvals, and cancels pending items. The audit/event page filters audit
records, searches semantic events across streams, supports `q` text search over audit
details and event payloads, exports filtered audit records as JSON, CSV, or a signed
archive, replays
session semantic events, and can live-tail a selected session's event stream over WebSocket.
The terminal lifecycle page shows tracked sessions, exit/loss counts, automatic restart
attempts, command allowlist patterns, policy blocks, backend supervision state, and can
trigger a bounded run-once scan.
The policy editor lists rules, edits allow/deny match criteria, runs
`/api/v1/access-policy/simulate`, and saves through the same audited REST APIs.
The device identities page lists active/revoked managed devices, creates or rotates
device keys, edits allowed scopes, allowed resource IDs, and certificate fingerprints,
issues or renews managed certificates from CSR PEM, shows last-used timestamps, shows
generated keys/certificates once, and revokes selected devices.
The Bot delivery page lists delivery records, shows retry worker state, displays
platform capability contracts and rate-limit policies, and can retry due failures.

Set `AGENTBRIDGE_ADMIN_TOKEN` or `AGENTBRIDGE_ADMIN_TOKEN_FILE` to require a browser
token before serving `/admin` pages. When `AGENTBRIDGE_API_TOKEN` or
`AGENTBRIDGE_API_TOKEN_FILE` is configured and no dedicated admin token is set, the API
token is also accepted for Admin Web unlock:

```bash
export AGENTBRIDGE_ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
# Or rotate without restart:
# export AGENTBRIDGE_ADMIN_TOKEN_FILE="$HOME/.agentbridge/admin.token"
```

Open `/admin?admin_token=<token>` once to set a short-lived HttpOnly, SameSite cookie,
or pass `Authorization: Bearer <token>` / `X-AgentBridge-Admin-Token: <token>` for
scripted admin page access. `AGENTBRIDGE_ADMIN_COOKIE_MAX_AGE_SECONDS` controls cookie
lifetime, and `AGENTBRIDGE_ADMIN_COOKIE_SECURE` can force Secure cookie behavior when
AgentBridge is deployed behind TLS. The unlocked Admin Web cookie is also accepted by
the optional REST API token gate and same-origin WebSocket event streams. Client
certificate fingerprint headers from a trusted TLS proxy can also serve `/admin` pages
without setting an Admin Web cookie. These gates do not replace the planned full
certificate issuance and rotation workflow.

## Console Client

Attach to a session through the local Terminal Agent socket:

```bash
export AGENTBRIDGE_LOCAL_TOKEN=...
export AGENTBRIDGE_TERMINAL_SOCKET="$HOME/.agentbridge/terminal-agent.sock"
uv run agentbridge-console <session-id> --start
```

By default the console runs in line mode. Add `--raw` to put the local TTY into raw passthrough mode:

```bash
uv run agentbridge-console <session-id> --start --raw --release
```

Before forwarding input, the console requests a `human` writer lease and sends input with the returned epoch. Use `--command` with `--start` only when you want to override the Session agent launch profile for that start. Raw mode restores terminal state on exit, forwards initial and `SIGWINCH` resize events, maps Ctrl-C/Ctrl-D to terminal signals, follows daemon `stream_output` frames so the user can see current output, and uses Ctrl-] to detach from the console. Use `--no-follow-output` to disable output following, and use `--send`, `--paste`, or `--snapshot` for scripted checks.

## API Smoke Test

```bash
curl http://127.0.0.1:8000/api/v1/health
```

The app uses in-memory storage by default, so data is reset when the process exits.
