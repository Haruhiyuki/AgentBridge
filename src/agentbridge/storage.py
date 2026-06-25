from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from threading import RLock
from uuid import uuid4

from agentbridge.domain import (
    Actor,
    AgentBridgeError,
    AgentSession,
    AgentType,
    AuditEvent,
    AuditOutcome,
    ChatContext,
    CommandResult,
    ErrorCode,
    Interaction,
    InteractionStatus,
    InteractionType,
    LeaseOwnerType,
    Project,
    ProjectBinding,
    ProjectStatus,
    SessionStatus,
    Turn,
    Visibility,
    Workspace,
    WorkspaceType,
    WriterLease,
    utc_now,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def slugify(value: str, fallback_prefix: str = "project") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if slug:
        return slug
    return f"{fallback_prefix}-{uuid4().hex[:6]}"


def normalize_lookup(value: str) -> str:
    return value.strip().lower()


def is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


class InMemoryRepository:
    """Thread-safe in-memory repository for MVP contract tests and local prototypes."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.projects: dict[str, Project] = {}
        self.workspaces: dict[str, Workspace] = {}
        self.bindings: dict[str, ProjectBinding] = {}
        self.chat_contexts: dict[str, ChatContext] = {}
        self.sessions: dict[str, AgentSession] = {}
        self.turns: dict[str, Turn] = {}
        self.interactions: dict[str, Interaction] = {}
        self.leases: dict[str, WriterLease] = {}
        self.lease_epochs: dict[str, int] = {}
        self.audit_events: list[AuditEvent] = []
        self.command_results: dict[str, CommandResult] = {}
        self._short_codes: set[str] = set()
        self._chat_context_index: dict[tuple[str, str, str, str | None, str | None], str] = {}

    def create_project(
        self,
        *,
        name: str,
        actor: Actor,
        slug: str | None = None,
        aliases: list[str] | None = None,
        description: str | None = None,
        default_agent: AgentType = AgentType.CLAUDE,
    ) -> Project:
        with self._lock:
            project_id = new_id("prj")
            normalized_slug = slugify(slug or name)
            if any(project.slug == normalized_slug for project in self.projects.values()):
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    f"项目 slug 已存在：{normalized_slug}",
                    next_step="请使用不同的项目 slug。",
                    status_code=409,
                )
            project = Project(
                id=project_id,
                name=name.strip(),
                slug=normalized_slug,
                aliases=[alias.strip() for alias in aliases or [] if alias.strip()],
                description=description,
                default_agent=default_agent,
                created_by=actor.id,
            )
            self.projects[project.id] = project
            return project

    def list_projects(self) -> list[Project]:
        with self._lock:
            return sorted(self.projects.values(), key=lambda project: project.created_at)

    def get_project(self, project_id: str) -> Project:
        with self._lock:
            project = self.projects.get(project_id)
            if not project:
                raise AgentBridgeError(
                    ErrorCode.NOT_FOUND,
                    f"项目不存在：{project_id}",
                    next_step="请执行 /agent project list 查看可用项目。",
                    status_code=404,
                )
            return project

    def resolve_project(self, token: str, chat_context_id: str | None = None) -> Project:
        lookup = normalize_lookup(token)
        with self._lock:
            matches: list[Project] = []
            for project in self.projects.values():
                if lookup in {
                    normalize_lookup(project.id),
                    normalize_lookup(project.slug),
                    normalize_lookup(project.name),
                }:
                    matches.append(project)
                    continue
                if lookup in {normalize_lookup(alias) for alias in project.aliases}:
                    matches.append(project)
            if chat_context_id:
                for binding in self.bindings.values():
                    if binding.chat_context_id != chat_context_id or not binding.alias_in_chat:
                        continue
                    if normalize_lookup(binding.alias_in_chat) == lookup:
                        matches.append(self.get_project(binding.project_id))

            unique = {project.id: project for project in matches}
            if len(unique) == 1:
                return next(iter(unique.values()))
            if len(unique) > 1:
                raise AgentBridgeError(
                    ErrorCode.TARGET_PROJECT_AMBIGUOUS,
                    f"项目标识不唯一：{token}",
                    next_step="请使用项目 ID 或更明确的别名。",
                    details={"candidates": [project.id for project in unique.values()]},
                )
            raise AgentBridgeError(
                ErrorCode.NOT_FOUND,
                f"未找到项目：{token}",
                next_step="请执行 /agent project list 查看可用项目。",
                status_code=404,
            )

    def add_workspace(
        self,
        *,
        project_id: str,
        machine_id: str,
        path: str,
        allowed_root: str,
        workspace_type: WorkspaceType = WorkspaceType.SHARED,
        is_writable: bool = True,
        max_write_sessions: int = 1,
    ) -> Workspace:
        with self._lock:
            self.get_project(project_id)
            resolved_path = Path(path).expanduser().resolve(strict=False)
            resolved_root = Path(allowed_root).expanduser().resolve(strict=False)
            if not is_within(resolved_path, resolved_root):
                raise AgentBridgeError(
                    ErrorCode.WORKSPACE_PATH_DENIED,
                    f"工作目录不在允许根目录内：{resolved_path}",
                    next_step="请登记 allowed_root 内的项目路径。",
                    status_code=403,
                    details={"path": str(resolved_path), "allowed_root": str(resolved_root)},
                )
            workspace = Workspace(
                id=new_id("wsp"),
                project_id=project_id,
                machine_id=machine_id,
                path=str(resolved_path),
                allowed_root=str(resolved_root),
                type=workspace_type,
                is_writable=is_writable,
                max_write_sessions=max_write_sessions,
            )
            self.workspaces[workspace.id] = workspace
            return workspace

    def list_workspaces(self, project_id: str) -> list[Workspace]:
        with self._lock:
            return [
                workspace
                for workspace in self.workspaces.values()
                if workspace.project_id == project_id
            ]

    def get_workspace(self, workspace_id: str) -> Workspace:
        with self._lock:
            workspace = self.workspaces.get(workspace_id)
            if not workspace:
                raise AgentBridgeError(
                    ErrorCode.NOT_FOUND,
                    f"工作区不存在：{workspace_id}",
                    next_step="请先为项目登记可用 Workspace。",
                    status_code=404,
                )
            return workspace

    def get_or_create_chat_context(
        self,
        *,
        bot_instance_id: str,
        platform: str,
        chat_space_id: str,
        thread_id: str | None = None,
        user_id: str | None = None,
    ) -> ChatContext:
        key = (bot_instance_id, platform, chat_space_id, thread_id, user_id)
        with self._lock:
            context_id = self._chat_context_index.get(key)
            if context_id:
                return self.chat_contexts[context_id]
            context = ChatContext(
                id=new_id("ctx"),
                bot_instance_id=bot_instance_id,
                platform=platform,
                chat_space_id=chat_space_id,
                thread_id=thread_id,
                user_id=user_id,
            )
            self.chat_contexts[context.id] = context
            self._chat_context_index[key] = context.id
            return context

    def get_chat_context(self, chat_context_id: str) -> ChatContext:
        with self._lock:
            context = self.chat_contexts.get(chat_context_id)
            if not context:
                raise AgentBridgeError(
                    ErrorCode.NOT_FOUND,
                    f"聊天上下文不存在：{chat_context_id}",
                    next_step="请重新发送命令或让 Bot Gateway 创建上下文。",
                    status_code=404,
                )
            return context

    def bind_project(
        self,
        *,
        chat_context_id: str,
        project_id: str,
        alias_in_chat: str | None = None,
        is_default: bool = False,
    ) -> ProjectBinding:
        with self._lock:
            self.get_chat_context(chat_context_id)
            self.get_project(project_id)
            for binding in self.bindings.values():
                if binding.chat_context_id == chat_context_id and binding.project_id == project_id:
                    return binding
            if is_default:
                for binding_id, binding in list(self.bindings.items()):
                    if binding.chat_context_id == chat_context_id and binding.is_default:
                        self.bindings[binding_id] = binding.model_copy(update={"is_default": False})
            binding = ProjectBinding(
                id=new_id("pbind"),
                chat_context_id=chat_context_id,
                project_id=project_id,
                alias_in_chat=alias_in_chat,
                is_default=is_default,
            )
            self.bindings[binding.id] = binding
            if is_default:
                self.update_active_project(chat_context_id, project_id, expected_version=None)
            return binding

    def update_active_project(
        self, chat_context_id: str, project_id: str, expected_version: int | None
    ) -> ChatContext:
        with self._lock:
            context = self.get_chat_context(chat_context_id)
            self.get_project(project_id)
            if expected_version is not None and expected_version != context.pointer_version:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    "活动项目指针版本冲突。",
                    next_step="请刷新当前上下文后重试。",
                    status_code=409,
                    details={
                        "expected_version": expected_version,
                        "current_version": context.pointer_version,
                    },
                )
            updated = context.model_copy(
                update={
                    "active_project_id": project_id,
                    "active_session_id": None,
                    "pointer_version": context.pointer_version + 1,
                }
            )
            self.chat_contexts[chat_context_id] = updated
            return updated

    def update_active_session(
        self, chat_context_id: str, session_id: str, expected_version: int | None
    ) -> ChatContext:
        with self._lock:
            context = self.get_chat_context(chat_context_id)
            session = self.get_session(session_id)
            if expected_version is not None and expected_version != context.pointer_version:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    "活动会话指针版本冲突。",
                    next_step="请刷新当前上下文后重试。",
                    status_code=409,
                    details={
                        "expected_version": expected_version,
                        "current_version": context.pointer_version,
                    },
                )
            updated = context.model_copy(
                update={
                    "active_project_id": session.project_id,
                    "active_session_id": session_id,
                    "pointer_version": context.pointer_version + 1,
                }
            )
            self.chat_contexts[chat_context_id] = updated
            return updated

    def create_session(
        self,
        *,
        project_id: str,
        workspace_id: str | None,
        name: str,
        agent_type: AgentType,
        visibility: Visibility,
        actor: Actor,
    ) -> AgentSession:
        with self._lock:
            project = self.get_project(project_id)
            if project.status != ProjectStatus.ACTIVE:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    f"项目当前不可创建会话：{project.status.value}",
                    next_step="请恢复项目或选择其他项目。",
                    status_code=409,
                )
            workspace = (
                self.get_workspace(workspace_id)
                if workspace_id
                else self._select_default_workspace(project_id)
            )
            if workspace.project_id != project_id:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    "Workspace 不属于目标项目。",
                    next_step="请使用该项目下的 Workspace。",
                    status_code=409,
                )
            short_code = self._next_short_code()
            session = AgentSession(
                id=new_id("ses"),
                short_code=short_code,
                name=name.strip() or f"Session {short_code}",
                project_id=project_id,
                workspace_id=workspace.id,
                agent_type=agent_type,
                visibility=visibility,
                created_by=actor.id,
            )
            self.sessions[session.id] = session
            self.lease_epochs[session.id] = 0
            return session

    def _select_default_workspace(self, project_id: str) -> Workspace:
        candidates = self.list_workspaces(project_id)
        if not candidates:
            raise AgentBridgeError(
                ErrorCode.TARGET_SESSION_REQUIRED,
                "项目还没有可用 Workspace，无法创建会话。",
                next_step="请先登记项目 Workspace。",
                status_code=409,
            )
        return candidates[0]

    def _next_short_code(self) -> str:
        for _ in range(100):
            code = uuid4().hex[:4].upper()
            if code not in self._short_codes:
                self._short_codes.add(code)
                return code
        raise AgentBridgeError(
            ErrorCode.RESOURCE_CONFLICT,
            "无法生成唯一会话短码。",
            next_step="请稍后重试。",
            status_code=409,
        )

    def list_sessions(self, project_id: str | None = None) -> list[AgentSession]:
        with self._lock:
            sessions = list(self.sessions.values())
            if project_id:
                sessions = [session for session in sessions if session.project_id == project_id]
            return sorted(sessions, key=lambda session: session.created_at)

    def get_session(self, session_id: str) -> AgentSession:
        with self._lock:
            session = self.sessions.get(session_id)
            if not session:
                raise AgentBridgeError(
                    ErrorCode.NOT_FOUND,
                    f"会话不存在：{session_id}",
                    next_step="请执行 /agent session list 查看可用会话。",
                    status_code=404,
                )
            return session

    def resolve_session(self, token: str, project_id: str | None = None) -> AgentSession:
        lookup = normalize_lookup(token)
        with self._lock:
            matches = [
                session
                for session in self.sessions.values()
                if lookup in {normalize_lookup(session.id), normalize_lookup(session.short_code)}
            ]
            if project_id:
                matches = [session for session in matches if session.project_id == project_id]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise AgentBridgeError(
                    ErrorCode.TARGET_SESSION_AMBIGUOUS,
                    f"会话标识不唯一：{token}",
                    next_step="请使用完整会话 ID。",
                    details={"candidates": [session.id for session in matches]},
                )
            raise AgentBridgeError(
                ErrorCode.NOT_FOUND,
                f"未找到会话：{token}",
                next_step="请执行 /agent session list 查看可用会话。",
                status_code=404,
            )

    def close_session(self, session_id: str) -> AgentSession:
        with self._lock:
            session = self.get_session(session_id)
            if session.status in {SessionStatus.CLOSED, SessionStatus.ARCHIVED}:
                return session
            updated = session.model_copy(
                update={"status": SessionStatus.CLOSED, "updated_at": utc_now()}
            )
            self.sessions[session_id] = updated
            self.leases.pop(session_id, None)
            return updated

    def enqueue_turn(self, *, session_id: str, prompt: str, actor: Actor) -> Turn:
        with self._lock:
            session = self.get_session(session_id)
            if session.status in {
                SessionStatus.CLOSED,
                SessionStatus.ARCHIVED,
                SessionStatus.ERROR,
            }:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    f"会话状态不接受新任务：{session.status.value}",
                    next_step="请选择其他会话或恢复该会话。",
                    status_code=409,
                )
            if not prompt.strip():
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "任务文本不能为空。",
                    next_step="请提供要发送给 Agent 的任务内容。",
                )
            turn = Turn(
                id=new_id("turn"),
                session_id=session_id,
                prompt=prompt.strip(),
                actor_id=actor.id,
            )
            self.turns[turn.id] = turn
            return turn

    def list_turns(self, session_id: str) -> list[Turn]:
        with self._lock:
            return [turn for turn in self.turns.values() if turn.session_id == session_id]

    def acquire_lease(
        self,
        *,
        session_id: str,
        owner_type: LeaseOwnerType,
        owner_id: str,
        ttl_seconds: int = 300,
    ) -> WriterLease:
        with self._lock:
            session = self.get_session(session_id)
            if session.status in {SessionStatus.CLOSED, SessionStatus.ARCHIVED}:
                raise AgentBridgeError(
                    ErrorCode.LEASE_CONFLICT,
                    "已关闭或归档的会话不能获取写入租约。",
                    next_step="请恢复会话或创建新会话。",
                    status_code=409,
                )
            current = self.leases.get(session_id)
            if current and current.is_active():
                if current.owner_type == owner_type and current.owner_id == owner_id:
                    renewed = WriterLease.issue(
                        session_id=session_id,
                        owner_type=owner_type,
                        owner_id=owner_id,
                        epoch=current.epoch,
                        ttl_seconds=ttl_seconds,
                    )
                    self.leases[session_id] = renewed
                    return renewed
                if self._lease_priority(owner_type) <= self._lease_priority(current.owner_type):
                    raise AgentBridgeError(
                        ErrorCode.LEASE_CONFLICT,
                        "当前已有更高或同级写入者持有租约。",
                        next_step="等待当前写入者释放，或由具备权限的本地用户抢占。",
                        status_code=409,
                        details={
                            "current_owner_type": current.owner_type.value,
                            "current_epoch": current.epoch,
                        },
                    )
            epoch = self.lease_epochs.get(session_id, 0) + 1
            lease = WriterLease.issue(
                session_id=session_id,
                owner_type=owner_type,
                owner_id=owner_id,
                epoch=epoch,
                ttl_seconds=ttl_seconds,
            )
            self.lease_epochs[session_id] = epoch
            self.leases[session_id] = lease
            if owner_type == LeaseOwnerType.HUMAN:
                self.sessions[session.id] = session.model_copy(
                    update={"status": SessionStatus.HUMAN_CONTROLLED, "updated_at": utc_now()}
                )
            return lease

    @staticmethod
    def _lease_priority(owner_type: LeaseOwnerType) -> int:
        return {
            LeaseOwnerType.BOT: 10,
            LeaseOwnerType.WEB_ADMIN: 20,
            LeaseOwnerType.SYSTEM: 30,
            LeaseOwnerType.HUMAN: 40,
        }[owner_type]

    def release_lease(self, *, session_id: str, epoch: int) -> int:
        with self._lock:
            current = self.leases.get(session_id)
            if not current:
                raise AgentBridgeError(
                    ErrorCode.LEASE_CONFLICT,
                    "当前没有可释放的写入租约。",
                    next_step="请刷新会话控制状态后重试。",
                    status_code=409,
                )
            if current.epoch != epoch:
                raise AgentBridgeError(
                    ErrorCode.LEASE_CONFLICT,
                    "租约 epoch 不匹配，旧写入者不能释放当前租约。",
                    next_step="请刷新会话控制状态后重试。",
                    status_code=409,
                    details={"current_epoch": current.epoch, "provided_epoch": epoch},
                )
            next_epoch = current.epoch + 1
            self.lease_epochs[session_id] = next_epoch
            self.leases.pop(session_id, None)
            session = self.get_session(session_id)
            if session.status == SessionStatus.HUMAN_CONTROLLED:
                self.sessions[session_id] = session.model_copy(
                    update={"status": SessionStatus.IDLE, "updated_at": utc_now()}
                )
            return next_epoch

    def current_lease(self, session_id: str) -> WriterLease | None:
        with self._lock:
            lease = self.leases.get(session_id)
            if lease and lease.is_active():
                return lease
            return None

    def create_interaction(
        self,
        *,
        session_id: str,
        interaction_type: InteractionType,
        prompt: str,
        turn_id: str | None = None,
        options: list[str] | None = None,
        required_votes: int = 1,
    ) -> Interaction:
        with self._lock:
            self.get_session(session_id)
            interaction = Interaction(
                id=new_id("int"),
                session_id=session_id,
                turn_id=turn_id,
                type=interaction_type,
                prompt=prompt,
                options=options or [],
                required_votes=required_votes,
            )
            self.interactions[interaction.id] = interaction
            return interaction

    def answer_interaction(self, interaction_id: str, answer: str) -> Interaction:
        with self._lock:
            interaction = self._get_pending_interaction(interaction_id)
            updated = interaction.model_copy(
                update={
                    "answer": answer,
                    "status": InteractionStatus.RESOLVED,
                    "resolved_at": utc_now(),
                    "version": interaction.version + 1,
                }
            )
            self.interactions[interaction_id] = updated
            return updated

    def vote_interaction(self, interaction_id: str, actor: Actor, approve: bool) -> Interaction:
        with self._lock:
            interaction = self._get_pending_interaction(interaction_id)
            votes = dict(interaction.votes)
            votes[actor.id] = approve
            approvals = sum(1 for vote in votes.values() if vote)
            status = (
                InteractionStatus.RESOLVED
                if approvals >= interaction.required_votes or not approve
                else InteractionStatus.PARTIALLY_APPROVED
            )
            updated = interaction.model_copy(
                update={
                    "votes": votes,
                    "status": status,
                    "resolved_at": utc_now() if status == InteractionStatus.RESOLVED else None,
                    "version": interaction.version + 1,
                }
            )
            self.interactions[interaction_id] = updated
            return updated

    def _get_pending_interaction(self, interaction_id: str) -> Interaction:
        interaction = self.interactions.get(interaction_id)
        if not interaction:
            raise AgentBridgeError(
                ErrorCode.NOT_FOUND,
                f"Interaction 不存在：{interaction_id}",
                next_step="请检查交互 ID。",
                status_code=404,
            )
        if interaction.expires_at and interaction.expires_at <= utc_now():
            expired = interaction.model_copy(update={"status": InteractionStatus.EXPIRED})
            self.interactions[interaction_id] = expired
            raise AgentBridgeError(
                ErrorCode.INTERACTION_EXPIRED,
                "Interaction 已过期。",
                next_step="请让 Agent 重新发起问题或审批。",
                status_code=409,
            )
        if interaction.status not in {
            InteractionStatus.PENDING,
            InteractionStatus.PARTIALLY_APPROVED,
        }:
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                f"Interaction 当前状态不可处理：{interaction.status.value}",
                next_step="请刷新交互状态。",
                status_code=409,
            )
        return interaction

    def store_command_result(self, key: str, result: CommandResult) -> None:
        with self._lock:
            self.command_results[key] = result

    def get_command_result(self, key: str) -> CommandResult | None:
        with self._lock:
            return self.command_results.get(key)

    def append_audit(
        self,
        *,
        action: str,
        actor_id: str,
        outcome: AuditOutcome,
        trace_id: str,
        chat_context_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        interaction_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> AuditEvent:
        with self._lock:
            previous_hash = self.audit_events[-1].entry_hash if self.audit_events else None
            base = {
                "id": new_id("aud"),
                "action": action,
                "actor_id": actor_id,
                "outcome": outcome.value,
                "trace_id": trace_id,
                "chat_context_id": chat_context_id,
                "project_id": project_id,
                "session_id": session_id,
                "interaction_id": interaction_id,
                "details": details or {},
                "created_at": utc_now().isoformat(),
                "previous_hash": previous_hash,
            }
            canonical = json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            event_data = dict(base)
            event_data["outcome"] = outcome
            event = AuditEvent(
                **event_data,
                entry_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            )
            self.audit_events.append(event)
            return event
