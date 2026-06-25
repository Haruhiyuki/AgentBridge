from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    delete,
    select,
)
from sqlalchemy.engine import Engine

from agentbridge.domain import (
    AccessPolicyRule,
    AgentSession,
    ApprovalPolicyOverride,
    AuditEvent,
    BotDeliveryRecord,
    ChatContext,
    CommandResult,
    DeviceIdentity,
    GroupRoleBinding,
    Interaction,
    Project,
    ProjectBinding,
    SemanticEvent,
    SemanticEventSource,
    Turn,
    Workspace,
    WriterLease,
)
from agentbridge.storage import (
    InMemoryRepository,
    created_at_in_range,
    payload_contains_query,
)

metadata = MetaData()

projects_table = Table(
    "projects",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("slug", String(255), nullable=False, unique=True, index=True),
    Column("payload", JSON, nullable=False),
)

workspaces_table = Table(
    "workspaces",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("path", String(2048), nullable=False),
    Column("payload", JSON, nullable=False),
)

chat_contexts_table = Table(
    "chat_contexts",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("bot_instance_id", String(255), nullable=False),
    Column("platform", String(255), nullable=False),
    Column("chat_space_id", String(255), nullable=False),
    Column("thread_id", String(255), nullable=True),
    Column("user_id", String(255), nullable=True),
    Column("payload", JSON, nullable=False),
    UniqueConstraint(
        "bot_instance_id",
        "platform",
        "chat_space_id",
        "thread_id",
        "user_id",
        name="uq_chat_context_identity",
    ),
)

project_bindings_table = Table(
    "project_bindings",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("chat_context_id", String(64), nullable=False, index=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("is_default", Boolean, nullable=False, default=False),
    Column("payload", JSON, nullable=False),
)

group_role_bindings_table = Table(
    "group_role_bindings",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("chat_context_id", String(64), nullable=False, index=True),
    Column("actor_id", String(255), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("chat_context_id", "actor_id", name="uq_group_role_binding_actor"),
)

approval_policy_overrides_table = Table(
    "approval_policy_overrides",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("scope_type", String(64), nullable=False, index=True),
    Column("scope_id", String(64), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("scope_type", "scope_id", name="uq_approval_policy_scope"),
)

access_policy_rules_table = Table(
    "access_policy_rules",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("effect", String(16), nullable=False, index=True),
    Column("action", String(128), nullable=False, index=True),
    Column("resource_type", String(128), nullable=False, index=True),
    Column("resource_id", String(255), nullable=True, index=True),
    Column("enabled", Boolean, nullable=False, index=True),
    Column("priority", Integer, nullable=False, index=True),
    Column("updated_at", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
)

device_identities_table = Table(
    "device_identities",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("device_id", String(255), nullable=False, unique=True, index=True),
    Column("status", String(64), nullable=False, index=True),
    Column("created_at", String(64), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
)

sessions_table = Table(
    "agent_sessions",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("short_code", String(32), nullable=False, unique=True, index=True),
    Column("project_id", String(64), nullable=False, index=True),
    Column("workspace_id", String(64), nullable=False, index=True),
    Column("status", String(64), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
)

turns_table = Table(
    "turns",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("session_id", String(64), nullable=False, index=True),
    Column("status", String(64), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
)

interactions_table = Table(
    "interactions",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("session_id", String(64), nullable=False, index=True),
    Column("status", String(64), nullable=False, index=True),
    Column("payload", JSON, nullable=False),
)

writer_leases_table = Table(
    "writer_leases",
    metadata,
    Column("session_id", String(64), primary_key=True),
    Column("epoch", Integer, nullable=False),
    Column("payload", JSON, nullable=False),
)

lease_epochs_table = Table(
    "lease_epochs",
    metadata,
    Column("session_id", String(64), primary_key=True),
    Column("epoch", Integer, nullable=False),
)

command_results_table = Table(
    "command_results",
    metadata,
    Column("idempotency_key", String(512), primary_key=True),
    Column("payload", JSON, nullable=False),
)

audit_events_table = Table(
    "audit_events",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("position", Integer, nullable=False, unique=True),
    Column("action", String(255), nullable=False, index=True),
    Column("actor_id", String(255), nullable=False, index=True),
    Column("entry_hash", String(128), nullable=False, unique=True),
    Column("payload", JSON, nullable=False),
)

semantic_events_table = Table(
    "semantic_events",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("position", Integer, nullable=False, unique=True),
    Column("stream_id", String(255), nullable=False, index=True),
    Column("seq", Integer, nullable=False),
    Column("type", String(255), nullable=False, index=True),
    Column("source", String(64), nullable=True, index=True),
    Column("trace_id", String(255), nullable=True, index=True),
    Column("project_id", String(64), nullable=True, index=True),
    Column("session_id", String(64), nullable=True, index=True),
    Column("turn_id", String(64), nullable=True, index=True),
    Column("interaction_id", String(64), nullable=True, index=True),
    Column("idempotency_key", String(512), nullable=True, unique=True),
    Column("payload", JSON, nullable=False),
    UniqueConstraint("stream_id", "seq", name="uq_semantic_events_stream_seq"),
)

event_stream_offsets_table = Table(
    "event_stream_offsets",
    metadata,
    Column("stream_id", String(255), primary_key=True),
    Column("seq", Integer, nullable=False),
)

bot_delivery_records_table = Table(
    "bot_delivery_records",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("idempotency_key", String(512), nullable=False, unique=True),
    Column("platform", String(64), nullable=False, index=True),
    Column("chat_context_id", String(64), nullable=False, index=True),
    Column("event_id", String(64), nullable=False, index=True),
    Column("event_seq", Integer, nullable=False),
    Column("status", String(64), nullable=False, index=True),
    Column("platform_state", String(64), nullable=False, index=True, default="pending"),
    Column("attempt_count", Integer, nullable=False, default=1),
    Column("next_retry_at", String(64), nullable=True, index=True),
    Column("acknowledged_at", String(64), nullable=True, index=True),
    Column("edited_at", String(64), nullable=True),
    Column("deleted_at", String(64), nullable=True, index=True),
    Column("updated_at", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
)


class SQLAlchemyRepository(InMemoryRepository):
    """Write-through SQLAlchemy repository for single-process MVP persistence."""

    storage_label = "sqlalchemy"
    _mutating_methods: ClassVar[set[str]] = {
        "create_project",
        "add_workspace",
        "get_or_create_chat_context",
        "grant_group_roles",
        "revoke_group_roles",
        "upsert_approval_policy_override",
        "upsert_access_policy_rule",
        "delete_access_policy_rule",
        "upsert_device_identity",
        "revoke_device_identity",
        "mark_device_identity_used",
        "bind_project",
        "update_active_project",
        "update_active_session",
        "create_session",
        "close_session",
        "enqueue_turn",
        "cancel_queued_turn",
        "clear_queued_turns",
        "reorder_queued_turn",
        "set_turn_queue_paused",
        "start_turn",
        "finish_turn",
        "acquire_lease",
        "release_lease",
        "create_interaction",
        "answer_interaction",
        "cancel_interaction",
        "expire_due_interactions",
        "vote_interaction",
        "store_command_result",
        "store_bot_delivery_record",
        "append_audit",
        "append_event",
    }

    def __init__(
        self,
        database_url: str,
        *,
        create_schema: bool = False,
        engine: Engine | None = None,
        engine_options: dict[str, Any] | None = None,
    ) -> None:
        self.engine = engine or create_engine(
            database_url,
            future=True,
            **(engine_options or {}),
        )
        if create_schema:
            metadata.create_all(self.engine)
        super().__init__()
        self._persisting_enabled = False
        self._load_state()
        self._persisting_enabled = True

    def __getattribute__(self, name: str) -> Any:
        attr = super().__getattribute__(name)
        if name in super().__getattribute__("_mutating_methods") and callable(attr):
            return super().__getattribute__("_with_persistence")(attr)
        return attr

    def _with_persistence(self, method: Callable[..., Any]) -> Callable[..., Any]:
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            result = method(*args, **kwargs)
            if self._persisting_enabled:
                self._persist_state()
            return result

        return wrapped

    def _load_state(self) -> None:
        with self.engine.connect() as connection:
            self.projects = self._load_mapping(connection, projects_table, Project)
            self.workspaces = self._load_mapping(connection, workspaces_table, Workspace)
            self.bindings = self._load_mapping(
                connection, project_bindings_table, ProjectBinding
            )
            self.group_role_bindings = {
                (binding.chat_context_id, binding.actor_id): binding
                for binding in self._load_mapping(
                    connection, group_role_bindings_table, GroupRoleBinding
                ).values()
            }
            self.approval_policy_overrides = {
                (override.scope_type, override.scope_id): override
                for override in self._load_mapping(
                    connection,
                    approval_policy_overrides_table,
                    ApprovalPolicyOverride,
                ).values()
            }
            self.access_policy_rules = self._load_mapping(
                connection, access_policy_rules_table, AccessPolicyRule
            )
            self.device_identities = {
                identity.device_id: identity
                for identity in self._load_mapping(
                    connection, device_identities_table, DeviceIdentity
                ).values()
            }
            self.chat_contexts = self._load_mapping(
                connection, chat_contexts_table, ChatContext
            )
            self.sessions = self._load_mapping(connection, sessions_table, AgentSession)
            self.turns = self._load_mapping(connection, turns_table, Turn)
            self.interactions = self._load_mapping(connection, interactions_table, Interaction)
            self.leases = {
                row.session_id: WriterLease.model_validate(row.payload)
                for row in connection.execute(select(writer_leases_table)).all()
            }
            self.lease_epochs = {
                row.session_id: int(row.epoch)
                for row in connection.execute(select(lease_epochs_table)).all()
            }
            self.command_results = {
                row.idempotency_key: CommandResult.model_validate(row.payload)
                for row in connection.execute(select(command_results_table)).all()
            }
            self.audit_events = [
                AuditEvent.model_validate(row.payload)
                for row in connection.execute(
                    select(audit_events_table).order_by(audit_events_table.c.position)
                ).all()
            ]
            self.semantic_events = [
                SemanticEvent.model_validate(row.payload)
                for row in connection.execute(
                    select(semantic_events_table).order_by(semantic_events_table.c.position)
                ).all()
            ]
            self.event_stream_seq = {
                row.stream_id: int(row.seq)
                for row in connection.execute(select(event_stream_offsets_table)).all()
            }
            self.bot_delivery_records = {
                row.idempotency_key: BotDeliveryRecord.model_validate(row.payload)
                for row in connection.execute(select(bot_delivery_records_table)).all()
            }

        self._short_codes = {session.short_code for session in self.sessions.values()}
        self._chat_context_index = {
            (
                context.bot_instance_id,
                context.platform,
                context.chat_space_id,
                context.thread_id,
                context.user_id,
            ): context.id
            for context in self.chat_contexts.values()
        }
        self.event_idempotency = {
            event.idempotency_key: event
            for event in self.semantic_events
            if event.idempotency_key is not None
        }
        if not self.event_stream_seq:
            for event in self.semantic_events:
                self.event_stream_seq[event.stream_id] = max(
                    self.event_stream_seq.get(event.stream_id, 0), event.seq
                )

    @staticmethod
    def _load_mapping(connection: Any, table: Table, model_type: type[Any]) -> dict[str, Any]:
        return {
            row.id: model_type.model_validate(row.payload)
            for row in connection.execute(select(table)).all()
        }

    def list_audit_events(
        self,
        *,
        actor_id: str | None = None,
        action: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        interaction_id: str | None = None,
        trace_id: str | None = None,
        payload_query: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        max_results = self._clamp_audit_limit(limit)
        stmt = select(audit_events_table).order_by(audit_events_table.c.position.desc())
        if action is not None:
            stmt = stmt.where(audit_events_table.c.action == action)
        if actor_id is not None:
            stmt = stmt.where(audit_events_table.c.actor_id == actor_id)

        events: list[AuditEvent] = []
        with self._lock, self.engine.connect() as connection:
            for row in connection.execute(stmt):
                event = AuditEvent.model_validate(row.payload)
                if (
                    (project_id is None or event.project_id == project_id)
                    and (session_id is None or event.session_id == session_id)
                    and (interaction_id is None or event.interaction_id == interaction_id)
                    and (trace_id is None or event.trace_id == trace_id)
                    and payload_contains_query(event.details, payload_query)
                    and created_at_in_range(
                        event.created_at,
                        created_from=created_from,
                        created_to=created_to,
                    )
                ):
                    events.append(event)
                    if len(events) >= max_results:
                        break
        return events

    def list_semantic_events(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        interaction_id: str | None = None,
        event_type: str | None = None,
        source: SemanticEventSource | None = None,
        trace_id: str | None = None,
        payload_query: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 100,
    ) -> list[SemanticEvent]:
        max_results = self._clamp_event_search_limit(limit)
        stmt = select(semantic_events_table).order_by(
            semantic_events_table.c.position.desc()
        )
        if project_id is not None:
            stmt = stmt.where(semantic_events_table.c.project_id == project_id)
        if session_id is not None:
            stmt = stmt.where(semantic_events_table.c.session_id == session_id)
        if turn_id is not None:
            stmt = stmt.where(semantic_events_table.c.turn_id == turn_id)
        if interaction_id is not None:
            stmt = stmt.where(semantic_events_table.c.interaction_id == interaction_id)
        if event_type is not None:
            stmt = stmt.where(semantic_events_table.c.type == event_type)
        if source is not None:
            stmt = stmt.where(semantic_events_table.c.source == source.value)
        if trace_id is not None:
            stmt = stmt.where(semantic_events_table.c.trace_id == trace_id)

        events: list[SemanticEvent] = []
        with self._lock, self.engine.connect() as connection:
            for row in connection.execute(stmt):
                event = SemanticEvent.model_validate(row.payload)
                if payload_contains_query(
                    event.payload,
                    payload_query,
                ) and created_at_in_range(
                    event.created_at,
                    created_from=created_from,
                    created_to=created_to,
                ):
                    events.append(event)
                    if len(events) >= max_results:
                        break
        return events

    def _persist_state(self) -> None:
        with self._lock, self.engine.begin() as connection:
            for table in (
                event_stream_offsets_table,
                bot_delivery_records_table,
                semantic_events_table,
                audit_events_table,
                command_results_table,
                lease_epochs_table,
                writer_leases_table,
                interactions_table,
                turns_table,
                sessions_table,
                device_identities_table,
                access_policy_rules_table,
                approval_policy_overrides_table,
                group_role_bindings_table,
                project_bindings_table,
                chat_contexts_table,
                workspaces_table,
                projects_table,
            ):
                connection.execute(delete(table))

            self._insert_many(
                connection,
                projects_table,
                [
                    {
                        "id": project.id,
                        "slug": project.slug,
                        "payload": project.model_dump(mode="json"),
                    }
                    for project in self.projects.values()
                ],
            )
            self._insert_many(
                connection,
                workspaces_table,
                [
                    {
                        "id": workspace.id,
                        "project_id": workspace.project_id,
                        "path": workspace.path,
                        "payload": workspace.model_dump(mode="json"),
                    }
                    for workspace in self.workspaces.values()
                ],
            )
            self._insert_many(
                connection,
                chat_contexts_table,
                [
                    {
                        "id": context.id,
                        "bot_instance_id": context.bot_instance_id,
                        "platform": context.platform,
                        "chat_space_id": context.chat_space_id,
                        "thread_id": context.thread_id,
                        "user_id": context.user_id,
                        "payload": context.model_dump(mode="json"),
                    }
                    for context in self.chat_contexts.values()
                ],
            )
            self._insert_many(
                connection,
                project_bindings_table,
                [
                    {
                        "id": binding.id,
                        "chat_context_id": binding.chat_context_id,
                        "project_id": binding.project_id,
                        "is_default": binding.is_default,
                        "payload": binding.model_dump(mode="json"),
                    }
                    for binding in self.bindings.values()
                ],
            )
            self._insert_many(
                connection,
                group_role_bindings_table,
                [
                    {
                        "id": binding.id,
                        "chat_context_id": binding.chat_context_id,
                        "actor_id": binding.actor_id,
                        "payload": binding.model_dump(mode="json"),
                    }
                    for binding in self.group_role_bindings.values()
                ],
            )
            self._insert_many(
                connection,
                approval_policy_overrides_table,
                [
                    {
                        "id": override.id,
                        "scope_type": override.scope_type.value,
                        "scope_id": override.scope_id,
                        "payload": override.model_dump(mode="json"),
                    }
                    for override in self.approval_policy_overrides.values()
                ],
            )
            self._insert_many(
                connection,
                access_policy_rules_table,
                [
                    {
                        "id": rule.id,
                        "effect": rule.effect.value,
                        "action": rule.action,
                        "resource_type": rule.resource_type,
                        "resource_id": rule.resource_id,
                        "enabled": rule.enabled,
                        "priority": rule.priority,
                        "updated_at": rule.updated_at.isoformat(),
                        "payload": rule.model_dump(mode="json"),
                    }
                    for rule in self.access_policy_rules.values()
                ],
            )
            self._insert_many(
                connection,
                device_identities_table,
                [
                    {
                        "id": identity.id,
                        "device_id": identity.device_id,
                        "status": identity.status.value,
                        "created_at": identity.created_at.isoformat(),
                        "payload": identity.model_dump(mode="json"),
                    }
                    for identity in self.device_identities.values()
                ],
            )
            self._insert_many(
                connection,
                sessions_table,
                [
                    {
                        "id": session.id,
                        "short_code": session.short_code,
                        "project_id": session.project_id,
                        "workspace_id": session.workspace_id,
                        "status": session.status.value,
                        "payload": session.model_dump(mode="json"),
                    }
                    for session in self.sessions.values()
                ],
            )
            self._insert_many(
                connection,
                turns_table,
                [
                    {
                        "id": turn.id,
                        "session_id": turn.session_id,
                        "status": turn.status.value,
                        "payload": turn.model_dump(mode="json"),
                    }
                    for turn in self.turns.values()
                ],
            )
            self._insert_many(
                connection,
                interactions_table,
                [
                    {
                        "id": interaction.id,
                        "session_id": interaction.session_id,
                        "status": interaction.status.value,
                        "payload": interaction.model_dump(mode="json"),
                    }
                    for interaction in self.interactions.values()
                ],
            )
            self._insert_many(
                connection,
                writer_leases_table,
                [
                    {
                        "session_id": lease.session_id,
                        "epoch": lease.epoch,
                        "payload": lease.model_dump(mode="json"),
                    }
                    for lease in self.leases.values()
                ],
            )
            self._insert_many(
                connection,
                lease_epochs_table,
                [
                    {"session_id": session_id, "epoch": epoch}
                    for session_id, epoch in self.lease_epochs.items()
                ],
            )
            self._insert_many(
                connection,
                command_results_table,
                [
                    {
                        "idempotency_key": key,
                        "payload": result.model_dump(mode="json"),
                    }
                    for key, result in self.command_results.items()
                ],
            )
            self._insert_many(
                connection,
                audit_events_table,
                [
                    {
                        "id": event.id,
                        "position": position,
                        "action": event.action,
                        "actor_id": event.actor_id,
                        "entry_hash": event.entry_hash,
                        "payload": event.model_dump(mode="json"),
                    }
                    for position, event in enumerate(self.audit_events, start=1)
                ],
            )
            self._insert_many(
                connection,
                semantic_events_table,
                [
                    {
                        "id": event.id,
                        "position": position,
                        "stream_id": event.stream_id,
                        "seq": event.seq,
                        "type": event.type,
                        "source": event.source.value,
                        "trace_id": event.trace_id,
                        "project_id": event.project_id,
                        "session_id": event.session_id,
                        "turn_id": event.turn_id,
                        "interaction_id": event.interaction_id,
                        "idempotency_key": event.idempotency_key,
                        "payload": event.model_dump(mode="json"),
                    }
                    for position, event in enumerate(self.semantic_events, start=1)
                ],
            )
            self._insert_many(
                connection,
                event_stream_offsets_table,
                [
                    {"stream_id": stream_id, "seq": seq}
                    for stream_id, seq in self.event_stream_seq.items()
                ],
            )
            self._insert_many(
                connection,
                bot_delivery_records_table,
                [
                    {
                        "id": record.id,
                        "idempotency_key": record.idempotency_key,
                        "platform": record.platform.value,
                        "chat_context_id": record.chat_context_id,
                        "event_id": record.event_id,
                        "event_seq": record.event_seq,
                        "status": record.status.value,
                        "platform_state": record.platform_state.value,
                        "attempt_count": record.attempt_count,
                        "next_retry_at": (
                            record.next_retry_at.isoformat() if record.next_retry_at else None
                        ),
                        "acknowledged_at": (
                            record.acknowledged_at.isoformat()
                            if record.acknowledged_at
                            else None
                        ),
                        "edited_at": record.edited_at.isoformat() if record.edited_at else None,
                        "deleted_at": (
                            record.deleted_at.isoformat() if record.deleted_at else None
                        ),
                        "updated_at": record.updated_at.isoformat(),
                        "payload": record.model_dump(mode="json"),
                    }
                    for record in self.bot_delivery_records.values()
                ],
            )

    @staticmethod
    def _insert_many(connection: Any, table: Table, rows: list[dict[str, Any]]) -> None:
        if rows:
            connection.execute(table.insert(), rows)
