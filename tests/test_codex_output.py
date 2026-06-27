from __future__ import annotations

from agentbridge.codex_output import extract_codex_answer

# 取自真实 Codex 0.142 TUI 的 pane 抓取（⏺ 标记 + 2 空格缩进续行 + ✻ 状态 + 输入框边框）。
REAL_PANE = """  - 继续之前持久会话方向的开发工作

✻ Crunched for 6s

❯ 用一句话介绍这个项目

  Read 1 file

⏺ AgentBridge 是一个本地编程 Agent 协作平台——它让 Claude Code、Codex 等原生 CLI
  Agent 在可见的本地终端里持续运行，并通过一个控制平面把项目、会话、指令、写锁、
  交互、审计等能力以结构化 API
  暴露出来，从而支持以聊天机器人驱动的群聊式多项目、多会话协作工作流。

✻ Worked for 8s

────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents"""


def test_extracts_codex_answer_block():
    answer = extract_codex_answer(REAL_PANE)
    assert answer.startswith("AgentBridge 是一个本地编程 Agent 协作平台")
    assert answer.endswith("多会话协作工作流。")
    # 不含 TUI chrome / 标记 / 状态行。
    assert "⏺" not in answer
    assert "✻" not in answer
    assert "❯" not in answer
    assert "─" not in answer
    assert "Worked for" not in answer
    assert "Read 1 file" not in answer


def test_takes_last_answer_block():
    pane = (
        "⏺ 第一段旧回答\n\n✻ Worked for 1s\n\n❯ 新问题\n\n⏺ 这是最新的回答。\n\n✻ Worked for 2s"
    )
    assert extract_codex_answer(pane) == "这是最新的回答。"


def test_returns_empty_without_marker():
    assert extract_codex_answer("没有任何助手标记的纯文本\n更多文本") == ""
    assert extract_codex_answer("") == ""
