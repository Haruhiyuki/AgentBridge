import pytest

from agentbridge.agent_adapter_events import (
    adapter_provider_version_verification,
    adapter_response_frames_from_events,
    adapter_schema_behavior_matrix_for,
    adapter_schema_snapshot_for,
    normalize_agent_adapter_event,
    validate_adapter_schema_version,
    validate_agent_adapter_event_context,
)
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import AgentBridgeError, AgentType, SemanticEventSource


def test_normalizes_claude_message_display_to_assistant_delta():
    normalized = normalize_agent_adapter_event(
        agent_type=AgentType.CLAUDE,
        adapter_event_type="MessageDisplay",
        schema_version="claude-hooks.v1",
        payload={"text": "hello from Claude", "session_id": "native-session"},
    )

    assert normalized.event_type == "assistant.delta"
    assert normalized.payload["agent_type"] == "claude"
    assert normalized.payload["adapter"] == "claude_hooks"
    assert normalized.payload["adapter_event_type"] == "MessageDisplay"
    assert normalized.payload["schema_version"] == "claude-hooks.v1"
    assert normalized.payload["text"] == "hello from Claude"
    assert normalized.payload["raw_event"] == {
        "text": "hello from Claude",
        "session_id": "native-session",
    }


def test_normalizes_codex_approval_request_to_approval_event():
    normalized = normalize_agent_adapter_event(
        agent_type=AgentType.CODEX,
        adapter_event_type="item/commandExecution/requestApproval",
        payload={
            "item": {"id": "cmd-1", "command": "pytest"},
            "reason": "Run project tests",
            "riskLevel": "high",
        },
    )

    assert normalized.event_type == "approval.requested"
    assert normalized.payload["agent_type"] == "codex"
    assert normalized.payload["adapter"] == "codex_app_server"
    assert normalized.payload["adapter_event_type"] == (
        "item/commandExecution/requestApproval"
    )
    assert normalized.payload["prompt"] == "Run project tests"
    assert normalized.payload["risk_level"] == "high"
    assert normalized.payload["tool_name"] == "pytest"
    assert normalized.payload["adapter_item_id"] == "cmd-1"


def test_normalizes_provider_codex_user_input_request_to_question_event():
    normalized = normalize_agent_adapter_event(
        agent_type=AgentType.CODEX,
        adapter_event_type="item/tool/requestUserInput",
        payload={
            "itemId": "question-1",
            "questions": [
                {
                    "id": "environment",
                    "question": "Which environment?",
                    "options": [{"label": "staging", "description": "Use staging"}],
                }
            ],
        },
        schema_version="codex-app-server.v1",
    )

    assert normalized.event_type == "question.requested"
    assert normalized.payload["adapter_item_id"] == "question-1"
    assert normalized.payload["prompt"] == "item/tool/requestUserInput"


def test_rejects_unknown_adapter_event_type():
    with pytest.raises(AgentBridgeError) as exc_info:
        normalize_agent_adapter_event(
            agent_type=AgentType.CLAUDE,
            adapter_event_type="UnknownHook",
            payload={},
        )

    assert exc_info.value.code == "COMMAND_ARGUMENT_INVALID"
    assert exc_info.value.details == {
        "agent_type": "claude",
        "adapter_event_type": "UnknownHook",
    }


def test_validates_adapter_schema_and_session_agent_match():
    assert (
        validate_agent_adapter_event_context(
            session_agent_type=AgentType.CLAUDE,
            agent_type=AgentType.CLAUDE,
            schema_version="claude-hooks.v1",
        )
        == "claude-hooks.v1"
    )

    with pytest.raises(AgentBridgeError) as mismatch:
        validate_agent_adapter_event_context(
            session_agent_type=AgentType.CLAUDE,
            agent_type=AgentType.CODEX,
            schema_version="codex-app-server.v1",
        )
    with pytest.raises(AgentBridgeError) as unsupported:
        validate_adapter_schema_version(
            agent_type=AgentType.CODEX,
            schema_version="codex-app-server.v999",
        )

    assert mismatch.value.details == {
        "session_agent_type": "claude",
        "agent_type": "codex",
    }
    assert unsupported.value.details == {
        "agent_type": "codex",
        "schema_version": "codex-app-server.v999",
        "supported_schema_versions": ["codex-app-server.v1"],
    }


def test_adapter_schema_snapshot_describes_versioned_mapping_and_extractors():
    snapshot = adapter_schema_snapshot_for(AgentType.CLAUDE, "claude-hooks.v1")

    assert snapshot["protocol"] == "agentbridge.adapter.v1"
    assert snapshot["agent_type"] == "claude"
    assert snapshot["adapter"] == "claude_hooks"
    assert snapshot["schema_version"] == "claude-hooks.v1"
    assert {
        "adapter_event_type": "PermissionRequest",
        "semantic_event_type": "approval.requested",
        "interaction_request": True,
    } in snapshot["adapter_event_types"]
    assert "approval.requested" in snapshot["interaction_request_semantic_types"]
    assert snapshot["payload_extractors"]["text_fields"] == [
        "text",
        "delta",
        "message",
        "content",
        "output",
    ]
    assert snapshot["normalization"]["raw_event_policy"] == "preserve_under_raw_event"
    assert snapshot["response_contract"]["matching_keys"] == [
        "request_event_id",
        "interaction_id",
        "adapter_item_id",
    ]
    assert snapshot["response_application"] == {
        "format": "claude.hooks.command_stdout.v1",
        "approval_events": ["PermissionRequest", "PreToolUse"],
        "question_events": [
            "AskUserQuestion",
            "QuestionRequested",
            "PlanRequested",
        ],
        "approval_output": "hookSpecificOutput",
        "question_output": "hookSpecificOutput.updatedInput",
    }
    compatibility = snapshot["compatibility"]
    assert compatibility["verification_status"] == "provider_snapshot_verified"
    assert compatibility["provider_version_matrix"]["verified_provider_versions"][0][
        "provider_version_text"
    ] == "2.1.193 (Claude Code)"
    provider_snapshot = snapshot["provider_schema_snapshot"]
    assert provider_snapshot["captured_from"]["claude_code_version"] == (
        "2.1.193 (Claude Code)"
    )
    assert "MessageDisplay" in provider_snapshot["hook_events"]
    assert provider_snapshot["tool_matchers"]["AskUserQuestion"][
        "provider_hook_event"
    ] == "PermissionRequest"
    coverage = snapshot["provider_schema_coverage"]
    assert "PermissionRequest" in coverage["verified_adapter_event_types"]
    assert "AskUserQuestion" in coverage["verified_adapter_event_types"]
    assert "QuestionRequested" in coverage["legacy_alias_event_types"]
    assert "PlanRequested" in coverage["legacy_alias_event_types"]
    assert coverage["unverified_adapter_event_types"] == []


def test_adapter_provider_version_verification_accepts_claude_snapshot_version():
    verification = adapter_provider_version_verification(
        agent_type=AgentType.CLAUDE,
        schema_version="claude-hooks.v1",
        provider_version_text="Claude Code 2.1.193",
    )

    assert verification["status"] == "verified"
    assert verification["provider_version"] == "2.1.193"
    assert verification["matched_provider_version"]["provider_version_text"] == (
        "2.1.193 (Claude Code)"
    )


def test_adapter_schema_behavior_matrix_lists_supported_versions():
    matrix = adapter_schema_behavior_matrix_for(AgentType.CODEX)

    assert matrix["agent_type"] == "codex"
    assert matrix["adapter"] == "codex_app_server"
    assert matrix["default_schema_version"] == "codex-app-server.v1"
    assert matrix["supported_schema_versions"] == ["codex-app-server.v1"]
    assert matrix["schemas"][0]["schema_version"] == "codex-app-server.v1"
    assert {
        "adapter_event_type": "item/commandExecution/requestApproval",
        "semantic_event_type": "approval.requested",
        "interaction_request": True,
    } in matrix["schemas"][0]["adapter_event_types"]
    assert matrix["schemas"][0]["response_application"]["json_rpc_response_format"] == (
        "codex.app_server.json_rpc_response.v1"
    )
    assert (
        matrix["schemas"][0]["response_application"]["json_rpc_result_path"]
        == "result.agentbridge"
    )
    provider_snapshot = matrix["schemas"][0]["provider_schema_snapshot"]
    assert provider_snapshot["captured_from"]["codex_cli_version"] == "codex-cli 0.141.0"
    assert provider_snapshot["server_requests"]["item/tool/requestUserInput"][
        "required_params"
    ] == ["itemId", "questions", "threadId", "turnId"]
    coverage = matrix["schemas"][0]["provider_schema_coverage"]
    assert "item/tool/requestUserInput" in coverage["verified_adapter_event_types"]
    assert "tool/requestUserInput" in coverage["legacy_alias_event_types"]
    assert "turn/failed" in coverage["legacy_alias_event_types"]
    assert coverage["unverified_adapter_event_types"] == []


def test_filters_adapter_response_frames_to_adapter_originated_interactions():
    control = ControlPlane()

    adapter_request = control.emit_event(
        event_type="question.requested",
        source=SemanticEventSource.AGENT_ADAPTER,
        trace_id="adapter-request",
        session_id="ses_1",
        interaction_id="int_adapter",
        payload={
            "adapter": "codex_app_server",
            "agent_type": "codex",
            "adapter_event_type": "tool/requestUserInput",
            "adapter_item_id": "question-1",
        },
    )
    response = control.emit_event(
        event_type="interaction.answered",
        source=SemanticEventSource.CONTROL_PLANE,
        trace_id="adapter-answer",
        session_id="ses_1",
        interaction_id="int_adapter",
        payload={"answer": "staging", "status": "resolved"},
    )
    control.emit_event(
        event_type="interaction.answered",
        source=SemanticEventSource.CONTROL_PLANE,
        trace_id="non-adapter-answer",
        session_id="ses_1",
        interaction_id="int_other",
        payload={"answer": "ignored", "status": "resolved"},
    )

    frames = adapter_response_frames_from_events(
        control.repository.list_events(session_id="ses_1")
    )

    assert len(frames) == 1
    assert frames[0]["seq"] == response.seq
    assert frames[0]["request_seq"] == adapter_request.seq
    assert frames[0]["decision"] == "answered"
    assert frames[0]["ready"] is True
    assert frames[0]["answer"] == "staging"
    assert frames[0]["adapter"] == "codex_app_server"
    assert frames[0]["adapter_item_id"] == "question-1"


def test_claude_ask_user_question_extracts_prompt_and_options():
    from agentbridge.agent_adapter_events import claude_ask_user_question

    # 单问题：prompt 为问题文本、options 为选项标签（可被 /ab answer 编号用）。
    single = {
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [
                {
                    "header": "格式",
                    "question": "想要什么格式?",
                    "options": [
                        {"label": "Markdown", "description": "保持 .md"},
                        {"label": "纯文本", "description": "转 .txt"},
                    ],
                    "multiSelect": False,
                }
            ]
        },
    }
    prompt, options = claude_ask_user_question(single)
    assert prompt == "[格式] 想要什么格式?"
    assert options == ["Markdown", "纯文本"]

    # 多问题：铺成富文本含全部问题+选项，options 留空。
    multi = {
        "tool_name": "AskUserQuestion",
        "tool_input": {
            "questions": [
                {"header": "A", "question": "问题一?", "options": [{"label": "甲"}], "multiSelect": True},
                {"header": "B", "question": "问题二?", "options": [{"label": "乙"}]},
            ]
        },
    }
    prompt2, options2 = claude_ask_user_question(multi)
    assert "1) [A] 问题一?（可多选）" in prompt2
    assert "甲" in prompt2 and "2) [B] 问题二?" in prompt2 and "乙" in prompt2
    # 选项用字母编号，便于「题号+字母」分题作答；末行给出作答示例与多选提示。
    assert "A. 甲" in prompt2 and "A. 乙" in prompt2
    assert "1A 2A" in prompt2 and "1AC" in prompt2
    assert options2 == []

    # 非 AskUserQuestion（无 questions）→ 不干预。
    assert claude_ask_user_question({"tool_input": {}}) == (None, [])


def test_parse_askuserquestion_selection_to_indices():
    from agentbridge.agent_adapter_events import parse_askuserquestion_selection

    questions = [
        {"question": "q1", "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]},
        {"question": "q2", "options": [{"label": "A"}, {"label": "B"}]},
        {"question": "q3", "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]},
    ]
    assert parse_askuserquestion_selection("1A 2B 3C", questions) == {0: [0], 1: [1], 2: [2]}
    # 同题多选用字母连写（数字会与题号歧义，故选项部分推荐用字母）。
    assert parse_askuserquestion_selection("1AC 2B", questions) == {0: [0, 2], 1: [1]}
    # 单题可裸写（字母或数字皆可，因无题号歧义）。
    assert parse_askuserquestion_selection("B", [questions[1]]) == {0: [1]}


def test_askuserquestion_keystrokes_single_and_multi_select():
    from agentbridge.agent_adapter_events import askuserquestion_keystrokes

    # 三道单选，分别选第 1/2/3 项：Down×idx + Enter（自动跳题），末尾再总提交 Enter。
    singles = [
        {"options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]},
        {"options": [{"label": "A"}, {"label": "B"}]},
        {"options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]},
    ]
    assert askuserquestion_keystrokes(singles, {0: [0], 1: [1], 2: [2]}) == [
        "Enter",                  # q1: idx0 → 直接 Enter
        "Down", "Enter",          # q2: idx1
        "Down", "Down", "Enter",  # q3: idx2
        "Enter",                  # 总提交
    ]

    # 一道多选（4 项），勾第 1、3 项：逐项 [Space?]+Down，过完再 Down 到 Next + Enter，末尾总提交。
    multi = [{"multiSelect": True, "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}, {"label": "D"}]}]
    assert askuserquestion_keystrokes(multi, {0: [0, 2]}) == [
        "Space", "Down",  # A 勾选 + 移下
        "Down",            # B 跳过
        "Space", "Down",  # C 勾选 + 移下
        # D 不勾、是末项不再 Down
        "Down", "Enter",  # 移到 Next 提交本题
        "Enter",           # 总提交
    ]
