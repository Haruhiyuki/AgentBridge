from __future__ import annotations

CLAUDE_HOOKS_PROVIDER_SCHEMA_SNAPSHOT_V1: dict[str, object] = {
    "provider": "anthropic.claude_code_hooks",
    "schema_version": "claude-hooks.v1",
    "captured_at": "2026-06-26",
    "captured_from": {
        "claude_code_version": "2.1.193 (Claude Code)",
        "command": "claude --version",
        "documentation_source": "https://code.claude.com/docs/en/hooks",
        "documentation_checked_at": "2026-06-26",
    },
    "wire_contract": {
        "command_hook_input": "stdin_json",
        "command_hook_output": "stdout_json_or_exit_code",
        "http_hook_input": "application_json_post_body",
        "http_hook_output": "json_response_body",
        "hook_specific_output_field": "hookSpecificOutput",
        "hook_event_name_field": "hook_event_name",
    },
    "projection_scope": "agentbridge_supported_hook_events_from_provider_reference",
    "provider_event_counts": {
        "hook_events": 11,
        "tool_matchers": 1,
        "legacy_aliases": 2,
    },
    "hook_events": {
        "SessionStart": {
            "semantic_hint": "agent_session_started",
            "cadence": "session_lifecycle",
        },
        "MessageDisplay": {
            "semantic_hint": "assistant_delta",
            "cadence": "assistant_stream",
            "output_fields": ["displayContent"],
        },
        "PreToolUse": {
            "semantic_hint": "tool_started_or_policy_gate",
            "cadence": "tool_call",
            "matcher_field": "tool_name",
        },
        "PostToolUse": {
            "semantic_hint": "tool_completed",
            "cadence": "tool_call",
            "matcher_field": "tool_name",
        },
        "PostToolUseFailure": {
            "semantic_hint": "tool_failed",
            "cadence": "tool_call",
            "matcher_field": "tool_name",
        },
        "FileChanged": {
            "semantic_hint": "file_change_completed",
            "cadence": "filesystem_watch",
            "matcher_field": "file_path",
        },
        "PermissionRequest": {
            "semantic_hint": "approval_requested",
            "cadence": "permission_dialog",
            "matcher_field": "tool_name",
            "output_fields": ["hookSpecificOutput.permissionDecision"],
        },
        "Stop": {
            "semantic_hint": "turn_completed",
            "cadence": "turn_lifecycle",
        },
        "StopFailure": {
            "semantic_hint": "turn_failed",
            "cadence": "turn_lifecycle",
        },
        "Notification": {
            "semantic_hint": "operator_notification",
            "cadence": "async_notification",
        },
        "SessionEnd": {
            "semantic_hint": "agent_session_ended",
            "cadence": "session_lifecycle",
        },
    },
    "tool_matchers": {
        "AskUserQuestion": {
            "provider_hook_event": "PermissionRequest",
            "semantic_hint": "question_requested",
            "reason": "Tool-name matcher projected as a stable AgentBridge adapter event.",
        },
    },
    "legacy_aliases": {
        "QuestionRequested": {
            "canonical_provider_method": "AskUserQuestion",
            "reason": "kept for pre-provider-snapshot AgentBridge adapter compatibility",
        },
        "PlanRequested": {
            "canonical_provider_method": "PermissionRequest",
            "reason": "kept for pre-provider-snapshot AgentBridge adapter compatibility",
        },
    },
}

CODEX_APP_SERVER_PROVIDER_SCHEMA_SNAPSHOT_V1: dict[str, object] = {
    "provider": "openai.codex_app_server",
    "schema_version": "codex-app-server.v1",
    "captured_at": "2026-06-26",
    "captured_from": {
        "codex_cli_version": "codex-cli 0.141.0",
        "command": "codex app-server generate-json-schema --out <dir>",
        "documentation_source": "https://developers.openai.com/codex/app-server",
        "manual_source": "https://developers.openai.com/codex/codex-manual.md",
    },
    "bundle": {
        "file_count": 263,
        "total_size_bytes": 2610838,
        "root_bundle_sha256": (
            "e43df1995300d244ce9f91e797362f7418010d11f06588766ab1157d9688905c"
        ),
        "v2_bundle_sha256": (
            "e0f02c45218b2f9f8f24502701500feb4609249a9707d6083b393c2a40d4c30f"
        ),
    },
    "wire_contract": {
        "jsonrpc_header": "omitted",
        "request_required": ["id", "method"],
        "response_required": ["id"],
        "response_outcome_fields": ["result", "error"],
        "notification_required": ["method"],
        "request_id_types": ["string", "integer"],
        "transports": ["stdio", "websocket", "unix_socket", "off"],
        "stdio_encoding": "newline_delimited_json",
    },
    "projection_scope": "adapter_supported_methods_from_provider_bundle",
    "provider_method_counts": {
        "server_requests": 10,
        "server_notifications": 66,
    },
    "schema_hashes": {
        "JSONRPCMessage.json": (
            "7b819754ee909272f46d45dbd51bc9eb7bab9861905ac1195f1fb5afd7ff5e83"
        ),
        "JSONRPCRequest.json": (
            "a174dbc58be007346f5a10fe2f8c8f8c14ccb36191dc1ad0fdd8ce1828f8db1c"
        ),
        "JSONRPCResponse.json": (
            "94ecf5e81bdbc2af858afad0044b95c7fb4decf77d7fd7d6321324dad79eef57"
        ),
        "ServerNotification.json": (
            "bd8988dda7475435b009d96f75ecc8bbd2acdc1a695ea2d1909a92cd3e703638"
        ),
        "ServerRequest.json": (
            "c2c02b5a0260cc585d61a79891a7d2ff55d6b3dcfc6205e86c66be99c66ccf3f"
        ),
    },
    "server_requests": {
        "item/commandExecution/requestApproval": {
            "params_schema": "CommandExecutionRequestApprovalParams.json",
            "response_schema": "CommandExecutionRequestApprovalResponse.json",
            "required_params": ["itemId", "startedAtMs", "threadId", "turnId"],
            "params_schema_sha256": (
                "0de58b85c33f274097e301eb3ead612563a363d69bd8466fb4a1620882268049"
            ),
            "response_schema_sha256": (
                "42010a48dd9ad989171728c30338e1ff8144c31bd33921cbfb5608fd6c85a3b5"
            ),
        },
        "item/fileChange/requestApproval": {
            "params_schema": "FileChangeRequestApprovalParams.json",
            "response_schema": "FileChangeRequestApprovalResponse.json",
            "required_params": ["itemId", "startedAtMs", "threadId", "turnId"],
            "params_schema_sha256": (
                "7b465f7c5671adffdc5c339f50799860950307456e2a2b52c5ce1d3018f4babd"
            ),
            "response_schema_sha256": (
                "7ccbd29e5f8840c7c8aa96c5c3b6d52bc71ec5c5d7e1ad05ab958afd44c0c94c"
            ),
        },
        "item/tool/requestUserInput": {
            "params_schema": "ToolRequestUserInputParams.json",
            "response_schema": "ToolRequestUserInputResponse.json",
            "required_params": ["itemId", "questions", "threadId", "turnId"],
            "experimental": True,
            "params_schema_sha256": (
                "21e569e32c05d51c1ee5e587730c182b911ede97a4df267f6e4ef24e1717f34e"
            ),
            "response_schema_sha256": (
                "14ede53c2e51b289fb3c80903292d4b0f0b387eae217dbb257c201b2b7c65bf1"
            ),
        },
    },
    "server_notifications": {
        "error": {
            "semantic_hint": "turn_error",
            "required_params": ["error", "threadId", "turnId", "willRetry"],
        },
        "item/agentMessage/delta": {"semantic_hint": "assistant_delta"},
        "item/commandExecution/outputDelta": {"semantic_hint": "tool_output_delta"},
        "item/completed": {"semantic_hint": "item_completed"},
        "item/started": {"semantic_hint": "item_started"},
        "turn/completed": {"semantic_hint": "turn_completed"},
        "turn/diff/updated": {"semantic_hint": "diff_updated"},
        "turn/plan/updated": {"semantic_hint": "plan_updated"},
    },
    "legacy_aliases": {
        "tool/requestUserInput": {
            "canonical_provider_method": "item/tool/requestUserInput",
            "reason": "kept for pre-provider-snapshot AgentBridge adapter compatibility",
        },
        "turn/failed": {
            "canonical_provider_method": "error",
            "reason": "kept for pre-provider-snapshot AgentBridge adapter compatibility",
        },
    },
}


def provider_schema_snapshot_for(
    *,
    agent_type_value: str,
    schema_version: str,
) -> dict[str, object] | None:
    if agent_type_value == "claude" and schema_version == "claude-hooks.v1":
        return CLAUDE_HOOKS_PROVIDER_SCHEMA_SNAPSHOT_V1
    if agent_type_value == "codex" and schema_version == "codex-app-server.v1":
        return CODEX_APP_SERVER_PROVIDER_SCHEMA_SNAPSHOT_V1
    return None
