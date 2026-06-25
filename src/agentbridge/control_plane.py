from __future__ import annotations

from datetime import datetime

from agentbridge.device_auth import (
    DEFAULT_DEVICE_KEY_ITERATIONS,
    generate_device_key,
    generate_device_key_salt,
    hash_device_key,
    normalize_certificate_fingerprint,
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
    DeviceIdentity,
    DeviceIdentityScope,
    ErrorCode,
    GroupRoleBinding,
    Interaction,
    InteractionStatus,
    InteractionType,
    LeaseOwnerType,
    PolicyScope,
    Project,
    RiskLevel,
    SemanticEvent,
    SemanticEventSource,
    Turn,
    Visibility,
    Workspace,
    WorkspaceType,
    WriterLease,
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
            payload={"name": project.name, "slug": project.slug},
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
                **self._chat_policy_attributes(chat_context_id),
            },
        )
        workspace = self.repository.add_workspace(
            project_id=project_id,
            machine_id=machine_id,
            path=path,
            allowed_root=allowed_root,
            workspace_type=workspace_type,
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
        context = self.repository.get_chat_context(chat_context_id)
        session = self.repository.resolve_session(session_token, context.active_project_id)
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
        turn = self.repository.enqueue_turn(
            session_id=session_id, prompt=prompt, actor=effective_actor
        )
        self.audit(
            action="turn.queued",
            actor=effective_actor,
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
        return next_epoch

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
        event_type = (
            "approval.requested"
            if interaction_type == InteractionType.APPROVAL
            else "interaction.requested"
        )
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
        certificate_fingerprints: set[str] | None = None,
        trace_id: str,
        chat_context_id: str | None = None,
    ) -> tuple[DeviceIdentity, str]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        normalized_device_id = self._validated_device_id(device_id)
        normalized_scopes = self._validated_device_scopes(allowed_scopes)
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
        secret = device_key.strip() if device_key else generate_device_key()
        if not secret:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备 key 不能为空。",
                next_step="请提供非空 device_key，或省略该字段由服务端生成。",
            )
        salt = generate_device_key_salt()
        identity = self.repository.upsert_device_identity(
            device_id=normalized_device_id,
            display_name=display_name,
            key_hash=hash_device_key(
                secret,
                salt=salt,
                iterations=DEFAULT_DEVICE_KEY_ITERATIONS,
            ),
            key_salt=salt,
            key_iterations=DEFAULT_DEVICE_KEY_ITERATIONS,
            allowed_scopes=normalized_scopes,
            certificate_fingerprints=normalized_fingerprints,
            updated_by=effective_actor.id,
        )
        allowed_scope_values = sorted(scope.value for scope in identity.allowed_scopes)
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
                "certificate_fingerprint_count": certificate_fingerprint_count,
                "updated_by": effective_actor.id,
            },
        )
        return identity, secret

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
                next_step="请至少选择一个 transport scope，或省略 allowed_scopes 使用默认值。",
                details={
                    "allowed_scopes": sorted(scope.value for scope in DeviceIdentityScope)
                },
            )
        return normalized

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
