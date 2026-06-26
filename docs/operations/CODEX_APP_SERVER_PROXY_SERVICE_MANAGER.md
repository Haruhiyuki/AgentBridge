# Codex App Server Proxy Service-Manager Deployment

`agentbridge-adapter-client codex-app-server-proxy` is a stdio proxy. It must stay in the
same stdin/stdout path as the upstream Codex app-server client. Do not install it as a
detached background service with no client connected to stdin/stdout; that creates a
healthy-looking process that cannot speak the JSON-RPC stream it is supposed to proxy.

Use the OS service manager to supervise the process that owns the real terminal or app-server
client, and run the proxy as the Codex launch command inside that process tree. The proxy then
supervises its child `codex app-server` process, emits health JSONL for external monitoring,
and preserves the primary JSON-RPC stream for the client.

## Recommended Topology

- Run `agentbridge-pty-host` under the OS service manager as documented in
  `docs/operations/PTY_HOST_SERVICE_MANAGER.md`.
- Configure Codex Sessions to launch a local wrapper around
  `agentbridge-adapter-client codex-app-server-proxy`.
- Keep the wrapper in the same stdin/stdout path as the Codex client.
- Store the adapter device key or API token in a mode-0600 file.
- Write proxy health JSONL and optional AgentBridge response side-channel JSONL to a
  user-private state directory.
- Let the proxy handle child `codex app-server` restarts with `--restart-policy` and
  `--max-restarts`; let the service manager restart the outer PTY host, local daemon, or
  custom client process.

This layering avoids competing supervisors. The service manager owns the long-running local
process boundary, while the proxy owns the app-server child it directly launched.

## Wrapper Template

Install and edit the template files:

```bash
install -m 0700 -d "$HOME/.config/agentbridge" "$HOME/.local/bin" "$HOME/.local/state/agentbridge"
install -m 0600 docs/operations/templates/agentbridge-codex-proxy.env.example \
  "$HOME/.config/agentbridge/codex-proxy.env"
install -m 0700 docs/operations/templates/agentbridge-codex-proxy-wrapper.sh \
  "$HOME/.local/bin/agentbridge-codex-proxy-wrapper"
```

Create or place the adapter key file referenced by the env file:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$HOME/.config/agentbridge/codex-adapter.key"
chmod 0600 "$HOME/.config/agentbridge/codex-adapter.key"
```

Edit `~/.config/agentbridge/codex-proxy.env`:

- Set `AGENTBRIDGE_API_URL` and `AGENTBRIDGE_SESSION_ID`.
- Set either `AGENTBRIDGE_DEVICE_ID` plus `AGENTBRIDGE_DEVICE_KEY_FILE`, or
  `AGENTBRIDGE_API_TOKEN_FILE`, depending on the API gate used by the deployment.
- Replace `__HOME__` placeholders with the local home directory.
- Keep `AGENTBRIDGE_CODEX_PROXY_INJECT_RESPONSES=true` only when AgentBridge should answer
  app-server approval/question requests directly.
- Tune `AGENTBRIDGE_CODEX_PROXY_RESTART_POLICY`, `AGENTBRIDGE_CODEX_PROXY_MAX_RESTARTS`,
  and `AGENTBRIDGE_CODEX_PROXY_HEALTH_INTERVAL_SECONDS` for the deployment.

Then point the Codex launch profile at the wrapper:

```bash
export AGENTBRIDGE_AGENT_CODEX_COMMAND="$HOME/.local/bin/agentbridge-codex-proxy-wrapper"
export AGENTBRIDGE_AGENT_CODEX_HANDSHAKE_COMMAND="agentbridge-adapter-client handshake --agent codex"
```

If the local Terminal Agent or API process is itself service-managed, put the two environment
variables above in that service's env file. The service manager then starts the normal
AgentBridge process, AgentBridge starts the Codex Session, and the Codex Session starts the
proxy wrapper with live stdin/stdout.

## systemd User Notes

For product-like local deployments, combine this wrapper with the PTY host user service:

```bash
systemctl --user enable --now agentbridge-pty-host.service
systemctl --user status agentbridge-pty-host.service
journalctl --user -u agentbridge-pty-host.service -f
```

Configure the API or local daemon service environment with:

```bash
AGENTBRIDGE_TERMINAL_BACKEND=pty_host
AGENTBRIDGE_AGENT_CODEX_COMMAND=%h/.local/bin/agentbridge-codex-proxy-wrapper
AGENTBRIDGE_AGENT_CODEX_HANDSHAKE_COMMAND=agentbridge-adapter-client handshake --agent codex
```

Avoid a standalone `agentbridge-codex-proxy.service` unless another component connects its
stdin/stdout to a real Codex app-server client. A plain background service has no valid
upstream JSON-RPC stream.

Inspect proxy health and side-channel output:

```bash
tail -f "$HOME/.local/state/agentbridge/codex-proxy-health.jsonl"
tail -f "$HOME/.local/state/agentbridge/codex-proxy-responses.jsonl"
```

## macOS launchd Notes

Use the launchd PTY host user agent from `docs/operations/PTY_HOST_SERVICE_MANAGER.md` for
the long-lived PTY owner. Put the Codex wrapper environment in the API or local daemon launchd
plist:

```xml
<key>AGENTBRIDGE_TERMINAL_BACKEND</key>
<string>pty_host</string>
<key>AGENTBRIDGE_AGENT_CODEX_COMMAND</key>
<string>__HOME__/.local/bin/agentbridge-codex-proxy-wrapper</string>
<key>AGENTBRIDGE_AGENT_CODEX_HANDSHAKE_COMMAND</key>
<string>agentbridge-adapter-client handshake --agent codex</string>
```

Keep proxy health output under `~/Library/Application Support/AgentBridge` or another
user-private state directory, and route process logs through the launchd service that owns the
API, daemon, or PTY host.

## Recovery Expectations

1. The service manager keeps the PTY host, API, local daemon, or custom app-server client
   process alive.
2. The proxy emits `started` and periodic `running` health JSONL while the child app-server is
   alive.
3. If the child exits, the proxy emits `exited`; when policy allows another attempt, it emits
   `restarting` and relaunches `codex app-server`.
4. If the proxy exits, it emits `stopped`; the outer service manager can restart the process
   that owns the upstream stdio path.
5. If the PTY host dies, owned terminals are still lost. Use the Terminal lifecycle monitor and
   the opt-in lost-terminal auto-restart policy from the PTY host service-manager guide to make
   that failure explicit and replay only allowlisted commands.

Use the health JSONL as the monitoring contract for the proxy process. Treat missing heartbeats
or repeated quick `exited`/`restarting` pairs as an operator-visible incident.
