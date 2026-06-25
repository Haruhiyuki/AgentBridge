from __future__ import annotations

from datetime import datetime

from agentbridge.domain import (
    Actor,
    AgentBridgeError,
    AgentSession,
    AgentType,
    ApprovalPolicyOverride,
    AuditEvent,
    AuditOutcome,
    ChatContext,
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
        self.policy.require(effective_actor, Permission.PROJECT_MANAGE)
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
        self.policy.require(actor, Permission.PROJECT_VIEW)
        return self.repository.list_projects()

    def list_projects_for_context(
        self, actor: Actor, chat_context_id: str | None
    ) -> list[Project]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.policy.require(effective_actor, Permission.PROJECT_VIEW)
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
        self.policy.require(effective_actor, Permission.PROJECT_MANAGE)
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
        self.policy.require(effective_actor, Permission.PROJECT_MANAGE)
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
        self.policy.require(effective_actor, Permission.SESSION_VIEW)
        project = self.repository.resolve_project(project_token, chat_context_id)
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
        self.policy.require(effective_actor, Permission.SESSION_CREATE)
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
        self.policy.require(effective_actor, Permission.SESSION_VIEW)
        context = self.repository.get_chat_context(chat_context_id)
        session = self.repository.resolve_session(session_token, context.active_project_id)
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
        self.policy.require(actor, Permission.SESSION_VIEW)
        return self.repository.list_sessions(project_id)

    def list_sessions_for_context(
        self,
        actor: Actor,
        project_id: str | None = None,
        chat_context_id: str | None = None,
    ) -> list[AgentSession]:
        effective_actor = self.effective_actor(actor, chat_context_id)
        self.policy.require(effective_actor, Permission.SESSION_VIEW)
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
        self.policy.require(effective_actor, Permission.SESSION_SEND)
        turn = self.repository.enqueue_turn(
            session_id=session_id, prompt=prompt, actor=effective_actor
        )
        session = self.repository.get_session(session_id)
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
        self.policy.require(effective_actor, Permission.SESSION_MANAGE)
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
        if owner_type in {LeaseOwnerType.WEB_ADMIN, LeaseOwnerType.HUMAN}:
            effective_actor = self.effective_actor(actor, chat_context_id)
            self.policy.require(effective_actor, Permission.TERMINAL_CONTROL)
        elif owner_type == LeaseOwnerType.BOT:
            effective_actor = self.effective_actor(actor, chat_context_id)
            self.policy.require(effective_actor, Permission.SESSION_SEND)
        else:
            effective_actor = self.effective_actor(actor, chat_context_id)
        lease = self.repository.acquire_lease(
            session_id=session_id,
            owner_type=owner_type,
            owner_id=owner_id,
            ttl_seconds=ttl_seconds,
        )
        session = self.repository.get_session(session_id)
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
        self.policy.require(effective_actor, Permission.TERMINAL_CONTROL)
        next_epoch = self.repository.release_lease(session_id=session_id, epoch=epoch)
        session = self.repository.get_session(session_id)
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
        self.policy.require(effective_actor, Permission.SESSION_SEND)
        if not prompt.strip():
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "Interaction prompt 不能为空。",
                next_step="请提供需要用户处理的问题或审批说明。",
            )
        session = self.repository.get_session(session_id)
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
        self.policy.require(effective_actor, Permission.SESSION_VIEW)
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
        self.policy.require(effective_actor, Permission.SESSION_VIEW)
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
        self.policy.require(effective_actor, Permission.SESSION_SEND)
        self.expire_due_interactions(
            actor=Actor(id="system", roles={"admin"}),
            trace_id=trace_id,
            chat_context_id=chat_context_id,
        )
        current = self.repository.get_interaction(interaction_id)
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
        self.policy.require(effective_actor, Permission.SESSION_MANAGE)
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
        self.policy.require_approval_vote(effective_actor, current.risk_level)
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
        session = self.repository.get_session(interaction.session_id)
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
        self.policy.require(effective_actor, Permission.GROUP_ROLE_MANAGE)
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
        self.policy.require(effective_actor, Permission.GROUP_ROLE_MANAGE)
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
        self.policy.require(effective_actor, Permission.GROUP_ROLE_MANAGE)
        return self.repository.list_group_role_bindings(chat_context_id)

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
        self.policy.require(effective_actor, Permission.POLICY_MANAGE)
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
        self.policy.require(effective_actor, Permission.POLICY_MANAGE)
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
