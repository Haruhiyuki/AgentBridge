# Release Candidate Handoff

This runbook defines the first usable AgentBridge handoff path. It is narrower than the
full product roadmap: the goal is a product-like release candidate that a user can run
against a real agent CLI, real terminal backend, and real Bot transport while preserving
the design document's MVP acceptance boundary.

## Gates

Use both release preflight and runtime readiness:

```bash
uv run agentbridge-release --profile rc --format actions
uv run alembic upgrade head
uv run agentbridge-readiness --format actions --fail-on-warn
```

`agentbridge-release` checks the local source/package handoff boundary: version
metadata, console entry points, required runbooks/templates, product-like configuration
variables, terminal backend selection, and configured acceptance manifest/bundle files.
It does not contact a running API server. `/api/v1/readiness` and
`agentbridge-readiness` check the live deployment and must still pass before handoff.

Exit codes:

- `0`: all required checks passed, or warnings were allowed.
- `2`: degraded preflight when `--fail-on-warn` is used.
- `3`: release preflight is not ready for handoff.

Use `--profile local` only for developer smoke checks; missing product credentials,
database, terminal, or acceptance files are warnings in that mode and failures in
`--profile rc`.

## Baseline Environment

Set a persistent database, explicit auth gates, a product-like terminal backend, and
acceptance evidence paths before starting the release candidate:

```bash
export AGENTBRIDGE_DATABASE_URL=sqlite:////var/lib/agentbridge/agentbridge.db
export AGENTBRIDGE_API_TOKEN=...
export AGENTBRIDGE_ADMIN_TOKEN=...
export AGENTBRIDGE_WS_TOKEN=...
export AGENTBRIDGE_DEVICE_KEYS='{"readiness-runner":"..."}'

export AGENTBRIDGE_TERMINAL_BACKEND=pty_host
export AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET=/run/user/1000/agentbridge/pty-host.sock
export AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE=/var/lib/agentbridge/pty-host.token

export AGENTBRIDGE_ACCEPTANCE_EVIDENCE_FILE=/var/lib/agentbridge/acceptance-evidence.json
export AGENTBRIDGE_ACCEPTANCE_ARTIFACT_ROOT=/var/lib/agentbridge/acceptance-artifacts
export AGENTBRIDGE_ACCEPTANCE_VERIFY_ARTIFACTS=true
export AGENTBRIDGE_ACCEPTANCE_BUNDLE_FILE=/var/lib/agentbridge/agentbridge-mvp-acceptance-bundle.zip
```

For a local single-user trial, `AGENTBRIDGE_TERMINAL_BACKEND=pty` can be acceptable, but
the RC gate should still be run so the deviation is explicit in the handoff notes.
For a restart-tolerant handoff, prefer the PTY host service-manager topology documented
in `docs/operations/PTY_HOST_SERVICE_MANAGER.md`.

## Handoff Steps

1. Sync dependencies and run the static quality gate:

   ```bash
   uv sync --extra dev
   uv run pytest
   uv run ruff check .
   uv run python -m compileall -q src tests alembic
   ```

2. Run migrations against the target database:

   ```bash
   uv run alembic upgrade head
   ```

3. Start the PTY host, API, and any Bot adapter process for the target environment.
   Use `agentbridge-pty-host` under systemd user services or launchd for a persistent
   PTY host. Use `agentbridge-api` or `uvicorn agentbridge.api:create_app --factory`
   for the API process.

4. Run release preflight:

   ```bash
   uv run agentbridge-release --profile rc --format actions
   ```

5. Run runtime readiness against the deployed API:

   ```bash
   uv run agentbridge-readiness \
     --api-url http://127.0.0.1:8000 \
     --api-token "$AGENTBRIDGE_API_TOKEN" \
     --format actions \
     --fail-on-warn
   ```

6. Execute the manual MVP acceptance matrix in
   `docs/operations/MVP_ACCEPTANCE_RUNBOOK.md`. Every design-document section from
   `34.1` through `34.8` must be marked `passed`, every checklist item must be marked
   `passed`, and every section must have at least one artifact.

7. Build and verify the portable acceptance bundle:

   ```bash
   uv run agentbridge-acceptance bundle \
     "$AGENTBRIDGE_ACCEPTANCE_EVIDENCE_FILE" \
     "$AGENTBRIDGE_ACCEPTANCE_BUNDLE_FILE" \
     --artifact-root "$AGENTBRIDGE_ACCEPTANCE_ARTIFACT_ROOT"
   uv run agentbridge-acceptance verify-bundle "$AGENTBRIDGE_ACCEPTANCE_BUNDLE_FILE"
   ```

8. Re-run release preflight and runtime readiness. Both must pass without warnings for
   an MVP-accepted handoff.

## Handoff Artifacts

Provide these artifacts with the first usable release candidate:

- Package version and commit hash.
- `agentbridge-release --profile rc --format json` output.
- `agentbridge-readiness --format json` output from the target deployment.
- `agentbridge-acceptance verify-bundle` output showing `valid=true` and `ready=true`.
- The acceptance bundle ZIP.
- Admin JSON exports or screenshots listed in the MVP acceptance runbook.
- Any known deviations from the baseline environment, especially terminal backend,
  Bot platform capability gaps, or skipped manual matrix items.
