"""面向聊天用户的事件投影。

AgentBridge 内部的语义事件流既服务于运营/审计（终端生命周期、租约、队列、输入回执…），
又服务于聊天用户。后者只该看到少数"对人有意义"的内容：Agent 的过程反馈与回答、需要处理的
提问/审批/计划、以及失败。本模块把原始事件流投影成可直接发给用户的聊天消息——过滤掉管道噪声；把工具
前的过程叙述即时作为"进度"消息输出（长任务实时反馈），把最终答案（最后一个工具之后的分片）
合并成一条干净消息；并把 markdown 降级成聊天平台易读的纯文本。

这是项目对外提供的标准能力：Bot 适配器只需拉取本投影并原样转发，无需了解任何内部事件类型
或自己实现过滤/合并/限频，从而保证不同平台的接入都干净一致、且不会因逐事件刷屏触发风控。
"""

from __future__ import annotations

import re
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


def _join_blocks(pieces: list[str]) -> str:
    """按块拼接 assistant 分片。适配器（如 Claude MessageDisplay）逐个 markdown 块发送分片，
    块末尾通常不带换行；直接相连会把标题/段落/列表项挤成一行，故在块边界补一个换行。"""
    out = ""
    for piece in pieces:
        if out and not out.endswith("\n") and not piece.startswith("\n"):
            out += "\n"
        out += piece
    return out


_MD_FENCE_LINE = re.compile(r"^[ \t]*```.*$", re.MULTILINE)
_MD_TABLE_SEP_LINE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}[-| :]*$", re.MULTILINE)
_MD_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]*", re.MULTILINE)
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_MULTI_BLANK = re.compile(r"\n{3,}")


def _to_plain_text(text: str) -> str:
    """把 agent 回答里的 markdown 降级成聊天平台（QQ 等不渲染 markdown）易读的纯文本：
    去代码围栏/表格分隔行、去标题 # 与 **加粗** 标记、行内 `代码`、链接 [文本](url) → 文本 (url)，
    并把表格行简化、压缩多余空行。保留换行与列表结构。"""
    text = _MD_FENCE_LINE.sub("", text)
    text = _MD_TABLE_SEP_LINE.sub("", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_BOLD.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MD_INLINE_CODE.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1 (\2)", text)
    rendered_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.rstrip()
        body = stripped.strip()
        if body.startswith("|") and body.endswith("|") and len(body) > 1:
            cells = [cell.strip() for cell in body.strip("|").split("|")]
            stripped = " | ".join(cell for cell in cells if cell)
        rendered_lines.append(stripped)
    return _MULTI_BLANK.sub("\n\n", "\n".join(rendered_lines)).strip()


def _format_ask(kind: str, payload: dict[str, object]) -> str:
    lines = [_ASK_LABELS.get(kind, "待处理")]
    prompt = _to_plain_text(str((payload or {}).get("prompt") or "")).strip()
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

    # 收集每个 turn 的 assistant 分片（带 seq）与"最后一个工具事件"的 seq。部分适配器事件不带
    # turn_id，跟踪"当前 turn"（任何带 turn_id 的事件都会更新它）把它们归位，并记录每个完成/失败
    # 事件解析到的 turn，保证回答与完成事件落在同一 key。
    deltas_by_turn: dict[str, list[tuple[int, str]]] = {}
    last_tool_seq: dict[str, int] = {}
    key_for_seq: dict[int, str] = {}
    current_turn = ""
    for event in ordered:
        if event.turn_id:
            current_turn = event.turn_id
        key = event.turn_id or current_turn
        key_for_seq[event.seq] = key
        if event.type == "assistant.delta":
            piece = _payload_text(event)
            if piece:
                deltas_by_turn.setdefault(key, []).append((event.seq, piece))
        elif event.type.startswith("tool."):
            last_tool_seq[key] = max(last_tool_seq.get(key, 0), event.seq)

    def is_intermediate_delta(seq: int) -> bool:
        # 该分片后面还有工具事件 → 属于"过程叙述"，即时作为进度输出；否则属于最终答案的尾段。
        return seq <= last_tool_seq.get(key_for_seq.get(seq, ""), 0)

    def final_answer_for(turn_key: str) -> str:
        # 最终答案 = 最后一个工具事件之后的分片（过程叙述已作为进度即时发过）。
        cutoff = last_tool_seq.get(turn_key, 0)
        final = [text for seq, text in deltas_by_turn.get(turn_key, []) if seq > cutoff]
        return _to_plain_text(_join_blocks(final))

    # 尚未结束的 turn，其"最终答案尾段"（最后工具之后的分片）要等 turn 结束才合并发出；
    # 在此之前游标不能越过这些分片，否则它们会被游标跳过、永远发不出来。
    finished_turns = {
        key_for_seq.get(event.seq, "")
        for event in ordered
        if event.type == "turn.completed" or event.type in FAIL_TYPES
    }
    held_min: int | None = None
    for turn_key, pieces in deltas_by_turn.items():
        if turn_key in finished_turns:
            continue
        cutoff = last_tool_seq.get(turn_key, 0)
        for seq, _text in pieces:
            if seq > cutoff:
                held_min = seq if held_min is None else min(held_min, seq)

    messages: list[dict[str, object]] = []
    max_seq = after_seq
    for event in ordered:
        max_seq = max(max_seq, event.seq)
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
        elif event.type == "assistant.delta":
            # 工具前的过程叙述即时作为"进度"消息输出，让用户在长任务中也能持续看到反馈；
            # 最终答案的尾段不在这里发，留到 turn.completed 合并成一条干净消息。
            if is_intermediate_delta(event.seq):
                piece = _to_plain_text(_payload_text(event)).strip()
                if piece:
                    messages.append(
                        {
                            "seq": event.seq,
                            "kind": "progress",
                            "turn_id": event.turn_id,
                            "text": piece,
                        }
                    )
        elif event.type == "turn.completed":
            text = final_answer_for(key_for_seq.get(event.seq, "")).strip()
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

    if held_min is not None:
        cursor = max(after_seq, min(max_seq, held_min - 1))
    else:
        cursor = max(after_seq, max_seq)
    return messages, cursor
