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
    adapter_response_matches_request,
    build_parser,
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


def test_adapter_client_emit_and_wait_returns_first_ready_matching_response():
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, payload))
        if method == "POST":
            return {
                "id": "evt_request",
                "seq": 5,
                "interaction_id": "int_1",
                "payload": {"adapter_item_id": "cmd-1"},
            }
        return {
            "responses": [
                {
                    "seq": 6,
                    "request_event_id": "other",
                    "interaction_id": "int_other",
                    "ready": True,
                    "decision": "answered",
                },
                {
                    "seq": 7,
                    "request_event_id": "evt_request",
                    "interaction_id": "int_1",
                    "ready": False,
                    "decision": "pending",
                },
                {
                    "seq": 8,
                    "request_event_id": "evt_request",
                    "interaction_id": "int_1",
                    "ready": True,
                    "decision": "approved",
                },
            ]
        }

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=transport,
    )
    client = CodexAppServerAdapterClient(control_client)

    result = client.emit_and_wait(
        "item/commandExecution/requestApproval",
        {"item": {"id": "cmd-1", "command": "pytest"}},
        idempotency_key="codex-approval-1",
        poll_interval_seconds=0.01,
    )

    assert result["event"]["id"] == "evt_request"
    assert result["response"]["seq"] == 8
    assert result["response"]["decision"] == "approved"
    assert calls == [
        (
            "POST",
            "http://bridge.local/api/v1/sessions/ses_1/agent-adapter/events",
            {
                "agent_type": "codex",
                "adapter_event_type": "item/commandExecution/requestApproval",
                "trace_id": "agent-adapter-client",
                "schema_version": "codex-app-server.v1",
                "payload": {"item": {"id": "cmd-1", "command": "pytest"}},
                "idempotency_key": "codex-approval-1",
            },
        ),
        (
            "GET",
            "http://bridge.local/api/v1/sessions/ses_1/agent-adapter/responses?"
            "limit=100&after_seq=5",
            None,
        ),
    ]


def test_wait_for_response_timeout_reports_last_pending_response():
    clock_values = iter([0.0, 0.0, 1.1])
    sleep_calls = []

    def clock():
        return next(clock_values)

    def sleep(seconds):
        sleep_calls.append(seconds)

    def transport(method, url, headers, payload, timeout_seconds):
        return {
            "responses": [
                {
                    "seq": 9,
                    "request_event_id": "evt_request",
                    "ready": False,
                    "decision": "pending",
                }
            ]
        }

    client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=transport,
        clock=clock,
        sleep=sleep,
    )

    with pytest.raises(AgentAdapterClientError) as exc_info:
        client.wait_for_response(
            {"id": "evt_request", "seq": 5},
            timeout_seconds=1.0,
            poll_interval_seconds=0.25,
        )

    assert sleep_calls == [0.25]
    assert exc_info.value.payload["request_event_id"] == "evt_request"
    assert exc_info.value.payload["last_response"] == {
        "seq": 9,
        "request_event_id": "evt_request",
        "ready": False,
        "decision": "pending",
    }


def test_adapter_response_matchers_use_event_interaction_or_adapter_item():
    response = {
        "request_event_id": "evt_1",
        "interaction_id": "int_1",
        "adapter_item_id": "item_1",
    }

    assert adapter_response_matches_request(response, request_event_id="evt_1") is True
    assert adapter_response_matches_request(response, interaction_id="int_1") is True
    assert adapter_response_matches_request(response, adapter_item_id="item_1") is True
    assert adapter_response_matches_request(response, request_event_id="evt_other") is False


def test_handshake_payload_for_agent_uses_supported_schema_and_capabilities():
    payload = handshake_payload_for_agent(AgentType.CODEX)

    assert payload["protocol"] == "agentbridge.adapter.v1"
    assert payload["compatible"] is True
    assert payload["agent_type"] == "codex"
    assert payload["schema_version"] == "codex-app-server.v1"
    assert payload["supported_schema_versions"] == ["codex-app-server.v1"]
    assert payload["capabilities"] == [
        "agentbridge.event_ingest",
        "agentbridge.response_poll",
        "codex.app_server",
    ]
    assert payload["warnings"] == []
    assert payload["schema_snapshot"]["schema_version"] == "codex-app-server.v1"
    assert {
        "adapter_event_type": "tool/requestUserInput",
        "semantic_event_type": "question.requested",
        "interaction_request": True,
    } in payload["schema_snapshot"]["adapter_event_types"]


def test_cli_handshake_prints_probe_compatible_json(capsys):
    result = main(["handshake", "--agent", "claude"])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["protocol"] == "agentbridge.adapter.v1"
    assert payload["compatible"] is True
    assert payload["agent_type"] == "claude"
    assert payload["schema_version"] == "claude-hooks.v1"
    assert payload["supported_schema_versions"] == ["claude-hooks.v1"]
    assert payload["schema_snapshot"]["adapter"] == "claude_hooks"


def test_cli_schemas_prints_selected_agent_matrix(capsys):
    result = main(["schemas", "--agent", "codex"])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent_type"] == "codex"
    assert payload["default_schema_version"] == "codex-app-server.v1"
    assert payload["schemas"][0]["response_contract"]["pending_decision"] == "pending"


def test_cli_schemas_prints_specific_snapshot(capsys):
    result = main(
        [
            "schemas",
            "--agent",
            "claude",
            "--schema-version",
            "claude-hooks.v1",
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent_type"] == "claude"
    assert payload["schema_version"] == "claude-hooks.v1"
    assert payload["normalization"]["unknown_adapter_event_type"] == "reject"


def test_cli_parser_accepts_emit_and_wait_options():
    args = build_parser().parse_args(
        [
            "emit-and-wait",
            "--agent",
            "codex",
            "--session-id",
            "ses_1",
            "--event-type",
            "tool/requestUserInput",
            "--payload-json",
            '{"question":"Deploy?"}',
            "--wait-timeout-seconds",
            "12.5",
            "--poll-interval-seconds",
            "0.2",
            "--include-pending",
        ]
    )

    assert args.command == "emit-and-wait"
    assert args.agent == AgentType.CODEX
    assert args.session_id == "ses_1"
    assert args.wait_timeout_seconds == 12.5
    assert args.poll_interval_seconds == 0.2
    assert args.include_pending is True


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
