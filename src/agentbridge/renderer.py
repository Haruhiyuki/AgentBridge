from __future__ import annotations

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
    label: str
    command: str
    style: RenderActionStyle = RenderActionStyle.DEFAULT


class RenderDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str | None = None
    blocks: list[RenderBlock] = Field(default_factory=list)
    actions: list[RenderAction] = Field(default_factory=list)
    update_key: str | None = None
    visibility: RenderVisibility = RenderVisibility.PUBLIC


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
        blocks.append(
            progress_block(
                "任务已排队",
                f"Turn：{event.turn_id}\nPrompt 长度：{payload.get('prompt_length')}",
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
    elif event.type == "interaction.requested":
        interaction_id = str(event.interaction_id or "")
        blocks.append(
            text_block(
                "需要回答",
                f"Interaction：{interaction_id}\n{payload.get('prompt') or ''}",
            )
        )
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
    else:
        blocks.append(text_block("事件", event.type))
        if payload:
            blocks.append(code_block("json", stable_jsonish(payload)))

    return RenderDocument(
        id=f"rend_{uuid4().hex[:12]}",
        title=title,
        blocks=blocks,
        actions=actions,
        update_key=f"{event.stream_id}:{event.seq}",
        visibility=visibility,
    )


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


def split_message(text: str, max_chars: int) -> list[str]:
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


def stable_jsonish(value: dict[str, Any]) -> str:
    lines = ["{"]
    for key in sorted(value):
        lines.append(f"  {key!r}: {value[key]!r},")
    lines.append("}")
    return "\n".join(lines)
