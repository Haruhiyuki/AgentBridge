"""从 Codex 的原生 TUI 输出里解析出"最终回答"。

显式终端路线下，Codex 在 tmux 里跑原生 TUI，没有结构化事件可用。Codex 把助手回复以
``⏺`` 标记起头、续行 2 空格缩进显示，回复后跟 ``✻ Worked for …`` 状态行与输入框边框。
本模块从抓取到的 pane 文本里提取最后一段助手回复，供"空闲完成"时作为 assistant.delta 投递，
让 Codex 也能在群里给出真实答案（而非占位）。

解析尽量保守：定位最后一个 ``⏺`` 标记，收集其后到状态/输入/边框等终止标记之前的行；提取不到
就返回空串，由上层回退到中性完成提示。
"""

from __future__ import annotations

# Codex 助手消息的起始标记（不同版本可能是实心圆点的变体）。
_ANSWER_MARKERS = ("⏺", "●", "•")
# 遇到这些行视为助手回复结束：状态行、输入提示、边框、模式条、或下一条消息标记。
_TERMINATORS = ("✻", "❯", "›", "─", "━", "╭", "╰", "│", "⏵", "⏺", "●", "•")


def extract_codex_answer(pane_text: str) -> str:
    """从 Codex pane 文本里提取最后一段助手回复（提取不到返回空串）。"""
    lines = pane_text.replace("\r", "").split("\n")
    marker_index: int | None = None
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].lstrip().startswith(_ANSWER_MARKERS):
            marker_index = index
            break
    if marker_index is None:
        return ""

    first = lines[marker_index].lstrip()
    answer = [first[1:].strip()]  # 去掉起始标记字符
    for line in lines[marker_index + 1 :]:
        stripped = line.strip()
        if stripped.startswith(_TERMINATORS):
            break
        answer.append(stripped)
    while answer and not answer[-1]:
        answer.pop()
    return "\n".join(answer).strip()
