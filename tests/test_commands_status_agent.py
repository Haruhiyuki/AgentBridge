from __future__ import annotations

from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, AgentType, LeaseOwnerType, SessionStatus


def make_context(control: ControlPlane):
    return control.get_or_create_chat_context(
        bot_instance_id="bot-test",
        platform="onebot.v11",
        chat_space_id="group-status",
    )


def run(commands: CommandService, raw_text: str, actor: Actor, context_id: str, key: str):
    invocation = commands.parse(
        raw_text=raw_text,
        actor=actor,
        chat_context_id=context_id,
        idempotency_key=key,
        trace_id=key,
    )
    return commands.execute(invocation)


def bootstrap(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    actor = Actor(id="usr_1", roles={"maintainer"})
    run(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path} --alias backend",
        actor,
        context.id,
        "create-project",
    )
    return control, commands, context, actor


def test_agent_switch_creates_then_reuses_session(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)

    first = run(commands, "/ab claude", actor, context.id, "claude-1")
    assert first.canonical_command == "agent.switch"
    assert first.data["agent_type"] == "claude"
    assert first.data["created"] is True
    claude_session_id = first.data["session_id"]
    assert control.repository.get_chat_context(context.id).active_session_id == claude_session_id
    assert "已新建并切换到 Claude" in first.message

    # 再次切到 Claude：复用已存在的会话，不再新建。
    again = run(commands, "/ab claude", actor, context.id, "claude-2")
    assert again.data["created"] is False
    assert again.data["session_id"] == claude_session_id

    # 切到 Codex：新建一个独立会话，活动指针随之切换。
    codex = run(commands, "/ab codex", actor, context.id, "codex-1")
    assert codex.data["agent_type"] == "codex"
    assert codex.data["created"] is True
    codex_session_id = codex.data["session_id"]
    assert codex_session_id != claude_session_id
    assert control.repository.get_chat_context(context.id).active_session_id == codex_session_id

    claude_session = control.repository.get_session(claude_session_id)
    codex_session = control.repository.get_session(codex_session_id)
    assert claude_session.agent_type == AgentType.CLAUDE
    assert codex_session.agent_type == AgentType.CODEX


def test_agent_switch_with_trailing_task_enqueues(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)

    result = run(commands, "/ab codex 修复登录接口的500错误", actor, context.id, "codex-task")
    assert result.data["agent_type"] == "codex"
    assert "turn_id" in result.data
    turn = control.repository.get_turn(result.data["turn_id"])
    assert turn.prompt == "修复登录接口的500错误"
    assert turn.session_id == result.data["session_id"]
    assert "任务已进入" in result.message


def test_agent_list_groups_and_marks_active(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    run(commands, "/ab claude", actor, context.id, "c1")
    run(commands, "/ab codex", actor, context.id, "x1")  # 切到 codex，codex 成为活动会话

    listing = run(commands, "/ab agents", actor, context.id, "agents-1")
    assert listing.canonical_command == "agent.list"
    assert "Claude" in listing.message
    assert "Codex" in listing.message
    # 活动会话（codex）应带有 ▶ 标记
    codex_line = [line for line in listing.message.splitlines() if "Codex" in line][0]
    assert codex_line.startswith("▶")
    assert len(listing.data["sessions"]) == 2


def test_status_card_reports_session_lease_and_queue(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    switched = run(commands, "/ab codex 跑一下测试", actor, context.id, "codex-q")
    session_id = switched.data["session_id"]

    status = run(commands, "/ab status", actor, context.id, "status-1")
    assert status.canonical_command == "status.show"
    assert "AgentBridge 状态" in status.message
    assert "Backend" in status.message
    assert "Codex" in status.message
    assert "控制权" in status.message
    assert "队列：1 个排队任务" in status.message
    assert status.data["session_id"] == session_id
    assert status.data["queued_count"] == 1


def test_status_reports_human_takeover(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    switched = run(commands, "/ab claude", actor, context.id, "claude-h")
    session_id = switched.data["session_id"]
    control.acquire_lease(
        actor=actor,
        session_id=session_id,
        owner_type=LeaseOwnerType.HUMAN,
        owner_id="local-human",
        ttl_seconds=300,
        trace_id="human-takeover",
        chat_context_id=context.id,
    )

    status = run(commands, "/ab status", actor, context.id, "status-h")
    assert "本地用户接管中" in status.message


def test_status_without_active_session(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    status = run(commands, "/ab status", actor, context.id, "status-empty")
    assert "无活动会话" in status.message
    assert "Backend" in status.message


def test_ask_auto_creates_and_binds_session_then_sticks(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    # 进项目后未指定会话；首次发消息默认新建并绑定。
    first = run(commands, "/ab ask 第一条任务", actor, context.id, "ask-1")
    assert first.canonical_command == "turn.enqueue"
    assert "已新建并绑定" in first.message
    session_id = first.data["session_id"]
    assert control.repository.get_chat_context(context.id).active_session_id == session_id

    # 再次发消息黏在同一会话，不再新建。
    second = run(commands, "/ab ask 第二条任务", actor, context.id, "ask-2")
    assert "已新建并绑定" not in second.message
    assert second.data["session_id"] == session_id


def test_session_list_shows_terminal_title_and_active_marker(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    run(commands, "/ab claude", actor, context.id, "c1")
    active_id = control.repository.get_chat_context(context.id).active_session_id
    control.repository.set_terminal_title(active_id, "修复登录 500")

    listing = run(commands, "/ab sessions", actor, context.id, "ls")
    assert listing.canonical_command == "session.list"
    assert "修复登录 500" in listing.message  # 终端标题展示
    assert "◀ 当前" in listing.message  # 当前会话标记


def test_projects_and_sessions_quick_aliases(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    assert run(commands, "/ab projects", actor, context.id, "p").canonical_command == "project.list"
    assert run(commands, "/ab sessions", actor, context.id, "s").canonical_command == "session.list"


def test_switch_locks_agent(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    res = run(commands, "/ab claude", actor, context.id, "lock-claude")
    assert "已锁定 Claude" in res.message
    assert control.repository.get_chat_context(context.id).preferred_agent.value == "claude"


def test_locked_agent_overrides_project_default_on_auto_create(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    # 项目默认是 claude；锁定 codex 后首次发消息自动新建的会话应为 codex。
    control.repository.set_preferred_agent(context.id, AgentType.CODEX)
    asked = run(commands, "/ab ask 干活", actor, context.id, "ask")
    new_session = control.repository.get_session(asked.data["session_id"])
    assert new_session.agent_type.value == "codex"


def test_agent_show_and_unlock(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    run(commands, "/ab codex", actor, context.id, "lock-codex")

    shown = run(commands, "/ab agent", actor, context.id, "show")
    assert shown.canonical_command == "agent.show"
    assert "Codex" in shown.message
    assert "🔒" in shown.message

    unlocked = run(commands, "/ab agent unlock", actor, context.id, "unlock")
    assert unlocked.canonical_command == "agent.unlock"
    assert control.repository.get_chat_context(context.id).preferred_agent is None


def test_status_shows_agent_lock_line(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    run(commands, "/ab claude 你好", actor, context.id, "c")
    status = run(commands, "/ab status", actor, context.id, "st")
    assert "Agent：" in status.message
    assert "已锁定 Claude" in status.message


def test_new_creates_fresh_bound_session_with_locked_agent(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    control.repository.set_preferred_agent(context.id, AgentType.CODEX)
    res = run(commands, "/ab new", actor, context.id, "new")
    assert res.canonical_command == "session.create"
    session = control.repository.get_session(res.data["session_id"])
    assert session.agent_type.value == "codex"  # 用锁定的 agent
    assert control.repository.get_chat_context(context.id).active_session_id == session.id


def test_clear_enqueues_agent_clear_command(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    run(commands, "/ab claude", actor, context.id, "c")
    cleared = run(commands, "/ab clear", actor, context.id, "clear")
    assert cleared.canonical_command == "session.clear"
    turn = control.repository.get_turn(cleared.data["turn_id"])
    assert turn.prompt == "/clear"


def test_sessions_hides_unusable_and_prune_closes_them(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    run(commands, "/ab claude", actor, context.id, "c1")  # active claude session
    # 再建一个会话并置为 recovering（离线保护）。
    dead = run(commands, "/ab new --agent codex", actor, context.id, "n1")
    dead_id = dead.data["session_id"]
    run(commands, "/ab claude", actor, context.id, "c2")  # 切回 claude，dead 不再是当前
    control.set_terminal_agent_offline_protection(
        actor=actor, offline=True, session_id=dead_id, trace_id="off"
    )
    assert control.repository.get_session(dead_id).status == SessionStatus.RECOVERING

    listed = run(commands, "/ab sessions", actor, context.id, "ls")
    assert dead.data["session_id"] not in [s["id"] for s in listed.data["sessions"]]
    assert listed.data["hidden_count"] >= 1
    assert "已隐藏" in listed.message

    pruned = run(commands, "/ab session prune", actor, context.id, "prune")
    assert pruned.canonical_command == "session.prune"
    assert control.repository.get_session(dead_id).status == SessionStatus.CLOSED


def test_sessions_all_shows_everything(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    run(commands, "/ab claude", actor, context.id, "c1")
    dead = run(commands, "/ab new --agent codex", actor, context.id, "n1")
    run(commands, "/ab claude", actor, context.id, "c2")
    control.set_terminal_agent_offline_protection(actor=actor, offline=True,
        session_id=dead.data["session_id"], trace_id="off"
    )
    listed = run(commands, "/ab sessions --all", actor, context.id, "all")
    assert dead.data["session_id"] in [s["id"] for s in listed.data["sessions"]]


def test_help_is_grouped_and_comprehensive(tmp_path):
    control, commands, context, actor = bootstrap(tmp_path)
    helped = run(commands, "/ab help", actor, context.id, "help-1")
    for marker in ("看状态", "切换 Agent", "发任务", "人工接管", "交互 / 审批"):
        assert marker in helped.message
