from __future__ import annotations

import pytest

from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    Actor,
    AgentBridgeError,
    ErrorCode,
    InteractionStatus,
    InteractionType,
    LeaseOwnerType,
    RiskLevel,
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
            f"--root {tmp_path} --max-running-turns 2 --max-queued-turns 1"
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
    assert session.canonical_command == "session.create"
    assert first_turn.canonical_command == "turn.enqueue"
    assert blocked.value.code == ErrorCode.QUOTA_EXCEEDED
    assert blocked.value.details == {
        "project_id": project_id,
        "queued_turns": 1,
        "max_queued_turns": 1,
        "queue_position": 2,
    }


def test_group_role_binding_grants_context_permissions(tmp_path):
    control = ControlPlane()
    commands = CommandService(control)
    context = make_context(control)
    maintainer = Actor(id="usr_maintainer", roles={"maintainer"})
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
            "/agent ask after revoke",
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
    maintainer = Actor(id="usr_1", roles={"maintainer"})

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
