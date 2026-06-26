from __future__ import annotations

from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    Actor,
    AgentType,
    LeaseOwnerType,
    SemanticEventSource,
    SessionStatus,
    TurnStatus,
    Visibility,
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
