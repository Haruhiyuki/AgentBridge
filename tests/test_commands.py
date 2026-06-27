from __future__ import annotations

import pytest

from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    Actor,
    AgentBridgeError,
    AuditOutcome,
    ErrorCode,
    InteractionStatus,
    InteractionType,
    LeaseOwnerType,
    RiskLevel,
    SemanticEventSource,
    SessionStatus,
    Visibility,
)


def make_context(control: ControlPlane):
    return control.get_or_create_chat_context(
        bot_instance_id="bot-test",
        platform="onebot.v11",
        chat_space_id="group-1",
    )


def execute(
    command_service: CommandService, raw_text: str, actor: Actor, context_id: str, key: str
):
    invocation = command_service.parse(
        raw_text=raw_text,
        actor=actor,
        chat_context_id=context_id,
        idempotency_key=key,
        trace_id=key,
    )
    return command_service.execute(invocation)


def test_project_session_and_turn_command_flow(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    project_result = execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path} --alias backend",
        maintainer,
        context.id,
        "create-project",
    )

    assert project_result.canonical_command == "project.create"
    project_id = project_result.data["project_id"]
    assert control.repository.get_chat_context(context.id).active_project_id == project_id
    assert len(control.repository.list_workspaces(project_id)) == 1

    session_result = execute(
        commands,
        "/agent session new Login Fix --agent claude",
        maintainer,
        context.id,
        "create-session",
    )

    session_id = session_result.data["session_id"]
    session = control.repository.get_session(session_id)
    assert session.name == "Login Fix"
    assert len(session.short_code) == 4
    assert control.repository.get_chat_context(context.id).active_session_id == session_id

    turn_result = execute(
        commands,
        f"/agent ask --session {session.short_code} fix the login 500 and run tests",
        maintainer,
        context.id,
        "turn-1",
    )

    assert turn_result.canonical_command == "turn.enqueue"
    assert turn_result.data["session_id"] == session_id
    assert control.repository.turns[turn_result.data["turn_id"]].prompt == (
        "fix the login 500 and run tests"
    )
    command_audits = [
        event for event in control.repository.audit_events if event.action == "command.executed"
    ]
    assert len(command_audits) == 3


def test_turn_enqueue_marks_queue_when_human_controls_session(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    project_result = execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
        maintainer,
        context.id,
        "human-queue-project",
    )
    session_result = execute(
        commands,
        "/agent session new Human Control",
        maintainer,
        context.id,
        "human-queue-session",
    )
    lease = control.acquire_lease(
        actor=maintainer,
        session_id=session_result.data["session_id"],
        owner_type=LeaseOwnerType.HUMAN,
        owner_id="local-user",
        ttl_seconds=300,
        trace_id="human-queue-lease",
    )

    result = execute(
        commands,
        "/agent ask continue after local edit",
        maintainer,
        context.id,
        "human-queue-turn",
    )

    assert result.canonical_command == "turn.enqueue"
    assert "本地控制中" in result.message
    assert result.data["turn"]["queue_reason"] == "human_control"

    queued_event = control.repository.list_events(
        session_id=session_result.data["session_id"]
    )[-1]
    assert queued_event.type == "turn.queued"
    assert queued_event.payload["queue_reason"] == "human_control"
    assert queued_event.payload["lease_owner_type"] == LeaseOwnerType.HUMAN.value
    assert queued_event.payload["lease_owner_id"] == "local-user"
    assert queued_event.payload["lease_epoch"] == lease.epoch

    queued_audit = [
        event for event in control.repository.audit_events if event.action == "turn.queued"
    ][0]
    assert queued_audit.project_id == project_result.data["project_id"]
    assert queued_audit.details["queue_reason"] == "human_control"
    assert queued_audit.details["lease_epoch"] == lease.epoch

    release_result = execute(
        commands,
        f"/agent control release --epoch {lease.epoch}",
        maintainer,
        context.id,
        "human-queue-release",
    )
    events = control.repository.list_events(session_id=session_result.data["session_id"])
    unblocked_event = events[-1]

    assert release_result.canonical_command == "control.release"
    assert release_result.data["next_epoch"] == lease.epoch + 1
    assert unblocked_event.type == "turn.queue_unblocked"
    assert unblocked_event.turn_id == result.data["turn_id"]
    assert unblocked_event.payload["queue_reason"] == "human_control"
    assert unblocked_event.payload["next_turn_id"] == result.data["turn_id"]
    assert unblocked_event.payload["unblocked_turn_count"] == 1
    assert unblocked_event.payload["queue_paused"] is False


def test_terminal_offline_protection_queues_bot_input_and_blocks_old_epoch(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
        maintainer,
        context.id,
        "offline-protection-project",
    )
    session_result = execute(
        commands,
        "/agent session new Offline Protection",
        maintainer,
        context.id,
        "offline-protection-session",
    )
    session_id = session_result.data["session_id"]
    bot_lease = control.acquire_lease(
        actor=maintainer,
        session_id=session_id,
        owner_type=LeaseOwnerType.BOT,
        owner_id="bot",
        ttl_seconds=300,
        trace_id="offline-protection-bot-lease",
    )

    protected_session, next_epoch = control.set_terminal_agent_offline_protection(
        actor=maintainer,
        session_id=session_id,
        offline=True,
        trace_id="offline-protection-enable",
    )

    assert protected_session.status == SessionStatus.RECOVERING
    assert next_epoch == bot_lease.epoch + 1
    assert control.repository.current_lease(session_id) is None

    result = execute(
        commands,
        "/agent ask continue after terminal reconnects",
        maintainer,
        context.id,
        "offline-protection-turn",
    )

    assert result.canonical_command == "turn.enqueue"
    assert "离线保护中" in result.message
    assert result.data["turn"]["queue_reason"] == "terminal_agent_offline"
    queued_event = control.repository.list_events(session_id=session_id)[-1]
    assert queued_event.type == "turn.queued"
    assert queued_event.payload["queue_reason"] == "terminal_agent_offline"

    with pytest.raises(AgentBridgeError) as exc_info:
        control.acquire_lease(
            actor=maintainer,
            session_id=session_id,
            owner_type=LeaseOwnerType.BOT,
            owner_id="bot",
            ttl_seconds=300,
            trace_id="offline-protection-bot-reacquire",
        )
    assert exc_info.value.code == ErrorCode.LEASE_CONFLICT
    assert exc_info.value.details["offline_protection"] is True

    with pytest.raises(AgentBridgeError) as exc_info:
        control.claim_next_turn(
            actor=maintainer,
            session_id=session_id,
            trace_id="offline-protection-claim",
        )
    assert exc_info.value.code == ErrorCode.RESOURCE_CONFLICT
    assert exc_info.value.details["offline_protection"] is True

    human_lease = control.acquire_lease(
        actor=maintainer,
        session_id=session_id,
        owner_type=LeaseOwnerType.HUMAN,
        owner_id="local-user",
        ttl_seconds=300,
        trace_id="offline-protection-human-lease",
    )
    assert human_lease.epoch == next_epoch + 1
    assert control.repository.get_session(session_id).status == SessionStatus.RECOVERING

    assert (
        control.release_lease(
            actor=maintainer,
            session_id=session_id,
            epoch=human_lease.epoch,
            trace_id="offline-protection-human-release",
        )
        == human_lease.epoch + 1
    )
    restored_session, restored_epoch = control.set_terminal_agent_offline_protection(
        actor=maintainer,
        session_id=session_id,
        offline=False,
        trace_id="offline-protection-disable",
    )
    events = control.repository.list_events(session_id=session_id)

    assert restored_session.status == SessionStatus.IDLE
    assert restored_epoch == human_lease.epoch + 1
    assert events[-2].type == "terminal.offline_protection_disabled"
    assert events[-1].type == "turn.queue_unblocked"
    assert events[-1].payload["queue_reason"] == "terminal_agent_offline"


def test_top_level_use_alias_switches_session_across_projects(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    alpha = execute(
        commands,
        f"/agent project create --name Alpha --path {tmp_path / 'alpha'} --root {tmp_path}",
        maintainer,
        context.id,
        "create-alpha",
    )
    alpha_session = execute(
        commands,
        "/agent session new Alpha Session",
        maintainer,
        context.id,
        "create-alpha-session",
    )
    beta = execute(
        commands,
        f"/agent project create --name Beta --path {tmp_path / 'beta'} --root {tmp_path}",
        maintainer,
        context.id,
        "create-beta",
    )
    beta_session = execute(
        commands,
        "/agent session new Beta Session",
        maintainer,
        context.id,
        "create-beta-session",
    )

    assert control.repository.get_chat_context(context.id).active_project_id == beta.data[
        "project_id"
    ]
    assert control.repository.get_chat_context(context.id).active_session_id == beta_session.data[
        "session_id"
    ]

    alpha_short_code = alpha_session.data["session"]["short_code"]
    switched = execute(
        commands,
        f"/agent 使用 {alpha_short_code}",
        maintainer,
        context.id,
        "use-alpha-session",
    )

    updated_context = control.repository.get_chat_context(context.id)
    assert switched.canonical_command == "session.use"
    assert switched.data["session_id"] == alpha_session.data["session_id"]
    assert switched.data["project_id"] == alpha.data["project_id"]
    assert updated_context.active_project_id == alpha.data["project_id"]
    assert updated_context.active_session_id == alpha_session.data["session_id"]


def test_command_idempotency_does_not_create_duplicate_session(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
        maintainer,
        context.id,
        "create-project",
    )

    first = execute(
        commands,
        "/agent session new Repeatable",
        maintainer,
        context.id,
        "same-delivery",
    )
    second = execute(
        commands,
        "/agent session new Repeatable",
        maintainer,
        context.id,
        "same-delivery",
    )

    assert first == second
    assert len(control.repository.sessions) == 1


def test_failed_command_execution_is_audited(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    with pytest.raises(AgentBridgeError) as exc_info:
        execute(
            commands,
            "/agent ask run without a session",
            maintainer,
            context.id,
            "missing-session-command",
        )

    failed_audits = [
        event for event in control.repository.audit_events if event.action == "command.failed"
    ]
    # 无活动项目时发任务无法进行（有项目时会自动新建会话），命令失败并被审计。
    assert exc_info.value.code == ErrorCode.TARGET_PROJECT_REQUIRED
    assert len(failed_audits) == 1
    assert failed_audits[0].outcome == AuditOutcome.FAILED
    assert failed_audits[0].actor_id == maintainer.id
    assert failed_audits[0].trace_id == "missing-session-command"
    assert failed_audits[0].details["canonical_command"] == "turn.enqueue"
    assert failed_audits[0].details["error_code"] == "TARGET_PROJECT_REQUIRED"
    assert (
        control.repository.get_command_result("missing-session-command")
        is None
    )


def test_permission_denied_command_failure_is_audited_as_denied(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    operator = Actor(id="usr_operator", roles={"operator"})

    with pytest.raises(AgentBridgeError) as exc_info:
        execute(
            commands,
            f"/agent project create --name Denied --path {tmp_path} --root {tmp_path}",
            operator,
            context.id,
            "denied-project-create",
        )

    failed_audits = [
        event for event in control.repository.audit_events if event.action == "command.failed"
    ]
    assert exc_info.value.code == ErrorCode.PERMISSION_DENIED
    assert len(failed_audits) == 1
    assert failed_audits[0].outcome == AuditOutcome.DENIED
    assert failed_audits[0].details["canonical_command"] == "project.create"
    assert failed_audits[0].details["status_code"] == 403


def test_select_commands_apply_numbered_project_and_session_choices(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    first_project = control.create_project(
        actor=maintainer,
        name="Alpha",
        slug="alpha",
        max_active_sessions=5,
        trace_id="select-alpha",
    )
    second_project = control.create_project(
        actor=maintainer,
        name="Beta",
        slug="beta",
        max_active_sessions=5,
        trace_id="select-beta",
    )
    first_workspace = control.add_workspace(
        actor=maintainer,
        project_id=first_project.id,
        machine_id="local",
        path=str(tmp_path / "alpha"),
        allowed_root=str(tmp_path),
        trace_id="select-alpha-workspace",
    )
    second_workspace = control.add_workspace(
        actor=maintainer,
        project_id=second_project.id,
        machine_id="local",
        path=str(tmp_path / "beta"),
        allowed_root=str(tmp_path),
        trace_id="select-beta-workspace",
    )
    first_session = control.create_session(
        actor=maintainer,
        project_id=first_project.id,
        workspace_id=first_workspace.id,
        name="Alpha One",
        agent_type=first_project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="select-alpha-session-1",
        chat_context_id=context.id,
    )
    second_session = control.create_session(
        actor=maintainer,
        project_id=first_project.id,
        workspace_id=first_workspace.id,
        name="Alpha Two",
        agent_type=first_project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="select-alpha-session-2",
        chat_context_id=context.id,
    )
    control.create_session(
        actor=maintainer,
        project_id=second_project.id,
        workspace_id=second_workspace.id,
        name="Beta One",
        agent_type=second_project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="select-beta-session-1",
        chat_context_id=context.id,
    )

    project_list = execute(
        commands,
        "/agent project list",
        maintainer,
        context.id,
        "select-project-list",
    )
    session_list = execute(
        commands,
        "/agent session list --project alpha",
        maintainer,
        context.id,
        "select-alpha-session-list",
    )
    project_result = execute(
        commands,
        "/agent select project 2",
        maintainer,
        context.id,
        "select-project-2",
    )
    session_result = execute(
        commands,
        "/agent select session 2 --project alpha",
        maintainer,
        context.id,
        "select-alpha-session-2",
    )

    updated_context = control.repository.get_chat_context(context.id)
    assert "1. Alpha (alpha)" in project_list.message
    assert "2. Beta (beta)" in project_list.message
    assert "/agent select project <编号>" in project_list.message
    assert f"1. [{first_session.short_code}] Alpha One" in session_list.message
    assert f"2. [{second_session.short_code}] Alpha Two" in session_list.message
    assert "select session <编号>" in session_list.message
    assert project_result.canonical_command == "project.select"
    assert project_result.data["project_id"] == second_project.id
    assert project_result.data["selected_index"] == 2
    assert session_result.canonical_command == "session.select"
    assert session_result.data["project_id"] == first_project.id
    assert session_result.data["session_id"] == second_session.id
    assert session_result.data["selected_index"] == 2
    assert updated_context.active_project_id == first_project.id
    assert updated_context.active_session_id == second_session.id
    assert first_session.id != second_session.id


def test_select_command_rejects_out_of_range_number(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    control.create_project(
        actor=maintainer,
        name="Only Project",
        slug="only-project",
        trace_id="select-only-project",
    )

    with pytest.raises(AgentBridgeError) as exc_info:
        execute(
            commands,
            "/agent select project 2",
            maintainer,
            context.id,
            "select-project-missing",
        )

    assert exc_info.value.code == ErrorCode.COMMAND_ARGUMENT_INVALID
    assert exc_info.value.details == {"index": 2, "count": 1}


def test_project_binding_commands_set_unique_default_and_aliases():
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    alpha = control.create_project(
        actor=maintainer,
        name="Alpha",
        slug="alpha",
        trace_id="bind-alpha",
    )
    beta = control.create_project(
        actor=maintainer,
        name="Beta",
        slug="beta",
        trace_id="bind-beta",
    )
    gamma = control.create_project(
        actor=maintainer,
        name="Gamma",
        slug="gamma",
        trace_id="bind-gamma",
    )

    alpha_binding = execute(
        commands,
        "/agent project bind alpha --alias main --default",
        maintainer,
        context.id,
        "bind-alpha-default",
    )
    beta_binding = execute(
        commands,
        "/agent project bind beta --alias backend",
        maintainer,
        context.id,
        "bind-beta",
    )
    gamma_binding = execute(
        commands,
        "/agent project bind gamma",
        maintainer,
        context.id,
        "bind-gamma",
    )
    default_result = execute(
        commands,
        "/agent project default backend",
        maintainer,
        context.id,
        "default-beta",
    )
    bindings_result = execute(
        commands,
        "/agent project bindings",
        maintainer,
        context.id,
        "list-bindings",
    )
    alias_info = execute(
        commands,
        "/agent project info backend",
        maintainer,
        context.id,
        "project-info-binding-alias",
    )

    updated_context = control.repository.get_chat_context(context.id)
    bindings = control.repository.list_project_bindings(context.id)
    default_bindings = [binding for binding in bindings if binding.is_default]
    binding_by_project = {binding.project_id: binding for binding in bindings}

    assert alpha_binding.canonical_command == "project.bind"
    assert alpha_binding.data["is_default"] is True
    assert beta_binding.data["binding"]["alias_in_chat"] == "backend"
    assert gamma_binding.data["project_id"] == gamma.id
    assert default_result.canonical_command == "project.default"
    assert default_result.data["project_id"] == beta.id
    assert updated_context.active_project_id == beta.id
    assert len(bindings) == 3
    assert [binding.project_id for binding in default_bindings] == [beta.id]
    assert binding_by_project[alpha.id].is_default is False
    assert binding_by_project[beta.id].alias_in_chat == "backend"
    assert alias_info.data["project_id"] == beta.id
    assert [item["project_id"] for item in bindings_result.data["bindings"]] == [
        alpha.id,
        beta.id,
        gamma.id,
    ]
    assert "默认 · Beta (beta)" in bindings_result.message
    assert "/agent project default <project>" in bindings_result.message


def test_project_create_command_sets_active_session_quota(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    project_result = execute(
        commands,
        (
            f"/agent project create --name Backend --path {tmp_path} "
            f"--root {tmp_path} --max-active-sessions 1"
        ),
        maintainer,
        context.id,
        "quota-project",
    )
    project_id = project_result.data["project_id"]

    first = execute(
        commands,
        "/agent session new Quota One",
        maintainer,
        context.id,
        "quota-session-one",
    )
    with pytest.raises(AgentBridgeError) as blocked:
        execute(
            commands,
            "/agent session new Quota Two",
            maintainer,
            context.id,
            "quota-session-two",
        )

    assert project_result.data["project"]["max_active_sessions"] == 1
    assert first.canonical_command == "session.create"
    assert blocked.value.code == ErrorCode.QUOTA_EXCEEDED
    assert blocked.value.details == {
        "project_id": project_id,
        "active_sessions": 1,
        "max_active_sessions": 1,
    }


def test_project_create_command_sets_queued_turn_quota(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    project_result = execute(
        commands,
        (
            f"/agent project create --name Backend --path {tmp_path} "
            f"--root {tmp_path} --max-running-turns 2 --max-queued-turns 1 "
            "--daily-turns-per-user 3"
        ),
        maintainer,
        context.id,
        "queued-quota-project",
    )
    project_id = project_result.data["project_id"]
    session = execute(
        commands,
        "/agent session new Queue Quota",
        maintainer,
        context.id,
        "queued-quota-session",
    )
    first_turn = execute(
        commands,
        "/agent ask first queued turn",
        maintainer,
        context.id,
        "queued-quota-turn-one",
    )
    with pytest.raises(AgentBridgeError) as blocked:
        execute(
            commands,
            "/agent ask second queued turn",
            maintainer,
            context.id,
            "queued-quota-turn-two",
        )

    assert project_result.data["project"]["max_queued_turns"] == 1
    assert project_result.data["project"]["max_running_turns"] == 2
    assert project_result.data["project"]["daily_turns_per_user"] == 3
    assert session.canonical_command == "session.create"
    assert first_turn.canonical_command == "turn.enqueue"
    assert blocked.value.code == ErrorCode.QUOTA_EXCEEDED
    assert blocked.value.details == {
        "project_id": project_id,
        "queued_turns": 1,
        "max_queued_turns": 1,
        "queue_position": 2,
    }


def test_queue_commands_list_remove_and_clear_queued_turns(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
        maintainer,
        context.id,
        "queue-command-project",
    )
    execute(
        commands,
        "/agent session new Queue Commands",
        maintainer,
        context.id,
        "queue-command-session",
    )
    first_turn = execute(
        commands,
        "/agent ask first queued command",
        maintainer,
        context.id,
        "queue-command-turn-one",
    )
    second_turn = execute(
        commands,
        "/agent ask second queued command",
        maintainer,
        context.id,
        "queue-command-turn-two",
    )
    third_turn = execute(
        commands,
        "/agent ask third queued command",
        maintainer,
        context.id,
        "queue-command-turn-three",
    )
    listed = execute(
        commands,
        "/agent queue list",
        maintainer,
        context.id,
        "queue-command-list",
    )
    paused = execute(
        commands,
        f"/agent queue pause --version {listed.data['queue_version']}",
        maintainer,
        context.id,
        "queue-command-pause",
    )
    resumed = execute(
        commands,
        f"/agent queue resume --version {paused.data['queue_version']}",
        maintainer,
        context.id,
        "queue-command-resume",
    )
    moved = execute(
        commands,
        (
            f"/agent queue move {third_turn.data['turn_id']} "
            f"--before {first_turn.data['turn_id']} "
            f"--version {resumed.data['queue_version']}"
        ),
        maintainer,
        context.id,
        "queue-command-move",
    )
    removed = execute(
        commands,
        (
            f"/agent queue remove {first_turn.data['turn_id']} "
            f"--version {moved.data['queue_version']}"
        ),
        maintainer,
        context.id,
        "queue-command-remove",
    )
    with pytest.raises(AgentBridgeError) as clear_without_confirm:
        execute(
            commands,
            f"/agent queue clear --version {removed.data['queue_version']}",
            maintainer,
            context.id,
            "queue-command-clear-without-confirm",
        )
    cleared = execute(
        commands,
        f"/agent queue clear --version {removed.data['queue_version']} --confirm 2",
        maintainer,
        context.id,
        "queue-command-clear",
    )
    listed_after_clear = execute(
        commands,
        "/agent queue list",
        maintainer,
        context.id,
        "queue-command-list-empty",
    )

    assert listed.canonical_command == "queue.list"
    assert [turn["id"] for turn in listed.data["turns"]] == [
        first_turn.data["turn_id"],
        second_turn.data["turn_id"],
        third_turn.data["turn_id"],
    ]
    assert listed.data["queue_version"].startswith("qv_")
    assert listed.data["queue_paused"] is False
    assert paused.canonical_command == "queue.pause"
    assert paused.data["queue_paused"] is True
    assert paused.data["queue_version"] != listed.data["queue_version"]
    assert resumed.canonical_command == "queue.resume"
    assert resumed.data["queue_paused"] is False
    assert resumed.data["queue_version"] != paused.data["queue_version"]
    assert moved.canonical_command == "queue.move"
    assert [turn["id"] for turn in moved.data["turns"]] == [
        third_turn.data["turn_id"],
        first_turn.data["turn_id"],
        second_turn.data["turn_id"],
    ]
    assert moved.data["queue_version"] != listed.data["queue_version"]
    assert removed.canonical_command == "queue.remove"
    assert removed.data["turn"]["status"] == "cancelled"
    assert clear_without_confirm.value.code == ErrorCode.COMMAND_ARGUMENT_INVALID
    assert clear_without_confirm.value.details["current_count"] == 2
    assert cleared.canonical_command == "queue.clear"
    assert cleared.data["count"] == 2
    assert [turn["id"] for turn in cleared.data["turns"]] == [
        third_turn.data["turn_id"],
        second_turn.data["turn_id"],
    ]
    assert listed_after_clear.data["turns"] == []


def test_group_role_binding_grants_context_permissions(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_maintainer", roles={"admin"})
    member = Actor(id="usr_member", roles={"member"})

    execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
        maintainer,
        context.id,
        "role-project",
    )

    with pytest.raises(AgentBridgeError) as denied_before_grant:
        execute(
            commands,
            "/agent session new Denied Session",
            member,
            context.id,
            "role-denied-session",
        )
    assert denied_before_grant.value.code == ErrorCode.PERMISSION_DENIED

    grant_result = execute(
        commands,
        "/agent role grant usr_member operator",
        maintainer,
        context.id,
        "role-grant",
    )
    assert grant_result.canonical_command == "role.grant"
    assert grant_result.data["binding"]["roles"] == ["operator"]
    assert control.effective_actor(member, context.id).roles == {"member", "operator"}

    list_result = execute(
        commands,
        "/agent role list",
        maintainer,
        context.id,
        "role-list",
    )
    assert [binding["actor_id"] for binding in list_result.data["bindings"]] == ["usr_member"]

    session_result = execute(
        commands,
        "/agent session new Granted Session",
        member,
        context.id,
        "role-granted-session",
    )
    assert session_result.data["session"]["created_by"] == "usr_member"

    revoke_result = execute(
        commands,
        "/agent role revoke usr_member operator",
        maintainer,
        context.id,
        "role-revoke",
    )
    assert revoke_result.data["binding"] is None

    with pytest.raises(AgentBridgeError) as denied_after_revoke:
        execute(
            commands,
            "/agent session new After Revoke",
            member,
            context.id,
            "role-denied-turn",
        )
    assert denied_after_revoke.value.code == ErrorCode.PERMISSION_DENIED


def test_approval_commands_list_show_vote_and_resolve(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    approver = Actor(id="usr_approver", roles={"approver"})
    second_approver = Actor(id="usr_second", roles={"approver"})

    execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
        maintainer,
        context.id,
        "approval-project",
    )
    session_result = execute(
        commands,
        "/agent session new Approval Session",
        maintainer,
        context.id,
        "approval-session",
    )
    interaction = control.create_interaction(
        actor=maintainer,
        session_id=session_result.data["session_id"],
        interaction_type=InteractionType.APPROVAL,
        prompt="Allow shell command?",
        required_votes=2,
        trace_id="approval-request",
        chat_context_id=context.id,
    )

    list_result = execute(
        commands,
        "/agent approvals",
        approver,
        context.id,
        "approval-list",
    )
    assert [item["id"] for item in list_result.data["interactions"]] == [interaction.id]

    show_result = execute(
        commands,
        f"/agent approval show {interaction.id}",
        approver,
        context.id,
        "approval-show",
    )
    assert show_result.data["interaction"]["prompt"] == "Allow shell command?"

    first_vote = execute(
        commands,
        f"/agent approve {interaction.id} once",
        approver,
        context.id,
        "approval-first-vote",
    )
    assert first_vote.data["interaction"]["status"] == InteractionStatus.PARTIALLY_APPROVED

    second_vote = execute(
        commands,
        f"/agent approve {interaction.id}",
        second_approver,
        context.id,
        "approval-second-vote",
    )
    assert second_vote.data["interaction"]["status"] == InteractionStatus.RESOLVED
    assert second_vote.data["interaction"]["votes"] == {
        "usr_approver": True,
        "usr_second": True,
    }


def test_approval_cancel_command_blocks_late_votes(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    approver = Actor(id="usr_approver", roles={"approver"})

    execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
        maintainer,
        context.id,
        "approval-cancel-project",
    )
    session_result = execute(
        commands,
        "/agent session new Approval Cancel",
        maintainer,
        context.id,
        "approval-cancel-session",
    )
    interaction = control.create_interaction(
        actor=maintainer,
        session_id=session_result.data["session_id"],
        interaction_type=InteractionType.APPROVAL,
        prompt="Allow operation?",
        trace_id="approval-cancel-request",
        chat_context_id=context.id,
    )

    cancel_result = execute(
        commands,
        f"/agent approval cancel {interaction.id} superseded",
        maintainer,
        context.id,
        "approval-cancel",
    )
    assert cancel_result.data["interaction"]["status"] == "cancelled"
    assert cancel_result.data["interaction"]["answer"] == "superseded"

    with pytest.raises(AgentBridgeError) as exc_info:
        execute(
            commands,
            f"/agent approve {interaction.id}",
            approver,
            context.id,
            "approval-cancel-late-vote",
        )
    assert exc_info.value.code == ErrorCode.RESOURCE_CONFLICT


def test_question_and_plan_commands_use_typed_interactions(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})

    execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
        maintainer,
        context.id,
        "plan-project",
    )
    session_result = execute(
        commands,
        "/agent session new Plan Commands",
        maintainer,
        context.id,
        "plan-session",
    )
    session_id = str(session_result.data["session_id"])
    question = control.create_interaction(
        actor=maintainer,
        session_id=session_id,
        interaction_type=InteractionType.QUESTION,
        prompt="Which environment?",
        trace_id="question-create",
        chat_context_id=context.id,
    )
    plan = control.create_interaction(
        actor=maintainer,
        session_id=session_id,
        interaction_type=InteractionType.PLAN,
        prompt="Plan: update tests before deploy.",
        trace_id="plan-create",
        chat_context_id=context.id,
    )

    question_show = execute(
        commands,
        f"/agent question show {question.id}",
        maintainer,
        context.id,
        "question-show",
    )
    plan_show = execute(
        commands,
        f"/agent plan show {plan.id}",
        maintainer,
        context.id,
        "plan-show",
    )
    plan_list = execute(
        commands,
        "/agent plan list",
        maintainer,
        context.id,
        "plan-list",
    )
    plan_approve = execute(
        commands,
        f"/agent plan approve {plan.id}",
        maintainer,
        context.id,
        "plan-approve",
    )
    revise_plan = control.create_interaction(
        actor=maintainer,
        session_id=session_id,
        interaction_type=InteractionType.PLAN,
        prompt="Plan: migrate database directly.",
        trace_id="plan-revise-create",
        chat_context_id=context.id,
    )
    plan_revise = execute(
        commands,
        f"/agent plan revise {revise_plan.id} Use expand-contract migration first",
        maintainer,
        context.id,
        "plan-revise",
    )
    cancel_plan = control.create_interaction(
        actor=maintainer,
        session_id=session_id,
        interaction_type=InteractionType.PLAN,
        prompt="Plan: skip tests.",
        trace_id="plan-cancel-create",
        chat_context_id=context.id,
    )
    plan_cancel = execute(
        commands,
        f"/agent plan cancel {cancel_plan.id} stale plan",
        maintainer,
        context.id,
        "plan-cancel",
    )
    events = control.repository.list_events(session_id=session_id, limit=20)

    assert question_show.canonical_command == "interaction.show"
    assert question_show.data["interaction"]["type"] == "question"
    assert plan_show.canonical_command == "interaction.show"
    assert plan_show.data["interaction"]["type"] == "plan"
    assert plan_list.canonical_command == "plan.list"
    assert [item["id"] for item in plan_list.data["interactions"]] == [plan.id]
    assert plan_approve.canonical_command == "plan.approve"
    assert plan_approve.data["plan_decision"] == "approved"
    assert plan_approve.data["interaction"]["answer"] == "approved"
    assert plan_revise.canonical_command == "plan.revise"
    assert plan_revise.data["plan_decision"] == "revise"
    assert plan_revise.data["interaction"]["answer"] == (
        "Use expand-contract migration first"
    )
    assert plan_cancel.canonical_command == "plan.cancel"
    assert plan_cancel.data["interaction"]["status"] == "cancelled"
    assert plan_cancel.data["interaction"]["answer"] == "stale plan"
    assert "question.requested" in [event.type for event in events]
    assert "plan.requested" in [event.type for event in events]

    with pytest.raises(AgentBridgeError) as exc_info:
        execute(
            commands,
            f"/agent plan approve {question.id}",
            maintainer,
            context.id,
            "plan-type-mismatch",
        )
    assert exc_info.value.code == ErrorCode.COMMAND_ARGUMENT_INVALID


def test_numbered_interaction_commands_use_type_filtered_pending_lists(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})

    execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
        maintainer,
        context.id,
        "interaction-code-project",
    )
    session_result = execute(
        commands,
        "/agent session new Interaction Codes",
        maintainer,
        context.id,
        "interaction-code-session",
    )
    session_id = str(session_result.data["session_id"])
    question = control.create_interaction(
        actor=maintainer,
        session_id=session_id,
        interaction_type=InteractionType.QUESTION,
        prompt="Which environment?",
        trace_id="interaction-code-question",
        chat_context_id=context.id,
    )
    approval = control.create_interaction(
        actor=maintainer,
        session_id=session_id,
        interaction_type=InteractionType.APPROVAL,
        prompt="Deploy now?",
        trace_id="interaction-code-approval",
        chat_context_id=context.id,
    )
    plan = control.create_interaction(
        actor=maintainer,
        session_id=session_id,
        interaction_type=InteractionType.PLAN,
        prompt="Plan: run tests then deploy.",
        trace_id="interaction-code-plan",
        chat_context_id=context.id,
    )

    question_list = execute(
        commands,
        "/agent question list",
        maintainer,
        context.id,
        "interaction-code-question-list",
    )
    approval_list = execute(
        commands,
        "/agent approvals",
        maintainer,
        context.id,
        "interaction-code-approval-list",
    )
    plan_list = execute(
        commands,
        "/agent plan list",
        maintainer,
        context.id,
        "interaction-code-plan-list",
    )
    answer_result = execute(
        commands,
        "/agent answer staging",
        maintainer,
        context.id,
        "interaction-code-answer",
    )
    approval_result = execute(
        commands,
        "/agent approve 1",
        maintainer,
        context.id,
        "interaction-code-approve",
    )
    plan_result = execute(
        commands,
        "/agent plan approve 1",
        maintainer,
        context.id,
        "interaction-code-plan-approve",
    )

    assert [item["id"] for item in question_list.data["interactions"]] == [question.id]
    assert [item["id"] for item in approval_list.data["interactions"]] == [approval.id]
    assert [item["id"] for item in plan_list.data["interactions"]] == [plan.id]
    assert "1. question · pending" in question_list.message
    assert "Which environment?" in question_list.message
    assert "/agent answer <答案>" in question_list.message
    assert "1. approval · pending" in approval_list.message
    assert "Deploy now?" in approval_list.message
    assert "/agent approve <编号>" in approval_list.message
    assert "1. plan · pending" in plan_list.message
    assert "Plan: run tests then deploy." in plan_list.message
    assert "/agent plan approve <编号>" in plan_list.message
    assert answer_result.data["interaction_id"] == question.id
    assert answer_result.data["interaction"]["answer"] == "staging"
    assert approval_result.data["interaction_id"] == approval.id
    assert approval_result.data["interaction"]["status"] == "resolved"
    assert plan_result.data["interaction_id"] == plan.id
    assert plan_result.data["interaction"]["answer"] == "approved"


def test_answer_auto_targets_current_pending_question(tmp_path):
    """/ab answer 不写编号时自动认准当前待答提问，整串都当作答案。"""
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
        maintainer,
        context.id,
        "auto-answer-project",
    )
    session_result = execute(
        commands, "/agent session new Auto Answer", maintainer, context.id, "auto-answer-session"
    )
    question = control.create_interaction(
        actor=maintainer,
        session_id=str(session_result.data["session_id"]),
        interaction_type=InteractionType.QUESTION,
        prompt="选哪个环境?",
        options=["staging", "prod"],
        trace_id="auto-answer-question",
        chat_context_id=context.id,
    )

    # 不带编号，整串就是答案（这里多题风格的作答串也应原样作为 answer 存下）。
    result = execute(
        commands, "/agent answer 1A 2B 3C", maintainer, context.id, "auto-answer-exec"
    )
    assert result.data["interaction_id"] == question.id
    assert result.data["interaction"]["answer"] == "1A 2B 3C"

    # 没有待答提问时给出清晰错误，而非把答案塞给不存在的目标。
    with pytest.raises(AgentBridgeError) as exc_info:
        execute(commands, "/agent answer A", maintainer, context.id, "auto-answer-none")
    assert exc_info.value.code == ErrorCode.COMMAND_ARGUMENT_INVALID


def test_numbered_interaction_selector_rejects_wrong_type(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})

    execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
        maintainer,
        context.id,
        "interaction-type-project",
    )
    session_result = execute(
        commands,
        "/agent session new Interaction Type",
        maintainer,
        context.id,
        "interaction-type-session",
    )
    control.create_interaction(
        actor=maintainer,
        session_id=str(session_result.data["session_id"]),
        interaction_type=InteractionType.QUESTION,
        prompt="Which environment?",
        trace_id="interaction-type-question",
        chat_context_id=context.id,
    )

    with pytest.raises(AgentBridgeError) as exc_info:
        execute(
            commands,
            "/agent approve 1",
            maintainer,
            context.id,
            "interaction-type-approve",
        )

    assert exc_info.value.code == ErrorCode.COMMAND_ARGUMENT_INVALID
    assert exc_info.value.details == {"index": 1, "count": 0}


def test_unknown_ascii_command_is_rejected_but_non_command_text_becomes_prompt():
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    actor = Actor(id="usr_1", roles={"operator"})

    with pytest.raises(AgentBridgeError) as exc_info:
        commands.parse(
            raw_text="/agent poject list",
            actor=actor,
            chat_context_id=context.id,
        )
    assert exc_info.value.code == ErrorCode.COMMAND_UNKNOWN

    invocation = commands.parse(
        raw_text="/agent 修复登录接口的测试失败",
        actor=actor,
        chat_context_id=context.id,
    )
    assert invocation.canonical_command == "ask"
    assert invocation.args["prompt"] == "修复登录接口的测试失败"


def test_missing_argument_errors_include_recovery_commands():
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    actor = Actor(id="usr_1", roles={"maintainer"})

    cases = [
        (
            "/agent project use",
            ["/agent project list", "/agent select project <编号>"],
        ),
        (
            "/agent project bind",
            ["/agent project list", "/agent project bind <project>"],
        ),
        (
            "/agent project default",
            ["/agent project bindings", "/agent project default <project>"],
        ),
        (
            "/agent session use",
            ["/agent session list", "/agent select session <编号>"],
        ),
        (
            "/agent select session",
            ["/agent session list", "/agent select session <编号>"],
        ),
        (
            "/agent queue pause",
            ["/agent queue list", "--version <queue_version>"],
        ),
        (
            "/agent queue move turn_1",
            ["/agent queue list", "--before <turn>"],
        ),
        (
            "/agent answer",
            ["认准当前提问", "/agent answer 1A 2B 3C"],
        ),
        (
            "/agent approve",
            ["/agent approvals", "/agent approve <编号>"],
        ),
        (
            "/agent deny",
            ["/agent approvals", "/agent deny <编号>"],
        ),
        (
            "/agent plan approve",
            ["/agent plan list", "/agent plan approve <编号>"],
        ),
        (
            "/agent plan revise 1",
            ["/agent plan revise 1", "Use expand-contract migration first"],
        ),
    ]

    for raw_text, expected_fragments in cases:
        with pytest.raises(AgentBridgeError) as exc_info:
            commands.parse(
                raw_text=raw_text,
                actor=actor,
                chat_context_id=context.id,
            )
        assert exc_info.value.code == ErrorCode.COMMAND_ARGUMENT_INVALID
        for fragment in expected_fragments:
            assert fragment in exc_info.value.next_step


def test_active_project_pointer_uses_optimistic_lock(tmp_path):
    control = ControlPlane()
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    first = control.create_project(actor=maintainer, name="First", trace_id="t1")
    second = control.create_project(actor=maintainer, name="Second", trace_id="t2")
    control.add_workspace(
        actor=maintainer,
        project_id=first.id,
        machine_id="local",
        path=str(tmp_path / "first"),
        allowed_root=str(tmp_path),
        trace_id="t3",
    )
    control.add_workspace(
        actor=maintainer,
        project_id=second.id,
        machine_id="local",
        path=str(tmp_path / "second"),
        allowed_root=str(tmp_path),
        trace_id="t4",
    )

    updated = control.use_project(
        actor=maintainer,
        chat_context_id=context.id,
        project_token=first.slug,
        expected_version=0,
        trace_id="use-first",
    )
    assert updated.pointer_version == 1

    with pytest.raises(AgentBridgeError) as exc_info:
        control.use_project(
            actor=maintainer,
            chat_context_id=context.id,
            project_token=second.slug,
            expected_version=0,
            trace_id="stale",
        )
    assert exc_info.value.code == ErrorCode.RESOURCE_CONFLICT


def test_workspace_path_must_stay_under_allowed_root(tmp_path):
    control = ControlPlane()
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    project = control.create_project(actor=maintainer, name="Backend", trace_id="project")

    with pytest.raises(AgentBridgeError) as exc_info:
        control.add_workspace(
            actor=maintainer,
            project_id=project.id,
            machine_id="local",
            path=str(tmp_path.parent / "outside"),
            allowed_root=str(tmp_path / "root"),
            trace_id="workspace",
        )

    assert exc_info.value.code == ErrorCode.WORKSPACE_PATH_DENIED


def test_workspace_symlink_must_resolve_under_allowed_root(tmp_path):
    control = ControlPlane()
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    project = control.create_project(actor=maintainer, name="Backend", trace_id="project")
    allowed_root = tmp_path / "allowed"
    allowed_target = allowed_root / "real-repo"
    outside_target = tmp_path / "outside-repo"
    allowed_target.mkdir(parents=True)
    outside_target.mkdir()
    allowed_link = allowed_root / "allowed-link"
    outside_link = allowed_root / "outside-link"
    try:
        allowed_link.symlink_to(allowed_target, target_is_directory=True)
        outside_link.symlink_to(outside_target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"filesystem does not support symlinks: {exc}")

    workspace = control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(allowed_link),
        allowed_root=str(allowed_root),
        trace_id="workspace-symlink-allowed",
    )

    assert workspace.path == str(allowed_target.resolve())

    with pytest.raises(AgentBridgeError) as exc_info:
        control.add_workspace(
            actor=maintainer,
            project_id=project.id,
            machine_id="local",
            path=str(outside_link),
            allowed_root=str(allowed_root),
            trace_id="workspace-symlink-denied",
        )

    assert exc_info.value.code == ErrorCode.WORKSPACE_PATH_DENIED
    assert exc_info.value.details["path"] == str(outside_target.resolve())
    assert exc_info.value.details["allowed_root"] == str(allowed_root.resolve())


def test_human_lease_preempts_bot_and_old_epoch_is_rejected(tmp_path):
    control = ControlPlane()
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    operator = Actor(id="usr_operator", roles={"operator"})
    project = control.create_project(actor=maintainer, name="Backend", trace_id="project")
    workspace = control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="workspace",
    )
    session = control.create_session(
        actor=operator,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Lease Test",
        agent_type=project.default_agent,
        visibility="group",
        trace_id="session",
    )

    bot_lease = control.acquire_lease(
        actor=operator,
        session_id=session.id,
        owner_type=LeaseOwnerType.BOT,
        owner_id="bot",
        ttl_seconds=300,
        trace_id="bot",
    )
    human_lease = control.acquire_lease(
        actor=maintainer,
        session_id=session.id,
        owner_type=LeaseOwnerType.HUMAN,
        owner_id="local-user",
        ttl_seconds=300,
        trace_id="human",
    )

    assert bot_lease.epoch == 1
    assert human_lease.epoch == 2

    with pytest.raises(AgentBridgeError) as exc_info:
        control.acquire_lease(
            actor=operator,
            session_id=session.id,
            owner_type=LeaseOwnerType.BOT,
            owner_id="bot",
            ttl_seconds=300,
            trace_id="bot-again",
        )
    assert exc_info.value.code == ErrorCode.LEASE_CONFLICT

    with pytest.raises(AgentBridgeError) as exc_info:
        control.release_lease(
            actor=maintainer,
            session_id=session.id,
            epoch=bot_lease.epoch,
            trace_id="old-release",
        )
    assert exc_info.value.code == ErrorCode.LEASE_CONFLICT

    assert (
        control.release_lease(
            actor=maintainer,
            session_id=session.id,
            epoch=human_lease.epoch,
            trace_id="release",
        )
        == 3
    )


def test_workspace_write_lease_capacity_blocks_parallel_writers(tmp_path):
    control = ControlPlane()
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
    project = control.create_project(actor=maintainer, name="Backend", trace_id="project")
    workspace = control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        max_write_sessions=1,  # 显式设 1 以测「容量到上限即阻塞」，不依赖（已放宽的）默认值。
        trace_id="workspace",
    )
    first_session = control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="First Writer",
        agent_type=project.default_agent,
        visibility="group",
        trace_id="first-session",
    )
    second_session = control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Second Writer",
        agent_type=project.default_agent,
        visibility="group",
        trace_id="second-session",
    )
    first_lease = control.acquire_lease(
        actor=maintainer,
        session_id=first_session.id,
        owner_type=LeaseOwnerType.WEB_ADMIN,
        owner_id=maintainer.id,
        ttl_seconds=300,
        trace_id="first-lease",
    )

    with pytest.raises(AgentBridgeError) as exc_info:
        control.acquire_lease(
            actor=maintainer,
            session_id=second_session.id,
            owner_type=LeaseOwnerType.WEB_ADMIN,
            owner_id=maintainer.id,
            ttl_seconds=300,
            trace_id="second-lease-conflict",
        )

    assert exc_info.value.code == ErrorCode.LEASE_CONFLICT
    assert exc_info.value.details["workspace_id"] == workspace.id
    assert exc_info.value.details["max_write_sessions"] == 1
    assert exc_info.value.details["active_write_sessions"] == 1

    control.release_lease(
        actor=maintainer,
        session_id=first_session.id,
        epoch=first_lease.epoch,
        trace_id="release-first-lease",
    )
    second_lease = control.acquire_lease(
        actor=maintainer,
        session_id=second_session.id,
        owner_type=LeaseOwnerType.WEB_ADMIN,
        owner_id=maintainer.id,
        ttl_seconds=300,
        trace_id="second-lease",
    )

    assert second_lease.epoch == 1


def test_session_event_stream_is_ordered_replayable_and_idempotent(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
        maintainer,
        context.id,
        "event-project",
    )
    session_result = execute(
        commands,
        "/agent session new Event Stream",
        maintainer,
        context.id,
        "event-session",
    )
    session_id = session_result.data["session_id"]
    execute(
        commands,
        "/agent ask verify event replay",
        maintainer,
        context.id,
        "event-turn",
    )

    events = control.repository.list_events(session_id=session_id)
    assert [event.seq for event in events] == [1, 2]
    assert [event.type for event in events] == ["session.created", "turn.queued"]
    replayed_events = control.repository.list_events(session_id=session_id, after_seq=1)
    assert [event.type for event in replayed_events] == ["turn.queued"]

    first = control.emit_event(
        event_type="assistant.delta",
        source=SemanticEventSource.TERMINAL_AGENT,
        trace_id="agent-event",
        project_id=session_result.data["project_id"],
        session_id=session_id,
        payload={"text": "hello"},
        idempotency_key="agent-event-1",
    )
    duplicate = control.emit_event(
        event_type="assistant.delta",
        source=SemanticEventSource.TERMINAL_AGENT,
        trace_id="agent-event",
        project_id=session_result.data["project_id"],
        session_id=session_id,
        payload={"text": "different duplicate"},
        idempotency_key="agent-event-1",
    )

    assert duplicate == first
    assert [event.type for event in control.repository.list_events(session_id=session_id)] == [
        "session.created",
        "turn.queued",
        "assistant.delta",
    ]


def test_policy_commands_manage_chat_context_approval_quorum(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_1", roles={"admin"})

    execute(
        commands,
        f"/agent project create --name Backend --path {tmp_path} --root {tmp_path}",
        maintainer,
        context.id,
        "policy-command-project",
    )
    session_result = execute(
        commands,
        "/agent session new Policy Commands",
        maintainer,
        context.id,
        "policy-command-session",
    )
    set_result = execute(
        commands,
        "/agent policy set approval.critical.quorum 3",
        maintainer,
        context.id,
        "policy-command-set",
    )
    show_result = execute(
        commands,
        "/agent policy show",
        maintainer,
        context.id,
        "policy-command-show",
    )
    interaction = control.create_interaction(
        actor=maintainer,
        session_id=str(session_result.data["session_id"]),
        interaction_type=InteractionType.APPROVAL,
        prompt="Use command policy?",
        risk_level=RiskLevel.CRITICAL,
        trace_id="policy-command-approval",
        chat_context_id=context.id,
    )

    assert set_result.canonical_command == "policy.set"
    assert set_result.data["override"]["quorum_by_risk"] == {"critical": 3}
    assert show_result.data["policy"]["effective_quorum_by_risk"]["critical"] == 3
    assert interaction.required_votes == 3
