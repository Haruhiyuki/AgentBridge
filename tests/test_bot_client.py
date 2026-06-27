from __future__ import annotations

from agentbridge.bot_client import (
    AgentBridgeBotClient,
    build_inbound_envelope,
    dig,
    iter_sse_frames,
)


def test_build_inbound_envelope_group_and_private():
    group = build_inbound_envelope(
        platform="discord", bot_instance_id="b1", user_id="u1",
        text="/agent health", channel_id="c1", roles=("admin",), message_id="m1",
    )
    assert group["platform"] == "discord"
    assert group["user_id"] == "u1"
    assert group["channel_id"] == "c1"
    assert group["command"] == "/agent health"
    assert group["default_roles"] == ["admin"]
    # 带 message_id → 自动派生幂等键。
    assert group["idempotency_key"] == "discord:m1"

    private = build_inbound_envelope(
        platform="telegram", bot_instance_id="b1", user_id="u9",
        text="hi", private=True,
    )
    assert private["scope"] == "private"
    assert "channel_id" not in private
    assert "idempotency_key" not in private  # 没给 message_id 就不带


def test_dig_finds_nested_session_id():
    assert dig({"result": {"data": {"session_id": "ses_x"}}}, "session_id") == "ses_x"
    assert dig({"a": [{"b": 1}, {"turn_id": "t1"}]}, "turn_id") == "t1"
    assert dig({"a": 1}, "missing") is None
    assert AgentBridgeBotClient.session_id_of({"result": {"session_id": "ses_y"}}) == "ses_y"
    assert AgentBridgeBotClient.session_id_of({"result": {"message": "ok"}}) is None


def test_iter_sse_frames_parses_messages_and_done_ignores_keepalive():
    lines = [
        ": keep-alive\n",
        "\n",
        "event: message\n",
        'data: {"seq": 1, "kind": "answer", "text": "你好"}\n',
        "\n",
        ": keep-alive\n",
        "event: done\n",
        'data: {"cursor": 5, "reason": "completed"}\n',
        "\n",
    ]
    frames = list(iter_sse_frames(lines))
    assert frames[0] == ("message", {"seq": 1, "kind": "answer", "text": "你好"})
    assert frames[-1] == ("done", {"cursor": 5, "reason": "completed"})
    # 保活注释行不产出帧。
    assert all(event in {"message", "done"} for event, _ in frames)


def test_iter_sse_frames_defaults_event_to_message_when_only_data():
    # 只有 data 行（无显式 event:）按 message 处理。
    frames = list(iter_sse_frames(['data: {"text": "x"}\n', "\n"]))
    assert frames == [("message", {"text": "x"})]
