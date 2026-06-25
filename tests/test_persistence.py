from __future__ import annotations

import json
from pathlib import Path

from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from agentbridge.api import create_app
from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    AccessPolicyEffect,
    Actor,
    DeviceIdentityScope,
    DeviceIdentityStatus,
    InteractionType,
    LeaseOwnerType,
    PolicyScope,
    RiskLevel,
    SemanticEventSource,
    Visibility,
)
from agentbridge.persistence import SQLAlchemyRepository
from agentbridge.policy import Permission
from agentbridge.terminal_agent import FakeTerminalBackend, TerminalAgentService, TerminalStatus
from alembic import command


def test_semantic_event_query_column_migration_backfills_payload(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'migration-backfill.db'}"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option("path_separator", "os")

    command.upgrade(config, "0007_access_policy_rules")
    engine = create_engine(database_url)
    legacy_payload = {
        "id": "evt_legacy",
        "stream_id": "session:sess_legacy",
        "seq": 1,
        "type": "assistant.delta",
        "source": "terminal_agent",
        "trace_id": "legacy-trace",
        "idempotency_key": None,
        "project_id": "prj_legacy",
        "session_id": "sess_legacy",
        "turn_id": "turn_legacy",
        "interaction_id": "int_legacy",
        "payload": {"text": "legacy"},
        "created_at": "2026-06-25T00:00:00+00:00",
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO semantic_events
                  (id, position, stream_id, seq, type, idempotency_key, payload)
                VALUES
                  (:id, :position, :stream_id, :seq, :type, :idempotency_key, :payload)
                """
            ),
            {
                "id": "evt_legacy",
                "position": 1,
                "stream_id": "session:sess_legacy",
                "seq": 1,
                "type": "assistant.delta",
                "idempotency_key": None,
                "payload": json.dumps(legacy_payload),
            },
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT source, trace_id, project_id, session_id, turn_id, interaction_id
                FROM semantic_events
                WHERE id = :id
                """
            ),
            {"id": "evt_legacy"},
        ).one()

    assert row.source == "terminal_agent"
    assert row.trace_id == "legacy-trace"
    assert row.project_id == "prj_legacy"
    assert row.session_id == "sess_legacy"
    assert row.turn_id == "turn_legacy"
    assert row.interaction_id == "int_legacy"


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


def test_sqlalchemy_repository_lists_filtered_audit_events(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'audit-query.db'}"
    maintainer = Actor(id="usr_audit", roles={"maintainer"})

    first_repo = SQLAlchemyRepository(database_url, create_schema=True)
    control = ControlPlane(repository=first_repo)
    project = control.create_project(
        actor=maintainer,
        name="Audit Backend",
        trace_id="audit-project",
    )
    workspace = control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="audit-workspace",
    )
    first_session = control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="First Audit Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="audit-session-one",
    )
    second_session = control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Second Audit Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="audit-session-two",
    )

    restored = SQLAlchemyRepository(database_url)

    newest = restored.list_audit_events(
        action="session.created",
        actor_id="usr_audit",
        project_id=project.id,
        limit=1,
    )
    assert len(newest) == 1
    assert newest[0].session_id == second_session.id
    assert newest[0].trace_id == "audit-session-two"

    session_filtered = restored.list_audit_events(
        action="session.created",
        actor_id="usr_audit",
        session_id=first_session.id,
        trace_id="audit-session-one",
    )
    assert [event.session_id for event in session_filtered] == [first_session.id]

    project_sessions = restored.list_audit_events(
        action="session.created",
        project_id=project.id,
    )
    assert [event.session_id for event in project_sessions] == [
        second_session.id,
        first_session.id,
    ]
    workspace_query = restored.list_audit_events(
        action="project.workspace_added",
        payload_query=workspace.id,
    )
    assert [event.details["workspace_id"] for event in workspace_query] == [workspace.id]
    assert (
        restored.list_audit_events(
            action="project.workspace_added",
            payload_query="missing-workspace",
        )
        == []
    )
    assert restored.list_audit_events(action="session.created", actor_id="missing") == []


def test_sqlalchemy_repository_lists_filtered_semantic_events(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'event-query.db'}"
    maintainer = Actor(id="usr_events", roles={"maintainer"})

    first_repo = SQLAlchemyRepository(database_url, create_schema=True)
    control = ControlPlane(repository=first_repo)
    project = control.create_project(
        actor=maintainer,
        name="Event Backend",
        trace_id="event-project",
    )
    workspace = control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="event-workspace",
    )
    first_session = control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="First Event Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="event-session-one",
    )
    second_session = control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Second Event Session",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="event-session-two",
    )
    first_event = control.emit_event(
        event_type="assistant.delta",
        source=SemanticEventSource.TERMINAL_AGENT,
        trace_id="event-search-one",
        project_id=project.id,
        session_id=first_session.id,
        payload={"text": "first"},
    )
    second_event = control.emit_event(
        event_type="assistant.delta",
        source=SemanticEventSource.TERMINAL_AGENT,
        trace_id="event-search-two",
        project_id=project.id,
        session_id=second_session.id,
        payload={"text": "second"},
    )

    restored = SQLAlchemyRepository(database_url)

    newest = restored.list_semantic_events(
        project_id=project.id,
        event_type="assistant.delta",
        source=SemanticEventSource.TERMINAL_AGENT,
        limit=1,
    )
    assert [event.id for event in newest] == [second_event.id]

    trace_filtered = restored.list_semantic_events(trace_id="event-search-one")
    assert [event.id for event in trace_filtered] == [first_event.id]

    session_filtered = restored.list_semantic_events(
        session_id=first_session.id,
        event_type="assistant.delta",
    )
    assert [event.id for event in session_filtered] == [first_event.id]
    payload_filtered = restored.list_semantic_events(
        project_id=project.id,
        event_type="assistant.delta",
        payload_query="second",
    )
    assert [event.id for event in payload_filtered] == [second_event.id]
    assert (
        restored.list_semantic_events(
            project_id=project.id,
            event_type="assistant.delta",
            payload_query="missing-payload",
        )
        == []
    )
    assert restored.list_semantic_events(trace_id="missing") == []


def test_sqlalchemy_repository_persists_device_identities(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'device-identities.db'}"
    admin = Actor(id="security-admin", roles={"admin"})

    first_repo = SQLAlchemyRepository(database_url, create_schema=True)
    first_control = ControlPlane(repository=first_repo)
    identity, device_key = first_control.upsert_device_identity(
        actor=admin,
        device_id="laptop",
        display_name="Maintainer laptop",
        device_key="managed-secret",
        allowed_scopes={
            DeviceIdentityScope.BOT_GATEWAY_MANAGE,
            DeviceIdentityScope.CHAT_CONTEXT_MANAGE,
            DeviceIdentityScope.COMMAND_EXECUTE,
            DeviceIdentityScope.DEVICE_MANAGE,
            DeviceIdentityScope.GROUP_ROLE_MANAGE,
            DeviceIdentityScope.HTTP_API,
            DeviceIdentityScope.INTERACTION_MANAGE,
            DeviceIdentityScope.POLICY_MANAGE,
            DeviceIdentityScope.PROJECT_MANAGE,
            DeviceIdentityScope.SESSION_MANAGE,
            DeviceIdentityScope.SESSION_EVENTS_WS,
            DeviceIdentityScope.TERMINAL_CONTROL,
        },
        certificate_fingerprints={"SHA256:AA:BB:CC"},
        trace_id="device-identity-create",
    )

    assert device_key == "managed-secret"
    assert identity.status == DeviceIdentityStatus.ACTIVE
    assert identity.key_hash != "managed-secret"
    cert_only_identity, cert_only_key = first_control.upsert_device_identity(
        actor=admin,
        device_id="cert-only",
        display_name="Certificate only",
        allowed_scopes={DeviceIdentityScope.HTTP_API},
        certificate_fingerprints={"SHA256:DD:EE:FF"},
        trace_id="device-identity-cert-only-create",
    )

    assert cert_only_key is None
    assert cert_only_identity.key_hash is None
    assert cert_only_identity.certificate_fingerprints == {"ddeeff"}

    restored = SQLAlchemyRepository(database_url)
    restored_identities = {
        identity.device_id: identity for identity in restored.list_device_identities()
    }
    restored_identity = restored_identities["laptop"]
    assert restored_identity.device_id == "laptop"
    assert restored_identity.display_name == "Maintainer laptop"
    assert restored_identity.status == DeviceIdentityStatus.ACTIVE
    assert restored_identity.key_hash == identity.key_hash
    assert restored_identity.allowed_scopes == {
        DeviceIdentityScope.BOT_GATEWAY_MANAGE,
        DeviceIdentityScope.CHAT_CONTEXT_MANAGE,
        DeviceIdentityScope.COMMAND_EXECUTE,
        DeviceIdentityScope.DEVICE_MANAGE,
        DeviceIdentityScope.GROUP_ROLE_MANAGE,
        DeviceIdentityScope.HTTP_API,
        DeviceIdentityScope.INTERACTION_MANAGE,
        DeviceIdentityScope.POLICY_MANAGE,
        DeviceIdentityScope.PROJECT_MANAGE,
        DeviceIdentityScope.SESSION_MANAGE,
        DeviceIdentityScope.SESSION_EVENTS_WS,
        DeviceIdentityScope.TERMINAL_CONTROL,
    }
    assert restored_identity.certificate_fingerprints == {"aabbcc"}
    restored_cert_only = restored_identities["cert-only"]
    assert restored_cert_only.key_hash is None
    assert restored_cert_only.certificate_fingerprints == {"ddeeff"}
    used_identity = restored.mark_device_identity_used("laptop")
    assert used_identity.last_used_at is not None

    restored_after_use = SQLAlchemyRepository(database_url)
    used_restored_identity = restored_after_use.get_device_identity("laptop")
    assert used_restored_identity.last_used_at == used_identity.last_used_at

    restored_control = ControlPlane(repository=restored_after_use)
    revoked = restored_control.revoke_device_identity(
        actor=admin,
        device_id="laptop",
        trace_id="device-identity-revoke",
    )
    assert revoked.status == DeviceIdentityStatus.REVOKED

    second_restore = SQLAlchemyRepository(database_url)
    active_after_revoke = {
        identity.device_id: identity for identity in second_restore.list_device_identities()
    }
    assert sorted(active_after_revoke) == ["cert-only"]
    restored_after_revoke = {
        identity.device_id: identity
        for identity in second_restore.list_device_identities(include_revoked=True)
    }
    revoked_identity = restored_after_revoke["laptop"]
    assert revoked_identity.device_id == "laptop"
    assert revoked_identity.status == DeviceIdentityStatus.REVOKED
    assert revoked_identity.revoked_at is not None


def test_terminal_lifecycle_tracking_recovers_from_persisted_events(tmp_path):
    class RecoveredExitedBackend(FakeTerminalBackend):
        def status(self, *, session_id: str) -> TerminalStatus:
            return TerminalStatus(
                started=True,
                running=False,
                exit_code=12,
                pid=2468,
                output_cursor=101,
            )

    database_url = f"sqlite:///{tmp_path / 'agentbridge.db'}"
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    first_repo = SQLAlchemyRepository(database_url, create_schema=True)
    first_control = ControlPlane(repository=first_repo)
    project = first_control.create_project(actor=maintainer, name="Backend", trace_id="project")
    workspace = first_control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="workspace",
    )
    session = first_control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Persisted Terminal",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="session",
    )
    first_terminal = TerminalAgentService(first_control, backend=FakeTerminalBackend())
    first_terminal.start_session(
        session_id=session.id,
        command="fake-cli",
        trace_id="terminal-start-before-repository-reload",
    )

    second_repo = SQLAlchemyRepository(database_url)
    second_control = ControlPlane(repository=second_repo)
    restarted_terminal = TerminalAgentService(second_control, backend=RecoveredExitedBackend())

    assert restarted_terminal.lifecycle_monitor_status()["tracked_sessions"] == 1
    observed = restarted_terminal.run_lifecycle_monitor_once(
        trace_id="terminal-monitor-after-repository-reload"
    )

    assert observed[session.id].exit_code == 12
    exited_events = [
        event
        for event in second_repo.list_events(session_id=session.id)
        if event.type == "terminal.exited"
    ]
    assert len(exited_events) == 1
    assert exited_events[0].payload["generation"] == 1
    assert exited_events[0].payload["exit_code"] == 12


def test_terminal_lifecycle_lost_state_survives_repository_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'agentbridge.db'}"
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    first_repo = SQLAlchemyRepository(database_url, create_schema=True)
    first_control = ControlPlane(repository=first_repo)
    project = first_control.create_project(actor=maintainer, name="Backend", trace_id="project")
    workspace = first_control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="workspace",
    )
    session = first_control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Lost Terminal",
        agent_type=project.default_agent,
        visibility=Visibility.GROUP,
        trace_id="session",
    )
    first_terminal = TerminalAgentService(first_control, backend=FakeTerminalBackend())
    first_terminal.start_session(
        session_id=session.id,
        command="fake-cli",
        trace_id="terminal-start-before-lost-recovery",
    )

    second_repo = SQLAlchemyRepository(database_url)
    second_control = ControlPlane(repository=second_repo)
    second_terminal = TerminalAgentService(second_control, backend=FakeTerminalBackend())

    observed = second_terminal.run_lifecycle_monitor_once(
        trace_id="terminal-monitor-lost-after-repository-reload"
    )

    assert observed[session.id] == TerminalStatus(started=False, running=False)
    lost_events = [
        event
        for event in second_repo.list_events(session_id=session.id)
        if event.type == "terminal.lost"
    ]
    assert len(lost_events) == 1
    assert lost_events[0].payload["generation"] == 1
    assert lost_events[0].payload["reason"] == "backend_state_missing"

    third_repo = SQLAlchemyRepository(database_url)
    third_control = ControlPlane(repository=third_repo)
    third_terminal = TerminalAgentService(third_control, backend=FakeTerminalBackend())
    third_terminal.run_lifecycle_monitor_once(
        trace_id="terminal-monitor-lost-after-second-repository-reload"
    )

    lost_events = [
        event
        for event in third_repo.list_events(session_id=session.id)
        if event.type == "terminal.lost"
    ]
    assert len(lost_events) == 1


def test_api_can_use_sqlalchemy_repository_from_environment(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'api.db'}"
    monkeypatch.setenv("AGENTBRIDGE_DATABASE_URL", database_url)
    monkeypatch.setenv("AGENTBRIDGE_AUTO_CREATE_SCHEMA", "true")

    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["storage"] == "sqlalchemy"


def test_api_sqlalchemy_repository_uses_pool_environment_options(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'api-pool.db'}"
    monkeypatch.setenv("AGENTBRIDGE_DATABASE_URL", database_url)
    monkeypatch.setenv("AGENTBRIDGE_AUTO_CREATE_SCHEMA", "true")
    monkeypatch.setenv("AGENTBRIDGE_DATABASE_POOL_SIZE", "3")
    monkeypatch.setenv("AGENTBRIDGE_DATABASE_MAX_OVERFLOW", "4")
    monkeypatch.setenv("AGENTBRIDGE_DATABASE_POOL_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("AGENTBRIDGE_DATABASE_POOL_RECYCLE_SECONDS", "6")
    monkeypatch.setenv("AGENTBRIDGE_DATABASE_POOL_PRE_PING", "true")
    monkeypatch.setenv("AGENTBRIDGE_DATABASE_ECHO", "true")

    app = create_app()
    repository = app.state.control.repository

    assert isinstance(repository, SQLAlchemyRepository)
    assert repository.engine.echo is True
    assert repository.engine.pool.size() == 3
    assert repository.engine.pool._max_overflow == 4
    assert repository.engine.pool._timeout == 5
    assert repository.engine.pool._recycle == 6
    assert repository.engine.pool._pre_ping is True


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
        risk_level=RiskLevel.CRITICAL,
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
    assert second_repo.get_interaction(interaction.id).risk_level == RiskLevel.CRITICAL
    assert second_repo.get_interaction(interaction.id).policy_snapshot[
        "dangerous_permission_required"
    ] is True
    assert second_repo.get_interaction(interaction.id).answer == "superseded"


def test_approval_policy_overrides_survive_repository_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'approval-policy.db'}"
    maintainer = Actor(id="usr_1", roles={"maintainer"})

    first_repo = SQLAlchemyRepository(database_url, create_schema=True)
    first_control = ControlPlane(repository=first_repo)
    project = first_control.create_project(
        actor=maintainer,
        name="Backend",
        trace_id="approval-policy-project",
    )
    workspace = first_control.add_workspace(
        actor=maintainer,
        project_id=project.id,
        machine_id="local",
        path=str(tmp_path),
        allowed_root=str(tmp_path),
        trace_id="approval-policy-workspace",
    )
    first_control.set_approval_policy_override(
        actor=maintainer,
        scope_type=PolicyScope.PROJECT,
        scope_id=project.id,
        quorum_by_risk={RiskLevel.CRITICAL: 4},
        trace_id="approval-policy-set",
    )

    second_repo = SQLAlchemyRepository(database_url)
    second_control = ControlPlane(repository=second_repo)
    restored_override = second_repo.get_approval_policy_override(
        scope_type=PolicyScope.PROJECT,
        scope_id=project.id,
    )
    session = second_control.create_session(
        actor=maintainer,
        project_id=project.id,
        workspace_id=workspace.id,
        name="Policy Restore",
        agent_type=project.default_agent,
        visibility="group",
        trace_id="approval-policy-session",
    )
    interaction = second_control.create_interaction(
        actor=maintainer,
        session_id=session.id,
        interaction_type=InteractionType.APPROVAL,
        prompt="Use restored policy?",
        risk_level=RiskLevel.CRITICAL,
        trace_id="approval-policy-interaction",
    )

    assert restored_override is not None
    assert restored_override.quorum_by_risk == {RiskLevel.CRITICAL: 4}
    assert interaction.required_votes == 4
    assert interaction.policy_snapshot["applied_overrides"][0]["scope_id"] == project.id


def test_access_policy_rules_survive_repository_restart(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'access-policy.db'}"
    maintainer = Actor(id="usr_1", roles={"maintainer"})
    operator = Actor(id="usr_operator", roles={"operator"})

    first_repo = SQLAlchemyRepository(database_url, create_schema=True)
    first_control = ControlPlane(repository=first_repo)
    rule = first_control.set_access_policy_rule(
        actor=maintainer,
        effect=AccessPolicyEffect.DENY,
        action=Permission.SESSION_SEND.value,
        roles=["operator"],
        trace_id="access-policy-set",
    )

    second_repo = SQLAlchemyRepository(database_url)
    second_control = ControlPlane(repository=second_repo)
    restored_rule = second_repo.get_access_policy_rule(rule.id)
    decision = second_control.simulate_access_policy(
        actor=maintainer,
        target_actor=operator,
        action=Permission.SESSION_SEND.value,
    )

    assert restored_rule == rule
    assert decision["decision"]["allowed"] is False
    assert decision["decision"]["source"] == "access_policy"
    assert decision["decision"]["matched_rule_id"] == rule.id
