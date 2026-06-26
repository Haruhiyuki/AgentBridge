import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from agentbridge.agent_adapter_client import (
    AgentAdapterClientConfig,
    AgentAdapterClientError,
    AgentAdapterControlClient,
    ClaudeHookAdapterClient,
    CodexAppServerAdapterClient,
    handshake_payload_for_agent,
    main,
    urllib_json_transport,
)
from agentbridge.domain import AgentBridgeError, AgentType


def test_claude_hook_client_posts_default_schema_checked_event():
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, headers, payload, timeout_seconds))
        return {"id": "evt_1", "type": "assistant.delta"}

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(
            base_url="http://bridge.local/",
            session_id="ses/with slash",
            api_token="api-secret",
            device_id="adapter-1",
            device_key="device-secret",
            timeout_seconds=3.5,
        ),
        transport=transport,
    )
    client = ClaudeHookAdapterClient(control_client)

    result = client.emit(
        "MessageDisplay",
        {"text": "hello"},
        idempotency_key="hook-event-1",
    )

    assert result == {"id": "evt_1", "type": "assistant.delta"}
    assert calls == [
        (
            "POST",
            "http://bridge.local/api/v1/sessions/ses%2Fwith%20slash/agent-adapter/events",
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer api-secret",
                "X-AgentBridge-Device-ID": "adapter-1",
                "X-AgentBridge-Device-Key": "device-secret",
            },
            {
                "agent_type": "claude",
                "adapter_event_type": "MessageDisplay",
                "trace_id": "agent-adapter-client",
                "schema_version": "claude-hooks.v1",
                "payload": {"text": "hello"},
                "idempotency_key": "hook-event-1",
            },
            3.5,
        )
    ]


def test_codex_client_rejects_unsupported_schema_before_transport():
    called = False

    def transport(method, url, headers, payload, timeout_seconds):
        nonlocal called
        called = True
        return {}

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=transport,
    )

    with pytest.raises(AgentBridgeError) as exc_info:
        CodexAppServerAdapterClient(
            control_client,
            schema_version="codex-app-server.v999",
        ).emit("turn/completed", {})

    assert called is False
    assert exc_info.value.details == {
        "agent_type": "codex",
        "schema_version": "codex-app-server.v999",
        "supported_schema_versions": ["codex-app-server.v1"],
    }


def test_control_client_polls_adapter_responses_with_cursor_and_limit():
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, payload))
        return {"responses": [{"seq": 12, "decision": "answered"}]}

    client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=transport,
    )

    result = client.poll_responses(after_seq=10, limit=25)

    assert result == {"responses": [{"seq": 12, "decision": "answered"}]}
    assert calls == [
        (
            "GET",
            "http://bridge.local/api/v1/sessions/ses_1/agent-adapter/responses?"
            "limit=25&after_seq=10",
            None,
        )
    ]


def test_handshake_payload_for_agent_uses_supported_schema_and_capabilities():
    assert handshake_payload_for_agent(AgentType.CODEX) == {
        "protocol": "agentbridge.adapter.v1",
        "compatible": True,
        "agent_type": "codex",
        "schema_version": "codex-app-server.v1",
        "supported_schema_versions": ["codex-app-server.v1"],
        "capabilities": [
            "agentbridge.event_ingest",
            "agentbridge.response_poll",
            "codex.app_server",
        ],
        "warnings": [],
    }


def test_cli_handshake_prints_probe_compatible_json(capsys):
    result = main(["handshake", "--agent", "claude"])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["protocol"] == "agentbridge.adapter.v1"
    assert payload["compatible"] is True
    assert payload["agent_type"] == "claude"
    assert payload["schema_version"] == "claude-hooks.v1"
    assert payload["supported_schema_versions"] == ["claude-hooks.v1"]


def test_urllib_transport_reports_structured_http_errors(monkeypatch):
    error = HTTPError(
        "http://bridge.local",
        400,
        "Bad Request",
        {},
        BytesIO(b'{"message":"Adapter schema_version unsupported","error_code":"BAD"}'),
    )

    def failing_urlopen(request, timeout):
        raise error

    monkeypatch.setattr("agentbridge.agent_adapter_client.urlopen", failing_urlopen)

    with pytest.raises(AgentAdapterClientError) as exc_info:
        urllib_json_transport(
            "POST",
            "http://bridge.local/api",
            {"Content-Type": "application/json"},
            {"hello": "world"},
            1.0,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.payload["error_code"] == "BAD"
