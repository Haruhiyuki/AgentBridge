from __future__ import annotations

from datetime import datetime

from agentbridge.device_auth import (
    DEFAULT_DEVICE_KEY_ITERATIONS,
    generate_device_key,
    generate_device_key_salt,
    hash_device_key,
    normalize_certificate_fingerprint,
)
from agentbridge.device_certificate_health import (
    datetime_payload,
    device_identity_certificate_health,
)
from agentbridge.device_certificates import (
    DeviceCertificateIssuer,
    ExternalDeviceCertificateIssuer,
    IssuedDeviceCertificate,
)
from agentbridge.domain import (
    AccessPolicyEffect,
    AccessPolicyRule,
    Actor,
    AgentBridgeError,
    AgentSession,
    AgentType,
    ApprovalPolicyOverride,
    AuditEvent,
    AuditOutcome,
    ChatContext,
    DeviceCertificateRecord,
    DeviceIdentity,
    DeviceIdentityScope,
    DeviceIdentityStatus,
    ErrorCode,
    GroupRoleBinding,
    Interaction,
    InteractionStatus,
    InteractionType,
    LeaseOwnerType,
    PolicyScope,
    Project,
    ProjectBinding,
    RiskLevel,
    SemanticEvent,
    SemanticEventSource,
    SessionStatus,
    Turn,
    TurnStatus,
    Visibility,
    Workspace,
    WorkspaceType,
    WriterLease,
    utc_now,
)
from agentbridge.policy import ROLE_PERMISSIONS, ApprovalPolicy, Permission, PolicyEngine
from agentbridge.storage import InMemoryRepository


class ControlPlane:
    def __init__(
        self,
        repository: InMemoryRepository | None = None,
        policy: PolicyEngine | None = None,
        approval_policy: ApprovalPolicy | None = None,
    ) -> None:
        self.repository = repository or InMemoryRepository()
        self.policy = policy or PolicyEngine()
        self.policy.set_rule_provider(self.repository.list_access_policy_rules)
        self.approval_policy = approval_policy or ApprovalPolicy.default()

    def health(self) -> dict[str, object]:
        return {
            "status": "ok",
            "storage": getattr(self.repository, "storage_label", "memory"),
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
        max_active_sessions: int = 10,
        max_running_turns: int = 4,
        max_queued_turns: int = 100,
        daily_turns_per_user: int = 50,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> Project:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_project_permission(
            effective_actor,
            Permission.PROJECT_MANAGE,
            resource_id=slug,
            attributes={
                "operation": "create_project",
                "name": name.strip(),
                "slug": slug or "",
                "default_agent": default_agent.value,
                "max_active_sessions": max_active_sessions,
                "max_running_turns": max_running_turns,
                "max_queued_turns": max_queued_turns,
                "daily_turns_per_user": daily_turns_per_user,
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        project = self.repository.create_project(
            name=name,
            actor=effective_actor,
            slug=slug,
            aliases=aliases,
            description=description,
            default_agent=default_agent,
            max_active_sessions=max_active_sessions,
            max_running_turns=max_running_turns,
            max_queued_turns=max_queued_turns,
            daily_turns_per_user=daily_turns_per_user,
        )
        self.audit(
            action="project.created",
            actor=effective_actor,
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
            payload={
                "name": project.name,
                "slug": project.slug,
                "max_active_sessions": project.max_active_sessions,
                "max_running_turns": project.max_running_turns,
                "max_queued_turns": project.max_queued_turns,
                "daily_turns_per_user": project.daily_turns_per_user,
            },
        )
        return project

    def list_projects(self, actor: Actor) -> list[Project]:
        self.require_project_permission(
            actor,
            Permission.PROJECT_VIEW,
            attributes={"operation": "list_projects"},
        )
        return self.repository.list_projects()

    def list_projects_for_context(
        self, actor: Actor, chat_context_id: str | None
    ) -> list[Project]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_project_permission(
            effective_actor,
            Permission.PROJECT_VIEW,
            attributes={
                "operation": "list_projects",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
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
        is_writable: bool = True,
        max_write_sessions: int = 1,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> Workspace:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_project_permission(
            effective_actor,
            Permission.PROJECT_MANAGE,
            project_id=project_id,
            attributes={
                "operation": "add_workspace",
                "workspace_type": workspace_type.value,
                "machine_id": machine_id,
                "is_writable": is_writable,
                "max_write_sessions": max_write_sessions,
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        workspace = self.repository.add_workspace(
            project_id=project_id,
            machine_id=machine_id,
            path=path,
            allowed_root=allowed_root,
            workspace_type=workspace_type,
            is_writable=is_writable,
            max_write_sessions=max_write_sessions,
        )
        self.audit(
            action="project.workspace_added",
            actor=effective_actor,
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
                "is_writable": workspace.is_writable,
                "max_write_sessions": workspace.max_write_sessions,
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
    ) -> ProjectBinding:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_project_permission(
            effective_actor,
            Permission.PROJECT_MANAGE,
            project_id=project_id,
            attributes={
                "operation": "bind_project",
                "is_default": is_default,
                "alias_in_chat": alias_in_chat or "",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        binding = self.repository.bind_project(
            chat_context_id=chat_context_id,
            project_id=project_id,
            alias_in_chat=alias_in_chat,
            is_default=is_default,
        )
        self.audit(
            action="project.binding_added",
            actor=effective_actor,
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
        return binding

    def list_project_bindings(
        self,
        *,
        actor: Actor,
        chat_context_id: str,
    ) -> list[ProjectBinding]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_collection_permission(
            effective_actor,
            Permission.PROJECT_VIEW,
            resource_type="chat_context",
            resource_id=chat_context_id,
            attributes={
                "operation": "list_project_bindings",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        return self.repository.list_project_bindings(chat_context_id)

    def use_project(
        self,
        *,
        actor: Actor,
        chat_context_id: str,
        project_token: str,
        expected_version: int | None,
        trace_id: str,
    ) -> ChatContext:
        effective_actor = self.effective_actor(actor, chat_context_id)
        project = self.repository.resolve_project(project_token, chat_context_id)
        self.require_project_permission(
            effective_actor,
            Permission.PROJECT_VIEW,
            project_id=project.id,
            attributes={
                "operation": "select_project",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        context = self.repository.update_active_project(
            chat_context_id, project.id, expected_version=expected_version
        )
        self.audit(
            action="project.context_selected",
            actor=effective_actor,
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
        effective_actor = self.effective_actor(actor, chat_context_id)
        agent_type_value = AgentType(agent_type).value
        visibility_value = Visibility(visibility).value
        self.require_project_permission(
            effective_actor,
            Permission.SESSION_CREATE,
            project_id=project_id,
            attributes={
                "operation": "create_session",
                "workspace_id": workspace_id or "",
                "agent_type": agent_type_value,
                "visibility": visibility_value,
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        session = self.repository.create_session(
            project_id=project_id,
            workspace_id=workspace_id,
            name=name,
            agent_type=agent_type,
            visibility=visibility,
            actor=effective_actor,
        )
        self.audit(
            action="session.created",
            actor=effective_actor,
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
        effective_actor = self.effective_actor(actor, chat_context_id)
        session = self.repository.resolve_session(session_token)
        self.require_session_permission(
            effective_actor,
            Permission.SESSION_VIEW,
            session_id=session.id,
            chat_context_id=chat_context_id,
            attributes={"operation": "select_session"},
        )
        updated = self.repository.update_active_session(
            chat_context_id, session.id, expected_version=expected_version
        )
        self.audit(
            action="session.context_selected",
            actor=effective_actor,
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
        self.require_collection_permission(
            actor,
            Permission.SESSION_VIEW,
            resource_type="session",
            attributes={"operation": "list_sessions", "project_id": project_id or ""},
        )
        return self.repository.list_sessions(project_id)

    def list_sessions_for_context(
        self,
        actor: Actor,
        project_id: str | None = None,
        chat_context_id: str | None = None,
    ) -> list[AgentSession]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_collection_permission(
            effective_actor,
            Permission.SESSION_VIEW,
            resource_type="session",
            attributes={
                "operation": "list_sessions",
                "project_id": project_id or "",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
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
        effective_actor = self.effective_actor(actor, chat_context_id)
        session = self.require_session_permission(
            effective_actor,
            Permission.SESSION_SEND,
            session_id=session_id,
            chat_context_id=chat_context_id,
            attributes={"operation": "enqueue_turn", "prompt_length": len(prompt)},
        )
        lease = self.repository.current_lease(session_id)
        if session.status == SessionStatus.RECOVERING:
            queue_reason = "terminal_agent_offline"
        elif lease is not None and lease.owner_type == LeaseOwnerType.HUMAN:
            queue_reason = "human_control"
        else:
            queue_reason = None
        turn = self.repository.enqueue_turn(
            session_id=session_id,
            prompt=prompt,
            actor=effective_actor,
            queue_reason=queue_reason,
        )
        queue_details: dict[str, object] = {"turn_id": turn.id}
        event_payload: dict[str, object] = {
            "actor_id": actor.id,
            "prompt_length": len(turn.prompt),
        }
        if queue_reason:
            queue_details["queue_reason"] = queue_reason
            event_payload["queue_reason"] = queue_reason
            if lease is not None:
                lease_details = {
                    "lease_owner_type": lease.owner_type.value,
                    "lease_owner_id": lease.owner_id,
                    "lease_epoch": lease.epoch,
                }
                queue_details.update(lease_details)
                event_payload.update(lease_details)
        self.audit(
            action="turn.queued",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session_id,
            details=queue_details,
        )
        self.emit_event(
            event_type="turn.queued",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            turn_id=turn.id,
            payload=event_payload,
        )
        return turn

    def list_turn_queue(
        self,
        *,
        actor: Actor,
        session_id: str,
        chat_context_id: str | None = None,
    ) -> tuple[list[Turn], str, bool]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_session_permission(
            effective_actor,
            Permission.SESSION_VIEW,
            session_id=session_id,
            chat_context_id=chat_context_id,
            attributes={"operation": "queue_list"},
        )
        return self.repository.queue_snapshot(session_id)

    def get_session_lease(
        self,
        *,
        actor: Actor,
        session_id: str,
        chat_context_id: str | None = None,
    ) -> WriterLease | None:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_session_permission(
            effective_actor,
            Permission.SESSION_VIEW,
            session_id=session_id,
            chat_context_id=chat_context_id,
            attributes={"operation": "lease_status"},
        )
        return self.repository.current_lease(session_id)

    def remove_queued_turn(
        self,
        *,
        actor: Actor,
        session_id: str,
        turn_id: str,
        trace_id: str,
        expected_queue_version: str | None = None,
        chat_context_id: str | None = None,
    ) -> tuple[Turn, str]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        session = self.require_session_permission(
            effective_actor,
            Permission.SESSION_SEND,
            session_id=session_id,
            chat_context_id=chat_context_id,
            attributes={"operation": "queue_remove", "turn_id": turn_id},
        )
        turn = self.repository.get_turn(turn_id)
        if turn.session_id != session_id:
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "Turn 不属于目标 Session。",
                next_step="请确认 session_id 与 turn_id 匹配。",
                status_code=409,
                details={
                    "turn_id": turn_id,
                    "turn_session_id": turn.session_id,
                    "session_id": session_id,
                },
            )
        if turn.actor_id != effective_actor.id:
            self.require_session_permission(
                effective_actor,
                Permission.SESSION_MANAGE,
                session_id=session_id,
                chat_context_id=chat_context_id,
                attributes={
                    "operation": "queue_remove_any",
                    "turn_id": turn_id,
                    "turn_actor_id": turn.actor_id,
                },
            )
        cancelled = self.repository.cancel_queued_turn(
            session_id=session_id,
            turn_id=turn_id,
            expected_queue_version=expected_queue_version,
        )
        queue_version = self.repository.queue_version(session_id)
        self.audit(
            action="turn.cancelled",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session_id,
            details={
                "turn_id": turn_id,
                "reason": "queue_remove",
                "queue_version": queue_version,
            },
        )
        self.emit_event(
            event_type="turn.cancelled",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            turn_id=turn_id,
            payload={
                "actor_id": effective_actor.id,
                "reason": "queue_remove",
                "queue_version": queue_version,
            },
        )
        return cancelled, queue_version

    def clear_turn_queue(
        self,
        *,
        actor: Actor,
        session_id: str,
        trace_id: str,
        expected_queue_version: str | None = None,
        confirmed_count: int | None = None,
        chat_context_id: str | None = None,
    ) -> tuple[list[Turn], str]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        session = self.require_session_permission(
            effective_actor,
            Permission.SESSION_MANAGE,
            session_id=session_id,
            chat_context_id=chat_context_id,
            attributes={"operation": "queue_clear"},
        )
        cancelled = self.repository.clear_queued_turns(
            session_id,
            expected_queue_version=expected_queue_version,
            confirmed_count=confirmed_count,
        )
        queue_version = self.repository.queue_version(session_id)
        turn_ids = [turn.id for turn in cancelled]
        self.audit(
            action="turn.queue_cleared",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session_id,
            details={
                "turn_ids": turn_ids,
                "count": len(turn_ids),
                "confirmed_count": confirmed_count,
                "queue_version": queue_version,
            },
        )
        self.emit_event(
            event_type="turn.queue_cleared",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            payload={
                "actor_id": effective_actor.id,
                "turn_ids": turn_ids,
                "count": len(turn_ids),
                "confirmed_count": confirmed_count,
                "queue_version": queue_version,
            },
        )
        return cancelled, queue_version

    def reorder_turn_queue(
        self,
        *,
        actor: Actor,
        session_id: str,
        turn_id: str,
        before_turn_id: str,
        expected_queue_version: str,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> tuple[list[Turn], str]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        session = self.require_session_permission(
            effective_actor,
            Permission.SESSION_MANAGE,
            session_id=session_id,
            chat_context_id=chat_context_id,
            attributes={
                "operation": "queue_reorder",
                "turn_id": turn_id,
                "before_turn_id": before_turn_id,
            },
        )
        turns = self.repository.reorder_queued_turn(
            session_id=session_id,
            turn_id=turn_id,
            before_turn_id=before_turn_id,
            expected_queue_version=expected_queue_version,
        )
        queue_version = self.repository.queue_version(session_id)
        turn_ids = [turn.id for turn in turns]
        self.audit(
            action="turn.queue_reordered",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session_id,
            details={
                "turn_id": turn_id,
                "before_turn_id": before_turn_id,
                "turn_ids": turn_ids,
                "queue_version": queue_version,
            },
        )
        self.emit_event(
            event_type="turn.queue_reordered",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            turn_id=turn_id,
            payload={
                "actor_id": effective_actor.id,
                "before_turn_id": before_turn_id,
                "turn_ids": turn_ids,
                "queue_version": queue_version,
            },
        )
        return turns, queue_version

    def set_turn_queue_paused(
        self,
        *,
        actor: Actor,
        session_id: str,
        paused: bool,
        expected_queue_version: str,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> tuple[AgentSession, str]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        session = self.require_session_permission(
            effective_actor,
            Permission.SESSION_MANAGE,
            session_id=session_id,
            chat_context_id=chat_context_id,
            attributes={
                "operation": "queue_pause" if paused else "queue_resume",
                "paused": paused,
            },
        )
        updated_session = self.repository.set_turn_queue_paused(
            session_id=session_id,
            paused=paused,
            expected_queue_version=expected_queue_version,
        )
        queue_version = self.repository.queue_version(session_id)
        action = "turn.queue_paused" if paused else "turn.queue_resumed"
        self.audit(
            action=action,
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session_id,
            details={
                "queue_paused": paused,
                "queue_version": queue_version,
            },
        )
        self.emit_event(
            event_type=action,
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            payload={
                "actor_id": effective_actor.id,
                "queue_paused": paused,
                "queue_version": queue_version,
            },
        )
        return updated_session, queue_version

    def claim_next_turn(
        self,
        *,
        actor: Actor,
        session_id: str,
        trace_id: str,
        expected_queue_version: str | None = None,
        chat_context_id: str | None = None,
    ) -> tuple[Turn | None, str]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        session = self.require_session_permission(
            effective_actor,
            Permission.SESSION_MANAGE,
            session_id=session_id,
            chat_context_id=chat_context_id,
            attributes={"operation": "queue_claim_next"},
        )
        turn = self.repository.start_next_turn(
            session_id=session_id,
            expected_queue_version=expected_queue_version,
        )
        queue_version = self.repository.queue_version(session_id)
        if turn is None:
            return None, queue_version
        self.audit(
            action="turn.claimed",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session_id,
            details={
                "turn_id": turn.id,
                "queue_version": queue_version,
            },
        )
        self.emit_event(
            event_type="turn.started",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            turn_id=turn.id,
            payload={
                "actor_id": effective_actor.id,
                "claim_source": "queue",
                "queue_version": queue_version,
                "prompt_length": len(turn.prompt),
            },
        )
        return turn, queue_version

    def close_session(
        self,
        *,
        actor: Actor,
        session_id: str,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> AgentSession:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_session_permission(
            effective_actor,
            Permission.SESSION_MANAGE,
            session_id=session_id,
            chat_context_id=chat_context_id,
            attributes={"operation": "close_session"},
        )
        session = self.repository.close_session(session_id)
        self.audit(
            action="session.closed",
            actor=effective_actor,
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
        effective_actor = self.effective_actor(actor, chat_context_id)
        if owner_type in {LeaseOwnerType.WEB_ADMIN, LeaseOwnerType.HUMAN}:
            session = self.require_terminal_control(
                effective_actor,
                session_id=session_id,
                chat_context_id=chat_context_id,
                attributes={
                    "operation": "acquire_lease",
                    "owner_type": owner_type.value,
                    "owner_id": owner_id,
                },
            )
        elif owner_type == LeaseOwnerType.BOT:
            session = self.require_session_permission(
                effective_actor,
                Permission.SESSION_SEND,
                session_id=session_id,
                chat_context_id=chat_context_id,
                attributes={
                    "operation": "acquire_lease",
                    "owner_type": owner_type.value,
                    "owner_id": owner_id,
                },
            )
        else:
            session = self.repository.get_session(session_id)
        lease = self.repository.acquire_lease(
            session_id=session_id,
            owner_type=owner_type,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
        )
        self.audit(
            action="lease.acquired",
            actor=effective_actor,
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

    def set_terminal_agent_offline_protection(
        self,
        *,
        actor: Actor,
        session_id: str,
        offline: bool,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> tuple[AgentSession, int]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        session = self.require_terminal_control(
            effective_actor,
            session_id=session_id,
            chat_context_id=chat_context_id,
            attributes={
                "operation": "terminal_offline_protection",
                "offline": offline,
            },
        )
        updated_session, removed_lease, next_epoch = (
            self.repository.set_terminal_agent_offline_protection(
                session_id=session_id,
                offline=offline,
            )
        )
        action = (
            "terminal.offline_protection_enabled"
            if offline
            else "terminal.offline_protection_disabled"
        )
        details: dict[str, object] = {
            "offline": offline,
            "status": updated_session.status.value,
            "next_epoch": next_epoch,
        }
        if removed_lease is not None:
            details.update(
                {
                    "removed_lease_owner_type": removed_lease.owner_type.value,
                    "removed_lease_owner_id": removed_lease.owner_id,
                    "removed_lease_epoch": removed_lease.epoch,
                }
            )
        self.audit(
            action=action,
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session_id,
            details=details,
        )
        self.emit_event(
            event_type=action,
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            payload=details,
        )
        if not offline:
            queued_turns, queue_version, queue_paused = self.repository.queue_snapshot(
                session_id
            )
            unblocked_turns = [
                turn
                for turn in queued_turns
                if turn.queue_reason == "terminal_agent_offline"
            ]
            if unblocked_turns:
                next_turn = unblocked_turns[0]
                self.emit_event(
                    event_type="turn.queue_unblocked",
                    source=SemanticEventSource.CONTROL_PLANE,
                    trace_id=trace_id,
                    project_id=session.project_id,
                    session_id=session_id,
                    turn_id=next_turn.id,
                    payload={
                        "queue_reason": "terminal_agent_offline",
                        "next_epoch": next_epoch,
                        "next_turn_id": next_turn.id,
                        "unblocked_turn_count": len(unblocked_turns),
                        "queued_turn_count": len(queued_turns),
                        "queue_version": queue_version,
                        "queue_paused": queue_paused,
                    },
                )
        return updated_session, next_epoch

    def release_lease(
        self,
        *,
        actor: Actor,
        session_id: str,
        epoch: int,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> int:
        effective_actor = self.effective_actor(actor, chat_context_id)
        session = self.require_terminal_control(
            effective_actor,
            session_id=session_id,
            chat_context_id=chat_context_id,
            attributes={"operation": "release_lease", "epoch": epoch},
        )
        released_lease = self.repository.current_lease(session_id)
        next_epoch = self.repository.release_lease(session_id=session_id, epoch=epoch)
        self.audit(
            action="lease.released",
            actor=effective_actor,
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
        if (
            released_lease is not None
            and released_lease.owner_type == LeaseOwnerType.HUMAN
        ):
            queued_turns, queue_version, queue_paused = self.repository.queue_snapshot(
                session_id
            )
            unblocked_turns = [
                turn for turn in queued_turns if turn.queue_reason == "human_control"
            ]
            if unblocked_turns:
                next_turn = unblocked_turns[0]
                self.emit_event(
                    event_type="turn.queue_unblocked",
                    source=SemanticEventSource.CONTROL_PLANE,
                    trace_id=trace_id,
                    project_id=session.project_id,
                    session_id=session_id,
                    turn_id=next_turn.id,
                    payload={
                        "queue_reason": "human_control",
                        "released_epoch": epoch,
                        "next_epoch": next_epoch,
                        "next_turn_id": next_turn.id,
                        "unblocked_turn_count": len(unblocked_turns),
                        "queued_turn_count": len(queued_turns),
                        "queue_version": queue_version,
                        "queue_paused": queue_paused,
                    },
                )
        return next_epoch

    def ingest_session_event(
        self,
        *,
        session_id: str,
        event_type: str,
        source: SemanticEventSource,
        trace_id: str,
        idempotency_key: str | None = None,
        turn_id: str | None = None,
        interaction_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> SemanticEvent:
        session = self.repository.get_session(session_id)
        if idempotency_key:
            existing = self.repository.get_event_by_idempotency_key(idempotency_key)
            if existing:
                return existing
        # 外部 Agent 适配器（如 Claude Code Hooks）不知道 AgentBridge 的 turn_id：它发的
        # assistant.delta / tool.* / turn.completed 等都属于当前活动 turn，统一补上 active_turn_id，
        # 这样回答能与完成事件归到同一 turn、被正确合并。
        if turn_id is None and (
            source == SemanticEventSource.AGENT_ADAPTER
            or event_type
            in {"turn.started", "turn.completed", "turn.failed", "turn.interrupted"}
        ):
            turn_id = session.active_turn_id
        self._apply_turn_lifecycle_event(
            session_id=session_id,
            event_type=event_type,
            turn_id=turn_id,
        )
        return self.emit_event(
            event_type=event_type,
            source=source,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            turn_id=turn_id,
            interaction_id=interaction_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def _apply_turn_lifecycle_event(
        self,
        *,
        session_id: str,
        event_type: str,
        turn_id: str | None,
    ) -> None:
        if event_type not in {
            "turn.started",
            "turn.completed",
            "turn.failed",
            "turn.interrupted",
        }:
            return
        if not turn_id:
            # 无法定位 turn（事件未带 turn_id 且会话当前没有活动 turn）：仍记录该事件，
            # 但不做生命周期状态变更，避免外部 hook 因此收到 400。
            return
        if event_type == "turn.started":
            self.repository.start_turn(session_id=session_id, turn_id=turn_id)
            return
        terminal_status = {
            "turn.completed": TurnStatus.COMPLETED,
            "turn.failed": TurnStatus.FAILED,
            "turn.interrupted": TurnStatus.CANCELLED,
        }[event_type]
        self.repository.finish_turn(
            session_id=session_id,
            turn_id=turn_id,
            status=terminal_status,
        )

    def create_interaction(
        self,
        *,
        actor: Actor,
        session_id: str,
        interaction_type: InteractionType,
        prompt: str,
        trace_id: str,
        chat_context_id: str | None = None,
        turn_id: str | None = None,
        options: list[str] | None = None,
        required_votes: int | None = None,
        expires_at: datetime | None = None,
        risk_level: RiskLevel = RiskLevel.MEDIUM,
    ) -> Interaction:
        effective_actor = self.effective_actor(actor, chat_context_id)
        if not prompt.strip():
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "Interaction prompt 不能为空。",
                next_step="请提供需要用户处理的问题或审批说明。",
            )
        session = self.require_session_permission(
            effective_actor,
            Permission.SESSION_SEND,
            session_id=session_id,
            chat_context_id=chat_context_id,
            attributes={
                "operation": "create_interaction",
                "interaction_type": interaction_type.value,
                "risk_level": risk_level.value,
            },
        )
        approval_policy, applied_overrides = self._effective_approval_policy(
            project_id=session.project_id,
            chat_context_id=chat_context_id,
        )
        computed_votes = (
            required_votes
            if required_votes is not None
            else approval_policy.quorum_for(risk_level)
        )
        if computed_votes < 1:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "required_votes 必须大于等于 1。",
                next_step="请提供至少 1 个所需票数。",
            )
        policy_snapshot = approval_policy.snapshot_for(risk_level)
        policy_snapshot["applied_overrides"] = [
            override.model_dump(mode="json") for override in applied_overrides
        ]
        policy_snapshot["requested_votes"] = required_votes
        interaction = self.repository.create_interaction(
            session_id=session_id,
            interaction_type=interaction_type,
            prompt=prompt,
            turn_id=turn_id,
            options=options,
            required_votes=computed_votes,
            expires_at=expires_at,
            risk_level=risk_level,
            requested_by=effective_actor.id,
            policy_snapshot=policy_snapshot,
        )
        event_type = interaction_request_event_type(interaction_type)
        self.audit(
            action=event_type,
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session_id,
            interaction_id=interaction.id,
            details={
                "type": interaction.type.value,
                "required_votes": interaction.required_votes,
                "risk_level": interaction.risk_level.value,
            },
        )
        self.emit_event(
            event_type=event_type,
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            turn_id=turn_id,
            interaction_id=interaction.id,
            payload={
                "type": interaction.type.value,
                "prompt": interaction.prompt,
                "options": interaction.options,
                "risk_level": interaction.risk_level.value,
                "required_votes": interaction.required_votes,
                "requested_by": interaction.requested_by,
                "policy_snapshot": interaction.policy_snapshot,
                "version": interaction.version,
                "expires_at": (
                    interaction.expires_at.isoformat() if interaction.expires_at else None
                ),
            },
        )
        return interaction

    def ingest_agent_adapter_interaction_event(
        self,
        *,
        session_id: str,
        event_type: str,
        trace_id: str,
        payload: dict[str, object],
        idempotency_key: str | None = None,
        turn_id: str | None = None,
        interaction_id: str | None = None,
    ) -> SemanticEvent:
        if idempotency_key:
            existing = self.repository.get_event_by_idempotency_key(idempotency_key)
            if existing:
                return existing
        session = self.repository.get_session(session_id)
        interaction_type = interaction_type_from_adapter_event(event_type)
        prompt_value = payload.get("prompt")
        prompt = str(prompt_value).strip() if prompt_value is not None else ""
        if not prompt:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "Adapter interaction prompt 不能为空。",
                next_step="请在 Adapter 事件 payload 中提供 prompt、reason 或 question。",
            )
        risk_level = risk_level_from_payload(payload.get("risk_level"))
        approval_policy, applied_overrides = self._effective_approval_policy(
            project_id=session.project_id,
            chat_context_id=None,
        )
        required_votes = approval_policy.quorum_for(risk_level)
        policy_snapshot = approval_policy.snapshot_for(risk_level)
        policy_snapshot["applied_overrides"] = [
            override.model_dump(mode="json") for override in applied_overrides
        ]
        policy_snapshot["requested_votes"] = None
        options_value = payload.get("options")
        options = (
            [str(option) for option in options_value]
            if isinstance(options_value, list)
            else []
        )
        interaction = self.repository.create_interaction(
            session_id=session_id,
            interaction_type=interaction_type,
            prompt=prompt,
            turn_id=turn_id,
            options=options,
            required_votes=required_votes,
            risk_level=risk_level,
            requested_by="agent-adapter",
            policy_snapshot=policy_snapshot,
        )
        event_payload = {
            **payload,
            "type": interaction.type.value,
            "prompt": interaction.prompt,
            "options": interaction.options,
            "risk_level": interaction.risk_level.value,
            "required_votes": interaction.required_votes,
            "requested_by": interaction.requested_by,
            "policy_snapshot": interaction.policy_snapshot,
            "version": interaction.version,
            "expires_at": (
                interaction.expires_at.isoformat() if interaction.expires_at else None
            ),
        }
        self.audit(
            action=event_type,
            actor=Actor(id="agent-adapter", roles={"admin"}),
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            interaction_id=interaction.id,
            details={
                "type": interaction.type.value,
                "adapter": payload.get("adapter"),
                "adapter_event_type": payload.get("adapter_event_type"),
                "required_votes": interaction.required_votes,
                "risk_level": interaction.risk_level.value,
            },
        )
        return self.emit_event(
            event_type=event_type,
            source=SemanticEventSource.AGENT_ADAPTER,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session_id,
            turn_id=turn_id,
            interaction_id=interaction.id,
            payload=event_payload,
            idempotency_key=idempotency_key,
        )

    def list_interactions(
        self,
        *,
        actor: Actor,
        chat_context_id: str | None = None,
        session_id: str | None = None,
        status: InteractionStatus | None = None,
    ) -> list[Interaction]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        if session_id:
            self.require_session_permission(
                effective_actor,
                Permission.SESSION_VIEW,
                session_id=session_id,
                chat_context_id=chat_context_id,
                attributes={"operation": "list_interactions"},
            )
        else:
            self.require_collection_permission(
                effective_actor,
                Permission.SESSION_VIEW,
                resource_type="session",
                attributes={
                    "operation": "list_interactions",
                    **self._chat_policy_attributes(chat_context_id),
                },
            )
        self.expire_due_interactions(
            actor=Actor(id="system", roles={"admin"}),
            trace_id="interaction-expire",
            chat_context_id=chat_context_id,
        )
        return self.repository.list_interactions(session_id=session_id, status=status)

    def get_interaction(
        self,
        *,
        actor: Actor,
        interaction_id: str,
        chat_context_id: str | None = None,
    ) -> Interaction:
        effective_actor = self.effective_actor(actor, chat_context_id)
        interaction = self.repository.get_interaction(interaction_id)
        self.require_session_permission(
            effective_actor,
            Permission.SESSION_VIEW,
            session_id=interaction.session_id,
            chat_context_id=chat_context_id,
            attributes={
                "operation": "get_interaction",
                "interaction_id": interaction_id,
                "interaction_type": interaction.type.value,
                "risk_level": interaction.risk_level.value,
            },
        )
        self.expire_due_interactions(
            actor=Actor(id="system", roles={"admin"}),
            trace_id="interaction-expire",
            chat_context_id=chat_context_id,
        )
        return self.repository.get_interaction(interaction_id)

    def answer_interaction(
        self,
        *,
        actor: Actor,
        interaction_id: str,
        answer: str,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> Interaction:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.expire_due_interactions(
            actor=Actor(id="system", roles={"admin"}),
            trace_id=trace_id,
            chat_context_id=chat_context_id,
        )
        current = self.repository.get_interaction(interaction_id)
        self.require_session_permission(
            effective_actor,
            Permission.SESSION_SEND,
            session_id=current.session_id,
            chat_context_id=chat_context_id,
            attributes={
                "operation": "answer_interaction",
                "interaction_id": interaction_id,
                "interaction_type": current.type.value,
                "risk_level": current.risk_level.value,
            },
        )
        if current.type == InteractionType.APPROVAL:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "审批类 Interaction 必须使用 approve 或 deny 处理。",
                next_step=(
                    "请执行 /agent approve <interaction-id> "
                    "或 /agent deny <interaction-id>。"
                ),
            )
        interaction = self.repository.answer_interaction(interaction_id, answer)
        session = self.repository.get_session(interaction.session_id)
        self.audit(
            action="interaction.answered",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session.id,
            interaction_id=interaction.id,
        )
        self.emit_event(
            event_type="interaction.answered",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session.id,
            turn_id=interaction.turn_id,
            interaction_id=interaction.id,
            payload={
                "answer": interaction.answer,
                "status": interaction.status.value,
                "version": interaction.version,
            },
        )
        return interaction

    def cancel_interaction(
        self,
        *,
        actor: Actor,
        interaction_id: str,
        trace_id: str,
        chat_context_id: str | None = None,
        reason: str | None = None,
    ) -> Interaction:
        effective_actor = self.effective_actor(actor, chat_context_id)
        current = self.repository.get_interaction(interaction_id)
        self.require_session_permission(
            effective_actor,
            Permission.SESSION_MANAGE,
            session_id=current.session_id,
            chat_context_id=chat_context_id,
            attributes={
                "operation": "cancel_interaction",
                "interaction_id": interaction_id,
                "interaction_type": current.type.value,
                "risk_level": current.risk_level.value,
            },
        )
        interaction = self.repository.cancel_interaction(interaction_id, reason)
        session = self.repository.get_session(interaction.session_id)
        self.audit(
            action="interaction.cancelled",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session.id,
            interaction_id=interaction.id,
            details={"reason": reason},
        )
        self.emit_event(
            event_type="interaction.cancelled",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session.id,
            turn_id=interaction.turn_id,
            interaction_id=interaction.id,
            payload={
                "status": interaction.status.value,
                "reason": reason,
                "version": interaction.version,
            },
        )
        return interaction

    def expire_due_interactions(
        self,
        *,
        actor: Actor,
        trace_id: str,
        chat_context_id: str | None = None,
        now: datetime | None = None,
    ) -> list[Interaction]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        expired = self.repository.expire_due_interactions(now)
        for interaction in expired:
            session = self.repository.get_session(interaction.session_id)
            self.audit(
                action="interaction.expired",
                actor=effective_actor,
                outcome=AuditOutcome.ALLOWED,
                trace_id=trace_id,
                chat_context_id=chat_context_id,
                project_id=session.project_id,
                session_id=session.id,
                interaction_id=interaction.id,
            )
            self.emit_event(
                event_type="interaction.expired",
                source=SemanticEventSource.CONTROL_PLANE,
                trace_id=trace_id,
                project_id=session.project_id,
                session_id=session.id,
                turn_id=interaction.turn_id,
                interaction_id=interaction.id,
                payload={
                    "status": interaction.status.value,
                    "version": interaction.version,
                    "expires_at": (
                        interaction.expires_at.isoformat()
                        if interaction.expires_at
                        else None
                    ),
                },
            )
        return expired

    def vote_interaction(
        self,
        *,
        actor: Actor,
        interaction_id: str,
        approve: bool,
        trace_id: str,
        chat_context_id: str | None = None,
        reason: str | None = None,
    ) -> Interaction:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.expire_due_interactions(
            actor=Actor(id="system", roles={"admin"}),
            trace_id=trace_id,
            chat_context_id=chat_context_id,
        )
        current = self.repository.get_interaction(interaction_id)
        if current.type != InteractionType.APPROVAL:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "非审批类 Interaction 不能投票。",
                next_step="请执行 /agent answer <interaction-id> <answer> 处理问题。",
            )
        session = self.repository.get_session(current.session_id)
        self.policy.require_approval_vote(
            effective_actor,
            current.risk_level,
            resource_type="interaction",
            resource_id=current.id,
            attributes={
                "operation": "vote_interaction",
                "interaction_id": current.id,
                "interaction_type": current.type.value,
                "risk_level": current.risk_level.value,
                "session_id": session.id,
                "project_id": session.project_id,
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        if (
            approve
            and current.requested_by == effective_actor.id
            and current.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        ):
            approvals_without_actor = sum(
                1
                for actor_id, vote in current.votes.items()
                if actor_id != effective_actor.id and vote
            )
            if approvals_without_actor + 1 >= current.required_votes:
                raise AgentBridgeError(
                    ErrorCode.PERMISSION_DENIED,
                    "请求人不能单独完成高危审批。",
                    next_step="请由其他具备高危审批权限的用户完成审批。",
                    status_code=403,
                    details={
                        "risk_level": current.risk_level.value,
                        "required_votes": current.required_votes,
                    },
                )
        interaction = self.repository.vote_interaction(
            interaction_id,
            actor=effective_actor,
            approve=approve,
        )
        self.audit(
            action="approval.voted",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            project_id=session.project_id,
            session_id=session.id,
            interaction_id=interaction.id,
            details={"approve": approve, "reason": reason},
        )
        self.emit_event(
            event_type="approval.voted",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=session.project_id,
            session_id=session.id,
            turn_id=interaction.turn_id,
            interaction_id=interaction.id,
            payload={
                "actor_id": effective_actor.id,
                "approve": approve,
                "reason": reason,
                "status": interaction.status.value,
                "risk_level": interaction.risk_level.value,
                "votes": interaction.votes,
                "required_votes": interaction.required_votes,
                "version": interaction.version,
            },
        )
        return interaction

    def effective_actor(self, actor: Actor, chat_context_id: str | None = None) -> Actor:
        return self.repository.effective_actor(actor, chat_context_id)

    def require_collection_permission(
        self,
        actor: Actor,
        permission: Permission,
        *,
        resource_type: str,
        resource_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        self.policy.require(
            actor,
            permission,
            resource_type=resource_type,
            resource_id=resource_id,
            attributes=attributes or {},
        )

    def require_project_permission(
        self,
        actor: Actor,
        permission: Permission,
        *,
        project_id: str | None = None,
        resource_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> Project | None:
        project = self.repository.get_project(project_id) if project_id else None
        self.policy.require(
            actor,
            permission,
            resource_type="project",
            resource_id=project.id if project else resource_id,
            attributes=self._project_policy_attributes(project, attributes),
        )
        return project

    def require_session_permission(
        self,
        actor: Actor,
        permission: Permission,
        *,
        session_id: str,
        chat_context_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> AgentSession:
        session = self.repository.get_session(session_id)
        self.policy.require(
            actor,
            permission,
            resource_type="session",
            resource_id=session.id,
            attributes=self._session_policy_attributes(
                session,
                chat_context_id=chat_context_id,
                attributes=attributes,
            ),
        )
        return session

    def require_terminal_control(
        self,
        actor: Actor,
        *,
        session_id: str,
        chat_context_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> AgentSession:
        session = self.repository.get_session(session_id)
        policy_attributes = self._session_policy_attributes(
            session,
            chat_context_id=chat_context_id,
            attributes=attributes,
        )
        policy_attributes["session_id"] = session.id
        self.policy.require(
            actor,
            Permission.TERMINAL_CONTROL,
            resource_type="terminal",
            resource_id=session.id,
            attributes=policy_attributes,
        )
        return session

    def grant_group_roles(
        self,
        *,
        actor: Actor,
        chat_context_id: str,
        target_actor_id: str,
        roles: set[str],
        trace_id: str,
    ) -> GroupRoleBinding:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_collection_permission(
            effective_actor,
            Permission.GROUP_ROLE_MANAGE,
            resource_type="chat_context",
            resource_id=chat_context_id,
            attributes={
                "operation": "grant_group_roles",
                "target_actor_id": target_actor_id,
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        roles = self._validated_group_roles(roles)
        binding = self.repository.grant_group_roles(
            chat_context_id=chat_context_id,
            actor_id=target_actor_id,
            roles=roles,
            granted_by=effective_actor.id,
        )
        self.audit(
            action="group.role_granted",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            details={"target_actor_id": target_actor_id, "roles": sorted(roles)},
        )
        self.emit_event(
            event_type="group.role_granted",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            payload={
                "chat_context_id": chat_context_id,
                "target_actor_id": target_actor_id,
                "roles": sorted(binding.roles),
            },
        )
        return binding

    def revoke_group_roles(
        self,
        *,
        actor: Actor,
        chat_context_id: str,
        target_actor_id: str,
        roles: set[str],
        trace_id: str,
    ) -> GroupRoleBinding | None:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_collection_permission(
            effective_actor,
            Permission.GROUP_ROLE_MANAGE,
            resource_type="chat_context",
            resource_id=chat_context_id,
            attributes={
                "operation": "revoke_group_roles",
                "target_actor_id": target_actor_id,
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        roles = self._validated_group_roles(roles)
        binding = self.repository.revoke_group_roles(
            chat_context_id=chat_context_id,
            actor_id=target_actor_id,
            roles=roles,
            revoked_by=effective_actor.id,
        )
        self.audit(
            action="group.role_revoked",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            details={"target_actor_id": target_actor_id, "roles": sorted(roles)},
        )
        self.emit_event(
            event_type="group.role_revoked",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            payload={
                "chat_context_id": chat_context_id,
                "target_actor_id": target_actor_id,
                "roles": sorted(binding.roles) if binding else [],
            },
        )
        return binding

    def list_group_role_bindings(
        self,
        *,
        actor: Actor,
        chat_context_id: str,
    ) -> list[GroupRoleBinding]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_collection_permission(
            effective_actor,
            Permission.GROUP_ROLE_MANAGE,
            resource_type="chat_context",
            resource_id=chat_context_id,
            attributes={
                "operation": "list_group_roles",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        return self.repository.list_group_role_bindings(chat_context_id)

    def list_access_policy_rules(
        self,
        *,
        actor: Actor,
        enabled: bool | None = None,
        chat_context_id: str | None = None,
    ) -> list[AccessPolicyRule]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_collection_permission(
            effective_actor,
            Permission.POLICY_MANAGE,
            resource_type="access_policy",
            attributes={
                "operation": "list_access_policy_rules",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        return self.repository.list_access_policy_rules(enabled)

    def set_access_policy_rule(
        self,
        *,
        actor: Actor,
        effect: AccessPolicyEffect,
        action: str,
        trace_id: str,
        rule_id: str | None = None,
        resource_type: str = "*",
        resource_id: str | None = None,
        actor_ids: list[str] | None = None,
        roles: list[str] | None = None,
        attributes: dict[str, object] | None = None,
        description: str | None = None,
        priority: int = 100,
        enabled: bool = True,
        chat_context_id: str | None = None,
    ) -> AccessPolicyRule:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_collection_permission(
            effective_actor,
            Permission.POLICY_MANAGE,
            resource_type="access_policy",
            resource_id=rule_id,
            attributes={
                "operation": "set_access_policy_rule",
                "target_action": action.strip(),
                "target_resource_type": resource_type or "*",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        normalized_roles = (
            sorted(self._validated_group_roles(set(roles))) if roles else []
        )
        normalized_action = self._validated_policy_pattern(action, "action")
        normalized_resource_type = self._validated_policy_pattern(
            resource_type or "*", "resource_type"
        )
        rule = self.repository.upsert_access_policy_rule(
            rule_id=rule_id,
            effect=effect,
            action=normalized_action,
            resource_type=normalized_resource_type,
            resource_id=resource_id,
            actor_ids=actor_ids,
            roles=normalized_roles,
            attributes=attributes,
            description=description,
            priority=priority,
            enabled=enabled,
            updated_by=effective_actor.id,
        )
        self.audit(
            action="access_policy.rule_upserted",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            details={
                "rule_id": rule.id,
                "effect": rule.effect.value,
                "action": rule.action,
                "resource_type": rule.resource_type,
                "resource_id": rule.resource_id,
                "enabled": rule.enabled,
            },
        )
        self.emit_event(
            event_type="access_policy.rule_upserted",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            payload={
                "rule": rule.model_dump(mode="json"),
                "updated_by": effective_actor.id,
            },
        )
        return rule

    def delete_access_policy_rule(
        self,
        *,
        actor: Actor,
        rule_id: str,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> AccessPolicyRule:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_collection_permission(
            effective_actor,
            Permission.POLICY_MANAGE,
            resource_type="access_policy",
            resource_id=rule_id,
            attributes={
                "operation": "delete_access_policy_rule",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        existing = self.repository.get_access_policy_rule(rule_id)
        deleted = self.repository.delete_access_policy_rule(rule_id) or existing
        self.audit(
            action="access_policy.rule_deleted",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            details={
                "rule_id": deleted.id,
                "effect": deleted.effect.value,
                "action": deleted.action,
                "resource_type": deleted.resource_type,
                "resource_id": deleted.resource_id,
            },
        )
        self.emit_event(
            event_type="access_policy.rule_deleted",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            payload={"rule_id": deleted.id, "deleted_by": effective_actor.id},
        )
        return deleted

    def simulate_access_policy(
        self,
        *,
        actor: Actor,
        target_actor: Actor,
        action: str,
        resource_type: str = "*",
        resource_id: str | None = None,
        attributes: dict[str, object] | None = None,
        chat_context_id: str | None = None,
    ) -> dict[str, object]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_collection_permission(
            effective_actor,
            Permission.POLICY_MANAGE,
            resource_type="access_policy",
            attributes={
                "operation": "simulate_access_policy",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        effective_target = self.effective_actor(target_actor, chat_context_id)
        normalized_action = self._validated_policy_pattern(action, "action")
        normalized_resource_type = self._validated_policy_pattern(
            resource_type or "*", "resource_type"
        )
        supplied_attributes = attributes or {}
        decision = self.policy.evaluate(
            effective_target,
            normalized_action,
            resource_type=normalized_resource_type,
            resource_id=resource_id,
            attributes=supplied_attributes,
        )
        return {
            "decision": decision.to_payload(),
            "target_actor": effective_target.model_dump(mode="json"),
            "resource": {
                "type": normalized_resource_type,
                "id": resource_id,
                "attributes": supplied_attributes,
            },
        }

    def list_device_identities(
        self,
        *,
        actor: Actor,
        include_revoked: bool = False,
        chat_context_id: str | None = None,
    ) -> list[DeviceIdentity]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_collection_permission(
            effective_actor,
            Permission.DEVICE_MANAGE,
            resource_type="device_identity",
            attributes={
                "operation": "list_device_identities",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        return self.repository.list_device_identities(include_revoked=include_revoked)

    def upsert_device_identity(
        self,
        *,
        actor: Actor,
        device_id: str,
        display_name: str | None = None,
        device_key: str | None = None,
        allowed_scopes: set[DeviceIdentityScope] | None = None,
        allowed_resource_ids: set[str] | None = None,
        certificate_fingerprints: set[str] | None = None,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> tuple[DeviceIdentity, str | None]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        normalized_device_id = self._validated_device_id(device_id)
        normalized_scopes = self._validated_device_scopes(allowed_scopes)
        normalized_resource_ids = self._validated_device_resource_ids(
            allowed_resource_ids
        )
        normalized_fingerprints = self._validated_certificate_fingerprints(
            certificate_fingerprints
        )
        self.require_collection_permission(
            effective_actor,
            Permission.DEVICE_MANAGE,
            resource_type="device_identity",
            resource_id=normalized_device_id,
            attributes={
                "operation": "upsert_device_identity",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        existing_identity: DeviceIdentity | None = None
        try:
            existing_identity = self.repository.get_device_identity(normalized_device_id)
        except AgentBridgeError as exc:
            if exc.code != ErrorCode.NOT_FOUND:
                raise

        secret: str | None = None
        if device_key is not None:
            secret = device_key.strip()
        elif existing_identity is None and not normalized_fingerprints:
            secret = generate_device_key()
        if device_key is not None and not secret:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备 key 不能为空。",
                next_step="请提供非空 device_key，或省略该字段保留/生成 key。",
            )
        salt = generate_device_key_salt() if secret else None
        key_hash = (
            hash_device_key(
                secret,
                salt=salt,
                iterations=DEFAULT_DEVICE_KEY_ITERATIONS,
            )
            if secret and salt
            else None
        )
        identity = self.repository.upsert_device_identity(
            device_id=normalized_device_id,
            display_name=display_name,
            key_hash=key_hash,
            key_salt=salt,
            key_iterations=DEFAULT_DEVICE_KEY_ITERATIONS,
            allowed_scopes=normalized_scopes,
            allowed_resource_ids=normalized_resource_ids,
            certificate_fingerprints=normalized_fingerprints,
            updated_by=effective_actor.id,
        )
        allowed_scope_values = sorted(scope.value for scope in identity.allowed_scopes)
        allowed_resource_id_values = sorted(identity.allowed_resource_ids)
        certificate_fingerprint_count = len(identity.certificate_fingerprints)
        self.audit(
            action="device_identity.upserted",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            details={
                "device_identity_id": identity.id,
                "device_id": identity.device_id,
                "display_name": identity.display_name,
                "status": identity.status.value,
                "allowed_scopes": allowed_scope_values,
                "allowed_resource_ids": allowed_resource_id_values,
                "certificate_fingerprint_count": certificate_fingerprint_count,
            },
        )
        self.emit_event(
            event_type="device_identity.upserted",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            payload={
                "device_identity_id": identity.id,
                "device_id": identity.device_id,
                "display_name": identity.display_name,
                "status": identity.status.value,
                "allowed_scopes": allowed_scope_values,
                "allowed_resource_ids": allowed_resource_id_values,
                "certificate_fingerprint_count": certificate_fingerprint_count,
                "updated_by": effective_actor.id,
            },
        )
        return identity, secret

    def rotate_device_identity_certificate_fingerprints(
        self,
        *,
        actor: Actor,
        device_id: str,
        add_fingerprints: set[str] | None = None,
        remove_fingerprints: set[str] | None = None,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> DeviceIdentity:
        effective_actor = self.effective_actor(actor, chat_context_id)
        normalized_device_id = self._validated_device_id(device_id)
        fingerprints_to_add = (
            self._validated_certificate_fingerprints(add_fingerprints) or set()
        )
        fingerprints_to_remove = (
            self._validated_certificate_fingerprints(remove_fingerprints) or set()
        )
        if not fingerprints_to_add and not fingerprints_to_remove:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "证书指纹轮换至少需要添加或移除一个指纹。",
                next_step=(
                    "请提供 add_fingerprints 或 remove_fingerprints，"
                    "或使用普通设备身份更新接口编辑完整指纹列表。"
                ),
            )
        self.require_collection_permission(
            effective_actor,
            Permission.DEVICE_MANAGE,
            resource_type="device_identity",
            resource_id=normalized_device_id,
            attributes={
                "operation": "rotate_device_identity_certificate_fingerprints",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        existing_identity = self.repository.get_device_identity(normalized_device_id)
        if existing_identity.status != DeviceIdentityStatus.ACTIVE:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "已撤销的设备身份不能轮换证书指纹。",
                next_step="请创建新的设备身份，或使用仍处于 active 状态的设备身份。",
            )
        current_fingerprints = set(existing_identity.certificate_fingerprints)
        next_fingerprints = (
            current_fingerprints.union(fingerprints_to_add).difference(
                fingerprints_to_remove
            )
        )
        if not next_fingerprints and not existing_identity.key_hash:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "证书-only 设备身份不能移除最后一个证书指纹。",
                next_step=(
                    "请先为设备身份配置 device_key，或在同一次轮换中添加新的证书指纹。"
                ),
            )
        certificate_records = self._certificate_records_for_fingerprint_update(
            existing_identity=existing_identity,
            next_fingerprints=next_fingerprints,
            actor_id=effective_actor.id,
            source="fingerprint_rotation",
        )
        identity = self.repository.upsert_device_identity(
            device_id=existing_identity.device_id,
            display_name=existing_identity.display_name,
            allowed_scopes=set(existing_identity.allowed_scopes),
            allowed_resource_ids=set(existing_identity.allowed_resource_ids),
            certificate_fingerprints=next_fingerprints,
            certificate_records=certificate_records,
            updated_by=effective_actor.id,
        )
        added_values = sorted(next_fingerprints.difference(current_fingerprints))
        removed_values = sorted(current_fingerprints.difference(next_fingerprints))
        self.audit(
            action="device_identity.certificate_fingerprints_rotated",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            details={
                "device_identity_id": identity.id,
                "device_id": identity.device_id,
                "added_fingerprints": added_values,
                "removed_fingerprints": removed_values,
                "certificate_fingerprint_count": len(identity.certificate_fingerprints),
            },
        )
        self.emit_event(
            event_type="device_identity.certificate_fingerprints_rotated",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            payload={
                "device_identity_id": identity.id,
                "device_id": identity.device_id,
                "added_fingerprint_count": len(added_values),
                "removed_fingerprint_count": len(removed_values),
                "certificate_fingerprint_count": len(identity.certificate_fingerprints),
                "updated_by": effective_actor.id,
            },
        )
        return identity

    def issue_device_identity_certificate(
        self,
        *,
        actor: Actor,
        device_id: str,
        csr_pem: str,
        issuer: DeviceCertificateIssuer | ExternalDeviceCertificateIssuer,
        validity_days: int | None,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> tuple[DeviceIdentity, IssuedDeviceCertificate]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        normalized_device_id = self._validated_device_id(device_id)
        self.require_collection_permission(
            effective_actor,
            Permission.DEVICE_MANAGE,
            resource_type="device_identity",
            resource_id=normalized_device_id,
            attributes={
                "operation": "issue_device_identity_certificate",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        existing_identity = self.repository.get_device_identity(normalized_device_id)
        if existing_identity.status != DeviceIdentityStatus.ACTIVE:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "已撤销的设备身份不能签发新证书。",
                next_step="请创建新的设备身份，或使用仍处于 active 状态的设备身份。",
            )
        issued_certificate = issuer.issue(
            device_id=normalized_device_id,
            csr_pem=csr_pem,
            validity_days=validity_days,
        )
        next_fingerprints = set(existing_identity.certificate_fingerprints)
        next_fingerprints.add(issued_certificate.certificate_fingerprint)
        certificate_records = self._certificate_records_for_issued_certificate(
            existing_identity=existing_identity,
            issued_certificate=issued_certificate,
            actor_id=effective_actor.id,
        )
        identity = self.repository.upsert_device_identity(
            device_id=existing_identity.device_id,
            display_name=existing_identity.display_name,
            allowed_scopes=set(existing_identity.allowed_scopes),
            allowed_resource_ids=set(existing_identity.allowed_resource_ids),
            certificate_fingerprints=next_fingerprints,
            certificate_records=certificate_records,
            updated_by=effective_actor.id,
        )
        self.audit(
            action="device_identity.certificate_issued",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            details={
                "device_identity_id": identity.id,
                "device_id": identity.device_id,
                "certificate_fingerprint": issued_certificate.certificate_fingerprint,
                "serial_number": issued_certificate.serial_number,
                "not_before": issued_certificate.not_before.isoformat(),
                "not_after": issued_certificate.not_after.isoformat(),
                "certificate_fingerprint_count": len(identity.certificate_fingerprints),
            },
        )
        self.emit_event(
            event_type="device_identity.certificate_issued",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            payload={
                "device_identity_id": identity.id,
                "device_id": identity.device_id,
                "certificate_fingerprint": issued_certificate.certificate_fingerprint,
                "serial_number": issued_certificate.serial_number,
                "not_after": issued_certificate.not_after.isoformat(),
                "updated_by": effective_actor.id,
            },
        )
        return identity, issued_certificate

    def renew_device_identity_certificate(
        self,
        *,
        actor: Actor,
        device_id: str,
        csr_pem: str,
        issuer: DeviceCertificateIssuer | ExternalDeviceCertificateIssuer,
        validity_days: int | None,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> tuple[DeviceIdentity, IssuedDeviceCertificate, list[str]]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        normalized_device_id = self._validated_device_id(device_id)
        self.require_collection_permission(
            effective_actor,
            Permission.DEVICE_MANAGE,
            resource_type="device_identity",
            resource_id=normalized_device_id,
            attributes={
                "operation": "renew_device_identity_certificate",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        existing_identity = self.repository.get_device_identity(normalized_device_id)
        if existing_identity.status != DeviceIdentityStatus.ACTIVE:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "已撤销的设备身份不能续期证书。",
                next_step="请创建新的设备身份，或使用仍处于 active 状态的设备身份。",
            )
        replaced_fingerprints = self._active_managed_ca_certificate_fingerprints(
            existing_identity
        )
        if not replaced_fingerprints:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备身份没有可续期的托管 CA 证书。",
                next_step="请先使用 /certificates/issue 签发托管证书。",
            )
        issued_certificate = issuer.issue(
            device_id=normalized_device_id,
            csr_pem=csr_pem,
            validity_days=validity_days,
        )
        next_fingerprints = set(existing_identity.certificate_fingerprints).difference(
            replaced_fingerprints
        )
        next_fingerprints.add(issued_certificate.certificate_fingerprint)
        certificate_records = self._certificate_records_for_renewed_certificate(
            existing_identity=existing_identity,
            issued_certificate=issued_certificate,
            actor_id=effective_actor.id,
            replaced_fingerprints=replaced_fingerprints,
        )
        identity = self.repository.upsert_device_identity(
            device_id=existing_identity.device_id,
            display_name=existing_identity.display_name,
            allowed_scopes=set(existing_identity.allowed_scopes),
            allowed_resource_ids=set(existing_identity.allowed_resource_ids),
            certificate_fingerprints=next_fingerprints,
            certificate_records=certificate_records,
            updated_by=effective_actor.id,
        )
        replaced_values = sorted(replaced_fingerprints)
        self.audit(
            action="device_identity.certificate_renewed",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            details={
                "device_identity_id": identity.id,
                "device_id": identity.device_id,
                "certificate_fingerprint": issued_certificate.certificate_fingerprint,
                "replaced_fingerprints": replaced_values,
                "serial_number": issued_certificate.serial_number,
                "not_before": issued_certificate.not_before.isoformat(),
                "not_after": issued_certificate.not_after.isoformat(),
                "certificate_fingerprint_count": len(identity.certificate_fingerprints),
            },
        )
        self.emit_event(
            event_type="device_identity.certificate_renewed",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            payload={
                "device_identity_id": identity.id,
                "device_id": identity.device_id,
                "certificate_fingerprint": issued_certificate.certificate_fingerprint,
                "replaced_fingerprint_count": len(replaced_values),
                "replaced_fingerprints": replaced_values,
                "serial_number": issued_certificate.serial_number,
                "not_after": issued_certificate.not_after.isoformat(),
                "updated_by": effective_actor.id,
            },
        )
        return identity, issued_certificate, replaced_values

    def scan_device_identity_certificates(
        self,
        *,
        actor: Actor,
        warning_days: int,
        include_revoked: bool = False,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> dict[str, object]:
        if warning_days <= 0:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "证书到期预警窗口必须为正整数天。",
                next_step="请提供大于 0 的 warning_days。",
            )
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.require_collection_permission(
            effective_actor,
            Permission.DEVICE_MANAGE,
            resource_type="device_identity",
            attributes={
                "operation": "scan_device_identity_certificates",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        scanned_at = utc_now()
        status_counts = {
            "ok": 0,
            "expiring": 0,
            "expired": 0,
            "unknown": 0,
            "none": 0,
            "revoked": 0,
        }
        renewal_status_counts = {
            "scheduled": 0,
            "due": 0,
            "overdue": 0,
            "unknown": 0,
            "not_applicable": 0,
            "none": 0,
            "revoked": 0,
        }
        devices: list[dict[str, object]] = []
        action_required_devices: list[dict[str, object]] = []
        renewal_action_required_count = 0
        for identity in self.repository.list_device_identities(
            include_revoked=include_revoked
        ):
            health = device_identity_certificate_health(
                identity,
                now=scanned_at,
                warning_days=warning_days,
            )
            health_status = str(health["status"])
            status_counts[health_status] = status_counts.get(health_status, 0) + 1
            renewal_status = str(health["renewal_status"])
            renewal_status_counts[renewal_status] = (
                renewal_status_counts.get(renewal_status, 0) + 1
            )
            if renewal_status in {"due", "overdue", "unknown"}:
                renewal_action_required_count += 1
            device_item = {
                "device_identity_id": identity.id,
                "device_id": identity.device_id,
                "display_name": identity.display_name,
                "identity_status": identity.status.value,
                "certificate_health": health,
            }
            devices.append(device_item)
            if health_status in {"expired", "expiring", "unknown"}:
                action_required_devices.append(
                    {
                        "device_identity_id": identity.id,
                        "device_id": identity.device_id,
                        "display_name": identity.display_name,
                        "identity_status": identity.status.value,
                        "certificate_health_status": health_status,
                        "expired_count": health["expired_count"],
                        "expiring_count": health["expiring_count"],
                        "untracked_certificate_count": health[
                            "untracked_certificate_count"
                        ],
                        "missing_validity_count": health["missing_validity_count"],
                        "next_expires_at": health["next_expires_at"],
                        "renewal_status": renewal_status,
                        "renewal_due_count": health["renewal_due_count"],
                        "renewal_overdue_count": health["renewal_overdue_count"],
                        "renewal_due_fingerprints": health[
                            "renewal_due_fingerprints"
                        ],
                        "renewal_overdue_fingerprints": health[
                            "renewal_overdue_fingerprints"
                        ],
                        "renewal_due_at": health["renewal_due_at"],
                    }
                )
        result = {
            "scanned_at": datetime_payload(scanned_at),
            "warning_days": warning_days,
            "include_revoked": include_revoked,
            "total_device_count": len(devices),
            "status_counts": status_counts,
            "renewal_status_counts": renewal_status_counts,
            "action_required_count": len(action_required_devices),
            "renewal_action_required_count": renewal_action_required_count,
            "action_required_devices": action_required_devices,
            "devices": devices,
        }
        event_summary = {
            "scanned_at": result["scanned_at"],
            "warning_days": warning_days,
            "include_revoked": include_revoked,
            "total_device_count": len(devices),
            "status_counts": status_counts,
            "renewal_status_counts": renewal_status_counts,
            "action_required_count": len(action_required_devices),
            "renewal_action_required_count": renewal_action_required_count,
            "action_required_devices": action_required_devices,
            "scanned_by": effective_actor.id,
        }
        self.audit(
            action="device_identity.certificates_scanned",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            details=event_summary,
        )
        self.emit_event(
            event_type="device_identity.certificates_scanned",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            payload=event_summary,
        )
        return result

    def _certificate_records_for_fingerprint_update(
        self,
        *,
        existing_identity: DeviceIdentity,
        next_fingerprints: set[str],
        actor_id: str,
        source: str,
    ) -> list[DeviceCertificateRecord]:
        now = utc_now()
        records: list[DeviceCertificateRecord] = []
        known_fingerprints: set[str] = set()
        for record in existing_identity.certificate_records:
            known_fingerprints.add(record.fingerprint)
            if record.fingerprint not in next_fingerprints and record.removed_at is None:
                records.append(
                    record.model_copy(
                        update={"removed_at": now, "removed_by": actor_id}
                    )
                )
            elif (
                record.fingerprint in next_fingerprints
                and record.removed_at is not None
            ):
                records.append(
                    record.model_copy(
                        update={
                            "source": source,
                            "issued_by": actor_id,
                            "issued_at": now,
                            "removed_by": None,
                            "removed_at": None,
                        }
                    )
                )
            else:
                records.append(record)
        removed_without_records = (
            set(existing_identity.certificate_fingerprints)
            .difference(next_fingerprints)
            .difference(known_fingerprints)
        )
        for fingerprint in sorted(removed_without_records):
            records.append(
                DeviceCertificateRecord(
                    fingerprint=fingerprint,
                    source="fingerprint_import",
                    issued_by=actor_id,
                    issued_at=now,
                    removed_by=actor_id,
                    removed_at=now,
                )
            )
        for fingerprint in sorted(next_fingerprints.difference(known_fingerprints)):
            record_source = (
                "fingerprint_import"
                if fingerprint in existing_identity.certificate_fingerprints
                else source
            )
            records.append(
                DeviceCertificateRecord(
                    fingerprint=fingerprint,
                    source=record_source,
                    issued_by=actor_id,
                    issued_at=now,
                )
            )
        return records

    def _certificate_records_for_issued_certificate(
        self,
        *,
        existing_identity: DeviceIdentity,
        issued_certificate: IssuedDeviceCertificate,
        actor_id: str,
    ) -> list[DeviceCertificateRecord]:
        now = utc_now()
        issued_record = DeviceCertificateRecord(
            fingerprint=issued_certificate.certificate_fingerprint,
            source="managed_ca",
            serial_number=issued_certificate.serial_number,
            subject=issued_certificate.subject,
            issuer=issued_certificate.issuer,
            not_before=issued_certificate.not_before,
            not_after=issued_certificate.not_after,
            issued_by=actor_id,
            issued_at=now,
        )
        records: list[DeviceCertificateRecord] = []
        replaced = False
        known_fingerprints: set[str] = set()
        for record in existing_identity.certificate_records:
            known_fingerprints.add(record.fingerprint)
            if record.fingerprint == issued_record.fingerprint:
                records.append(issued_record)
                replaced = True
            else:
                records.append(record)
        missing_existing_fingerprints = (
            set(existing_identity.certificate_fingerprints)
            .difference(known_fingerprints)
            .difference({issued_record.fingerprint})
        )
        for fingerprint in sorted(missing_existing_fingerprints):
            records.append(
                DeviceCertificateRecord(
                    fingerprint=fingerprint,
                    source="fingerprint_import",
                    issued_by=actor_id,
                    issued_at=now,
                )
            )
        if not replaced:
            records.append(issued_record)
        return records

    @staticmethod
    def _active_managed_ca_certificate_fingerprints(
        identity: DeviceIdentity,
    ) -> set[str]:
        active_fingerprints = set(identity.certificate_fingerprints)
        return {
            record.fingerprint
            for record in identity.certificate_records
            if record.source == "managed_ca"
            and record.removed_at is None
            and record.fingerprint in active_fingerprints
        }

    def _certificate_records_for_renewed_certificate(
        self,
        *,
        existing_identity: DeviceIdentity,
        issued_certificate: IssuedDeviceCertificate,
        actor_id: str,
        replaced_fingerprints: set[str],
    ) -> list[DeviceCertificateRecord]:
        now = utc_now()
        issued_record = DeviceCertificateRecord(
            fingerprint=issued_certificate.certificate_fingerprint,
            source="managed_ca",
            serial_number=issued_certificate.serial_number,
            subject=issued_certificate.subject,
            issuer=issued_certificate.issuer,
            not_before=issued_certificate.not_before,
            not_after=issued_certificate.not_after,
            issued_by=actor_id,
            issued_at=now,
        )
        records: list[DeviceCertificateRecord] = []
        replaced = False
        for record in existing_identity.certificate_records:
            if record.fingerprint in replaced_fingerprints and record.removed_at is None:
                records.append(
                    record.model_copy(
                        update={"removed_by": actor_id, "removed_at": now}
                    )
                )
            elif record.fingerprint == issued_record.fingerprint:
                records.append(issued_record)
                replaced = True
            else:
                records.append(record)
        if not replaced:
            records.append(issued_record)
        return records

    def revoke_device_identity(
        self,
        *,
        actor: Actor,
        device_id: str,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> DeviceIdentity:
        effective_actor = self.effective_actor(actor, chat_context_id)
        normalized_device_id = self._validated_device_id(device_id)
        self.require_collection_permission(
            effective_actor,
            Permission.DEVICE_MANAGE,
            resource_type="device_identity",
            resource_id=normalized_device_id,
            attributes={
                "operation": "revoke_device_identity",
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        identity = self.repository.revoke_device_identity(normalized_device_id)
        self.audit(
            action="device_identity.revoked",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
            details={
                "device_identity_id": identity.id,
                "device_id": identity.device_id,
                "status": identity.status.value,
            },
        )
        self.emit_event(
            event_type="device_identity.revoked",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            payload={
                "device_identity_id": identity.id,
                "device_id": identity.device_id,
                "status": identity.status.value,
                "revoked_by": effective_actor.id,
            },
        )
        return identity

    def get_approval_policy_state(
        self,
        *,
        actor: Actor,
        scope_type: PolicyScope,
        scope_id: str,
        chat_context_id: str | None = None,
    ) -> dict[str, object]:
        effective_actor = self.effective_actor(
            actor,
            chat_context_id or (scope_id if scope_type == PolicyScope.CHAT_CONTEXT else None),
        )
        self.require_collection_permission(
            effective_actor,
            Permission.POLICY_MANAGE,
            resource_type="approval_policy",
            resource_id=scope_id,
            attributes={
                "operation": "get_approval_policy",
                "scope_type": scope_type.value,
                **self._chat_policy_attributes(
                    chat_context_id
                    or (scope_id if scope_type == PolicyScope.CHAT_CONTEXT else None)
                ),
            },
        )
        override = self.repository.get_approval_policy_override(
            scope_type=scope_type,
            scope_id=scope_id,
        )
        policy, applied_overrides = self._effective_approval_policy_for_scope(
            scope_type=scope_type,
            scope_id=scope_id,
        )
        return {
            "scope_type": scope_type.value,
            "scope_id": scope_id,
            "override": override.model_dump(mode="json") if override else None,
            "effective_quorum_by_risk": {
                risk_level.value: policy.quorum_for(risk_level) for risk_level in RiskLevel
            },
            "applied_overrides": [
                item.model_dump(mode="json") for item in applied_overrides
            ],
        }

    def set_approval_policy_override(
        self,
        *,
        actor: Actor,
        scope_type: PolicyScope,
        scope_id: str,
        quorum_by_risk: dict[RiskLevel, int],
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> ApprovalPolicyOverride:
        effective_actor = self.effective_actor(
            actor,
            chat_context_id or (scope_id if scope_type == PolicyScope.CHAT_CONTEXT else None),
        )
        self.require_collection_permission(
            effective_actor,
            Permission.POLICY_MANAGE,
            resource_type="approval_policy",
            resource_id=scope_id,
            attributes={
                "operation": "set_approval_policy",
                "scope_type": scope_type.value,
                **self._chat_policy_attributes(
                    chat_context_id
                    or (scope_id if scope_type == PolicyScope.CHAT_CONTEXT else None)
                ),
            },
        )
        normalized_quorum = self._validated_quorum_by_risk(quorum_by_risk)
        override = self.repository.upsert_approval_policy_override(
            scope_type=scope_type,
            scope_id=scope_id,
            quorum_by_risk=normalized_quorum,
            updated_by=effective_actor.id,
        )
        project_id = scope_id if scope_type == PolicyScope.PROJECT else None
        self.audit(
            action="approval.policy_updated",
            actor=effective_actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=trace_id,
            chat_context_id=chat_context_id
            or (scope_id if scope_type == PolicyScope.CHAT_CONTEXT else None),
            project_id=project_id,
            details={
                "scope_type": scope_type.value,
                "scope_id": scope_id,
                "quorum_by_risk": {
                    risk.value: quorum for risk, quorum in normalized_quorum.items()
                },
            },
        )
        self.emit_event(
            event_type="approval.policy_updated",
            source=SemanticEventSource.CONTROL_PLANE,
            trace_id=trace_id,
            project_id=project_id,
            payload={
                "scope_type": scope_type.value,
                "scope_id": scope_id,
                "quorum_by_risk": {
                    risk.value: quorum for risk, quorum in normalized_quorum.items()
                },
                "updated_by": effective_actor.id,
            },
        )
        return override

    def update_approval_policy_quorum(
        self,
        *,
        actor: Actor,
        scope_type: PolicyScope,
        scope_id: str,
        risk_level: RiskLevel,
        quorum: int,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> ApprovalPolicyOverride:
        existing = self.repository.get_approval_policy_override(
            scope_type=scope_type,
            scope_id=scope_id,
        )
        quorum_by_risk = dict(existing.quorum_by_risk) if existing else {}
        quorum_by_risk[risk_level] = quorum
        return self.set_approval_policy_override(
            actor=actor,
            scope_type=scope_type,
            scope_id=scope_id,
            quorum_by_risk=quorum_by_risk,
            trace_id=trace_id,
            chat_context_id=chat_context_id,
        )

    def _effective_approval_policy(
        self,
        *,
        project_id: str,
        chat_context_id: str | None,
    ) -> tuple[ApprovalPolicy, list[ApprovalPolicyOverride]]:
        quorum_by_risk = dict(self.approval_policy.quorum_by_risk)
        applied_overrides: list[ApprovalPolicyOverride] = []
        project_override = self.repository.get_approval_policy_override(
            scope_type=PolicyScope.PROJECT,
            scope_id=project_id,
        )
        if project_override:
            quorum_by_risk.update(project_override.quorum_by_risk)
            applied_overrides.append(project_override)
        if chat_context_id:
            chat_override = self.repository.get_approval_policy_override(
                scope_type=PolicyScope.CHAT_CONTEXT,
                scope_id=chat_context_id,
            )
            if chat_override:
                quorum_by_risk.update(chat_override.quorum_by_risk)
                applied_overrides.append(chat_override)
        return ApprovalPolicy(quorum_by_risk=quorum_by_risk), applied_overrides

    def _effective_approval_policy_for_scope(
        self,
        *,
        scope_type: PolicyScope,
        scope_id: str,
    ) -> tuple[ApprovalPolicy, list[ApprovalPolicyOverride]]:
        if scope_type == PolicyScope.PROJECT:
            return self._effective_approval_policy(project_id=scope_id, chat_context_id=None)
        context = self.repository.get_chat_context(scope_id)
        project_id = context.active_project_id
        if project_id is None:
            quorum_by_risk = dict(self.approval_policy.quorum_by_risk)
            override = self.repository.get_approval_policy_override(
                scope_type=PolicyScope.CHAT_CONTEXT,
                scope_id=scope_id,
            )
            applied = []
            if override:
                quorum_by_risk.update(override.quorum_by_risk)
                applied.append(override)
            return ApprovalPolicy(quorum_by_risk=quorum_by_risk), applied
        return self._effective_approval_policy(
            project_id=project_id,
            chat_context_id=scope_id,
        )

    def _validated_quorum_by_risk(
        self, quorum_by_risk: dict[RiskLevel, int]
    ) -> dict[RiskLevel, int]:
        normalized: dict[RiskLevel, int] = {}
        for risk_level, quorum in quorum_by_risk.items():
            risk = RiskLevel(risk_level)
            if int(quorum) < 1:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "审批 quorum 必须大于等于 1。",
                    next_step="请提供至少 1 个所需票数。",
                )
            normalized[risk] = int(quorum)
        if not normalized:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "审批策略不能为空。",
                next_step="请至少设置一个风险等级的 quorum。",
            )
        return normalized

    def _validated_group_roles(self, roles: set[str]) -> set[str]:
        normalized = {role.strip() for role in roles if role.strip()}
        if not normalized:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "角色集合不能为空。",
                next_step="请提供至少一个角色，例如 operator。",
            )
        unknown = sorted(normalized.difference(ROLE_PERMISSIONS))
        if unknown:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                f"未知角色：{', '.join(unknown)}。",
                next_step=(
                    "请使用 member、operator、approver、dangerous_approver、"
                    "maintainer 或 admin。"
                ),
                details={"allowed_roles": sorted(ROLE_PERMISSIONS)},
            )
        return normalized

    def _validated_device_id(self, device_id: str) -> str:
        normalized = device_id.strip()
        if not normalized:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备 ID 不能为空。",
                next_step="请提供稳定的 device_id，例如 macbook-pro。",
            )
        return normalized

    def _validated_device_scopes(
        self,
        scopes: set[DeviceIdentityScope] | None,
    ) -> set[DeviceIdentityScope] | None:
        if scopes is None:
            return None
        normalized = set(scopes)
        if not normalized:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备授权 scope 不能为空。",
                next_step=(
                    "请至少选择一个 device identity scope，"
                    "或省略 allowed_scopes 使用默认值。"
                ),
                details={
                    "allowed_scopes": sorted(scope.value for scope in DeviceIdentityScope)
                },
            )
        return normalized

    def _validated_device_resource_ids(
        self,
        resource_ids: set[str] | None,
    ) -> set[str] | None:
        if resource_ids is None:
            return None
        return {resource_id for value in resource_ids if (resource_id := value.strip())}

    def _validated_certificate_fingerprints(
        self,
        fingerprints: set[str] | None,
    ) -> set[str] | None:
        if fingerprints is None:
            return None
        return {
            fingerprint
            for value in fingerprints
            if (fingerprint := normalize_certificate_fingerprint(value))
        }

    def _project_policy_attributes(
        self,
        project: Project | None,
        attributes: dict[str, object] | None = None,
    ) -> dict[str, object]:
        policy_attributes: dict[str, object] = {}
        if project:
            policy_attributes.update(
                {
                    "project_id": project.id,
                    "project_slug": project.slug,
                    "project_status": project.status.value,
                    "default_agent": project.default_agent.value,
                    "max_active_sessions": project.max_active_sessions,
                    "max_running_turns": project.max_running_turns,
                    "max_queued_turns": project.max_queued_turns,
                    "daily_turns_per_user": project.daily_turns_per_user,
                    "created_by": project.created_by,
                }
            )
        policy_attributes.update(attributes or {})
        return policy_attributes

    def _session_policy_attributes(
        self,
        session: AgentSession,
        *,
        chat_context_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> dict[str, object]:
        policy_attributes: dict[str, object] = {
            "session_id": session.id,
            "project_id": session.project_id,
            "workspace_id": session.workspace_id,
            "agent_type": session.agent_type.value,
            "visibility": session.visibility.value,
            "session_status": session.status.value,
            "created_by": session.created_by,
            **self._chat_policy_attributes(chat_context_id),
        }
        policy_attributes.update(attributes or {})
        return policy_attributes

    def _chat_policy_attributes(self, chat_context_id: str | None) -> dict[str, object]:
        if not chat_context_id:
            return {}
        return {"chat_context_id": chat_context_id}

    def _validated_policy_pattern(self, value: str, field_name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                f"访问策略 {field_name} 不能为空。",
                next_step="请提供权限动作或资源类型，或使用 * 表示通配。",
            )
        return normalized

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


def interaction_request_event_type(interaction_type: InteractionType) -> str:
    if interaction_type == InteractionType.APPROVAL:
        return "approval.requested"
    if interaction_type == InteractionType.QUESTION:
        return "question.requested"
    if interaction_type == InteractionType.PLAN:
        return "plan.requested"
    return "interaction.requested"


def interaction_type_from_adapter_event(event_type: str) -> InteractionType:
    if event_type == "approval.requested":
        return InteractionType.APPROVAL
    if event_type == "question.requested":
        return InteractionType.QUESTION
    if event_type == "plan.requested":
        return InteractionType.PLAN
    raise AgentBridgeError(
        ErrorCode.COMMAND_ARGUMENT_INVALID,
        "该 Adapter 事件不能创建 Interaction。",
        next_step=(
            "请只为 approval.requested、question.requested 或 plan.requested "
            "创建交互。"
        ),
        details={"event_type": event_type},
    )


def risk_level_from_payload(value: object) -> RiskLevel:
    if value is None:
        return RiskLevel.MEDIUM
    try:
        return RiskLevel(str(value))
    except ValueError as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "Adapter interaction risk_level 无效。",
            next_step="请使用 low、medium、high 或 critical。",
            details={"risk_level": value},
        ) from exc
