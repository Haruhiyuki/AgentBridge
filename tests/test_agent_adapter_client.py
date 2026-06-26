import json
from io import BytesIO, StringIO
from urllib.error import HTTPError

import pytest

from agentbridge.agent_adapter_client import (
    AgentAdapterClientConfig,
    AgentAdapterClientError,
    AgentAdapterControlClient,
    ClaudeHookAdapterClient,
    CodexAppServerAdapterClient,
    adapter_response_matches_request,
    bridge_codex_app_server_jsonl_stream,
    build_parser,
    claude_adapter_event_type_from_hook_payload,
    claude_hook_failure_stdout_json,
    claude_hook_idempotency_key,
    claude_hook_settings_fragment,
    codex_app_server_event_payload_from_message,
    codex_app_server_event_type_from_message,
    codex_app_server_idempotency_key,
    codex_app_server_json_rpc_response,
    format_adapter_response_for_agent,
    handle_claude_hook_payload,
    handle_codex_app_server_message,
    handshake_payload_for_agent,
    main,
    urllib_json_transport,
    write_claude_hook_settings_file,
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
        "codex.app_server.json_rpc",
        "codex.app_server.jsonl_stream",
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


def test_cli_parser_accepts_codex_app_server_event_options():
    args = build_parser().parse_args(
        [
            "codex-app-server-event",
            "--session-id",
            "ses_1",
            "--input-file",
            "-",
            "--wait-timeout-seconds",
            "12.5",
            "--poll-interval-seconds",
            "0.2",
            "--json-rpc-response",
        ]
    )

    assert args.command == "codex-app-server-event"
    assert args.session_id == "ses_1"
    assert str(args.input_file) == "-"
    assert args.wait_timeout_seconds == 12.5
    assert args.poll_interval_seconds == 0.2
    assert args.json_rpc_response is True


def test_cli_parser_accepts_codex_app_server_stream_options():
    args = build_parser().parse_args(
        [
            "codex-app-server-stream",
            "--session-id",
            "ses_1",
            "--input-file",
            "-",
            "--wait-timeout-seconds",
            "12.5",
            "--poll-interval-seconds",
            "0.2",
            "--output-format",
            "action",
        ]
    )

    assert args.command == "codex-app-server-stream"
    assert args.session_id == "ses_1"
    assert str(args.input_file) == "-"
    assert args.wait_timeout_seconds == 12.5
    assert args.poll_interval_seconds == 0.2
    assert args.output_format == "action"


def test_formats_claude_permission_response_as_hook_stdout_json():
    formatted = format_adapter_response_for_agent(
        AgentType.CLAUDE,
        {
            "decision": "denied",
            "reason": "Requires two approvals",
            "adapter_event_type": "PermissionRequest",
            "request_event_id": "evt_1",
        },
    )

    assert formatted["format"] == "claude.hooks.command_stdout.v1"
    assert formatted["exit_code"] == 0
    assert formatted["stdout_json"] == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {
                "behavior": "deny",
                "message": "Requires two approvals",
            },
        }
    }


def test_formats_claude_question_answer_as_pre_tool_use_updated_input():
    formatted = format_adapter_response_for_agent(
        AgentType.CLAUDE,
        {
            "decision": "answered",
            "answer": "staging",
            "adapter_event_type": "AskUserQuestion",
            "request_payload": {
                "raw_event": {
                    "tool_input": {
                        "questions": [
                            {"text": "Which environment?"},
                        ],
                        "allowMultiple": False,
                    }
                }
            },
        },
    )

    assert formatted["stdout_json"] == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "Answered through AgentBridge.",
            "updatedInput": {
                "questions": [{"text": "Which environment?"}],
                "allowMultiple": False,
                "answers": {"Which environment?": "staging"},
            },
        }
    }


def test_formats_codex_response_as_agentbridge_action_envelope():
    formatted = format_adapter_response_for_agent(
        AgentType.CODEX,
        {
            "decision": "approved",
            "approve": True,
            "adapter_item_id": "cmd-1",
            "interaction_id": "int_1",
            "request_event_id": "evt_1",
        },
    )

    assert formatted["format"] == "codex.app_server.agentbridge_action.v1"
    assert formatted["action"] == "approval_decision"
    assert formatted["payload"] == {
        "action": "approval_decision",
        "decision": "approved",
        "approve": True,
        "answer": None,
        "reason": None,
        "adapter_item_id": "cmd-1",
        "interaction_id": "int_1",
        "request_event_id": "evt_1",
        "request_seq": None,
        "payload": None,
    }


def test_formats_codex_response_as_json_rpc_result_when_request_id_is_known():
    response = {
        "decision": "denied",
        "approve": False,
        "reason": "Needs a maintainer",
        "adapter_item_id": "cmd-1",
        "request_payload": {
            "raw_event": {
                "json_rpc_id": 12,
                "json_rpc_method": "item/commandExecution/requestApproval",
            }
        },
    }

    assert codex_app_server_json_rpc_response(response) == {
        "id": 12,
        "result": {
            "agentbridge": {
                "action": "approval_decision",
                "decision": "denied",
                "approve": False,
                "answer": None,
                "reason": "Needs a maintainer",
                "adapter_item_id": "cmd-1",
                "interaction_id": None,
                "request_event_id": None,
                "request_seq": None,
                "payload": None,
            }
        },
    }


def test_codex_app_server_message_extracts_method_params_and_idempotency():
    message = {
        "method": "item/commandExecution/requestApproval",
        "id": "rpc-7",
        "params": {
            "item": {"id": "cmd-1", "command": "pytest"},
            "reason": "Run tests",
        },
    }

    assert (
        codex_app_server_event_type_from_message(message)
        == "item/commandExecution/requestApproval"
    )
    assert codex_app_server_event_payload_from_message(message) == {
        "item": {"id": "cmd-1", "command": "pytest"},
        "reason": "Run tests",
        "json_rpc_method": "item/commandExecution/requestApproval",
        "json_rpc_id": "rpc-7",
    }
    assert (
        codex_app_server_idempotency_key(
            "item/commandExecution/requestApproval",
            message,
        )
        == "codex-app-server:item/commandExecution/requestApproval:rpc:rpc-7"
    )


def test_codex_app_server_message_bridge_reports_notification_without_action():
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, payload))
        return {"id": "evt_delta", "seq": 9}

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=transport,
    )

    result = handle_codex_app_server_message(
        control_client=control_client,
        message={
            "method": "item/agentMessage/delta",
            "params": {"delta": "hello"},
        },
    )

    assert result["adapter_event_type"] == "item/agentMessage/delta"
    assert result["action"] is None
    assert result["json_rpc_response"] is None
    assert calls == [
        (
            "POST",
            "http://bridge.local/api/v1/sessions/ses_1/agent-adapter/events",
            {
                "agent_type": "codex",
                "adapter_event_type": "item/agentMessage/delta",
                "trace_id": "codex-app-server-adapter",
                "schema_version": "codex-app-server.v1",
                "payload": {
                    "delta": "hello",
                    "json_rpc_method": "item/agentMessage/delta",
                },
                "idempotency_key": result["idempotency_key"],
            },
        )
    ]


def test_codex_app_server_message_bridge_waits_for_approval_and_outputs_json_rpc():
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, payload))
        if method == "POST":
            return {
                "id": "evt_request",
                "seq": 4,
                "interaction_id": "int_1",
                "payload": {"adapter_item_id": "cmd-1"},
            }
        return {
            "responses": [
                {
                    "seq": 5,
                    "request_event_id": "evt_request",
                    "interaction_id": "int_1",
                    "adapter_event_type": "item/commandExecution/requestApproval",
                    "adapter_item_id": "cmd-1",
                    "ready": True,
                    "decision": "approved",
                    "approve": True,
                    "request_payload": {
                        "raw_event": {
                            "json_rpc_id": 42,
                            "json_rpc_method": "item/commandExecution/requestApproval",
                        }
                    },
                }
            ]
        }

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=transport,
    )

    result = handle_codex_app_server_message(
        control_client=control_client,
        message={
            "method": "item/commandExecution/requestApproval",
            "id": 42,
            "params": {
                "item": {"id": "cmd-1", "command": "pytest"},
                "reason": "Run tests",
            },
        },
        poll_interval_seconds=0.01,
    )

    assert result["action"]["action"] == "approval_decision"
    assert result["action"]["decision"] == "approved"
    assert result["json_rpc_response"] == {
        "id": 42,
        "result": {"agentbridge": result["action"]},
    }
    assert calls[0] == (
        "POST",
        "http://bridge.local/api/v1/sessions/ses_1/agent-adapter/events",
        {
            "agent_type": "codex",
            "adapter_event_type": "item/commandExecution/requestApproval",
            "trace_id": "codex-app-server-adapter",
            "schema_version": "codex-app-server.v1",
            "payload": {
                "item": {"id": "cmd-1", "command": "pytest"},
                "reason": "Run tests",
                "json_rpc_method": "item/commandExecution/requestApproval",
                "json_rpc_id": 42,
            },
            "idempotency_key": (
                "codex-app-server:item/commandExecution/requestApproval:rpc:42"
            ),
        },
    )


def test_codex_app_server_jsonl_stream_outputs_json_rpc_for_interactions_only():
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, payload))
        if method == "POST" and payload["adapter_event_type"] == "item/agentMessage/delta":
            return {"id": "evt_delta", "seq": 2}
        if method == "POST":
            return {
                "id": "evt_request",
                "seq": 4,
                "interaction_id": "int_1",
                "payload": {"adapter_item_id": "cmd-1"},
            }
        return {
            "responses": [
                {
                    "seq": 5,
                    "request_event_id": "evt_request",
                    "interaction_id": "int_1",
                    "adapter_event_type": "item/commandExecution/requestApproval",
                    "adapter_item_id": "cmd-1",
                    "ready": True,
                    "decision": "approved",
                    "approve": True,
                }
            ]
        }

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=transport,
    )
    output_file = StringIO()
    error_file = StringIO()

    summary = bridge_codex_app_server_jsonl_stream(
        control_client=control_client,
        input_file=StringIO(
            "\n"
            '{"id":1,"result":{"thread":{"id":"thr_1"}}}\n'
            '{"method":"item/agentMessage/delta","params":{"delta":"hello"}}\n'
            '{"method":"item/commandExecution/requestApproval","id":42,'
            '"params":{"item":{"id":"cmd-1","command":"pytest"},"reason":"Run tests"}}\n'
        ),
        output_file=output_file,
        error_file=error_file,
        poll_interval_seconds=0.01,
    )

    assert summary == {
        "processed": 2,
        "skipped": 2,
        "emitted": 1,
        "errors": 0,
    }
    assert error_file.getvalue() == ""
    output_lines = output_file.getvalue().splitlines()
    assert len(output_lines) == 1
    assert json.loads(output_lines[0]) == {
        "id": 42,
        "result": {
            "agentbridge": {
                "action": "approval_decision",
                "decision": "approved",
                "approve": True,
                "answer": None,
                "reason": None,
                "adapter_item_id": "cmd-1",
                "interaction_id": "int_1",
                "request_event_id": "evt_request",
                "request_seq": None,
                "payload": None,
            }
        },
    }
    assert [call[0] for call in calls] == ["POST", "POST", "GET"]


def test_codex_app_server_jsonl_stream_fails_closed_for_interaction_errors():
    def transport(method, url, headers, payload, timeout_seconds):
        raise AgentAdapterClientError("bridge unavailable")

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=transport,
    )
    output_file = StringIO()

    summary = bridge_codex_app_server_jsonl_stream(
        control_client=control_client,
        input_file=StringIO(
            '{"method":"item/commandExecution/requestApproval","id":"rpc-1",'
            '"params":{"item":{"id":"cmd-1","command":"pytest"}}}\n'
        ),
        output_file=output_file,
        output_format="json-rpc",
    )

    assert summary == {
        "processed": 1,
        "skipped": 0,
        "emitted": 1,
        "errors": 0,
    }
    assert json.loads(output_file.getvalue()) == {
        "id": "rpc-1",
        "result": {
            "agentbridge": {
                "action": "approval_decision",
                "decision": "denied",
                "approve": False,
                "answer": None,
                "reason": "AgentBridge adapter failed closed: bridge unavailable",
                "adapter_item_id": None,
                "interaction_id": None,
                "request_event_id": None,
                "request_seq": None,
                "payload": None,
            }
        },
    }


def test_codex_app_server_jsonl_stream_reports_invalid_lines_without_stopping():
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, payload))
        return {"id": "evt_delta", "seq": 2}

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=transport,
    )
    error_file = StringIO()

    summary = bridge_codex_app_server_jsonl_stream(
        control_client=control_client,
        input_file=StringIO(
            "not json\n"
            '{"method":"item/agentMessage/delta","params":{"delta":"hello"}}\n'
        ),
        output_file=StringIO(),
        error_file=error_file,
    )

    assert summary == {
        "processed": 1,
        "skipped": 0,
        "emitted": 0,
        "errors": 1,
    }
    assert "line 1 failed open" in error_file.getvalue()
    assert calls[0][2]["adapter_event_type"] == "item/agentMessage/delta"


def test_cli_format_response_prints_native_stdout_json(capsys):
    result = main(
        [
            "format-response",
            "--agent",
            "claude",
            "--stdout-json",
            "--response-json",
            json.dumps(
                {
                    "decision": "approved",
                    "adapter_event_type": "PermissionRequest",
                }
            ),
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        }
    }


def test_claude_hook_bridge_waits_for_permission_and_outputs_hook_json():
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, payload))
        if method == "POST":
            return {
                "id": "evt_permission",
                "seq": 3,
                "interaction_id": "int_permission",
                "payload": {"adapter_item_id": "toolu_1"},
            }
        return {
            "responses": [
                {
                    "seq": 4,
                    "request_event_id": "evt_permission",
                    "interaction_id": "int_permission",
                    "adapter_event_type": "PermissionRequest",
                    "ready": True,
                    "decision": "approved",
                    "approve": True,
                }
            ]
        }

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=transport,
    )

    result = handle_claude_hook_payload(
        control_client=control_client,
        hook_payload={
            "session_id": "claude-native-1",
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
            "tool_use_id": "toolu_1",
        },
        poll_interval_seconds=0.01,
    )

    assert result["adapter_event_type"] == "PermissionRequest"
    assert result["idempotency_key"] == "claude-hook:PermissionRequest:toolu_1"
    assert result["stdout_json"] == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": "allow"},
        }
    }
    assert calls[0] == (
        "POST",
        "http://bridge.local/api/v1/sessions/ses_1/agent-adapter/events",
        {
            "agent_type": "claude",
            "adapter_event_type": "PermissionRequest",
            "trace_id": "claude-hook-adapter",
            "schema_version": "claude-hooks.v1",
            "payload": {
                "session_id": "claude-native-1",
                "hook_event_name": "PermissionRequest",
                "tool_name": "Bash",
                "tool_input": {"command": "pytest"},
                "tool_use_id": "toolu_1",
            },
            "idempotency_key": "claude-hook:PermissionRequest:toolu_1",
        },
    )


def test_claude_hook_bridge_reports_non_interaction_hook_without_stdout():
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, payload))
        return {"id": "evt_message", "seq": 9}

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=transport,
    )

    result = handle_claude_hook_payload(
        control_client=control_client,
        hook_payload={
            "session_id": "claude-native-1",
            "hook_event_name": "MessageDisplay",
            "text": "hello",
        },
    )

    assert result["adapter_event_type"] == "MessageDisplay"
    assert result["stdout_json"] is None
    assert calls == [
        (
            "POST",
            "http://bridge.local/api/v1/sessions/ses_1/agent-adapter/events",
            {
                "agent_type": "claude",
                "adapter_event_type": "MessageDisplay",
                "trace_id": "claude-hook-adapter",
                "schema_version": "claude-hooks.v1",
                "payload": {
                    "session_id": "claude-native-1",
                    "hook_event_name": "MessageDisplay",
                    "text": "hello",
                },
                "idempotency_key": result["idempotency_key"],
            },
        )
    ]


def test_claude_hook_payload_maps_pre_tool_use_questions():
    payload = {
        "session_id": "claude-native-1",
        "hook_event_name": "PreToolUse",
        "tool_name": "AskUserQuestion",
        "tool_use_id": "toolu_question",
    }

    assert claude_adapter_event_type_from_hook_payload(payload) == "AskUserQuestion"
    assert (
        claude_hook_idempotency_key("AskUserQuestion", payload)
        == "claude-hook:AskUserQuestion:toolu_question"
    )


def test_claude_hook_failure_defaults_interactions_to_deny():
    stdout_json = claude_hook_failure_stdout_json(
        {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_use_id": "toolu_1",
        },
        "timed out",
    )

    assert stdout_json == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {
                "behavior": "deny",
                "message": "AgentBridge adapter failed closed: timed out",
            },
        }
    }


def test_claude_hook_settings_fragment_uses_exec_form_for_supported_events():
    payload = claude_hook_settings_fragment(
        api_url="http://bridge.local",
        session_id="ses_1",
        device_id="device_1",
        device_key_file="/tmp/agentbridge device.key",
        wait_timeout_seconds=60.0,
        poll_interval_seconds=0.5,
        hook_timeout_seconds=75.0,
    )

    hooks = payload["hooks"]
    assert set(hooks) == {
        "SessionStart",
        "MessageDisplay",
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
        "PostToolUseFailure",
        "Stop",
        "StopFailure",
        "SessionEnd",
    }
    pre_tool_group = hooks["PreToolUse"][0]
    assert pre_tool_group["matcher"] == "*"
    message_group = hooks["MessageDisplay"][0]
    assert "matcher" not in message_group
    handler = pre_tool_group["hooks"][0]
    assert handler == {
        "type": "command",
        "command": "agentbridge-adapter-client",
        "args": [
            "claude-hook",
            "--api-url",
            "http://bridge.local",
            "--session-id",
            "ses_1",
            "--device-id",
            "device_1",
            "--device-key-file",
            "/tmp/agentbridge device.key",
            "--trace-id",
            "claude-hook-adapter",
            "--timeout-seconds",
            "10",
            "--wait-timeout-seconds",
            "60",
            "--poll-interval-seconds",
            "0.5",
        ],
        "timeout": 75,
    }


def test_claude_hook_settings_fragment_includes_file_changed_when_watchers_are_set():
    payload = claude_hook_settings_fragment(
        events=["SessionStart"],
        file_watch_patterns=["pyproject.toml", ".env"],
    )

    hooks = payload["hooks"]
    assert set(hooks) == {"SessionStart", "FileChanged"}
    assert hooks["FileChanged"][0]["matcher"] == "pyproject.toml|.env"


def test_claude_hook_settings_fragment_rejects_direct_secret_values_by_default():
    with pytest.raises(ValueError, match="refusing to embed --device-key"):
        claude_hook_settings_fragment(device_key="secret-value")


def test_claude_hook_settings_write_file_replaces_agentbridge_handlers(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps(
            {
                "theme": "dark",
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {"type": "command", "command": "lint", "args": []},
                                {
                                    "type": "command",
                                    "command": "agentbridge-adapter-client",
                                    "args": ["claude-hook", "--session-id", "old"],
                                },
                            ],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    fragment = claude_hook_settings_fragment(
        events=["PreToolUse"],
        session_id="ses_new",
        hook_timeout_seconds=20,
    )

    merged = write_claude_hook_settings_file(settings_file, fragment)
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))

    assert persisted == merged
    assert merged["theme"] == "dark"
    pre_tool_groups = merged["hooks"]["PreToolUse"]
    assert pre_tool_groups[0]["hooks"] == [
        {"type": "command", "command": "lint", "args": []}
    ]
    assert pre_tool_groups[1]["hooks"][0]["args"] == [
        "claude-hook",
        "--session-id",
        "ses_new",
        "--trace-id",
        "claude-hook-adapter",
        "--timeout-seconds",
        "10",
        "--wait-timeout-seconds",
        "300",
        "--poll-interval-seconds",
        "1",
    ]


def test_cli_claude_hooks_config_prints_settings_fragment(capsys):
    result = main(
        [
            "claude-hooks-config",
            "--session-id",
            "ses_1",
            "--device-id",
            "device_1",
            "--device-key-file",
            "/tmp/device.key",
            "--wait-timeout-seconds",
            "60",
            "--hook-timeout-seconds",
            "65",
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    permission_handler = payload["hooks"]["PermissionRequest"][0]["hooks"][0]
    assert permission_handler["timeout"] == 65
    assert permission_handler["args"] == [
        "claude-hook",
        "--session-id",
        "ses_1",
        "--device-id",
        "device_1",
        "--device-key-file",
        "/tmp/device.key",
        "--trace-id",
        "claude-hook-adapter",
        "--timeout-seconds",
        "10",
        "--wait-timeout-seconds",
        "60",
        "--poll-interval-seconds",
        "1",
    ]


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
