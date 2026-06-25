from __future__ import annotations

from agentbridge.domain import (
    Actor,
    AgentSession,
    AgentType,
    AuditEvent,
    AuditOutcome,
    ChatContext,
    LeaseOwnerType,
    Project,
    SemanticEvent,
    SemanticEventSource,
    Turn,
    Visibility,
    Workspace,
    WorkspaceType,
    WriterLease,
)
from agentbridge.policy import Permission, PolicyEngine
from agentbridge.storage import InMemoryRepository


class ControlPlane:
    def __init__(
        self,
        repository: InMemoryRepository | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        self.repository = repository or InMemoryRepository()
        self.policy = policy or PolicyEngine()

    def health(self) -> dict[str, object]:
        return {
            "status": "ok",
            "storage": "memory",
            "projects": len(self.repository.projects),
            "sessions": len(self.repository.sessions),
        }

    def get_or_create_chat_context(
        self,
        *,
        bot_instance_id: str,
        platform: str,
        chat_space_id: str,
        thread_id: str | None = None,
        user_id: str | None = None,
    ) -> ChatContext:
        return self.repository.get_or_create_chat_context(
            bot_instance_id=bot_instance_id,
            platform=platform,
            chat_space_id=chat_space_id,
            thread_id=thread_id,
            user_id=user_id,
        )

    def create_project(
        self,
        *,
        actor: Actor,
        name: str,
        slug: str | None = None,
        aliases: list[str] | None = None,
        description: str | None = None,
        default_agent: AgentType = AgentType.CLAUDE,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> Project:
        self.policy.require(actor, Permission.PROJECT_MANAGE)
        project = self.repository.create_project(
            name=name,
            actor=actor,
            slug=slug,
            aliases=aliases,
            description=description,
            default_agent=default_agent,
        )
        self.audit(
            action="project.created",
            actor=actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=project.id,
        )
        self.emit_event(
            event_type="project.created",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=project.id,
            payload={"name": project.name, "slug": project.slug},
        )
        return project

    def list_projects(self, actor: Actor) -> list[Project]:
        self.policy.require(actor, Permission.PROJECT_VIEW)
        return self.repository.list_projects()

    def add_workspace(
        self,
        *,
        actor: Actor,
        project_id: str,
        machine_id: str,
        path: str,
        allowed_root: str,
        workspace_type: WorkspaceType = WorkspaceType.SHARED,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> Workspace:
        self.policy.require(actor, Permission.PROJECT_MANAGE)
        workspace = self.repository.add_workspace(
            project_id=project_id,
            machine_id=machine_id,
            path=path,
            allowed_root=allowed_root,
            workspace_type=workspace_type,
        )
        self.audit(
            action="project.workspace_added",
            actor=actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=project_id,
            details={"workspace_id": workspace.id},
        )
        self.emit_event(
            event_type="project.workspace_added",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=project_id,
            payload={
                "workspace_id": workspace.id,
                "machine_id": workspace.machine_id,
                "type": workspace.type.value,
            },
        )
        return workspace

    def bind_project(
        self,
        *,
        actor: Actor,
        chat_context_id: str,
        project_id: str,
        alias_in_chat: str | None,
        is_default: bool,
        trace_id: str,
    ) -> None:
        self.policy.require(actor, Permission.PROJECT_MANAGE)
        binding = self.repository.bind_project(
            chat_context_id=chat_context_id,
            project_id=project_id,
            alias_in_chat=alias_in_chat,
            is_default=is_default,
        )
        self.audit(
            action="project.binding_added",
            actor=actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=project_id,
            details={"binding_id": binding.id, "is_default": is_default},
        )
        self.emit_event(
            event_type="project.binding_added",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=project_id,
            payload={
                "binding_id": binding.id,
                "chat_context_id": chat_context_id,
                "is_default": is_default,
            },
        )

    def use_project(
        self,
        *,
        actor: Actor,
        chat_context_id: str,
        project_token: str,
        expected_version: int | None,
        trace_id: str,
    ) -> ChatContext:
        self.policy.require(actor, Permission.SESSION_VIEW)
        project = self.repository.resolve_project(project_token, chat_context_id)
        context = self.repository.update_active_project(
            chat_context_id, project.id, expected_version=expected_version
        )
        self.audit(
            action="project.context_selected",
            actor=actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=project.id,
            details={"pointer_version": context.pointer_version},
        )
        self.emit_event(
            event_type="project.context_selected",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=project.id,
            payload={
                "chat_context_id": chat_context_id,
                "pointer_version": context.pointer_version,
            },
        )
        return context

    def create_session(
        self,
        *,
        actor: Actor,
        project_id: str,
        workspace_id: str | None,
        name: str,
        agent_type: AgentType,
        visibility: Visibility,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> AgentSession:
        self.policy.require(actor, Permission.SESSION_CREATE)
        session = self.repository.create_session(
            project_id=project_id,
            workspace_id=workspace_id,
            name=name,
            agent_type=agent_type,
            visibility=visibility,
            actor=actor,
        )
        self.audit(
            action="session.created",
            actor=actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=project_id,
            session_id=session.id,
        )
        self.emit_event(
            event_type="session.created",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=project_id,
            session_id=session.id,
            payload={
                "short_code": session.short_code,
                "name": session.name,
                "workspace_id": session.workspace_id,
                "agent_type": session.agent_type.value,
            },
        )
        return session

    def use_session(
        self,
        *,
        actor: Actor,
        chat_context_id: str,
        session_token: str,
        expected_version: int | None,
        trace_id: str,
    ) -> ChatContext:
        self.policy.require(actor, Permission.SESSION_VIEW)
        context = self.repository.get_chat_context(chat_context_id)
        session = self.repository.resolve_session(session_token, context.active_project_id)
        updated = self.repository.update_active_session(
            chat_context_id, session.id, expected_version=expected_version
        )
        self.audit(
            action="session.context_selected",
            actor=actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session.id,
            details={"pointer_version": updated.pointer_version},
        )
        self.emit_event(
            event_type="session.context_selected",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session.id,
            payload={
                "chat_context_id": chat_context_id,
                "pointer_version": updated.pointer_version,
            },
        )
        return updated

    def list_sessions(self, actor: Actor, project_id: str | None = None) -> list[AgentSession]:
        self.policy.require(actor, Permission.SESSION_VIEW)
        return self.repository.list_sessions(project_id)

    def enqueue_turn(
        self,
        *,
        actor: Actor,
        session_id: str,
        prompt: str,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> Turn:
        self.policy.require(actor, Permission.SESSION_SEND)
        turn = self.repository.enqueue_turn(session_id=session_id, prompt=prompt, actor=actor)
        session = self.repository.get_session(session_id)
        self.audit(
            action="turn.queued",
            actor=actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session_id,
            details={"turn_id": turn.id},
        )
        self.emit_event(
            event_type="turn.queued",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            turn_id=turn.id,
            payload={"actor_id": actor.id, "prompt_length": len(turn.prompt)},
        )
        return turn

    def close_session(
        self,
        *,
        actor: Actor,
        session_id: str,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> AgentSession:
        self.policy.require(actor, Permission.SESSION_MANAGE)
        session = self.repository.close_session(session_id)
        self.audit(
            action="session.closed",
            actor=actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session_id,
        )
        self.emit_event(
            event_type="session.closed",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            payload={"status": session.status.value},
        )
        return session

    def acquire_lease(
        self,
        *,
        actor: Actor,
        session_id: str,
        owner_type: LeaseOwnerType,
        owner_id: str,
        ttl_seconds: int,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> WriterLease:
        if owner_type in {LeaseOwnerType.WEB_ADMIN, LeaseOwnerType.HUMAN}:
            self.policy.require(actor, Permission.TERMINAL_CONTROL)
        elif owner_type == LeaseOwnerType.BOT:
            self.policy.require(actor, Permission.SESSION_SEND)
        lease = self.repository.acquire_lease(
            session_id=session_id,
            owner_type=owner_type,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
        )
        session = self.repository.get_session(session_id)
        self.audit(
            action="lease.acquired",
            actor=actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session_id,
            details={
                "owner_type": owner_type.value,
                "owner_id": owner_id,
                "epoch": lease.epoch,
            },
        )
        self.emit_event(
            event_type="lease.acquired",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            payload={
                "owner_type": owner_type.value,
                "owner_id": owner_id,
                "epoch": lease.epoch,
                "expires_at": lease.expires_at.isoformat(),
            },
        )
        return lease

    def release_lease(
        self,
        *,
        actor: Actor,
        session_id: str,
        epoch: int,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> int:
        self.policy.require(actor, Permission.TERMINAL_CONTROL)
        next_epoch = self.repository.release_lease(session_id=session_id, epoch=epoch)
        session = self.repository.get_session(session_id)
        self.audit(
            action="lease.released",
            actor=actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session_id,
            details={"released_epoch": epoch, "next_epoch": next_epoch},
        )
        self.emit_event(
            event_type="lease.released",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            payload={"released_epoch": epoch, "next_epoch": next_epoch},
        )
        return next_epoch

    def audit(
        self,
        *,
        action: str,
        actor: Actor,
        outcome: AuditOutcome,
        trace_id: str,
        chat_context_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        interaction_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> AuditEvent:
        return self.repository.append_audit(
            action=action,
            actor_id=actor.id,
            outcome=outcome,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=project_id,
            session_id=session_id,
            interaction_id=interaction_id,
            details=details,
        )

    def emit_event(
        self,
        *,
        event_type: str,
        source: SemanticEventSource,
        trace_id: str,
        project_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        interaction_id: str | None = None,
        payload: dict[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> SemanticEvent:
        return self.repository.append_event(
            event_type=event_type,
            source=source,
            trace_id=trace_id,
            project_id=project_id,
            session_id=session_id,
            turn_id=turn_id,
            interaction_id=interaction_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
