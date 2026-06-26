import pytest

from agentbridge.agent_adapter_events import normalize_agent_adapter_event
from agentbridge.domain import AgentBridgeError, AgentType


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
