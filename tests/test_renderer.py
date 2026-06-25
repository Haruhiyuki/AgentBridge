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


def test_approval_request_event_renders_approver_actions():
    event = make_event(
        "approval.requested",
        {
            "prompt": "Allow shell command?",
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
    assert "/agent approve int_1 once" in messages[0]


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
