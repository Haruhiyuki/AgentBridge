from __future__ import annotations

from agentbridge.domain import SemanticEvent, SemanticEventSource
from agentbridge.renderer import (
    OneBotV11TextRenderer,
    PlainTextRenderer,
    RenderAction,
    RenderBlock,
    RenderBlockType,
    RenderDocument,
    code_block,
    document_from_event,
    render_action_descriptors,
)


def make_event(event_type: str, payload: dict[str, object]) -> SemanticEvent:
    return SemanticEvent(
        id="evt_1",
        stream_id="session:ses_1",
        seq=1,
        type=event_type,
        source=SemanticEventSource.TERMINAL_AGENT,
        trace_id="trace",
        session_id="ses_1",
        payload=payload,
    )


def test_terminal_rejection_event_renders_operator_warning():
    event = make_event(
        "terminal.input.rejected",
        {"reason": "lease_mismatch", "provided_epoch": 1, "current_epoch": 2},
    )

    document = document_from_event(event)
    messages = OneBotV11TextRenderer().render(document)

    assert document.visibility == "operators"
    assert messages == [
        "terminal.input.rejected · ses_1\n\n"
        "终端输入已拒绝\nWARNING: 原因：lease_mismatch；请求 epoch=1；当前 epoch=2。"
    ]


def test_terminal_exited_event_renders_operator_warning():
    event = make_event(
        "terminal.exited",
        {"exit_code": 7, "pid": 1234, "output_cursor": 42},
    )

    document = document_from_event(event)
    messages = OneBotV11TextRenderer().render(document)

    assert document.visibility == "operators"
    assert messages == [
        "terminal.exited · ses_1\n\n"
        "终端已退出\nWARNING: exit_code=7；pid=1234；output_cursor=42"
    ]


def test_terminal_lost_event_renders_operator_warning():
    event = make_event(
        "terminal.lost",
        {
            "generation": 1,
            "reason": "backend_state_missing",
            "backend": "PtyTerminalBackend",
        },
    )

    document = document_from_event(event)
    messages = OneBotV11TextRenderer().render(document)

    assert document.visibility == "operators"
    assert messages == [
        "terminal.lost · ses_1\n\n"
        "终端状态丢失\n"
        "WARNING: generation=1；reason=backend_state_missing；backend=PtyTerminalBackend"
    ]


def test_terminal_auto_restart_skipped_event_renders_operator_warning():
    event = make_event(
        "terminal.auto_restart.skipped",
        {
            "generation": 1,
            "reason": "command_not_allowlisted",
            "command": "dangerous-cli --apply",
            "allowed_patterns": ["codex*", "claude*"],
        },
    )

    document = document_from_event(event)
    messages = OneBotV11TextRenderer().render(document)

    assert document.visibility == "operators"
    assert messages == [
        "terminal.auto_restart.skipped · ses_1\n\n"
        "终端自动重启已跳过\n"
        "WARNING: generation=1；reason=command_not_allowlisted；"
        "command=dangerous-cli --apply；allowed_patterns=['codex*', 'claude*']"
    ]


def test_approval_request_event_renders_approver_actions():
    event = make_event(
        "approval.requested",
        {
            "prompt": "Allow shell command?",
            "risk_level": "high",
            "required_votes": 1,
            "version": 1,
        },
    )
    event = event.model_copy(update={"interaction_id": "int_1"})

    document = document_from_event(event)
    messages = OneBotV11TextRenderer().render(document)

    assert document.visibility == "approvers"
    assert [action.command for action in document.actions] == [
        "/agent approve int_1 once",
        "/agent deny int_1",
    ]
    assert "需要审批" in messages[0]
    assert "风险等级：high" in messages[0]
    assert "/agent approve int_1 once" in messages[0]


def test_plan_request_event_renders_plan_actions_without_clicking_revision():
    event = make_event(
        "plan.requested",
        {
            "prompt": "Plan: migrate with expand-contract steps.",
            "version": 1,
        },
    )
    event = event.model_copy(update={"interaction_id": "int_plan"})

    document = document_from_event(event)
    messages = OneBotV11TextRenderer().render(document)

    assert [action.command for action in document.actions] == [
        "/agent plan approve int_plan",
        "/agent plan show int_plan",
        "/agent plan cancel int_plan",
    ]
    assert [action.style for action in document.actions] == [
        "primary",
        "default",
        "danger",
    ]
    assert "需要确认计划" in messages[0]
    assert "/agent plan revise int_plan <feedback>" in messages[0]
    assert "<feedback>" not in [action.command for action in document.actions]


def test_render_action_descriptors_are_callback_ready():
    action = RenderAction(
        id="approve-int_1",
        label="批准一次",
        command="/agent approve int_1 once",
    )

    descriptors = render_action_descriptors([action])

    assert descriptors == [
        {
            "id": "approve-int_1",
            "type": "button",
            "label": "批准一次",
            "style": "default",
            "command": "/agent approve int_1 once",
            "callback_data": "/agent approve int_1 once",
            "payload": {
                "action_id": "approve-int_1",
                "command": "/agent approve int_1 once",
                "callback_data": "/agent approve int_1 once",
                "label": "批准一次",
                "style": "default",
            },
        }
    ]


def test_interaction_expired_event_renders_operator_warning():
    event = make_event(
        "interaction.expired",
        {"status": "expired", "expires_at": "2026-06-25T12:00:00Z"},
    )
    event = event.model_copy(update={"interaction_id": "int_expired"})

    document = document_from_event(event)
    messages = OneBotV11TextRenderer().render(document)

    assert document.visibility == "operators"
    assert "交互已过期" in messages[0]
    assert "int_expired" in messages[0]


def test_code_blocks_actions_and_message_splitting_are_stable():
    document = RenderDocument(
        id="rend_1",
        title="Result",
        blocks=[
            RenderBlock(type=RenderBlockType.TEXT, title="Summary", text="done"),
            code_block("python", "print('ok')"),
        ],
        actions=[
            RenderAction(id="a1", label="批准一次", command="/agent approve AP7D once"),
        ],
    )

    text = PlainTextRenderer(max_message_chars=80).render(document)

    assert text == [
        "Result\n\nSummary\ndone\n\n```python\nprint('ok')\n```",
        "可用操作：\n1. 批准一次 -> /agent approve AP7D once",
    ]


def test_long_plain_text_is_split_without_dropping_content():
    document = RenderDocument(
        id="rend_1",
        blocks=[RenderBlock(type=RenderBlockType.TEXT, text="x" * 205)],
    )

    chunks = PlainTextRenderer(max_message_chars=100).render(document)

    assert "".join(chunks) == "x" * 205
    assert [len(chunk) for chunk in chunks] == [100, 100, 5]
