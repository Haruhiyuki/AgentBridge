from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agentbridge.domain import SemanticEvent


class RenderVisibility(StrEnum):
    PUBLIC = "public"
    OPERATORS = "operators"
    APPROVERS = "approvers"
    PRIVATE = "private"


class RenderBlockType(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"
    CODE = "code"
    PROGRESS = "progress"
    TOOL = "tool"
    WARNING = "warning"
    DIFF = "diff"
    FILE = "file"
    DIVIDER = "divider"


class RenderActionStyle(StrEnum):
    DEFAULT = "default"
    PRIMARY = "primary"
    DANGER = "danger"


class RenderActionType(StrEnum):
    BUTTON = "button"
    MODAL = "modal"
    SELECT = "select"


class RenderActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    placeholder: str | None = None
    required: bool = True
    multiline: bool = False


class RenderActionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: str
    description: str | None = None


class RenderBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: RenderBlockType
    text: str | None = None
    title: str | None = None
    language: str | None = None
    code: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RenderAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: RenderActionType = RenderActionType.BUTTON
    label: str
    command: str
    style: RenderActionStyle = RenderActionStyle.DEFAULT
    command_template: str | None = None
    input: RenderActionInput | None = None
    options: list[RenderActionOption] | None = None


class RenderActionDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: RenderActionType = RenderActionType.BUTTON
    label: str
    style: RenderActionStyle = RenderActionStyle.DEFAULT
    command: str
    callback_data: str
    command_template: str | None = None
    input: RenderActionInput | None = None
    options: list[RenderActionOption] | None = None
    payload: dict[str, Any]


class RenderDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str | None = None
    blocks: list[RenderBlock] = Field(default_factory=list)
    actions: list[RenderAction] = Field(default_factory=list)
    update_key: str | None = None
    visibility: RenderVisibility = RenderVisibility.PUBLIC


@dataclass(frozen=True)
class _MessagePart:
    text: str
    language: str | None = None
    code: str | None = None


_CODE_FENCE_RE = re.compile(
    r"^```([^\n]*)\n(.*?)\n```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


def document_from_event(event: SemanticEvent) -> RenderDocument:
    payload = event.payload
    title = event_title(event)
    blocks: list[RenderBlock] = []
    actions: list[RenderAction] = []
    visibility = RenderVisibility.PUBLIC

    if event.type == "assistant.delta":
        blocks.append(markdown_block(str(payload.get("text") or "")))
        title = None
    elif event.type == "terminal.input.rejected":
        visibility = RenderVisibility.OPERATORS
        blocks.append(
            warning_block(
                "终端输入已拒绝",
                (
                    f"原因：{payload.get('reason', 'unknown')}；"
                    f"请求 epoch={payload.get('provided_epoch')}；"
                    f"当前 epoch={payload.get('current_epoch')}。"
                ),
            )
        )
    elif event.type == "terminal.input.accepted":
        visibility = RenderVisibility.OPERATORS
        blocks.append(
            text_block(
                "终端输入已接受",
                (
                    f"{payload.get('owner_type')}:{payload.get('owner_id')} "
                    f"· epoch={payload.get('epoch')}"
                ),
            )
        )
    elif event.type == "session.created":
        blocks.append(
            text_block(
                "会话",
                (
                    f"[{payload.get('short_code')}] {payload.get('name')}\n"
                    f"Agent：{payload.get('agent_type')}\n"
                    f"Workspace：{payload.get('workspace_id')}"
                ),
            )
        )
    elif event.type == "turn.queued":
        details = f"Turn：{event.turn_id}\nPrompt 长度：{payload.get('prompt_length')}"
        if payload.get("queue_reason") == "human_control":
            lease_details = []
            if payload.get("lease_owner_id"):
                lease_details.append(f"owner={payload.get('lease_owner_id')}")
            if payload.get("lease_epoch") is not None:
                lease_details.append(f"epoch={payload.get('lease_epoch')}")
            lease_suffix = f"（{'；'.join(lease_details)}）" if lease_details else ""
            details += f"\n本地控制中：任务已排队等待人工释放{lease_suffix}。"
        blocks.append(
            progress_block(
                "任务已排队",
                details,
            )
        )
    elif event.type == "turn.queue_unblocked":
        blocks.append(
            progress_block(
                "队列可继续",
                (
                    "本地控制已释放，Bot 可以继续同一会话。\n"
                    f"下一个 Turn：{payload.get('next_turn_id') or event.turn_id}\n"
                    f"可继续任务数：{payload.get('unblocked_turn_count')}\n"
                    f"队列状态：{'paused' if payload.get('queue_paused') else 'active'}\n"
                    f"next_epoch={payload.get('next_epoch')}"
                ),
            )
        )
    elif event.type == "tool.started":
        blocks.append(
            progress_block(
                "工具已开始",
                format_tool_progress(payload, status="running"),
            )
        )
    elif event.type == "tool.output.delta":
        blocks.append(
            progress_block(
                "工具输出",
                format_tool_progress(payload, status="output", text_label="输出"),
            )
        )
    elif event.type == "tool.completed":
        blocks.append(
            progress_block(
                "工具已完成",
                format_tool_progress(payload, status="completed", text_label="结果"),
            )
        )
    elif event.type == "tool.failed":
        blocks.append(
            warning_block(
                "工具失败",
                format_tool_progress(payload, status="failed", text_label="错误"),
            )
        )
    elif event.type == "approval.requested":
        visibility = RenderVisibility.APPROVERS
        interaction_id = str(event.interaction_id or "")
        required_votes = payload.get("required_votes")
        blocks.append(
            warning_block(
                "需要审批",
                (
                    f"Interaction：{interaction_id}\n"
                    f"风险等级：{payload.get('risk_level')}\n"
                    f"需要票数：{required_votes}\n"
                    f"{payload.get('prompt') or ''}"
                ),
            )
        )
        actions.extend(
            [
                RenderAction(
                    id=f"approve-{interaction_id}",
                    label="批准一次",
                    command=f"/agent approve {interaction_id} once",
                    style=RenderActionStyle.PRIMARY,
                ),
                RenderAction(
                    id=f"deny-{interaction_id}",
                    label="拒绝",
                    command=f"/agent deny {interaction_id}",
                    style=RenderActionStyle.DANGER,
                ),
            ]
        )
    elif event.type == "plan.requested":
        interaction_id = str(event.interaction_id or "")
        revise_command = f"/agent plan revise {interaction_id} <feedback>"
        title = "需要确认计划"
        blocks.append(
            text_block(
                title,
                (
                    f"Interaction：{interaction_id}\n"
                    f"{payload.get('prompt') or ''}\n"
                    f"要求修改：{revise_command}"
                ),
            )
        )
        actions.extend(
            [
                RenderAction(
                    id=f"plan-approve-{interaction_id}",
                    label="批准计划",
                    command=f"/agent plan approve {interaction_id}",
                    style=RenderActionStyle.PRIMARY,
                ),
                RenderAction(
                    id=f"plan-revise-{interaction_id}",
                    type=RenderActionType.MODAL,
                    label="要求修改",
                    command=revise_command,
                    command_template=f"/agent plan revise {interaction_id} {{feedback}}",
                    input=RenderActionInput(
                        name="feedback",
                        label="修改意见",
                        placeholder="说明希望 Agent 调整的计划",
                        multiline=True,
                    ),
                ),
                RenderAction(
                    id=f"plan-show-{interaction_id}",
                    label="查看计划",
                    command=f"/agent plan show {interaction_id}",
                ),
                RenderAction(
                    id=f"plan-cancel-{interaction_id}",
                    label="取消计划",
                    command=f"/agent plan cancel {interaction_id}",
                    style=RenderActionStyle.DANGER,
                ),
            ]
        )
    elif event.type in {"interaction.requested", "question.requested"}:
        interaction_id = str(event.interaction_id or "")
        title = "需要回答"
        question_options = render_options_from_payload(payload.get("options"))
        option_lines = "\n".join(
            f"{index}. {option.label}" for index, option in enumerate(question_options, start=1)
        )
        option_text = f"\n选项：\n{option_lines}" if option_lines else ""
        blocks.append(
            text_block(
                title,
                (
                    f"Interaction：{interaction_id}\n"
                    f"{payload.get('prompt') or ''}"
                    f"{option_text}"
                ),
            )
        )
        if question_options:
            actions.append(
                RenderAction(
                    id=f"answer-select-{interaction_id}",
                    type=RenderActionType.SELECT,
                    label="选择回答",
                    command=f"/agent answer {interaction_id} <answer>",
                    command_template=f"/agent answer {interaction_id} {{answer}}",
                    input=RenderActionInput(
                        name="answer",
                        label="回答",
                        placeholder="选择一个回答",
                    ),
                    options=question_options,
                    style=RenderActionStyle.PRIMARY,
                )
            )
        else:
            actions.append(
                RenderAction(
                    id=f"answer-{interaction_id}",
                    label="回答",
                    command=f"/agent answer {interaction_id} <answer>",
                    style=RenderActionStyle.PRIMARY,
                )
            )
    elif event.type == "approval.voted":
        visibility = RenderVisibility.APPROVERS
        vote_label = "批准" if payload.get("approve") else "拒绝"
        blocks.append(
            text_block(
                "审批投票",
                (
                    f"{payload.get('actor_id')} 已{vote_label}\n"
                    f"状态：{payload.get('status')}\n"
                    f"票数：{len(payload.get('votes') or {})}/{payload.get('required_votes')}"
                ),
            )
        )
    elif event.type == "interaction.answered":
        blocks.append(
            text_block(
                "交互已回答",
                f"状态：{payload.get('status')}\n回答：{payload.get('answer')}",
            )
        )
    elif event.type == "interaction.cancelled":
        visibility = RenderVisibility.OPERATORS
        blocks.append(
            warning_block(
                "交互已取消",
                f"状态：{payload.get('status')}\n原因：{payload.get('reason') or '未提供'}",
            )
        )
    elif event.type == "interaction.expired":
        visibility = RenderVisibility.OPERATORS
        blocks.append(
            warning_block(
                "交互已过期",
                (
                    f"Interaction：{event.interaction_id}\n"
                    f"过期时间：{payload.get('expires_at')}"
                ),
            )
        )
    elif event.type == "bot.interaction.ack":
        visibility = RenderVisibility.OPERATORS
        blocks.append(
            text_block(
                "Bot 交互已确认",
                (
                    f"平台：{payload.get('platform')}\n"
                    f"类型：{payload.get('interaction_kind')}\n"
                    f"Actor：{payload.get('actor_id')}\n"
                    f"命令：{payload.get('canonical_command')}"
                ),
            )
        )
    elif event.type == "lease.acquired":
        visibility = RenderVisibility.OPERATORS
        blocks.append(
            text_block(
                "控制权",
                (
                    f"{payload.get('owner_type')}:{payload.get('owner_id')} "
                    f"取得写入租约 epoch={payload.get('epoch')}"
                ),
            )
        )
    elif event.type == "lease.released":
        visibility = RenderVisibility.OPERATORS
        blocks.append(
            text_block(
                "控制权",
                (
                    f"释放 epoch={payload.get('released_epoch')}；"
                    f"next_epoch={payload.get('next_epoch')}"
                ),
            )
        )
    elif event.type == "terminal.started":
        visibility = RenderVisibility.OPERATORS
        blocks.append(
            text_block(
                "终端",
                f"已启动 `{payload.get('command')}`，Workspace：{payload.get('workspace_id')}",
            )
        )
    elif event.type == "terminal.exited":
        visibility = RenderVisibility.OPERATORS
        blocks.append(
            warning_block(
                "终端已退出",
                (
                    f"exit_code={payload.get('exit_code')}；"
                    f"pid={payload.get('pid')}；"
                    f"output_cursor={payload.get('output_cursor')}"
                ),
            )
        )
    elif event.type == "terminal.lost":
        visibility = RenderVisibility.OPERATORS
        blocks.append(
            warning_block(
                "终端状态丢失",
                (
                    f"generation={payload.get('generation')}；"
                    f"reason={payload.get('reason')}；"
                    f"backend={payload.get('backend')}"
                ),
            )
        )
    elif event.type == "terminal.auto_restart.skipped":
        visibility = RenderVisibility.OPERATORS
        blocks.append(
            warning_block(
                "终端自动重启已跳过",
                (
                    f"generation={payload.get('generation')}；"
                    f"reason={payload.get('reason')}；"
                    f"command={payload.get('command')}；"
                    f"allowed_patterns={payload.get('allowed_patterns')}"
                ),
            )
        )
    elif event.type == "device_identity.certificates_scanned":
        visibility = RenderVisibility.OPERATORS
        action_required_count = int(payload.get("action_required_count") or 0)
        renewal_action_required_count = int(
            payload.get("renewal_action_required_count") or 0
        )
        status_counts = payload.get("status_counts") or {}
        renewal_status_counts = payload.get("renewal_status_counts") or {}
        action_required_devices = payload.get("action_required_devices") or []
        block_factory = warning_block if action_required_count else text_block
        blocks.append(
            block_factory(
                "设备证书扫描",
                (
                    f"扫描设备数：{payload.get('total_device_count', 0)}\n"
                    f"需要处理：{action_required_count}\n"
                    f"状态汇总：{format_status_counts(status_counts)}\n"
                    f"续期需处理：{renewal_action_required_count}\n"
                    f"续期汇总：{format_status_counts(renewal_status_counts)}\n"
                    f"预警窗口：{payload.get('warning_days')} 天\n"
                    f"扫描时间：{payload.get('scanned_at')}"
                ),
            )
        )
        if action_required_devices:
            blocks.append(
                text_block(
                    "需要处理的设备",
                    format_certificate_scan_devices(action_required_devices),
                )
            )
    elif event.type == "bot.notification":
        visibility = RenderVisibility.OPERATORS
        delivery_records = payload.get("delivery_records") or []
        delivery_count = len(delivery_records) if isinstance(delivery_records, list) else 0
        blocks.append(
            text_block(
                "Bot 通知",
                (
                    f"源事件：{payload.get('source_event_type')}\n"
                    f"源事件 ID：{payload.get('source_event_id')}\n"
                    f"Chat Context：{payload.get('chat_context_id')}\n"
                    f"平台：{payload.get('platform')}\n"
                    f"投递记录：{delivery_count}\n"
                    f"状态汇总：{format_status_counts(payload.get('delivery_status_counts'))}"
                ),
            )
        )
    elif event.type in {
        "bot.message.received",
        "bot.command.received",
        "bot.slash_command.received",
        "bot.action.clicked",
        "bot.selection.submitted",
        "bot.modal.submitted",
        "bot.attachment.received",
    }:
        visibility = RenderVisibility.OPERATORS
        blocks.append(
            text_block(
                "Bot 上行事件",
                (
                    f"类型：{event.type}\n"
                    f"平台：{payload.get('platform')}\n"
                    f"Chat Context：{payload.get('chat_context_id')}\n"
                    f"Actor：{payload.get('actor_id')}\n"
                    f"平台事件：{payload.get('platform_event_id')}\n"
                    f"原文：{payload.get('raw_text') or '-'}"
                ),
            )
        )
    else:
        blocks.append(text_block("事件", event.type))
        if payload:
            blocks.append(code_block("json", stable_jsonish(payload)))

    return RenderDocument(
        id=f"rend_{uuid4().hex[:12]}",
        title=title,
        blocks=blocks,
        actions=actions,
        update_key=render_document_update_key(event),
        visibility=visibility,
    )


def render_document_update_key(event: SemanticEvent) -> str:
    if event.type in {"assistant.delta", "assistant.completed"}:
        answer_scope = event.turn_id or event.session_id or "stream"
        return f"{event.stream_id}:assistant:{answer_scope}"
    return f"{event.stream_id}:{event.seq}"


def event_title(event: SemanticEvent) -> str:
    session_suffix = f" · {event.session_id}" if event.session_id else ""
    return f"{event.type}{session_suffix}"


def text_block(title: str | None, text: str) -> RenderBlock:
    return RenderBlock(type=RenderBlockType.TEXT, title=title, text=text)


def markdown_block(text: str) -> RenderBlock:
    return RenderBlock(type=RenderBlockType.MARKDOWN, text=text)


def warning_block(title: str, text: str) -> RenderBlock:
    return RenderBlock(type=RenderBlockType.WARNING, title=title, text=text)


def progress_block(title: str, text: str) -> RenderBlock:
    return RenderBlock(type=RenderBlockType.PROGRESS, title=title, text=text)


def code_block(language: str, code: str) -> RenderBlock:
    return RenderBlock(type=RenderBlockType.CODE, language=language, code=code)


def format_tool_progress(
    payload: dict[str, Any],
    *,
    status: str,
    text_label: str | None = None,
) -> str:
    tool_name = first_non_empty_string(
        payload.get("tool_name"),
        payload.get("name"),
        payload.get("command"),
        default="unknown",
    )
    lines = [f"工具：{tool_name}", f"状态：{status}"]
    adapter_item_id = first_non_empty_string(
        payload.get("adapter_item_id"),
        payload.get("item_id"),
        payload.get("request_id"),
    )
    if adapter_item_id:
        lines.append(f"Item：{adapter_item_id}")
    detail = first_non_empty_string(
        payload.get("error") if status == "failed" else None,
        payload.get("text"),
        payload.get("output"),
        payload.get("summary"),
    )
    if detail and text_label:
        lines.append(f"{text_label}：")
        lines.append(detail)
    return "\n".join(lines)


def first_non_empty_string(*values: Any, default: str | None = None) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


class PlainTextRenderer:
    def __init__(self, max_message_chars: int = 1800) -> None:
        if max_message_chars < 80:
            raise ValueError("max_message_chars must be at least 80")
        self.max_message_chars = max_message_chars

    def render(self, document: RenderDocument) -> list[str]:
        text = self.render_one(document)
        return split_message(text, self.max_message_chars)

    def render_one(self, document: RenderDocument) -> str:
        parts: list[str] = []
        if document.title:
            parts.append(document.title)
        for block in document.blocks:
            rendered = self.render_block(block)
            if rendered:
                parts.append(rendered)
        if document.actions:
            parts.append(self.render_actions(document.actions))
        return "\n\n".join(parts).strip()

    def render_block(self, block: RenderBlock) -> str:
        prefix = f"{block.title}\n" if block.title else ""
        if block.type in {
            RenderBlockType.TEXT,
            RenderBlockType.MARKDOWN,
            RenderBlockType.PROGRESS,
            RenderBlockType.TOOL,
            RenderBlockType.FILE,
        }:
            return f"{prefix}{block.text or ''}".strip()
        if block.type == RenderBlockType.WARNING:
            return f"{prefix}WARNING: {block.text or ''}".strip()
        if block.type in {RenderBlockType.CODE, RenderBlockType.DIFF}:
            language = block.language or ""
            code = block.code or block.text or ""
            return f"{prefix}```{language}\n{code}\n```".strip()
        if block.type == RenderBlockType.DIVIDER:
            return "---"
        return block.text or ""

    def render_actions(self, actions: list[RenderAction]) -> str:
        lines = ["可用操作："]
        for index, action in enumerate(actions, start=1):
            lines.append(f"{index}. {action.label} -> {action.command}")
        return "\n".join(lines)


class OneBotV11TextRenderer(PlainTextRenderer):
    """OneBot V11-safe text fallback. Rich CQ buttons are intentionally not emitted yet."""


def render_action_descriptors(actions: list[RenderAction]) -> list[dict[str, Any]]:
    return [
        render_action_descriptor(action).model_dump(mode="json", exclude_none=True)
        for action in actions
    ]


def render_action_descriptor(action: RenderAction) -> RenderActionDescriptor:
    payload: dict[str, Any] = {
        "action_id": action.id,
        "label": action.label,
        "style": action.style.value,
    }
    callback_data = action.command
    if action.type in {RenderActionType.MODAL, RenderActionType.SELECT}:
        callback_data = action.id
        payload["type"] = action.type.value
        payload["fallback_command"] = action.command
        if action.command_template:
            payload["command_template"] = action.command_template
        if action.input:
            payload["input"] = action.input.model_dump(mode="json", exclude_none=True)
        if action.options:
            payload["options"] = [
                option.model_dump(mode="json", exclude_none=True)
                for option in action.options
            ]
    else:
        payload["command"] = action.command
        payload["callback_data"] = action.command
    return RenderActionDescriptor(
        id=action.id,
        type=action.type,
        label=action.label,
        style=action.style,
        command=action.command,
        callback_data=callback_data,
        command_template=action.command_template,
        input=action.input,
        options=action.options or None,
        payload=payload,
    )


def render_options_from_payload(value: Any) -> list[RenderActionOption]:
    if not isinstance(value, list):
        return []
    options: list[RenderActionOption] = []
    for item in value:
        if isinstance(item, str):
            label = item.strip()
            if label:
                options.append(RenderActionOption(label=label, value=label))
            continue
        if not isinstance(item, dict):
            continue
        label_value = item.get("label") or item.get("text") or item.get("name")
        value_value = item.get("value") or item.get("id") or label_value
        label = str(label_value).strip() if label_value is not None else ""
        option_value = str(value_value).strip() if value_value is not None else ""
        if not label or not option_value:
            continue
        description = item.get("description")
        options.append(
            RenderActionOption(
                label=label,
                value=option_value,
                description=str(description).strip() if description is not None else None,
            )
        )
    return options


def split_message(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for part in iter_message_parts(text):
        if part.code is not None and len(part.text) > max_chars:
            current = flush_message_chunk(chunks, current)
            chunks.extend(split_code_fence(part.language or "", part.code, max_chars))
            continue
        for piece in split_plain_text(part.text, max_chars):
            current = append_message_piece(chunks, current, piece, max_chars)
    flush_message_chunk(chunks, current)
    return chunks


def iter_message_parts(text: str) -> list[_MessagePart]:
    parts: list[_MessagePart] = []
    cursor = 0
    for match in _CODE_FENCE_RE.finditer(text):
        if match.start() > cursor:
            parts.append(_MessagePart(text=text[cursor : match.start()]))
        parts.append(
            _MessagePart(
                text=match.group(0),
                language=match.group(1).strip(),
                code=match.group(2),
            )
        )
        cursor = match.end()
    if cursor < len(text):
        parts.append(_MessagePart(text=text[cursor:]))
    return parts


def append_message_piece(
    chunks: list[str], current: str, piece: str, max_chars: int
) -> str:
    if not piece:
        return current
    if not current:
        return piece.lstrip("\n")
    if len(current) + len(piece) <= max_chars:
        return f"{current}{piece}"
    flush_message_chunk(chunks, current)
    return piece.lstrip("\n")


def flush_message_chunk(chunks: list[str], current: str) -> str:
    chunk = current.rstrip("\n")
    if chunk:
        chunks.append(chunk)
    return ""


def split_plain_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in text.split("\n\n"):
        paragraph_len = len(paragraph)
        separator_len = 2 if current else 0
        if current and current_len + separator_len + paragraph_len > max_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        if paragraph_len > max_chars:
            chunks.extend(split_hard(paragraph, max_chars))
            continue
        current.append(paragraph)
        current_len += separator_len + paragraph_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def split_hard(text: str, max_chars: int) -> list[str]:
    return [text[index : index + max_chars] for index in range(0, len(text), max_chars)]


def split_code_fence(language: str, code: str, max_chars: int) -> list[str]:
    opening = code_fence_opening(language, max_chars)
    closing = "\n```"
    content_limit = max_chars - len(opening) - len(closing)
    pieces = split_code_content(code, max(content_limit, 1))
    return [f"{opening}{piece}{closing}" for piece in pieces]


def code_fence_opening(language: str, max_chars: int) -> str:
    opening = f"```{language}\n" if language else "```\n"
    if len(opening) + len("\n```") + 1 <= max_chars:
        return opening
    return "```\n"


def split_code_content(code: str, max_chars: int) -> list[str]:
    if len(code) <= max_chars:
        return [code]
    chunks: list[str] = []
    current = ""
    for line in code.splitlines(keepends=True):
        while len(line) > max_chars:
            current = flush_code_content_chunk(chunks, current)
            chunks.append(line[:max_chars])
            line = line[max_chars:]
        if current and len(current) + len(line) > max_chars:
            current = flush_code_content_chunk(chunks, current)
        current += line
    flush_code_content_chunk(chunks, current)
    return chunks or [""]


def flush_code_content_chunk(chunks: list[str], current: str) -> str:
    if current:
        chunks.append(current)
    return ""


def stable_jsonish(value: dict[str, Any]) -> str:
    lines = ["{"]
    for key in sorted(value):
        lines.append(f"  {key!r}: {value[key]!r},")
    lines.append("}")
    return "\n".join(lines)


def format_status_counts(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "-"
    return ", ".join(
        f"{key}={value[key]}" for key in sorted(value) if value[key]
    ) or "无异常"


def format_certificate_scan_devices(value: Any) -> str:
    if not isinstance(value, list):
        return "-"
    lines: list[str] = []
    valid_items = [item for item in value if isinstance(item, dict)]
    for item in valid_items[:8]:
        pieces = [
            str(item.get("device_id") or "-"),
            str(item.get("certificate_health_status") or "unknown"),
        ]
        if item.get("expired_count"):
            pieces.append(f"expired={item['expired_count']}")
        if item.get("expiring_count"):
            pieces.append(f"expiring={item['expiring_count']}")
        if item.get("untracked_certificate_count"):
            pieces.append(f"untracked={item['untracked_certificate_count']}")
        if item.get("missing_validity_count"):
            pieces.append(f"missing_validity={item['missing_validity_count']}")
        if item.get("renewal_status"):
            pieces.append(f"renewal={item['renewal_status']}")
        if item.get("renewal_due_count"):
            pieces.append(f"renewal_due={item['renewal_due_count']}")
        if item.get("renewal_overdue_count"):
            pieces.append(f"renewal_overdue={item['renewal_overdue_count']}")
        if item.get("renewal_due_at"):
            pieces.append(f"renewal_due_at={item['renewal_due_at']}")
        if item.get("next_expires_at"):
            pieces.append(f"next={item['next_expires_at']}")
        lines.append(" · ".join(pieces))
    remaining = len(valid_items) - len(lines)
    if remaining > 0:
        lines.append(f"... {remaining} more")
    return "\n".join(lines) if lines else "-"
