"""面向聊天用户的事件投影。

AgentBridge 内部的语义事件流既服务于运营/审计（终端生命周期、租约、队列、输入回执…），
又服务于聊天用户。后者只该看到少数"对人有意义"的内容：Agent 的回答、需要处理的提问/审批/
计划、以及失败。本模块把原始事件流投影成可直接发给用户的聊天消息——过滤掉全部管道噪声，
并把 assistant 的分片文本按 turn 合并成一条回答。

这是项目对外提供的标准能力：Bot 适配器只需拉取本投影并原样转发，无需了解任何内部事件类型
或自己实现过滤/合并/限频，从而保证不同平台的接入都干净一致、且不会因逐事件刷屏触发风控。
"""

from __future__ import annotations

from collections.abc import Iterable

from agentbridge.domain import SemanticEvent

# 即时呈现、需要用户处理的交互类事件。
ASK_KINDS: dict[str, str] = {
    "question.requested": "question",
    "approval.requested": "approval",
    "plan.requested": "plan",
}
FAIL_TYPES = {"turn.failed", "turn.interrupted"}

_ASK_LABELS = {
    "question": "❓ Agent 提问",
    "approval": "🔐 需要审批",
    "plan": "📋 计划待确认",
}
_ASK_REPLY_HINTS = {
    "question": "回复 /ab answer <编号> <内容>",
    "approval": "回复 /ab approve <编号> 或 /ab deny <编号>",
    "plan": "回复 /ab plan approve <编号> 或 /ab plan revise <编号> <意见>",
}

_MAX_ERROR_CHARS = 500
_MAX_PROMPT_CHARS = 1000


def _payload_text(event: SemanticEvent) -> str:
    value = event.payload.get("text") if event.payload else None
    return str(value) if value else ""


def _format_ask(kind: str, payload: dict[str, object]) -> str:
    lines = [_ASK_LABELS.get(kind, "待处理")]
    prompt = str((payload or {}).get("prompt") or "").strip()
    if prompt:
        lines.append(prompt[:_MAX_PROMPT_CHARS])
    options = (payload or {}).get("options")
    if isinstance(options, list) and options:
        for index, option in enumerate(options, 1):
            lines.append(f"  {index}. {option}")
    lines.append(_ASK_REPLY_HINTS.get(kind, ""))
    return "\n".join(line for line in lines if line)


def chat_messages_from_events(
    events: Iterable[SemanticEvent], *, after_seq: int = 0
) -> tuple[list[dict[str, object]], int]:
    """把会话语义事件流投影成面向用户的聊天消息。

    返回 ``(messages, cursor)``。``messages`` 中每条形如
    ``{"seq", "kind", "text", "turn_id"?, "interaction_id"?}``，``kind`` 取值
    ``answer`` / ``question`` / ``approval`` / ``plan`` / ``error``，``text`` 可直接发给用户。
    ``cursor`` 是已处理到的最大 seq，下次以 ``after_seq=cursor`` 继续。

    为正确合并，调用方应传入该会话的完整事件（而非仅 after_seq 之后的），本函数据此把每个
    turn 的 assistant 分片合并，并只在 ``seq > after_seq`` 时产出消息。
    """
    ordered = sorted(events, key=lambda event: event.seq)

    # 先按 turn 聚合 assistant 文本，完成时作为一条回答输出。部分适配器事件可能不带 turn_id，
    # 因此跟踪"当前 turn"（任何带 turn_id 的事件都会更新它），把无 turn_id 的分片归到它，
    # 并记录每个完成/失败事件解析到的 turn，保证回答与完成事件落在同一 key。
    answer_by_turn: dict[str, str] = {}
    resolved_turn_for_seq: dict[int, str] = {}
    current_turn = ""
    for event in ordered:
        if event.turn_id:
            current_turn = event.turn_id
        if event.type == "assistant.delta":
            piece = _payload_text(event)
            if piece:
                key = event.turn_id or current_turn
                answer_by_turn[key] = answer_by_turn.get(key, "") + piece
        elif event.type == "turn.completed" or event.type in FAIL_TYPES:
            resolved_turn_for_seq[event.seq] = event.turn_id or current_turn

    messages: list[dict[str, object]] = []
    cursor = after_seq
    for event in ordered:
        cursor = max(cursor, event.seq)
        if event.seq <= after_seq:
            continue
        kind = ASK_KINDS.get(event.type)
        if kind is not None:
            messages.append(
                {
                    "seq": event.seq,
                    "kind": kind,
                    "interaction_id": event.interaction_id,
                    "text": _format_ask(kind, event.payload),
                }
            )
        elif event.type == "turn.completed":
            text = answer_by_turn.get(resolved_turn_for_seq.get(event.seq, ""), "").strip()
            messages.append(
                {
                    "seq": event.seq,
                    "kind": "answer",
                    "turn_id": event.turn_id,
                    # 无结构化输出时（如 Codex 暂无 hooks）给出中性完成提示。
                    "text": text or "✅ 本轮已完成。",
                }
            )
        elif event.type in FAIL_TYPES:
            error = str((event.payload or {}).get("error") or "任务未完成")
            messages.append(
                {
                    "seq": event.seq,
                    "kind": "error",
                    "turn_id": event.turn_id,
                    "text": f"⚠️ 任务未完成：{error[:_MAX_ERROR_CHARS]}",
                }
            )
        # 其余事件（session/terminal/lease/queue/turn.queued/started/input…）一律忽略。

    return messages, cursor
