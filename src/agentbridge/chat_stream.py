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
    # 单题：/ab answer <提问编号> <选项字母>（多选连写如 AC）；多题：作答串里逐题写「题号+字母」。
    "question": "回复 /ab answer <提问编号> <选项字母>（多选连写如 AC；多题逐题写 1A 2B 3C）",
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
_TABLE_SEP_CELL = re.compile(r"^:?-{1,}:?$")


def _split_table_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _is_table_separator(line: str) -> bool:
    if "|" not in line and "-" not in line:
        return False
    cells = _split_table_row(line)
    return bool(cells) and all(_TABLE_SEP_CELL.match(cell) for cell in cells if cell != "")


def _render_table(header: list[str], rows: list[list[str]]) -> list[str]:
    """把一个 markdown 表格渲染成聊天框易读的记录式列表：
    2 列 → 「键：值」每行一条；≥3 列 → 每行一个小记录（首列作标题，其余「表头：值」缩进）。"""
    ncol = max(len(header), max((len(r) for r in rows), default=0))
    out: list[str] = []
    head_label = " / ".join(h for h in header if h)
    if head_label:
        out.append(head_label + "：")
    for row in rows:
        cells = (row + [""] * ncol)[:ncol]
        if ncol <= 1:
            value = cells[0] if cells else ""
            if value:
                out.append(f"• {value}")
        elif ncol == 2:
            left, right = cells[0], cells[1]
            out.append(f"• {left}：{right}" if right else f"• {left}")
        else:
            out.append(f"• {cells[0]}")
            for index in range(1, ncol):
                label = header[index] if index < len(header) else f"列{index + 1}"
                if cells[index]:
                    out.append(f"  {label}：{cells[index]}")
    return out


def _is_table_row(line: str) -> bool:
    """是否像一行 markdown 表格数据：去空白后以 | 收尾、且能切出 ≥2 个单元格、不是分隔行。"""
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return False
    if _is_table_separator(line):
        return False
    return len(_split_table_row(line)) >= 2


def _convert_tables(text: str) -> list[str]:
    """识别 markdown 表格块（表头行 + 分隔行 + 数据行）并替换成记录式列表，其余行原样保留。

    流式 delta 常把一个表格拆散，导致部分数据行落单（前面没有表头+分隔行）。为避免这些
    落单行以原始 ``|...|`` 形态刷屏到 QQ，任何「像表格行」的落单行也降级成 ``单元格 / 单元格``。
    """
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if "|" in line and i + 1 < n and _is_table_separator(lines[i + 1]):
            header = _split_table_row(line)
            j = i + 2
            rows: list[list[str]] = []
            while j < n and "|" in lines[j] and lines[j].strip():
                rows.append(_split_table_row(lines[j]))
                j += 1
            out.extend(_render_table(header, rows))
            i = j
            continue
        if _is_table_row(line):
            # 落单的表格行：去掉竖线、单元格用 ` / ` 连接，避免原始 |...| 刷屏。
            cells = [cell for cell in _split_table_row(line) if cell]
            out.append(" / ".join(cells) if cells else line)
            i += 1
            continue
        out.append(line)
        i += 1
    return out


def _to_plain_text(text: str) -> str:
    """把 agent 回答里的 markdown 降级成聊天平台（QQ 等不渲染 markdown）易读的纯文本：
    把表格解析成记录式列表、去代码围栏、去标题 # 与 **加粗** 标记、行内 `代码`、
    链接 [文本](url) → 文本 (url)，并压缩多余空行。保留换行与列表结构。"""
    text = _MD_FENCE_LINE.sub("", text)
    text = "\n".join(_convert_tables(text))
    text = _MD_TABLE_SEP_LINE.sub("", text)  # 清理未成块的残留分隔行
    text = _MD_HEADING.sub("", text)
    text = _MD_BOLD.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MD_INLINE_CODE.sub(r"\1", text)
    text = _MD_LINK.sub(r"\1 (\2)", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _MULTI_BLANK.sub("\n\n", text).strip()


def _format_ask(kind: str, payload: dict[str, object]) -> str:
    lines = [_ASK_LABELS.get(kind, "待处理")]
    prompt = _to_plain_text(str((payload or {}).get("prompt") or "")).strip()
    if prompt:
        lines.append(prompt[:_MAX_PROMPT_CHARS])
    options = (payload or {}).get("options")
    if isinstance(options, list) and options:
        for index, option in enumerate(options):
            letter = chr(ord("A") + index) if index < 26 else str(index + 1)
            lines.append(f"  {letter}. {option}")
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

    # 完成边界感知的最终答案：每个 turn.completed 只取"自上一次完成以来、且在本工具之后"的
    # 新分片。后台命令（如 sleep）会让 Claude 多次 Stop、产生多个 turn.completed；若每次都把
    # "最后工具之后的全部分片"重算合并，迟到分片就会被反复并入、形成增长式重复投递。这里用
    # answered_upto 记录每个 turn key 已被前序完成事件消费到的 seq，预扫一遍所有完成/失败事件，
    # 算出每个完成事件该发的那段文本，保证同一段分片只随其首个完成事件发出一次。
    answered_upto: dict[str, int] = {}
    answer_text_by_seq: dict[int, str] = {}
    skip_completion_seqs: set[int] = set()
    orphan_delta_seqs: set[int] = set()
    seen_completion_key: set[str] = set()
    for event in ordered:
        ckey = key_for_seq.get(event.seq, "")
        if event.type == "assistant.delta":
            # 孤儿尾段：turn 已完成之后、该 key 又冒出的最终段分片（后台命令跑完后 agent 才续写的
            # 答案，如「休眠 N 秒后回答」）。此后通常不会再有 completion 来收集它，必须在此独立投递
            # 一次，并推进 answered_upto——这样即便之后又来一个 completion，也不会把它重复并入。
            if event.seq > last_tool_seq.get(ckey, 0) and ckey in seen_completion_key:
                orphan_delta_seqs.add(event.seq)
                answered_upto[ckey] = max(answered_upto.get(ckey, 0), event.seq)
        elif event.type == "turn.completed" or event.type in FAIL_TYPES:
            cutoff = last_tool_seq.get(ckey, 0)
            lower = max(cutoff, answered_upto.get(ckey, 0))
            final = [
                text
                for seq, text in deltas_by_turn.get(ckey, [])
                if lower < seq <= event.seq
            ]
            answered_upto[ckey] = max(answered_upto.get(ckey, 0), event.seq)
            if event.type == "turn.completed":
                text = _to_plain_text(_join_blocks(final))
                if text:
                    answer_text_by_seq[event.seq] = text
                elif ckey not in seen_completion_key and ckey not in deltas_by_turn:
                    # 该 key 从未产出任何分片（如 Codex 暂无 hooks）→ 给中性完成提示。
                    answer_text_by_seq[event.seq] = ""
                else:
                    # 已发过进度/答案，或属后台命令多次 Stop 的空收尾 → 跳过，避免「✅本轮已完成」刷屏。
                    skip_completion_seqs.add(event.seq)
            seen_completion_key.add(ckey)

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
            elif event.seq in orphan_delta_seqs:
                # 已完成 turn 的迟到尾段：独立作为一条答案投递（不再有 completion 来合并它）。
                piece = _to_plain_text(_payload_text(event)).strip()
                if piece:
                    messages.append(
                        {
                            "seq": event.seq,
                            "kind": "answer",
                            "turn_id": event.turn_id,
                            "text": piece,
                        }
                    )
        elif event.type == "turn.completed":
            if event.seq in skip_completion_seqs:
                continue
            text = answer_text_by_seq.get(event.seq, "").strip()
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

    # 铁律：游标永远不能退到"本轮已投递消息"的前面——否则下一轮会把刚发出的消息重复投递（刷屏）。
    # held_min 的本意是 turn 未结束时别让游标跳过其最终尾段；但尾段分片始终保留在全量历史里、会在
    # turn.completed 时重算合并（调用方传入完整事件，见上文），故扣住游标并非"防丢失"。一旦出现僵尸
    # turn（永不 completed）把 held_min 钉在低位，而其后的正常 turn 仍照常产出答案，被钉住的游标就会
    # 每轮重发这些答案 → 同一条消息刷屏。因此即便 held_min 存在，也必须越过本轮已投递的最大 seq。
    delivered_max = max((int(message["seq"]) for message in messages), default=after_seq)
    if held_min is not None:
        cursor = max(after_seq, delivered_max, min(max_seq, held_min - 1))
    else:
        cursor = max(after_seq, max_seq)
    return messages, cursor
