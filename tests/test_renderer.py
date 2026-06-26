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


def test_question_request_event_renders_select_action_for_options():
    event = make_event(
        "question.requested",
        {
            "prompt": "Which environment?",
            "options": ["staging", "production"],
        },
    )
    event = event.model_copy(update={"interaction_id": "int_question"})

    document = document_from_event(event)
    messages = OneBotV11TextRenderer().render(document)
    descriptors = render_action_descriptors(document.actions)

    assert [action.type for action in document.actions] == ["select"]
    assert document.actions[0].command_template == (
        "/agent answer int_question {answer}"
    )
    assert [option.value for option in document.actions[0].options] == [
        "staging",
        "production",
    ]
    assert "选项：" in messages[0]
    assert "1. staging" in messages[0]
    assert descriptors[0]["type"] == "select"
    assert descriptors[0]["command_template"] == "/agent answer int_question {answer}"
    assert descriptors[0]["input"]["name"] == "answer"
    assert descriptors[0]["options"] == [
        {"label": "staging", "value": "staging"},
        {"label": "production", "value": "production"},
    ]


def test_plan_request_event_renders_plan_actions_with_modal_revision():
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
        "/agent plan revise int_plan <feedback>",
        "/agent plan show int_plan",
        "/agent plan cancel int_plan",
    ]
    assert [action.style for action in document.actions] == [
        "primary",
        "default",
        "default",
        "danger",
    ]
    assert [action.type for action in document.actions] == [
        "button",
        "modal",
        "button",
        "button",
    ]
    assert document.actions[1].command_template == "/agent plan revise int_plan {feedback}"
    assert document.actions[1].input is not None
    assert document.actions[1].input.name == "feedback"
    assert "需要确认计划" in messages[0]
    assert "/agent plan revise int_plan <feedback>" in messages[0]


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


def test_modal_action_descriptor_carries_template_and_input_metadata():
    event = make_event("plan.requested", {"prompt": "Plan: deploy."})
    event = event.model_copy(update={"interaction_id": "int_plan"})

    document = document_from_event(event)
    descriptors = render_action_descriptors(document.actions)
    revise = descriptors[1]

    assert revise["type"] == "modal"
    assert revise["label"] == "要求修改"
    assert revise["command"] == "/agent plan revise int_plan <feedback>"
    assert revise["callback_data"] == "plan-revise-int_plan"
    assert revise["command_template"] == "/agent plan revise int_plan {feedback}"
    assert revise["input"] == {
        "name": "feedback",
        "label": "修改意见",
        "placeholder": "说明希望 Agent 调整的计划",
        "required": True,
        "multiline": True,
    }
    assert revise["payload"]["command_template"] == (
        "/agent plan revise int_plan {feedback}"
    )
    assert revise["payload"]["input"]["name"] == "feedback"


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


def test_bot_interaction_ack_event_renders_operator_status():
    event = make_event(
        "bot.interaction.ack",
        {
            "platform": "onebot.v11",
            "interaction_kind": "selection",
            "actor_id": "onebot:20002",
            "canonical_command": "interaction.answer",
        },
    )

    document = document_from_event(event)
    messages = OneBotV11TextRenderer().render(document)

    assert document.visibility == "operators"
    assert "Bot 交互已确认" in messages[0]
    assert "类型：selection" in messages[0]
    assert "命令：interaction.answer" in messages[0]


def test_bot_inbound_event_renders_operator_status():
    event = make_event(
        "bot.action.clicked",
        {
            "platform": "onebot.v11",
            "chat_context_id": "ctx_1",
            "actor_id": "onebot:20002",
            "platform_event_id": "callback-1",
            "raw_text": "/agent approve int_1 once",
        },
    )

    document = document_from_event(event)
    messages = OneBotV11TextRenderer().render(document)

    assert document.visibility == "operators"
    assert "Bot 上行事件" in messages[0]
    assert "类型：bot.action.clicked" in messages[0]
    assert "原文：/agent approve int_1 once" in messages[0]


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


def test_long_code_block_splits_into_balanced_fences():
    code = "\n".join(f"print({index})" for index in range(30))
    document = RenderDocument(id="rend_1", blocks=[code_block("python", code)])

    chunks = PlainTextRenderer(max_message_chars=120).render(document)

    assert len(chunks) > 1
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert all(chunk.startswith("```python\n") for chunk in chunks)
    assert all(chunk.endswith("\n```") for chunk in chunks)
    assert all(chunk.count("```") == 2 for chunk in chunks)
    combined = "\n".join(chunks)
    for index in range(30):
        assert f"print({index})" in combined


def test_markdown_fenced_code_splits_without_breaking_format():
    code = "\n".join(f"line_{index} = {index}" for index in range(20))
    document = RenderDocument(
        id="rend_1",
        blocks=[
            RenderBlock(
                type=RenderBlockType.MARKDOWN,
                text=f"Intro\n\n```python\n{code}\n```\n\nDone",
            )
        ],
    )

    chunks = PlainTextRenderer(max_message_chars=120).render(document)

    assert len(chunks) > 1
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert chunks[0].startswith("Intro")
    assert chunks[-1].endswith("Done")
    assert all(chunk.count("```") in {0, 2} for chunk in chunks)
    combined = "\n".join(chunks)
    for index in range(20):
        assert f"line_{index} = {index}" in combined
