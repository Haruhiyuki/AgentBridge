# MVP Acceptance Runbook

This runbook maps the MVP acceptance criteria in section 34 of the design document to
the checks and evidence currently available in this repository. The readiness API and
CLI are operational gates; they do not replace the manual end-to-end acceptance flows
that require real Claude Code, a real terminal backend, and a real Bot transport.

## Product-Like Baseline

Use persistent storage and explicit gates before collecting acceptance evidence:

```bash
export AGENTBRIDGE_DATABASE_URL=sqlite:////var/lib/agentbridge/agentbridge.db
export AGENTBRIDGE_API_TOKEN=...
export AGENTBRIDGE_ADMIN_TOKEN=...
export AGENTBRIDGE_WS_TOKEN=...
export AGENTBRIDGE_DEVICE_KEYS='{"readiness-runner":"..."}'
export AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS_FILE=/etc/agentbridge/client-fingerprints.txt
export AGENTBRIDGE_ACCEPTANCE_EVIDENCE_FILE=/var/lib/agentbridge/acceptance-evidence.json
export AGENTBRIDGE_ACCEPTANCE_ARTIFACT_ROOT=/var/lib/agentbridge/acceptance-artifacts
export AGENTBRIDGE_ACCEPTANCE_VERIFY_ARTIFACTS=true
export AGENTBRIDGE_TERMINAL_EVENT_OUTBOX=/var/lib/agentbridge/terminal-events.jsonl
export AGENTBRIDGE_BOT_RETRY_WORKER_ENABLED=true
export AGENTBRIDGE_DEVICE_CERT_SCAN_WORKER_ENABLED=true
export AGENTBRIDGE_TERMINAL_LIFECYCLE_MONITOR_ENABLED=true
export AGENTBRIDGE_AGENT_CLAUDE_COMMAND=claude
export AGENTBRIDGE_AGENT_CODEX_COMMAND=codex
```

Run schema and readiness gates:

```bash
uv run alembic upgrade head
uv run agentbridge-readiness --format actions --fail-on-warn
curl -H "Authorization: Bearer $AGENTBRIDGE_API_TOKEN" \
  http://127.0.0.1:8000/api/v1/readiness
```

`--format actions` prints only failing or degraded checks with the next operator action.
Use `--fail-on-warn` for release gates and `--fail-on-fail` when warnings are acceptable
in a staged environment.

Readiness security checks intentionally distinguish local development from product-like
deployment. Missing HTTP API, Admin Web, WebSocket, device credential, or client
certificate gates are warnings; configured token files, static device keys, managed
devices, or fingerprint sources that do not yield a usable credential are failures.

Manual acceptance evidence uses the `agentbridge.acceptance_evidence.v1` manifest
schema. Start from `docs/operations/templates/acceptance_evidence.example.json`, set
each design-document section `34.1` through `34.8` to `passed`, and attach at least one
artifact reference per section. Readiness treats a missing manifest as a warning, an
unreadable or malformed manifest as a failure, and any failed section as a failure.
When `AGENTBRIDGE_ACCEPTANCE_VERIFY_ARTIFACTS=true`, artifact paths are resolved under
`AGENTBRIDGE_ACCEPTANCE_ARTIFACT_ROOT` or the manifest directory, and missing files,
root escapes, non-files, or sha256 mismatches fail the relevant section. Prefer
`agentbridge-acceptance attach-artifact` for release-candidate evidence because it copies
the source file into the artifact root, computes the sha256 digest, and writes the
digest-backed manifest reference in one step.

```bash
uv run agentbridge-acceptance init "$AGENTBRIDGE_ACCEPTANCE_EVIDENCE_FILE" \
  --environment staging
uv run agentbridge-acceptance attach-artifact "$AGENTBRIDGE_ACCEPTANCE_EVIDENCE_FILE" \
  34.1 ./acceptance/native-session-run.json \
  --artifact-root "$AGENTBRIDGE_ACCEPTANCE_ARTIFACT_ROOT" \
  --status passed --notes "Native PTY acceptance passed."
uv run agentbridge-acceptance summary "$AGENTBRIDGE_ACCEPTANCE_EVIDENCE_FILE" \
  --verify-artifacts --artifact-root "$AGENTBRIDGE_ACCEPTANCE_ARTIFACT_ROOT" \
  --fail-on-warn
```

## Acceptance Evidence Matrix

| Design section | Automated evidence | Manual or integration evidence still required |
| --- | --- | --- |
| 34.1 Native session | Readiness covers Control Plane health, persistence, session inventory, launch-profile executability, lifecycle monitor, and terminal event outbox. | Start Claude Code in a real PTY without `claude -p`, keep the same CLI alive across Bot restart, and complete 20 consecutive turns in one Session. |
| 34.2 Visible terminal and takeover | Writer leases reject stale epochs; terminal snapshots and lifecycle state are exposed through REST, WebSocket, daemon, console, and Admin pages. | Verify local terminal window opening for the target OS, native TUI visibility, first local key acquiring the human lease, Bot input blocking/queueing during human control, release back to Bot, and no crossed input. |
| 34.3 Bot experience | Bot Gateway rendering, text fallback, code-block splitting, interaction commands, delivery records, retries, and OneBot capability readiness are covered by automated tests and readiness. | Exercise a real OneBot V11 group: create/bind Session, observe incremental answers and tool progress, answer questions, approve/deny, and confirm long code formatting on the actual platform. |
| 34.4 Permissions and management | RBAC, access policy, managed-device gates, audit export, Admin pages, approval interaction records, and workspace/write-limit checks have automated coverage. | Validate real group member role mappings, button callback re-authorization with platform user IDs, and operator audit review in the deployed Admin UI. |
| 34.5 Multi-project management | Project/workspace APIs, bindings, allowed-root checks, symlink escape rejection, quotas, write concurrency, and Admin project/session operations have automated coverage. | In one Bot chat, bind at least three projects, set one unique default, and verify `/agent project list/use/info` against names, aliases, and short IDs. |
| 34.6 Multi-session management | Session APIs, queue operations, active pointers, short IDs, leases, and per-session queue serialization have automated coverage. | Run at least three concurrent Sessions under one project, restart Bot/control clients, and verify session switching never closes or cross-writes other Sessions. |
| 34.7 Slash commands | `/agent` parsing, unified invocation metadata, RBAC checks, idempotency, interactions, unknown-command handling, and audit records have automated coverage. | Confirm OneBot text commands and action callbacks behave correctly through the deployed adapter, including missing-argument recovery paths. |
| 34.8 Recovery | Event replay cursors, terminal event outbox, old epoch rejection, interaction expiry, Bot retry worker, pty-host supervision, and lifecycle restart paths have automated coverage. | Disconnect/reconnect the Control Plane from a running local CLI, replay missed events, and validate the OS-specific terminal backend recovery path used in production. |

## Sign-Off Artifacts

Collect these artifacts for a release candidate:

- `agentbridge-readiness --format json` output and `--format actions --fail-on-warn`
  exit status.
- `AGENTBRIDGE_ACCEPTANCE_EVIDENCE_FILE` manifest with every design-document section
  from `34.1` through `34.8` marked `passed` and backed by artifact references.
- Admin screenshots or exports for System Health, Project/Session, Interaction,
  Terminal Lifecycle, Audit/Event, Device Identity, and Bot Delivery pages.
- Audit JSON/CSV/signed archive covering the manual acceptance run.
- Bot delivery records for incremental answers, tool progress, interactions, retries,
  fallback text, and any native platform actions.
- Terminal lifecycle evidence showing start, takeover, release, restart/recovery, and
  event-outbox flush behavior.

## Current Boundary

A deployment is not MVP-accepted until readiness has no warnings under product-like
configuration and every manual item in the matrix has been exercised against the target
OS, target agent CLI versions, and target Bot platform.
