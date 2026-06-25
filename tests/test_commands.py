from __future__ import annotations

import pytest

from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    Actor,
    AgentBridgeError,
    ErrorCode,
    LeaseOwnerType,
    SemanticEventSource,
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
