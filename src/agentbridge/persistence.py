from __future__ import annotations

from collections.abc import Callable
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
    AgentSession,
    AuditEvent,
    BotDeliveryRecord,
    ChatContext,
    CommandResult,
    GroupRoleBinding,
    Interaction,
    Project,
    ProjectBinding,
    SemanticEvent,
    Turn,
    Workspace,
    WriterLease,
)
from agentbridge.storage import InMemoryRepository

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
    Column("attempt_count", Integer, nullable=False, default=1),
    Column("next_retry_at", String(64), nullable=True, index=True),
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
        "bind_project",
        "update_active_project",
        "update_active_session",
        "create_session",
        "close_session",
        "enqueue_turn",
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
    ) -> None:
        self.engine = engine or create_engine(database_url, future=True)
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
                        "attempt_count": record.attempt_count,
                        "next_retry_at": (
                            record.next_retry_at.isoformat() if record.next_retry_at else None
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
