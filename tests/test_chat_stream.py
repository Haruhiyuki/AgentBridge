from __future__ import annotations

from agentbridge.chat_stream import chat_messages_from_events
from agentbridge.domain import SemanticEvent, SemanticEventSource


def ev(seq: int, type_: str, *, turn_id=None, interaction_id=None, payload=None):
    return SemanticEvent(
        id=f"evt_{seq}",
        stream_id="ses_1",
        seq=seq,
        type=type_,
        source=SemanticEventSource.AGENT_ADAPTER,
        trace_id="t",
        session_id="ses_1",
        turn_id=turn_id,
        interaction_id=interaction_id,
        payload=payload or {},
    )


def kinds(messages):
    return [m["kind"] for m in messages]


def test_filters_plumbing_and_coalesces_answer():
    events = [
        ev(1, "session.created"),
        ev(2, "turn.queued", turn_id="turn_1"),
        ev(3, "terminal.started"),
        ev(4, "lease.acquired"),
        ev(5, "turn.started", turn_id="turn_1"),
        ev(6, "terminal.input.accepted"),
        ev(7, "assistant.delta", turn_id="turn_1", payload={"text": "AgentBridge 是平台。"}),
        ev(8, "assistant.delta", turn_id="turn_1", payload={"text": "它维护两条通道。"}),
        ev(9, "turn.completed", turn_id="turn_1"),
    ]
    messages, cursor = chat_messages_from_events(events)
    # 只剩一条"回答"，管道事件全部被过滤；块级分片之间补换行。
    assert kinds(messages) == ["answer"]
    assert messages[0]["text"] == "AgentBridge 是平台。\n它维护两条通道。"
    assert cursor == 9


def test_question_and_approval_emit_immediately():
    events = [
        ev(1, "turn.started", turn_id="turn_1"),
        ev(
            2,
            "question.requested",
            turn_id="turn_1",
            interaction_id="int_1",
            payload={"prompt": "用哪种迁移方式？", "options": ["兼容迁移", "重建"]},
        ),
        ev(
            3,
            "approval.requested",
            turn_id="turn_1",
            interaction_id="int_2",
            payload={"prompt": "执行生产部署？"},
        ),
    ]
    messages, _ = chat_messages_from_events(events)
    assert kinds(messages) == ["question", "approval"]
    q = messages[0]
    assert q["interaction_id"] == "int_1"
    assert "用哪种迁移方式？" in q["text"]
    assert "1. 兼容迁移" in q["text"]
    assert "/ab answer" in q["text"]
    assert "/ab approve" in messages[1]["text"]


def test_failure_message():
    events = [
        ev(1, "turn.started", turn_id="turn_1"),
        ev(2, "turn.failed", turn_id="turn_1", payload={"error": "boom"}),
    ]
    messages, _ = chat_messages_from_events(events)
    assert kinds(messages) == ["error"]
    assert "boom" in messages[0]["text"]


def test_after_seq_only_emits_new_but_coalesces_full_turn():
    # turn 的分片在 after_seq 之前，但完成在之后：回答仍应是完整文本。
    events = [
        ev(10, "assistant.delta", turn_id="turn_1", payload={"text": "前半"}),
        ev(11, "assistant.delta", turn_id="turn_1", payload={"text": "后半"}),
        ev(12, "turn.completed", turn_id="turn_1"),
    ]
    messages, cursor = chat_messages_from_events(events, after_seq=11)
    assert kinds(messages) == ["answer"]
    assert messages[0]["text"] == "前半\n后半"
    assert cursor == 12


def test_orphan_assistant_delta_attaches_to_current_turn():
    # 复现真实坑：assistant.delta 来自 hook 不带 turn_id，turn.completed 带（解析出的）turn_id；
    # 投影需把无 turn_id 的分片归到当前 turn，使回答与完成事件落在同一 key。
    events = [
        ev(7, "turn.started", turn_id="turn_1"),
        ev(8, "assistant.delta", turn_id=None, payload={"text": "这是回答。"}),
        ev(9, "turn.completed", turn_id="turn_1"),
    ]
    messages, _ = chat_messages_from_events(events)
    assert kinds(messages) == ["answer"]
    assert messages[0]["text"] == "这是回答。"


def test_markdown_is_downgraded_to_plain_text():
    events = [
        ev(1, "turn.started", turn_id="turn_1"),
        ev(2, "assistant.delta", turn_id="turn_1", payload={"text": "# 标题\n\n## 一句话定位"}),
        ev(
            3,
            "assistant.delta",
            turn_id="turn_1",
            payload={"text": "**AgentBridge** 是一个 `本地` 平台。"},
        ),
        ev(4, "turn.completed", turn_id="turn_1"),
    ]
    text = chat_messages_from_events(events)[0][0]["text"]
    # 标题 # 与 **加粗**、`行内代码` 标记都被去掉，但换行/结构保留。
    assert "#" not in text
    assert "**" not in text
    assert "`" not in text
    assert "标题" in text and "一句话定位" in text
    assert "AgentBridge 是一个 本地 平台。" in text
    # 块级分片之间有换行，不挤成一行。
    assert "一句话定位\nAgentBridge" in text


def test_drops_pre_tool_narration():
    # 工具前的过程性叙述（"让我先看看…"）应被丢弃，只保留最后工具之后的真实回答。
    events = [
        ev(1, "turn.started", turn_id="turn_1"),
        ev(2, "assistant.delta", turn_id="turn_1", payload={"text": "让我先看看代码库。"}),
        ev(3, "tool.started", turn_id="turn_1", payload={"tool_name": "Bash"}),
        ev(4, "tool.completed", turn_id="turn_1", payload={"tool_name": "Bash"}),
        ev(5, "assistant.delta", turn_id="turn_1", payload={"text": "这是最终答案。"}),
        ev(6, "turn.completed", turn_id="turn_1"),
    ]
    text = chat_messages_from_events(events)[0][0]["text"]
    assert text == "这是最终答案。"
    assert "让我先看看" not in text


def test_empty_answer_falls_back_to_neutral_done():
    # Codex 暂无 hooks：没有 assistant.delta，完成时给中性提示。
    events = [
        ev(1, "turn.started", turn_id="turn_1"),
        ev(2, "turn.completed", turn_id="turn_1"),
    ]
    messages, _ = chat_messages_from_events(events)
    assert kinds(messages) == ["answer"]
    assert "完成" in messages[0]["text"]
