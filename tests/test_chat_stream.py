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
        ev(7, "assistant.delta", turn_id="turn_1", payload={"text": "这个项目"}),
        ev(8, "assistant.delta", turn_id="turn_1", payload={"text": "是一个控制平台。"}),
        ev(9, "turn.completed", turn_id="turn_1"),
    ]
    messages, cursor = chat_messages_from_events(events)
    # 只剩一条"回答"，管道事件全部被过滤。
    assert kinds(messages) == ["answer"]
    assert messages[0]["text"] == "这个项目是一个控制平台。"
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
    assert messages[0]["text"] == "前半后半"
    assert cursor == 12


def test_empty_answer_falls_back_to_neutral_done():
    # Codex 暂无 hooks：没有 assistant.delta，完成时给中性提示。
    events = [
        ev(1, "turn.started", turn_id="turn_1"),
        ev(2, "turn.completed", turn_id="turn_1"),
    ]
    messages, _ = chat_messages_from_events(events)
    assert kinds(messages) == ["answer"]
    assert "完成" in messages[0]["text"]
