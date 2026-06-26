#!/bin/sh
set -eu

ENV_FILE="${AGENTBRIDGE_CODEX_PROXY_ENV_FILE:-$HOME/.config/agentbridge/codex-proxy.env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

: "${AGENTBRIDGE_SESSION_ID:?AGENTBRIDGE_SESSION_ID is required}"

ADAPTER_CLIENT_BIN="${AGENTBRIDGE_ADAPTER_CLIENT_BIN:-agentbridge-adapter-client}"
CODEX_APP_SERVER_COMMAND="${AGENTBRIDGE_CODEX_APP_SERVER_COMMAND:-codex app-server}"

set -- "$ADAPTER_CLIENT_BIN" codex-app-server-proxy \
  --restart-policy "${AGENTBRIDGE_CODEX_PROXY_RESTART_POLICY:-on-failure}" \
  --max-restarts "${AGENTBRIDGE_CODEX_PROXY_MAX_RESTARTS:-3}" \
  --restart-delay-seconds "${AGENTBRIDGE_CODEX_PROXY_RESTART_DELAY_SECONDS:-1}" \
  --restart-min-uptime-seconds "${AGENTBRIDGE_CODEX_PROXY_RESTART_MIN_UPTIME_SECONDS:-0}" \
  --health-interval-seconds "${AGENTBRIDGE_CODEX_PROXY_HEALTH_INTERVAL_SECONDS:-30}"

if [ -n "${AGENTBRIDGE_CODEX_PROXY_HEALTH_OUTPUT_FILE:-}" ]; then
  set -- "$@" --health-output-file "$AGENTBRIDGE_CODEX_PROXY_HEALTH_OUTPUT_FILE"
fi

if [ -n "${AGENTBRIDGE_CODEX_PROXY_BRIDGE_OUTPUT_FILE:-}" ]; then
  set -- "$@" --bridge-output-file "$AGENTBRIDGE_CODEX_PROXY_BRIDGE_OUTPUT_FILE"
fi

if [ "${AGENTBRIDGE_CODEX_PROXY_INJECT_RESPONSES:-true}" = "true" ]; then
  set -- "$@" --inject-responses
fi

if [ "${AGENTBRIDGE_CODEX_PROXY_FORWARD_INJECTED_REQUESTS:-false}" = "true" ]; then
  set -- "$@" --forward-injected-requests
fi

exec "$@" -- sh -lc "exec ${CODEX_APP_SERVER_COMMAND}"
