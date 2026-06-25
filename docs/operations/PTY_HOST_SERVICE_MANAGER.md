# PTY Host Service Manager Deployment

`agentbridge-pty-host` owns local stdlib PTY sessions. For product-like use, run it under
the OS service manager instead of relying only on API/daemon in-process supervision. This
keeps the PTY host independent from AgentBridge API or local Terminal Agent restarts and gives
operators a standard place to inspect logs and restart policy.

## Recommended Topology

- Run exactly one `agentbridge-pty-host` per local user account.
- Put the host socket under a user-private runtime directory.
- Put the host state registry under a user-private state directory.
- Store `AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN` in a mode-0600 file or launchd plist.
- Configure API/daemon processes with `AGENTBRIDGE_TERMINAL_BACKEND=pty_host`,
  the same socket path, and the same token.
- Prefer the service manager as the primary host restart mechanism. Keep
  `AGENTBRIDGE_TERMINAL_PTY_HOST_AUTO_START=false` and
  `AGENTBRIDGE_TERMINAL_PTY_HOST_WATCHDOG_ENABLED=false` unless you intentionally want the
  API/daemon process to act as a fallback supervisor.
- Enable `AGENTBRIDGE_TERMINAL_AUTO_RESTART_ON_LOST=true` only after deciding that command
  restart is acceptable for your native CLI workflow.

## systemd User Service

Install the user unit and env file:

```bash
install -m 0700 -d "$HOME/.config/systemd/user" "$HOME/.config/agentbridge" "$HOME/.local/state/agentbridge"
install -m 0600 docs/operations/templates/agentbridge-pty-host.env.example "$HOME/.config/agentbridge/pty-host.env"
sed "s#__AGENTBRIDGE_PTY_HOST_BIN__#$(command -v agentbridge-pty-host)#" \
  docs/operations/templates/agentbridge-pty-host.systemd.user.service \
  > "$HOME/.config/systemd/user/agentbridge-pty-host.service"
```

Edit `~/.config/agentbridge/pty-host.env`:

- Replace `__XDG_RUNTIME_DIR__` with `echo "$XDG_RUNTIME_DIR"`.
- Replace `__HOME__` with your home directory.
- Replace `__GENERATE_STRONG_TOKEN__` with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.

Start and inspect:

```bash
systemctl --user daemon-reload
systemctl --user enable --now agentbridge-pty-host.service
systemctl --user status agentbridge-pty-host.service
journalctl --user -u agentbridge-pty-host.service -f
```

Configure API or local daemon clients:

```bash
export AGENTBRIDGE_TERMINAL_BACKEND=pty_host
export AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET="$XDG_RUNTIME_DIR/agentbridge/pty-host.sock"
export AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN="<same-token>"
export AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH="$HOME/.local/state/agentbridge/pty-host-state.json"
```

## macOS launchd User Agent

Prepare directories and plist:

```bash
install -m 0700 -d "$HOME/Library/LaunchAgents" "$HOME/Library/Application Support/AgentBridge" "$HOME/Library/Logs/AgentBridge"
sed \
  -e "s#__AGENTBRIDGE_PTY_HOST_BIN__#$(command -v agentbridge-pty-host)#" \
  -e "s#__HOME__#$HOME#g" \
  -e "s#__GENERATE_STRONG_TOKEN__#$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')#" \
  docs/operations/templates/com.agentbridge.pty-host.launchd.plist \
  > "$HOME/Library/LaunchAgents/com.agentbridge.pty-host.plist"
chmod 0600 "$HOME/Library/LaunchAgents/com.agentbridge.pty-host.plist"
```

Start and inspect:

```bash
plutil -lint "$HOME/Library/LaunchAgents/com.agentbridge.pty-host.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.agentbridge.pty-host.plist"
launchctl kickstart -k "gui/$(id -u)/com.agentbridge.pty-host"
launchctl print "gui/$(id -u)/com.agentbridge.pty-host"
tail -f "$HOME/Library/Logs/AgentBridge/pty-host.err.log"
```

Configure API or local daemon clients:

```bash
export AGENTBRIDGE_TERMINAL_BACKEND=pty_host
export AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET="$HOME/Library/Application Support/AgentBridge/pty-host.sock"
export AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN="<same-token>"
export AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH="$HOME/Library/Application Support/AgentBridge/pty-host-state.json"
```

## Recovery Expectations

Service managers restart the host process, but a host-process death still destroys the PTYs
owned by that process. AgentBridge can make that failure explicit:

1. The service manager restarts `agentbridge-pty-host`.
2. The Terminal lifecycle monitor observes that a previously started session is missing.
3. AgentBridge emits `terminal.lost`.
4. If `AGENTBRIDGE_TERMINAL_AUTO_RESTART_ON_LOST=true`, AgentBridge starts a new terminal
   generation from the latest persisted `terminal.started` command.

Use `GET /api/v1/terminal/lifecycle-monitor` or the local daemon `lifecycle_status` action to
inspect tracked sessions, lost/exited counts, auto-restart attempts, and backend-supervision
state. Use the run-once operation only as an operator action because it can emit lifecycle
events and trigger opt-in restarts.
