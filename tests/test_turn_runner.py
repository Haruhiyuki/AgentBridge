from __future__ import annotations

import shutil

import pytest

from datetime import timedelta

from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    Actor,
    AgentType,
    InteractionStatus,
    InteractionType,
    LeaseOwnerType,
    SemanticEventSource,
    SessionStatus,
    TurnStatus,
    Visibility,
    utc_now,
)
from agentbridge.terminal_agent import (
    FakeTerminalBackend,
    TerminalAgentService,
    TerminalInputKind,
)


def bootstrap(tmp_path, *, agent_type: AgentType = AgentType.CLAUDE, **terminal_kwargs):
    control = ControlPlane()
    terminal = TerminalAgentService(
        control, backend=FakeTerminalBackend(), **terminal_kwargs
    )
    actor = Actor(id="usr_1", roles={"maintainer"})
    project = control.create_project(
        actor=actor, name="Backend", default_agent=agent_type, trace_id="p"
    )
    workspace = control.add_workspace(
        actor=actor,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="w",
    )
    session = control.create_session(
        actor=actor,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Codex",
        agent_type=agent_type,
        visibility=Visibility.GROUP,
        trace_id="s",
    )
    return control, terminal, actor, session


def complete_turn(control: ControlPlane, session_id: str, turn_id: str, key: str) -> None:
    """模拟语义通道（Claude Stop hook / Codex 空闲启发式）落成的 turn 完成事件。"""
    control.ingest_session_event(
        session_id=session_id,
        event_type="turn.completed",
        source=SemanticEventSource.AGENT_ADAPTER,
        trace_id=key,
        turn_id=turn_id,
        idempotency_key=key,
    )


def test_advance_loop_runs_queue_to_completion(tmp_path):
    control, terminal, actor, session = bootstrap(tmp_path)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")

    turn1 = control.enqueue_turn(
        actor=actor, session_id=session.id, prompt="第一轮：跑测试", trace_id="t1"
    )
    turn2 = control.enqueue_turn(
        actor=actor, session_id=session.id, prompt="第二轮：修复失败用例", trace_id="t2"
    )

    first = terminal.advance_queue(session_id=session.id)
    assert first["action"] == "submitted"
    assert first["turn_id"] == turn1.id
    assert control.repository.get_session(session.id).active_turn_id == turn1.id

    # 上一轮还在跑时不抢提交。
    blocked = terminal.advance_queue(session_id=session.id)
    assert blocked["action"] == "skipped"
    assert blocked["reason"] == "turn_active"

    # 语义通道报告第一轮完成 → 会话回到 IDLE → 接力第二轮。
    complete_turn(control, session.id, turn1.id, "done-1")
    assert control.repository.get_session(session.id).status == SessionStatus.IDLE

    second = terminal.advance_queue(session_id=session.id)
    assert second["action"] == "submitted"
    assert second["turn_id"] == turn2.id

    complete_turn(control, session.id, turn2.id, "done-2")
    drained = terminal.advance_queue(session_id=session.id)
    assert drained["action"] == "skipped"
    assert drained["reason"] == "queue_empty"


def test_lifecycle_completion_without_turn_id_resolves_active_turn(tmp_path):
    # 复现真实坑：Claude Stop hook 报 turn.completed 但不知道 AgentBridge 的 turn_id，
    # 应归到会话当前活动的 turn 并完成，而不是 400 失败导致 turn 永不完成。
    control, terminal, actor, session = bootstrap(tmp_path)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    turn = control.enqueue_turn(
        actor=actor, session_id=session.id, prompt="任务", trace_id="t1"
    )
    terminal.advance_queue(session_id=session.id)
    assert control.repository.get_session(session.id).active_turn_id == turn.id

    control.ingest_session_event(
        session_id=session.id,
        event_type="turn.completed",
        source=SemanticEventSource.AGENT_ADAPTER,
        trace_id="stop-hook",
        turn_id=None,  # 适配器不带 turn_id
    )
    assert control.repository.get_turn(turn.id).status == TurnStatus.COMPLETED
    assert control.repository.get_session(session.id).status == SessionStatus.IDLE


def test_terminal_exit_fails_active_turn(tmp_path):
    # 终端在 turn 运行中退出/丢失时，该 turn 必须失败、会话回到 IDLE，否则僵尸占用会话。
    control, terminal, actor, session = bootstrap(tmp_path)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    turn = control.enqueue_turn(
        actor=actor, session_id=session.id, prompt="任务", trace_id="t1"
    )
    terminal.advance_queue(session_id=session.id)
    assert control.repository.get_turn(turn.id).status == TurnStatus.RUNNING

    terminal._fail_active_turn_after_terminal_loss(
        session_id=session.id, reason="terminal_exited", trace_id="exit"
    )
    assert control.repository.get_turn(turn.id).status == TurnStatus.FAILED
    assert control.repository.get_session(session.id).status == SessionStatus.IDLE
    assert control.repository.get_session(session.id).active_turn_id is None


def test_auto_open_visible_terminal_once(tmp_path):
    # 会话启动后自动打开一个可见桌面终端 attach；每会话只开一次。
    opened: list[str] = []
    control = ControlPlane()
    backend = FakeTerminalBackend()
    backend.attach_command = lambda *, session_id: f"tmux attach -t {session_id}"
    terminal = TerminalAgentService(
        control,
        backend=backend,
        auto_open_terminal=True,
        terminal_opener=opened.append,
    )
    actor = Actor(id="u", roles={"maintainer"})
    project = control.create_project(
        actor=actor, name="P", default_agent=AgentType.CLAUDE, trace_id="p"
    )
    ws = control.add_workspace(
        actor=actor,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="w",
    )
    session = control.create_session(
        actor=actor,
        project_id=project.id,
        workspace_id=ws.id,
        name="S",
        agent_type=AgentType.CLAUDE,
        visibility=Visibility.GROUP,
        trace_id="s",
    )
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    assert opened == [f"tmux attach -t {session.id}"]
    # 同一会话再次启动不重复开窗。
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start2")
    assert len(opened) == 1


def test_ensure_visible_window_grace_prevents_double_window(tmp_path):
    """刚开窗后宽限期内 ensure_visible_window 不再开窗：避免 attach 未完成时被误判为没人 attach
    而开出第二个 attach 同一会话的重复窗口（用户实测：一个会话冒出两个终端窗口）。"""
    opened: list[str] = []
    control = ControlPlane()
    backend = FakeTerminalBackend()
    backend.attach_command = lambda *, session_id: f"tmux attach -t {session_id}"
    # attach 握手未完成 → is_attached 瞬时为 False（真实场景里的竞态）。
    backend.is_attached = lambda *, session_id: False
    terminal = TerminalAgentService(
        control, backend=backend, auto_open_terminal=True, terminal_opener=opened.append
    )
    actor = Actor(id="u", roles={"maintainer"})
    project = control.create_project(
        actor=actor, name="P", default_agent=AgentType.CLAUDE, trace_id="p"
    )
    ws = control.add_workspace(
        actor=actor, project_id=project.id, machine_id="local",
        path=str(tmp_path), allowed_root=str(tmp_path), trace_id="w",
    )
    session = control.create_session(
        actor=actor, project_id=project.id, workspace_id=ws.id, name="S",
        agent_type=AgentType.CLAUDE, visibility=Visibility.GROUP, trace_id="s",
    )
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    assert opened == [f"tmux attach -t {session.id}"]
    # 紧接着生命周期监控调 ensure_visible_window（is_attached 仍 False）→ 宽限期内不重复开窗。
    assert terminal.ensure_visible_window(session.id) is False
    assert len(opened) == 1

    # 宽限期过后仍没人 attach（用户确实关掉了窗口）→ 才重新开窗。
    terminal.visible_window_reopen_grace_seconds = 0
    terminal.ensure_visible_window(session.id)
    assert len(opened) == 2


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux 未安装")
def test_tmux_backend_attach_command_and_cursor():
    from agentbridge.terminal_agent import TmuxTerminalBackend

    backend = TmuxTerminalBackend()
    cmd = backend.attach_command(session_id="ses_abc")
    assert "attach" in cmd
    assert "agentbridge_ses_abc" in cmd


def test_flush_pending_terminal_inputs_submits(tmp_path):
    control, terminal, actor, session = bootstrap(tmp_path)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    control.repository.queue_terminal_input(session.id, "立刻追加")

    submitted = terminal.flush_pending_terminal_inputs(session.id)
    assert len(submitted) == 1
    assert "立刻追加\r" in terminal.backend.snapshot(session_id=session.id)
    assert control.repository.drain_terminal_inputs(session.id) == []


def test_flush_yields_to_human_control(tmp_path):
    control, terminal, actor, session = bootstrap(tmp_path)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    control.acquire_lease(
        actor=actor,
        session_id=session.id,
        owner_type=LeaseOwnerType.HUMAN,
        owner_id="local",
        ttl_seconds=300,
        trace_id="h",
    )
    control.repository.queue_terminal_input(session.id, "x")

    # 人工接管时不抢输入，回退重排等下一拍。
    assert terminal.flush_pending_terminal_inputs(session.id) == []
    assert control.repository.drain_terminal_inputs(session.id) == ["x"]


def test_advance_yields_to_human_takeover(tmp_path):
    control, terminal, actor, session = bootstrap(tmp_path)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    control.enqueue_turn(actor=actor, session_id=session.id, prompt="任务", trace_id="t1")

    control.acquire_lease(
        actor=actor,
        session_id=session.id,
        owner_type=LeaseOwnerType.HUMAN,
        owner_id="local-user",
        ttl_seconds=300,
        trace_id="human",
    )

    outcome = terminal.advance_queue(session_id=session.id)
    assert outcome["action"] == "skipped"
    # 取得 HUMAN 租约会把会话置为 human_controlled，两道护栏（状态/租约）任一命中均可。
    assert "human" in str(outcome["reason"])
    # 任务仍排队，没有被机器人抢走提交。
    assert control.repository.get_session(session.id).active_turn_id is None


def test_advance_waits_for_terminal_warmup(tmp_path):
    # 预热未满时不向刚启动的 TUI 提交（避免丢键/不被当作回车）。
    control, terminal, actor, session = bootstrap(tmp_path, submit_warmup_seconds=100.0)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    control.enqueue_turn(actor=actor, session_id=session.id, prompt="任务", trace_id="t1")

    outcome = terminal.advance_queue(session_id=session.id)
    assert outcome["action"] == "skipped"
    assert outcome["reason"] == "terminal_warming_up"
    assert control.repository.get_session(session.id).active_turn_id is None


def test_advance_auto_starts_terminal(tmp_path):
    control, terminal, actor, session = bootstrap(tmp_path, agent_type=AgentType.GENERIC_TUI)
    control.enqueue_turn(actor=actor, session_id=session.id, prompt="任务", trace_id="t1")

    outcome = terminal.advance_queue(session_id=session.id, auto_start_terminal=True)
    assert outcome["action"] == "started_and_submitted"
    assert terminal.status(session_id=session.id).running is True


def test_advance_pending_queues_scans_idle_sessions(tmp_path):
    control, terminal, actor, session = bootstrap(tmp_path)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    control.enqueue_turn(actor=actor, session_id=session.id, prompt="任务", trace_id="t1")

    results = terminal.advance_pending_queues()
    assert len(results) == 1
    assert results[0]["session_id"] == session.id
    assert results[0]["action"] == "submitted"


def test_idle_completion_marks_codex_turn_done(tmp_path):
    control, terminal, actor, session = bootstrap(
        tmp_path,
        agent_type=AgentType.CODEX,
        idle_complete_seconds=3.0,
        idle_min_active_seconds=1.0,
    )
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    turn = control.enqueue_turn(
        actor=actor, session_id=session.id, prompt="跑测试", trace_id="t1"
    )
    submitted = terminal.advance_queue(session_id=session.id)
    assert submitted["turn_id"] == turn.id

    # t=100 建立基线（无完成）。
    assert terminal.check_idle_turn_completions(now=100.0) == []
    # Codex 产出一些输出 → 游标增长 → 刷新"最后变化"。
    terminal.backend.write(
        session_id=session.id, data="codex 正在分析…", kind=TerminalInputKind.TEXT
    )
    assert terminal.check_idle_turn_completions(now=101.0) == []
    # 仅静默 3 秒不足以判定（idle=3 但需 > 阈值的累积，且这里刚好等于）。
    assert terminal.check_idle_turn_completions(now=103.0) == []
    # 静默 >= 3 秒、活跃 >= 1 秒 → 判定完成。
    done = terminal.check_idle_turn_completions(now=110.0)
    assert len(done) == 1
    assert done[0]["turn_id"] == turn.id
    assert control.repository.get_turn(turn.id).status == TurnStatus.COMPLETED
    assert control.repository.get_session(session.id).status == SessionStatus.IDLE


def test_idle_completion_skips_claude(tmp_path):
    control, terminal, actor, session = bootstrap(
        tmp_path,
        agent_type=AgentType.CLAUDE,
        idle_complete_seconds=1.0,
        idle_min_active_seconds=0.0,
    )
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    turn = control.enqueue_turn(
        actor=actor, session_id=session.id, prompt="任务", trace_id="t1"
    )
    terminal.advance_queue(session_id=session.id)

    terminal.check_idle_turn_completions(now=1000.0)
    # 即便长时间静默，Claude 也不靠空闲启发式（它走 Stop hook）。
    assert terminal.check_idle_turn_completions(now=9000.0) == []
    assert control.repository.get_turn(turn.id).status == TurnStatus.RUNNING


def test_persistent_loop_drives_codex_queue_to_drain(tmp_path):
    # 完整持久回路：提交→空闲判定完成→接力下一轮→…→排空。阈值取 0 便于确定性推进。
    control, terminal, actor, session = bootstrap(
        tmp_path,
        agent_type=AgentType.CODEX,
        auto_advance_queues=True,
        idle_turn_completion=True,
        idle_complete_seconds=0.0,
        idle_min_active_seconds=0.0,
    )
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    turn1 = control.enqueue_turn(
        actor=actor, session_id=session.id, prompt="第一轮", trace_id="t1"
    )
    turn2 = control.enqueue_turn(
        actor=actor, session_id=session.id, prompt="第二轮", trace_id="t2"
    )

    for _ in range(10):
        terminal.run_lifecycle_monitor_once()

    assert control.repository.get_turn(turn1.id).status == TurnStatus.COMPLETED
    assert control.repository.get_turn(turn2.id).status == TurnStatus.COMPLETED
    queue_turns, _version, _paused = control.list_turn_queue(actor=actor, session_id=session.id)
    assert queue_turns == []


def test_lifecycle_monitor_auto_advances_when_enabled(tmp_path):
    control = ControlPlane()
    terminal = TerminalAgentService(
        control, backend=FakeTerminalBackend(), auto_advance_queues=True
    )
    actor = Actor(id="usr_1", roles={"maintainer"})
    project = control.create_project(
        actor=actor, name="Backend", default_agent=AgentType.CLAUDE, trace_id="p"
    )
    workspace = control.add_workspace(
        actor=actor,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="w",
    )
    session = control.create_session(
        actor=actor,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Claude",
        agent_type=AgentType.CLAUDE,
        visibility=Visibility.GROUP,
        trace_id="s",
    )
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    turn = control.enqueue_turn(
        actor=actor, session_id=session.id, prompt="跑测试", trace_id="t1"
    )

    terminal.run_lifecycle_monitor_once()

    assert control.repository.get_session(session.id).active_turn_id == turn.id


def test_advance_releases_bot_lease_when_queue_drains(tmp_path):
    """队列跑空后，advance 应释放自动获取的 bot 写租约，否则空闲会话会一直占着工作区写槽。"""
    control, terminal, actor, session = bootstrap(tmp_path)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    turn = control.enqueue_turn(
        actor=actor, session_id=session.id, prompt="任务", trace_id="t1"
    )
    terminal.advance_queue(session_id=session.id)
    lease = control.repository.current_lease(session.id)
    assert lease is not None and lease.owner_type == LeaseOwnerType.BOT

    complete_turn(control, session.id, turn.id, "done")
    drained = terminal.advance_queue(session_id=session.id)
    assert drained["reason"] == "queue_empty"
    # 队列空 → bot 写租约已释放。
    assert control.repository.current_lease(session.id) is None


def test_idle_session_lease_does_not_block_sibling_in_shared_workspace(tmp_path):
    """同一工作区（max_write_sessions=1）下，A 跑完后空闲不应再占着写槽堵住 B —— 用户实测场景。"""
    control, terminal, actor, session_a = bootstrap(tmp_path)
    session_b = control.create_session(
        actor=actor,
        project_id=session_a.project_id,
        workspace_id=session_a.workspace_id,
        name="B",
        agent_type=session_a.agent_type,
        visibility=Visibility.GROUP,
        trace_id="sb",
    )
    terminal.start_session(session_id=session_a.id, command="fake-cli", trace_id="sa")
    terminal.start_session(session_id=session_b.id, command="fake-cli", trace_id="sb")

    ta = control.enqueue_turn(actor=actor, session_id=session_a.id, prompt="A", trace_id="ta")
    terminal.advance_queue(session_id=session_a.id)
    complete_turn(control, session_a.id, ta.id, "a-done")
    # A 队列空 → 释放 A 的写租约。
    terminal.advance_queue(session_id=session_a.id)

    tb = control.enqueue_turn(actor=actor, session_id=session_b.id, prompt="B", trace_id="tb")
    res = terminal.advance_queue(session_id=session_b.id)
    assert res["action"] == "submitted"
    assert res["turn_id"] == tb.id


def test_stuck_interaction_turn_is_reconciled(tmp_path):
    """Claude 提问 hook 超时被判 declined、Stop hook 没回传时，挂着的 active_turn 应被兜底收尾。

    复现真实卡死：active_turn 长挂、伴随一条早已超过 hook 等待上限的 pending 提问交互，
    终端仍活着（旧的"终端已死"自愈兜不住）。check_stuck_interaction_turns 应取消过期交互
    并补发 turn.completed，让会话回 IDLE、队列得以前进。"""
    control, terminal, actor, session = bootstrap(tmp_path, stuck_turn_reconcile_seconds=420)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    turn = control.enqueue_turn(
        actor=actor, session_id=session.id, prompt="用交互式窗口问我三个问题", trace_id="t1"
    )
    terminal.advance_queue(session_id=session.id)
    assert control.repository.get_turn(turn.id).status == TurnStatus.RUNNING

    interaction = control.create_interaction(
        actor=actor,
        session_id=session.id,
        interaction_type=InteractionType.QUESTION,
        prompt="要不要补充内容？",
        options=["逐小时预报", "不用补充"],
        trace_id="ask",
    )
    # 未到阈值（终端仍活着、hook 可能仍在合法阻塞等待人类作答）→ 不动。
    fresh = terminal.check_stuck_interaction_turns(now=interaction.created_at + timedelta(seconds=120))
    assert fresh == []
    assert control.repository.get_turn(turn.id).status == TurnStatus.RUNNING

    # 超过 hook 等待上限 → hook 必然早已超时返回、agent 回到空闲：兜底收尾。
    reconciled = terminal.check_stuck_interaction_turns(
        now=interaction.created_at + timedelta(seconds=600)
    )
    assert len(reconciled) == 1
    assert reconciled[0]["turn_id"] == turn.id
    assert interaction.id in reconciled[0]["cancelled_interactions"]
    assert control.repository.get_turn(turn.id).status == TurnStatus.COMPLETED
    assert control.repository.get_session(session.id).status == SessionStatus.IDLE
    assert control.repository.get_session(session.id).active_turn_id is None
    assert (
        control.repository.get_interaction(interaction.id).status
        == InteractionStatus.CANCELLED
    )


def test_stuck_idle_turn_reconciled_without_pending_interaction(tmp_path):
    """答完交互后 Claude 续写、但 Stop 没回传、且已无 pending 交互的僵尸：靠输出游标静默兜底收尾。"""
    control, terminal, actor, session = bootstrap(tmp_path, stuck_turn_idle_seconds=30)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    turn = control.enqueue_turn(actor=actor, session_id=session.id, prompt="任务", trace_id="t1")
    terminal.advance_queue(session_id=session.id)
    assert control.repository.get_turn(turn.id).status == TurnStatus.RUNNING

    t0 = utc_now()
    # 第一拍：建立游标基线，不收尾。
    assert terminal.check_stuck_interaction_turns(now=t0) == []
    # 输出有变化（游标推进）→ 刷新静默起点，不收尾。
    terminal.backend.write(
        session_id=session.id, data="still working", kind=TerminalInputKind.TEXT
    )
    assert terminal.check_stuck_interaction_turns(now=t0 + timedelta(seconds=40)) == []
    assert control.repository.get_turn(turn.id).status == TurnStatus.RUNNING
    # 此后输出静默超过阈值 → 判定已回到空闲提示符，补发收尾。
    out = terminal.check_stuck_interaction_turns(now=t0 + timedelta(seconds=80))
    assert len(out) == 1 and out[0]["turn_id"] == turn.id and out[0]["reason"] == "stuck_idle_reconcile"
    assert control.repository.get_turn(turn.id).status == TurnStatus.COMPLETED
    assert control.repository.get_session(session.id).status == SessionStatus.IDLE


def test_stuck_reconcile_skips_codex_and_disabled(tmp_path):
    """兜底仅针对依赖 Stop hook 的 Claude；Codex 走空闲启发式，且开关关闭时完全不动。"""
    # 关闭开关：即便有超期交互也不收尾。
    control, terminal, actor, session = bootstrap(tmp_path, stuck_turn_reconcile=False)
    terminal.start_session(session_id=session.id, command="fake-cli", trace_id="start")
    turn = control.enqueue_turn(actor=actor, session_id=session.id, prompt="任务", trace_id="t1")
    terminal.advance_queue(session_id=session.id)
    interaction = control.create_interaction(
        actor=actor,
        session_id=session.id,
        interaction_type=InteractionType.QUESTION,
        prompt="问题",
        trace_id="ask",
    )
    assert terminal.check_stuck_interaction_turns(now=utc_now() + timedelta(hours=1)) == []
    assert control.repository.get_turn(turn.id).status == TurnStatus.RUNNING

    # Codex 会话：开关开着也不归本兜底处理（交由空闲启发式）。
    control_c, terminal_c, actor_c, session_c = bootstrap(
        tmp_path, agent_type=AgentType.CODEX, stuck_turn_reconcile_seconds=420
    )
    terminal_c.start_session(session_id=session_c.id, command="fake-cli", trace_id="start")
    turn_c = control_c.enqueue_turn(
        actor=actor_c, session_id=session_c.id, prompt="任务", trace_id="t1"
    )
    terminal_c.advance_queue(session_id=session_c.id)
    control_c.create_interaction(
        actor=actor_c,
        session_id=session_c.id,
        interaction_type=InteractionType.QUESTION,
        prompt="问题",
        trace_id="ask",
    )
    assert terminal_c.check_stuck_interaction_turns(now=utc_now() + timedelta(hours=1)) == []
    assert control_c.repository.get_turn(turn_c.id).status == TurnStatus.RUNNING
