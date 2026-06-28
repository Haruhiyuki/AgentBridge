import json
import sys
from io import BytesIO, StringIO
from urllib.error import HTTPError

import pytest

import agentbridge.agent_adapter_client as adapter_client_module
from agentbridge.agent_adapter_client import (
    AgentAdapterClientConfig,
    AgentAdapterClientError,
    AgentAdapterControlClient,
    ClaudeHookAdapterClient,
    CodexAppServerAdapterClient,
    adapter_response_matches_request,
    bridge_codex_app_server_jsonl_stream,
    bridge_codex_app_server_stdio_proxy,
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


def test_control_client_spools_transient_event_to_offline_outbox(tmp_path):
    outbox_path = tmp_path / "adapter-outbox.jsonl"

    def transport(method, url, headers, payload, timeout_seconds):
        raise AgentAdapterClientError("connection refused")

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(
            base_url="http://bridge.local/",
            session_id="ses_1",
            offline_outbox_path=outbox_path,
        ),
        transport=transport,
    )

    result = control_client.ingest_event(
        agent_type=AgentType.CLAUDE,
        adapter_event_type="MessageDisplay",
        payload={"text": "offline answer"},
        idempotency_key="offline-event-1",
    )

    assert result == {
        "offline_queued": True,
        "outbox_path": str(outbox_path),
        "queued_count": 1,
        "agent_type": "claude",
        "adapter_event_type": "MessageDisplay",
        "trace_id": "agent-adapter-client",
        "schema_version": "claude-hooks.v1",
        "idempotency_key": "offline-event-1",
    }
    queued = [json.loads(line) for line in outbox_path.read_text(encoding="utf-8").splitlines()]
    assert [entry["payload"]["adapter_event_type"] for entry in queued] == [
        "MessageDisplay"
    ]
    assert queued[0]["payload"]["payload"] == {"text": "offline answer"}


def test_control_client_does_not_spool_permanent_event_error(tmp_path):
    outbox_path = tmp_path / "adapter-outbox.jsonl"

    def transport(method, url, headers, payload, timeout_seconds):
        raise AgentAdapterClientError("schema rejected", status_code=400)

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(
            base_url="http://bridge.local/",
            session_id="ses_1",
            offline_outbox_path=outbox_path,
        ),
        transport=transport,
    )

    with pytest.raises(AgentAdapterClientError) as exc_info:
        control_client.ingest_event(
            agent_type=AgentType.CLAUDE,
            adapter_event_type="MessageDisplay",
            payload={"text": "bad schema"},
            idempotency_key="permanent-event-1",
        )

    assert exc_info.value.status_code == 400
    assert not outbox_path.exists()


def test_control_client_flushes_offline_outbox_before_current_event(tmp_path):
    outbox_path = tmp_path / "adapter-outbox.jsonl"

    def failing_transport(method, url, headers, payload, timeout_seconds):
        raise AgentAdapterClientError("connection refused")

    first_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(
            base_url="http://bridge.local",
            session_id="ses_1",
            offline_outbox_path=outbox_path,
        ),
        transport=failing_transport,
    )
    first_client.ingest_event(
        agent_type=AgentType.CLAUDE,
        adapter_event_type="MessageDisplay",
        payload={"text": "first"},
        idempotency_key="offline-first",
    )

    calls = []

    def recovered_transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, payload))
        return {"id": f"evt_{len(calls)}", "type": "assistant.delta"}

    recovered_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(
            base_url="http://bridge.local",
            session_id="ses_1",
            offline_outbox_path=outbox_path,
        ),
        transport=recovered_transport,
    )
    result = recovered_client.ingest_event(
        agent_type=AgentType.CLAUDE,
        adapter_event_type="Stop",
        payload={"status": "done"},
        idempotency_key="offline-second",
    )

    assert result == {"id": "evt_2", "type": "assistant.delta"}
    assert [call[2]["idempotency_key"] for call in calls] == [
        "offline-first",
        "offline-second",
    ]
    assert not outbox_path.exists()


def test_emit_and_wait_fails_closed_when_interaction_event_is_queued_offline(tmp_path):
    outbox_path = tmp_path / "adapter-outbox.jsonl"

    def transport(method, url, headers, payload, timeout_seconds):
        raise AgentAdapterClientError("connection refused")

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(
            base_url="http://bridge.local",
            session_id="ses_1",
            offline_outbox_path=outbox_path,
        ),
        transport=transport,
    )
    client = CodexAppServerAdapterClient(control_client)

    with pytest.raises(AgentAdapterClientError) as exc_info:
        client.emit_and_wait(
            "item/tool/requestUserInput",
            {"item": {"id": "question-1"}},
            idempotency_key="offline-question",
            poll_interval_seconds=0.01,
        )

    assert "queued offline" in exc_info.value.message
    assert exc_info.value.payload["offline_queued"] is True
    assert outbox_path.exists()


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
        "codex.app_server.provider_schema_snapshot",
        "codex.app_server.stdio_proxy",
    ]
    assert payload["warnings"] == []
    assert payload["schema_snapshot"]["schema_version"] == "codex-app-server.v1"
    compatibility = payload["schema_snapshot"]["compatibility"]
    assert compatibility["verification_status"] == "provider_snapshot_verified"
    assert compatibility["provider_version_matrix"]["verified_provider_versions"][0][
        "provider_version"
    ] == "0.141.0"
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
    assert payload["capabilities"] == [
        "agentbridge.event_ingest",
        "agentbridge.response_poll",
        "claude.hooks",
        "claude.hooks.provider_snapshot",
    ]
    assert payload["schema_snapshot"]["adapter"] == "claude_hooks"
    assert payload["schema_snapshot"]["compatibility"]["verification_status"] == (
        "provider_snapshot_verified"
    )


def test_cli_schemas_prints_selected_agent_matrix(capsys):
    result = main(["schemas", "--agent", "codex"])

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["agent_type"] == "codex"
    assert payload["default_schema_version"] == "codex-app-server.v1"
    assert payload["compatibility_matrices"][0]["verification_status"] == (
        "provider_snapshot_verified"
    )
    assert payload["schemas"][0]["response_contract"]["pending_decision"] == "pending"
    assert payload["schemas"][0]["compatibility"]["provider_version_matrix"][
        "verified_provider_versions"
    ][0]["provider_version_text"] == "codex-cli 0.141.0"


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
    assert payload["provider_schema_snapshot"]["captured_from"][
        "claude_code_version"
    ] == "2.1.193 (Claude Code)"
    assert "QuestionRequested" in payload["provider_schema_coverage"][
        "legacy_alias_event_types"
    ]


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


def test_cli_parser_accepts_flush_outbox_options(tmp_path):
    outbox_path = tmp_path / "adapter-outbox.jsonl"

    args = build_parser().parse_args(
        [
            "flush-outbox",
            "--session-id",
            "ses_1",
            "--offline-outbox",
            str(outbox_path),
        ]
    )

    assert args.command == "flush-outbox"
    assert args.session_id == "ses_1"
    assert args.offline_outbox == outbox_path


def test_cli_flush_outbox_posts_cached_events(monkeypatch, tmp_path, capsys):
    outbox_path = tmp_path / "adapter-outbox.jsonl"
    cached_payload = {
        "agent_type": "claude",
        "adapter_event_type": "MessageDisplay",
        "trace_id": "cached-adapter-event",
        "schema_version": "claude-hooks.v1",
        "payload": {"text": "cached answer"},
        "idempotency_key": "cached-adapter-event-1",
    }
    outbox_path.write_text(
        json.dumps(
            {
                "schema_version": "agentbridge.adapter_outbox.v1",
                "payload": cached_payload,
                "enqueued_at_monotonic": 1.0,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, payload))
        return {"id": "evt_cached", "type": "assistant.delta"}

    monkeypatch.setattr(adapter_client_module, "urllib_json_transport", transport)

    result = main(
        [
            "flush-outbox",
            "--api-url",
            "http://bridge.local",
            "--session-id",
            "ses_1",
            "--offline-outbox",
            str(outbox_path),
        ]
    )

    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"flushed": 1, "outbox_path": str(outbox_path)}
    assert calls == [
        (
            "POST",
            "http://bridge.local/api/v1/sessions/ses_1/agent-adapter/events",
            cached_payload,
        )
    ]
    assert not outbox_path.exists()


def test_cli_flush_outbox_without_config_is_noop(capsys):
    result = main(["flush-outbox", "--session-id", "ses_1"])

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "flushed": 0,
        "outbox_path": None,
    }


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


def test_cli_parser_accepts_codex_app_server_proxy_command():
    args = build_parser().parse_args(
        [
            "codex-app-server-proxy",
            "--session-id",
            "ses_1",
            "--bridge-output-file",
            "bridge.jsonl",
            "--bridge-output-format",
            "json-rpc",
            "--inject-responses",
            "--forward-injected-requests",
            "--restart-policy",
            "on-failure",
            "--max-restarts",
            "2",
            "--restart-delay-seconds",
            "0.05",
            "--restart-min-uptime-seconds",
            "1.5",
            "--health-output-file",
            "health.jsonl",
            "--health-interval-seconds",
            "0.1",
            "--",
            "codex",
            "app-server",
            "--listen",
            "stdio://",
        ]
    )

    assert args.command == "codex-app-server-proxy"
    assert args.session_id == "ses_1"
    assert str(args.bridge_output_file) == "bridge.jsonl"
    assert args.bridge_output_format == "json-rpc"
    assert args.inject_responses is True
    assert args.forward_injected_requests is True
    assert args.restart_policy == "on-failure"
    assert args.max_restarts == 2
    assert args.restart_delay_seconds == 0.05
    assert args.restart_min_uptime_seconds == 1.5
    assert str(args.health_output_file) == "health.jsonl"
    assert args.health_interval_seconds == 0.1
    assert args.app_server_command == [
        "--",
        "codex",
        "app-server",
        "--listen",
        "stdio://",
    ]


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


def _ask_question_payload(answer: str, questions: list[dict]) -> dict:
    return {
        "decision": "answered",
        "answer": answer,
        "adapter_event_type": "AskUserQuestion",
        "request_payload": {"raw_event": {"tool_input": {"questions": questions}}},
    }


def test_multi_question_answer_maps_each_letter_to_its_own_option_label():
    """『1A 2B 3C』应按题分别映射到各题真实选项标签，而非把同一答案套给所有问题。"""
    questions = [
        {
            "question": "要补充什么?",
            "options": [{"label": "逐小时预报"}, {"label": "生活指数"}, {"label": "不用补充"}],
            "multiSelect": True,
        },
        {
            "question": "什么格式?",
            "options": [{"label": "保持Markdown"}, {"label": "纯文本TXT"}],
        },
        {
            "question": "要定期更新吗?",
            "options": [{"label": "不用"}, {"label": "每天早上"}, {"label": "出行前提醒"}],
        },
    ]
    formatted = format_adapter_response_for_agent(
        AgentType.CLAUDE, _ask_question_payload("1A 2B 3C", questions)
    )
    answers = formatted["stdout_json"]["hookSpecificOutput"]["updatedInput"]["answers"]
    assert answers == {
        "要补充什么?": "逐小时预报",
        "什么格式?": "纯文本TXT",
        "要定期更新吗?": "出行前提醒",
    }


def test_multi_select_question_joins_multiple_chosen_labels():
    """同题多选『1AC』应解析成该题的两个选项标签，用「, 」连接。"""
    questions = [
        {
            "question": "要补充什么?",
            "options": [{"label": "逐小时预报"}, {"label": "生活指数"}, {"label": "空气质量"}],
            "multiSelect": True,
        },
        {"question": "什么格式?", "options": [{"label": "Markdown"}, {"label": "TXT"}]},
    ]
    formatted = format_adapter_response_for_agent(
        AgentType.CLAUDE, _ask_question_payload("1AC 2B", questions)
    )
    answers = formatted["stdout_json"]["hookSpecificOutput"]["updatedInput"]["answers"]
    assert answers == {"要补充什么?": "逐小时预报, 空气质量", "什么格式?": "TXT"}


def test_single_question_accepts_bare_letter_and_option_text():
    questions = [
        {"question": "选哪个环境?", "options": [{"label": "staging"}, {"label": "prod"}]}
    ]
    bare_letter = format_adapter_response_for_agent(
        AgentType.CLAUDE, _ask_question_payload("B", questions)
    )["stdout_json"]["hookSpecificOutput"]["updatedInput"]["answers"]
    assert bare_letter == {"选哪个环境?": "prod"}

    by_text = format_adapter_response_for_agent(
        AgentType.CLAUDE, _ask_question_payload("staging", questions)
    )["stdout_json"]["hookSpecificOutput"]["updatedInput"]["answers"]
    assert by_text == {"选哪个环境?": "staging"}


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


def test_codex_app_server_message_bridge_waits_for_provider_user_input_request():
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, payload))
        if method == "POST":
            return {
                "id": "evt_question",
                "seq": 8,
                "interaction_id": "int_question",
                "payload": {"adapter_item_id": "question-1"},
            }
        return {
            "responses": [
                {
                    "seq": 9,
                    "request_event_id": "evt_question",
                    "interaction_id": "int_question",
                    "adapter_event_type": "item/tool/requestUserInput",
                    "adapter_item_id": "question-1",
                    "ready": True,
                    "decision": "answered",
                    "answer": "staging",
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
            "method": "item/tool/requestUserInput",
            "id": "rpc-question",
            "params": {
                "itemId": "question-1",
                "questions": [{"id": "env", "question": "Which environment?"}],
            },
        },
        poll_interval_seconds=0.01,
    )

    assert result["action"]["action"] == "user_input_response"
    assert result["action"]["answer"] == "staging"
    assert result["json_rpc_response"] == {
        "id": "rpc-question",
        "result": {"agentbridge": result["action"]},
    }
    assert calls[0][2]["adapter_event_type"] == "item/tool/requestUserInput"
    assert calls[0][2]["payload"]["json_rpc_method"] == "item/tool/requestUserInput"
    assert calls[0][2]["idempotency_key"] == (
        "codex-app-server:item/tool/requestUserInput:rpc:rpc-question"
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


def test_codex_app_server_stdio_proxy_forwards_stdout_and_collects_events(tmp_path):
    script = tmp_path / "fake_codex_app_server.py"
    script.write_text(
        "\n".join(
            [
                "import json",
                "messages = [",
                "    {'id': 1, 'result': {'thread': {'id': 'thr_1'}}},",
                "    {'method': 'item/agentMessage/delta', 'params': {'delta': 'hello'}},",
                "    {",
                "        'method': 'item/commandExecution/requestApproval',",
                "        'id': 42,",
                "        'params': {",
                "            'item': {'id': 'cmd-1', 'command': 'pytest'},",
                "            'reason': 'Run tests',",
                "        },",
                "    },",
                "]",
                "for message in messages:",
                "    print(json.dumps(message), flush=True)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
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
    downstream_output = StringIO()
    bridge_output = StringIO()
    error_file = StringIO()

    summary = bridge_codex_app_server_stdio_proxy(
        control_client=control_client,
        command_args=[sys.executable, str(script)],
        upstream_input=StringIO(),
        downstream_output=downstream_output,
        bridge_output=bridge_output,
        error_file=error_file,
        poll_interval_seconds=0.01,
    )

    assert summary == {
        "command": [sys.executable, str(script)],
        "return_code": 0,
        "attempts": 1,
        "restarts": 0,
        "restart_policy": "never",
        "unhealthy_exits": 0,
        "stdin_write_errors": 0,
        "processed": 2,
        "skipped": 1,
        "emitted": 1,
        "injected": 0,
        "suppressed": 0,
        "errors": 0,
    }
    forwarded_lines = downstream_output.getvalue().splitlines()
    assert [json.loads(line).get("method") for line in forwarded_lines] == [
        None,
        "item/agentMessage/delta",
        "item/commandExecution/requestApproval",
    ]
    assert json.loads(bridge_output.getvalue()) == {
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
    assert error_file.getvalue() == ""
    assert [call[0] for call in calls] == ["POST", "POST", "GET"]


def test_codex_app_server_stdio_proxy_injects_agentbridge_response(tmp_path):
    script = tmp_path / "fake_codex_app_server.py"
    script.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "request = {",
                "    'method': 'item/commandExecution/requestApproval',",
                "    'id': 42,",
                "    'params': {",
                "        'item': {'id': 'cmd-1', 'command': 'pytest'},",
                "        'reason': 'Run tests',",
                "    },",
                "}",
                "print(json.dumps(request), flush=True)",
                "response = sys.stdin.readline()",
                "print(response.strip(), file=sys.stderr, flush=True)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
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
                }
            ]
        }

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=transport,
    )
    downstream_output = StringIO()
    bridge_output = StringIO()
    error_file = StringIO()

    summary = bridge_codex_app_server_stdio_proxy(
        control_client=control_client,
        command_args=[sys.executable, str(script)],
        upstream_input=StringIO(),
        downstream_output=downstream_output,
        bridge_output=bridge_output,
        error_file=error_file,
        poll_interval_seconds=0.01,
        inject_responses=True,
    )

    expected_response = {
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
    assert summary == {
        "command": [sys.executable, str(script)],
        "return_code": 0,
        "attempts": 1,
        "restarts": 0,
        "restart_policy": "never",
        "unhealthy_exits": 0,
        "stdin_write_errors": 0,
        "processed": 1,
        "skipped": 0,
        "emitted": 1,
        "injected": 1,
        "suppressed": 1,
        "errors": 0,
    }
    assert downstream_output.getvalue() == ""
    assert json.loads(bridge_output.getvalue()) == expected_response
    assert json.loads(error_file.getvalue()) == expected_response
    assert [call[0] for call in calls] == ["POST", "GET"]


def test_codex_app_server_stdio_proxy_restarts_failed_child(tmp_path):
    script = tmp_path / "fake_codex_app_server.py"
    state_file = tmp_path / "runs.txt"
    script.write_text(
        "\n".join(
            [
                "import json",
                "import sys",
                "from pathlib import Path",
                f"state_file = Path({str(state_file)!r})",
                "try:",
                "    count = int(state_file.read_text(encoding='utf-8'))",
                "except FileNotFoundError:",
                "    count = 0",
                "count += 1",
                "state_file.write_text(str(count), encoding='utf-8')",
                "print(",
                "    json.dumps({",
                "        'method': 'item/agentMessage/delta',",
                "        'params': {'delta': f'run-{count}'},",
                "    }),",
                "    flush=True,",
                ")",
                "if count == 1:",
                "    sys.exit(7)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, payload))
        return {"id": f"evt_{len(calls)}", "seq": len(calls)}

    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=transport,
    )
    downstream_output = StringIO()
    error_file = StringIO()
    health_output = StringIO()

    summary = bridge_codex_app_server_stdio_proxy(
        control_client=control_client,
        command_args=[sys.executable, str(script)],
        upstream_input=StringIO(),
        downstream_output=downstream_output,
        error_file=error_file,
        health_output=health_output,
        restart_policy="on-failure",
        max_restarts=1,
        restart_delay_seconds=0,
        restart_min_uptime_seconds=60,
    )

    assert summary == {
        "command": [sys.executable, str(script)],
        "return_code": 0,
        "attempts": 2,
        "restarts": 1,
        "restart_policy": "on-failure",
        "unhealthy_exits": 2,
        "stdin_write_errors": 0,
        "processed": 2,
        "skipped": 0,
        "emitted": 0,
        "injected": 0,
        "suppressed": 0,
        "errors": 0,
        "health_events": 6,
        "health_write_errors": 0,
    }
    assert state_file.read_text(encoding="utf-8") == "2"
    forwarded_lines = [json.loads(line) for line in downstream_output.getvalue().splitlines()]
    assert [line["params"]["delta"] for line in forwarded_lines] == ["run-1", "run-2"]
    assert error_file.getvalue() == ""
    assert [call[2]["payload"]["delta"] for call in calls] == ["run-1", "run-2"]
    assert [json.loads(line)["status"] for line in health_output.getvalue().splitlines()] == [
        "started",
        "exited",
        "restarting",
        "started",
        "exited",
        "stopped",
    ]


def test_codex_app_server_stdio_proxy_emits_health_heartbeats(tmp_path):
    script = tmp_path / "fake_codex_app_server.py"
    script.write_text(
        "\n".join(
            [
                "import time",
                "time.sleep(0.2)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    control_client = AgentAdapterControlClient(
        AgentAdapterClientConfig(base_url="http://bridge.local", session_id="ses_1"),
        transport=lambda method, url, headers, payload, timeout_seconds: {},
    )
    health_output = StringIO()

    summary = bridge_codex_app_server_stdio_proxy(
        control_client=control_client,
        command_args=[sys.executable, str(script)],
        upstream_input=StringIO(),
        downstream_output=StringIO(),
        health_output=health_output,
        health_interval_seconds=0.05,
    )

    health_events = [
        json.loads(line)
        for line in health_output.getvalue().splitlines()
    ]
    statuses = [event["status"] for event in health_events]
    assert summary["health_events"] == len(health_events)
    assert summary["health_write_errors"] == 0
    assert statuses[0] == "started"
    assert "running" in statuses
    assert statuses[-2:] == ["exited", "stopped"]
    assert all(
        event["type"] == "agentbridge.codex_app_server_proxy.health"
        for event in health_events
    )
    assert health_events[0]["attempt"] == 1
    assert health_events[-2]["return_code"] == 0
    assert health_events[-1]["attempts"] == 1


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


def test_claude_hook_observes_interaction_without_blocking_native_ui():
    """交互类事件（PermissionRequest / AskUserQuestion / 计划）只观察、不拦截：单次 emit、返回
    空 stdout，让 Claude 显示原生交互 UI——绝不阻塞或替答（否则会压住本地真人的原生选择器）。"""
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, payload))
        return {"id": "evt_permission", "seq": 3, "interaction_id": "int_permission"}

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
    )

    assert result["adapter_event_type"] == "PermissionRequest"
    assert result["idempotency_key"] == "claude-hook:PermissionRequest:toolu_1"
    # 关键：不替答 → Claude 显示原生交互 UI。
    assert result["stdout_json"] is None
    # 只发一次 emit（POST），不再轮询等待响应/阻塞。
    assert len(calls) == 1
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
