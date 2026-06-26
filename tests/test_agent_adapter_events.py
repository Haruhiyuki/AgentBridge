import pytest

from agentbridge.agent_adapter_events import (
    adapter_response_frames_from_events,
    normalize_agent_adapter_event,
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
