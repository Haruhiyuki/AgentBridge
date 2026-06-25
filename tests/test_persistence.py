from __future__ import annotations

from fastapi.testclient import TestClient

from agentbridge.api import create_app
from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import Actor, InteractionType, LeaseOwnerType, SemanticEventSource
from agentbridge.persistence import SQLAlchemyRepository


def test_sqlalchemy_repository_recovers_control_plane_state(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'agentbridge.db'}"
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    first_repo = SQLAlchemyRepository(database_url, create_schema=True)
    first_control = ControlPlane(repository=first_repo)
    first_commands = CommandService(first_control)
    context = first_control.get_or_create_chat_context(
        bot_instance_id="bot-test",
        platform="onebot.v11",
        chat_space_id="group-persist",
    )

    project_invocation = first_commands.parse(
        raw_text=(
            f"/agent project create --name Backend --path {tmp_path} "
            f"--root {tmp_path} --alias backend"
        ),
        actor=maintainer,
        chat_context_id=context.id,
        idempotency_key="persist-project",
        trace_id="persist-project",
    )
    project_result = first_commands.execute(project_invocation)
    session_invocation = first_commands.parse(
        raw_text="/agent session new Persistent Session",
        actor=maintainer,
        chat_context_id=context.id,
        idempotency_key="persist-session",
        trace_id="persist-session",
    )
    session_result = first_commands.execute(session_invocation)
    turn_invocation = first_commands.parse(
        raw_text="/agent ask preserve this turn",
        actor=maintainer,
        chat_context_id=context.id,
        idempotency_key="persist-turn",
        trace_id="persist-turn",
    )
    turn_result = first_commands.execute(turn_invocation)

    session_id = session_result.data["session_id"]
    lease = first_control.acquire_lease(
        actor=maintainer,
        session_id=session_id,
        owner_type=LeaseOwnerType.WEB_ADMIN,
        owner_id=maintainer.id,
        ttl_seconds=300,
        trace_id="persist-lease",
        chat_context_id=context.id,
    )
    first_control.emit_event(
        event_type="assistant.delta",
        source=SemanticEventSource.TERMINAL_AGENT,
        trace_id="persist-terminal-event",
        project_id=project_result.data["project_id"],
        session_id=session_id,
        turn_id=turn_result.data["turn_id"],
        payload={"text": "hello"},
        idempotency_key="persist-terminal-event",
    )
    role_binding = first_control.grant_group_roles(
        actor=maintainer,
        chat_context_id=context.id,
        target_actor_id="usr_member",
        roles={"operator"},
        trace_id="persist-role",
    )
    interaction = first_control.create_interaction(
        actor=maintainer,
        session_id=session_id,
        interaction_type=InteractionType.APPROVAL,
        prompt="Approve persisted action?",
        required_votes=1,
        trace_id="persist-interaction",
        chat_context_id=context.id,
    )
    voted_interaction = first_control.vote_interaction(
        actor=maintainer,
        interaction_id=interaction.id,
        approve=True,
        trace_id="persist-interaction-vote",
        chat_context_id=context.id,
    )

    second_repo = SQLAlchemyRepository(database_url)
    second_control = ControlPlane(repository=second_repo)
    second_commands = CommandService(second_control)

    restored_context = second_repo.get_chat_context(context.id)
    restored_session = second_repo.get_session(session_id)
    restored_events = second_repo.list_events(session_id=session_id)

    assert restored_context.active_project_id == project_result.data["project_id"]
    assert restored_context.active_session_id == session_id
    assert restored_session.name == "Persistent Session"
    assert second_repo.current_lease(session_id) == lease
    assert second_repo.lease_epochs[session_id] == lease.epoch
    assert second_repo.list_group_role_bindings(context.id) == [role_binding]
    assert second_control.effective_actor(
        Actor(id="usr_member", roles={"member"}), context.id
    ).roles == {"member", "operator"}
    assert second_repo.get_interaction(interaction.id) == voted_interaction
    assert [event.type for event in restored_events] == [
        "session.created",
        "turn.queued",
        "lease.acquired",
        "assistant.delta",
        "approval.requested",
        "approval.voted",
    ]
    assert "group.role_granted" in [event.type for event in second_repo.semantic_events]
    assert len(second_repo.audit_events) >= 5

    duplicate_result = second_commands.execute(
        second_commands.parse(
            raw_text="/agent session new Persistent Session",
            actor=maintainer,
            chat_context_id=context.id,
            idempotency_key="persist-session",
            trace_id="persist-session-duplicate",
        )
    )
    assert duplicate_result == session_result
    assert len(second_repo.sessions) == 1


def test_api_can_use_sqlalchemy_repository_from_environment(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'api.db'}"
    monkeypatch.setenv("AGENTBRIDGE_DATABASE_URL", database_url)
    monkeypatch.setenv("AGENTBRIDGE_AUTO_CREATE_SCHEMA", "true")

    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["storage"] == "sqlalchemy"


def test_interaction_cancellation_survives_repository_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'interaction-cancel.db'}"
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    first_repo = SQLAlchemyRepository(database_url, create_schema=True)
    first_control = ControlPlane(repository=first_repo)
    project = first_control.create_project(
        actor=maintainer,
        name="Backend",
        trace_id="interaction-cancel-project",
    )
    workspace = first_control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="interaction-cancel-workspace",
    )
    session = first_control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Interaction Cancel",
        agent_type=project.default_agent,
        visibility="group",
        trace_id="interaction-cancel-session",
    )
    interaction = first_control.create_interaction(
        actor=maintainer,
        session_id=session.id,
        interaction_type=InteractionType.APPROVAL,
        prompt="Approve persistent cancellation?",
        trace_id="interaction-cancel-create",
    )
    cancelled = first_control.cancel_interaction(
        actor=maintainer,
        interaction_id=interaction.id,
        reason="superseded",
        trace_id="interaction-cancel",
    )

    second_repo = SQLAlchemyRepository(database_url)

    assert second_repo.get_interaction(interaction.id) == cancelled
    assert second_repo.get_interaction(interaction.id).status.value == "cancelled"
    assert second_repo.get_interaction(interaction.id).answer == "superseded"
