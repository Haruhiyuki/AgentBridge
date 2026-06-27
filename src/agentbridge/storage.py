from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from uuid import uuid4

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
    BotDeliveryRecord,
    BotDeliveryStatus,
    ChatContext,
    CommandResult,
    DeviceCertificateRecord,
    DeviceIdentity,
    DeviceIdentityScope,
    DeviceIdentityStatus,
    ErrorCode,
    EventConsumerOffset,
    GroupRoleBinding,
    Interaction,
    InteractionStatus,
    InteractionType,
    LeaseOwnerType,
    PolicyScope,
    Project,
    ProjectBinding,
    ProjectStatus,
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


def payload_contains_query(payload: object, query: str | None) -> bool:
    normalized_query = normalized_payload_query(query)
    if normalized_query is None:
        return True
    return normalized_query in payload_search_text(payload)


def normalized_payload_query(query: str | None) -> str | None:
    if query is None:
        return None
    normalized_query = query.strip().casefold()
    return normalized_query or None


def payload_search_text(payload: object) -> str:
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    except TypeError:
        serialized = str(payload)
    return serialized.casefold()


_MISSING = object()


def payload_field_path(field: str | None) -> tuple[str, ...] | None:
    if field is None:
        return None
    parts = tuple(part.strip() for part in field.split(".") if part.strip())
    return parts or None


def payload_field_matches(
    payload: object,
    field: str | None,
    expected: str | None,
) -> bool:
    path = payload_field_path(field)
    if path is None:
        return True
    value = payload_field_value(payload, path)
    if value is _MISSING:
        return False
    normalized_expected = normalized_payload_query(expected)
    if normalized_expected is None:
        return True
    return payload_value_search_text(value) == normalized_expected


def payload_field_value(payload: object, path: tuple[str, ...]) -> object:
    value = payload
    for part in path:
        if isinstance(value, dict) and part in value:
            value = value[part]
            continue
        return _MISSING
    return value


def payload_value_search_text(value: object) -> str:
    if isinstance(value, str):
        return value.casefold()
    if value is None or isinstance(value, bool):
        return json.dumps(value).casefold()
    if isinstance(value, (int, float)):
        return str(value).casefold()
    return payload_search_text(value)


def created_at_in_range(
    created_at: datetime,
    *,
    created_from: datetime | None,
    created_to: datetime | None,
) -> bool:
    value = aware_utc(created_at)
    if created_from is not None and value < aware_utc(created_from):
        return False
    if created_to is not None and value > aware_utc(created_to):
        return False
    return True


def utc_datetime_key(value: datetime) -> str:
    return aware_utc(value).isoformat(timespec="microseconds")


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class InMemoryRepository:
    """Thread-safe in-memory repository for MVP contract tests and local prototypes."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.projects: dict[str, Project] = {}
        self.workspaces: dict[str, Workspace] = {}
        self.bindings: dict[str, ProjectBinding] = {}
        self.group_role_bindings: dict[tuple[str, str], GroupRoleBinding] = {}
        self.approval_policy_overrides: dict[
            tuple[PolicyScope, str], ApprovalPolicyOverride
        ] = {}
        self.access_policy_rules: dict[str, AccessPolicyRule] = {}
        self.device_identities: dict[str, DeviceIdentity] = {}
        self.chat_contexts: dict[str, ChatContext] = {}
        self.sessions: dict[str, AgentSession] = {}
        self.turns: dict[str, Turn] = {}
        self.interactions: dict[str, Interaction] = {}
        self.leases: dict[str, WriterLease] = {}
        self.lease_epochs: dict[str, int] = {}
        self.audit_events: list[AuditEvent] = []
        self.semantic_events: list[SemanticEvent] = []
        self.bot_delivery_records: dict[str, BotDeliveryRecord] = {}
        self.event_consumer_offsets: dict[tuple[str, str], EventConsumerOffset] = {}
        self.command_results: dict[str, CommandResult] = {}
        self.event_idempotency: dict[str, SemanticEvent] = {}
        self.event_stream_seq: dict[str, int] = {}
        # 待"立刻追加"到运行中终端的输入（瞬态，由终端服务每拍冲刷，不持久化）。
        self.pending_terminal_inputs: dict[str, list[str]] = {}
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
        max_active_sessions: int = 10,
        max_running_turns: int = 4,
        max_queued_turns: int = 100,
        daily_turns_per_user: int = 50,
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
            if max_active_sessions < 0:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "项目最大活跃会话数不能为负。",
                    next_step="请将 max_active_sessions 设置为 0 或更大的整数。",
                    status_code=400,
                    details={"max_active_sessions": max_active_sessions},
                )
            if max_queued_turns < 0:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "项目最大排队任务数不能为负。",
                    next_step="请将 max_queued_turns 设置为 0 或更大的整数。",
                    status_code=400,
                    details={"max_queued_turns": max_queued_turns},
                )
            if max_running_turns < 0:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "项目最大运行任务数不能为负。",
                    next_step="请将 max_running_turns 设置为 0 或更大的整数。",
                    status_code=400,
                    details={"max_running_turns": max_running_turns},
                )
            if daily_turns_per_user < 0:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "项目每日每用户任务数不能为负。",
                    next_step="请将 daily_turns_per_user 设置为 0 或更大的整数。",
                    status_code=400,
                    details={"daily_turns_per_user": daily_turns_per_user},
                )
            project = Project(
                id=project_id,
                name=name.strip(),
                slug=normalized_slug,
                aliases=[alias.strip() for alias in aliases or [] if alias.strip()],
                description=description,
                default_agent=default_agent,
                max_active_sessions=max_active_sessions,
                max_running_turns=max_running_turns,
                max_queued_turns=max_queued_turns,
                daily_turns_per_user=daily_turns_per_user,
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
            if max_write_sessions < 0:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "Workspace 写入并发上限不能为负。",
                    next_step="请将 max_write_sessions 设置为 0 或更大的整数。",
                    status_code=400,
                    details={"max_write_sessions": max_write_sessions},
                )
            effective_is_writable = is_writable and workspace_type != WorkspaceType.READ_ONLY
            effective_max_write_sessions = (
                max_write_sessions if effective_is_writable else 0
            )
            if effective_is_writable and effective_max_write_sessions < 1:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "可写 Workspace 至少需要 1 个写入会话名额。",
                    next_step="请设置 max_write_sessions >= 1，或将 Workspace 标记为不可写。",
                    status_code=400,
                    details={
                        "is_writable": effective_is_writable,
                        "max_write_sessions": effective_max_write_sessions,
                    },
                )
            workspace = Workspace(
                id=new_id("wsp"),
                project_id=project_id,
                machine_id=machine_id,
                path=str(resolved_path),
                allowed_root=str(resolved_root),
                type=workspace_type,
                is_writable=effective_is_writable,
                max_write_sessions=effective_max_write_sessions,
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

    def grant_group_roles(
        self,
        *,
        chat_context_id: str,
        actor_id: str,
        roles: set[str],
        granted_by: str,
    ) -> GroupRoleBinding:
        with self._lock:
            self.get_chat_context(chat_context_id)
            key = (chat_context_id, actor_id)
            existing = self.group_role_bindings.get(key)
            now = utc_now()
            if existing:
                binding = existing.model_copy(
                    update={
                        "roles": set(existing.roles).union(roles),
                        "granted_by": granted_by,
                        "updated_at": now,
                    }
                )
            else:
                binding = GroupRoleBinding(
                    id=new_id("grb"),
                    chat_context_id=chat_context_id,
                    actor_id=actor_id,
                    roles=set(roles),
                    granted_by=granted_by,
                    created_at=now,
                    updated_at=now,
                )
            self.group_role_bindings[key] = binding
            return binding

    def revoke_group_roles(
        self,
        *,
        chat_context_id: str,
        actor_id: str,
        roles: set[str],
        revoked_by: str,
    ) -> GroupRoleBinding | None:
        with self._lock:
            self.get_chat_context(chat_context_id)
            key = (chat_context_id, actor_id)
            existing = self.group_role_bindings.get(key)
            if not existing:
                return None
            remaining_roles = set(existing.roles).difference(roles)
            if not remaining_roles:
                self.group_role_bindings.pop(key, None)
                return None
            binding = existing.model_copy(
                update={
                    "roles": remaining_roles,
                    "granted_by": revoked_by,
                    "updated_at": utc_now(),
                }
            )
            self.group_role_bindings[key] = binding
            return binding

    def list_group_role_bindings(
        self, chat_context_id: str | None = None
    ) -> list[GroupRoleBinding]:
        with self._lock:
            bindings = list(self.group_role_bindings.values())
            if chat_context_id:
                bindings = [
                    binding for binding in bindings if binding.chat_context_id == chat_context_id
                ]
            return sorted(bindings, key=lambda binding: (binding.chat_context_id, binding.actor_id))

    def effective_actor(self, actor: Actor, chat_context_id: str | None = None) -> Actor:
        with self._lock:
            if not chat_context_id:
                return actor
            binding = self.group_role_bindings.get((chat_context_id, actor.id))
            if not binding:
                return actor
            return actor.model_copy(update={"roles": set(actor.roles).union(binding.roles)})

    def upsert_approval_policy_override(
        self,
        *,
        scope_type: PolicyScope,
        scope_id: str,
        quorum_by_risk: dict[RiskLevel, int],
        updated_by: str,
    ) -> ApprovalPolicyOverride:
        with self._lock:
            self._require_policy_scope(scope_type, scope_id)
            key = (scope_type, scope_id)
            existing = self.approval_policy_overrides.get(key)
            now = utc_now()
            if existing:
                override = existing.model_copy(
                    update={
                        "quorum_by_risk": dict(quorum_by_risk),
                        "updated_by": updated_by,
                        "updated_at": now,
                    }
                )
            else:
                override = ApprovalPolicyOverride(
                    id=new_id("apol"),
                    scope_type=scope_type,
                    scope_id=scope_id,
                    quorum_by_risk=dict(quorum_by_risk),
                    updated_by=updated_by,
                    created_at=now,
                    updated_at=now,
                )
            self.approval_policy_overrides[key] = override
            return override

    def get_approval_policy_override(
        self,
        *,
        scope_type: PolicyScope,
        scope_id: str,
    ) -> ApprovalPolicyOverride | None:
        with self._lock:
            self._require_policy_scope(scope_type, scope_id)
            return self.approval_policy_overrides.get((scope_type, scope_id))

    def list_approval_policy_overrides(
        self,
        *,
        scope_type: PolicyScope | None = None,
        scope_id: str | None = None,
    ) -> list[ApprovalPolicyOverride]:
        with self._lock:
            overrides = list(self.approval_policy_overrides.values())
            if scope_type:
                overrides = [
                    override for override in overrides if override.scope_type == scope_type
                ]
            if scope_id:
                overrides = [override for override in overrides if override.scope_id == scope_id]
            return sorted(
                overrides,
                key=lambda override: (override.scope_type.value, override.scope_id),
            )

    def upsert_access_policy_rule(
        self,
        *,
        rule_id: str | None = None,
        effect: AccessPolicyEffect,
        action: str,
        resource_type: str = "*",
        resource_id: str | None = None,
        actor_ids: list[str] | None = None,
        roles: list[str] | None = None,
        attributes: dict[str, object] | None = None,
        description: str | None = None,
        priority: int = 100,
        enabled: bool = True,
        updated_by: str,
    ) -> AccessPolicyRule:
        with self._lock:
            existing = self.access_policy_rules.get(rule_id) if rule_id else None
            now = utc_now()
            rule = AccessPolicyRule(
                id=rule_id or new_id("arul"),
                effect=effect,
                action=action,
                resource_type=resource_type or "*",
                resource_id=resource_id,
                actor_ids=actor_ids or [],
                roles=roles or [],
                attributes=attributes or {},
                description=description,
                priority=priority,
                enabled=enabled,
                created_by=existing.created_by if existing else updated_by,
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
            self.access_policy_rules[rule.id] = rule
            return rule

    def get_access_policy_rule(self, rule_id: str) -> AccessPolicyRule:
        with self._lock:
            rule = self.access_policy_rules.get(rule_id)
            if not rule:
                raise AgentBridgeError(
                    ErrorCode.NOT_FOUND,
                    f"访问策略规则不存在：{rule_id}",
                    next_step="请先查看访问策略规则列表。",
                    status_code=404,
                )
            return rule

    def delete_access_policy_rule(self, rule_id: str) -> AccessPolicyRule | None:
        with self._lock:
            return self.access_policy_rules.pop(rule_id, None)

    def list_access_policy_rules(
        self, enabled: bool | None = None
    ) -> list[AccessPolicyRule]:
        with self._lock:
            rules = list(self.access_policy_rules.values())
            if enabled is not None:
                rules = [rule for rule in rules if rule.enabled == enabled]
            return sorted(rules, key=lambda rule: (rule.priority, rule.created_at, rule.id))

    def upsert_device_identity(
        self,
        *,
        device_id: str,
        display_name: str | None,
        updated_by: str,
        key_hash: str | None = None,
        key_salt: str | None = None,
        key_iterations: int = 210000,
        allowed_scopes: set[DeviceIdentityScope] | None = None,
        allowed_resource_ids: set[str] | None = None,
        certificate_fingerprints: set[str] | None = None,
        certificate_records: list[DeviceCertificateRecord] | None = None,
    ) -> DeviceIdentity:
        normalized_device_id = device_id.strip()
        if not normalized_device_id:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备 ID 不能为空。",
                next_step="请提供稳定的 device_id，例如 macbook-pro。",
            )
        with self._lock:
            existing = self.device_identities.get(normalized_device_id)
            now = utc_now()
            identity_scopes = (
                set(allowed_scopes)
                if allowed_scopes is not None
                else (
                    set(existing.allowed_scopes)
                    if existing
                    else set(DeviceIdentityScope)
                )
            )
            identity_certificate_fingerprints = (
                set(certificate_fingerprints)
                if certificate_fingerprints is not None
                else (
                    set(existing.certificate_fingerprints)
                    if existing
                    else set()
                )
            )
            identity_resource_ids = (
                set(allowed_resource_ids)
                if allowed_resource_ids is not None
                else (set(existing.allowed_resource_ids) if existing else set())
            )
            identity_certificate_records = self._device_certificate_records_for_update(
                existing=existing,
                fingerprints=identity_certificate_fingerprints,
                updated_by=updated_by,
                certificate_records=certificate_records,
            )
            identity_key_hash = key_hash if key_hash is not None else (
                existing.key_hash if existing else None
            )
            identity_key_salt = key_salt if key_salt is not None else (
                existing.key_salt if existing else None
            )
            identity_key_iterations = (
                key_iterations
                if key_hash is not None
                else (existing.key_iterations if existing else key_iterations)
            )
            if not identity_key_hash and not identity_certificate_fingerprints:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "设备身份至少需要一个 device_key 或客户端证书指纹。",
                    next_step=(
                        "请提供非空 device_key，或配置 certificate_fingerprints。"
                    ),
                )
            identity = DeviceIdentity(
                id=existing.id if existing else new_id("dev"),
                device_id=normalized_device_id,
                display_name=display_name.strip() if display_name else None,
                key_hash=identity_key_hash,
                key_salt=identity_key_salt,
                key_iterations=identity_key_iterations,
                status=DeviceIdentityStatus.ACTIVE,
                allowed_scopes=identity_scopes,
                allowed_resource_ids=identity_resource_ids,
                certificate_fingerprints=identity_certificate_fingerprints,
                certificate_records=identity_certificate_records,
                created_by=existing.created_by if existing else updated_by,
                created_at=existing.created_at if existing else now,
                revoked_at=None,
                last_used_at=existing.last_used_at if existing else None,
            )
            self.device_identities[identity.device_id] = identity
            return identity

    def _device_certificate_records_for_update(
        self,
        *,
        existing: DeviceIdentity | None,
        fingerprints: set[str],
        updated_by: str,
        certificate_records: list[DeviceCertificateRecord] | None,
    ) -> list[DeviceCertificateRecord]:
        if certificate_records is not None:
            return list(certificate_records)
        now = utc_now()
        records = list(existing.certificate_records) if existing else []
        known_fingerprints = {record.fingerprint for record in records}
        updated_records: list[DeviceCertificateRecord] = []
        for record in records:
            if record.fingerprint not in fingerprints and record.removed_at is None:
                updated_records.append(
                    record.model_copy(
                        update={"removed_at": now, "removed_by": updated_by}
                    )
                )
            elif record.fingerprint in fingerprints and record.removed_at is not None:
                updated_records.append(
                    record.model_copy(
                        update={
                            "removed_at": None,
                            "removed_by": None,
                            "source": "fingerprint_import",
                            "issued_by": updated_by,
                            "issued_at": now,
                        }
                    )
                )
            else:
                updated_records.append(record)
        for fingerprint in sorted(fingerprints.difference(known_fingerprints)):
            updated_records.append(
                DeviceCertificateRecord(
                    fingerprint=fingerprint,
                    source="fingerprint_import",
                    issued_by=updated_by,
                    issued_at=now,
                )
            )
        return updated_records

    def get_device_identity(self, device_id: str) -> DeviceIdentity:
        with self._lock:
            identity = self.device_identities.get(device_id.strip())
            if not identity:
                raise AgentBridgeError(
                    ErrorCode.NOT_FOUND,
                    f"设备身份不存在：{device_id}",
                    next_step="请先创建设备身份，或检查 device_id。",
                    status_code=404,
                )
            return identity

    def list_device_identities(
        self,
        *,
        include_revoked: bool = False,
    ) -> list[DeviceIdentity]:
        with self._lock:
            identities = list(self.device_identities.values())
            if not include_revoked:
                identities = [
                    identity
                    for identity in identities
                    if identity.status == DeviceIdentityStatus.ACTIVE
                ]
            return sorted(identities, key=lambda identity: identity.created_at)

    def revoke_device_identity(self, device_id: str) -> DeviceIdentity:
        with self._lock:
            identity = self.get_device_identity(device_id)
            if identity.status == DeviceIdentityStatus.REVOKED:
                return identity
            revoked = identity.model_copy(
                update={
                    "status": DeviceIdentityStatus.REVOKED,
                    "revoked_at": utc_now(),
                }
            )
            self.device_identities[revoked.device_id] = revoked
            return revoked

    def mark_device_identity_used(
        self,
        device_id: str,
        *,
        used_at: datetime | None = None,
    ) -> DeviceIdentity:
        with self._lock:
            identity = self.get_device_identity(device_id)
            updated = identity.model_copy(
                update={"last_used_at": used_at or utc_now()}
            )
            self.device_identities[updated.device_id] = updated
            return updated

    def _require_policy_scope(self, scope_type: PolicyScope, scope_id: str) -> None:
        if scope_type == PolicyScope.PROJECT:
            self.get_project(scope_id)
            return
        if scope_type == PolicyScope.CHAT_CONTEXT:
            self.get_chat_context(scope_id)
            return
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            f"不支持的策略作用域：{scope_type.value}",
            next_step="请使用 project 或 chat_context 策略作用域。",
        )

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
            normalized_alias = alias_in_chat.strip() if alias_in_chat else None
            for binding_id, binding in list(self.bindings.items()):
                if binding.chat_context_id == chat_context_id and binding.project_id == project_id:
                    updates: dict[str, object] = {}
                    if alias_in_chat is not None:
                        updates["alias_in_chat"] = normalized_alias
                    if is_default:
                        for other_id, other in list(self.bindings.items()):
                            if (
                                other.chat_context_id == chat_context_id
                                and other.id != binding.id
                                and other.is_default
                            ):
                                self.bindings[other_id] = other.model_copy(
                                    update={"is_default": False}
                                )
                        updates["is_default"] = True
                    if updates:
                        binding = binding.model_copy(update=updates)
                        self.bindings[binding_id] = binding
                    if is_default:
                        self.update_active_project(
                            chat_context_id,
                            project_id,
                            expected_version=None,
                        )
                    return binding
            if is_default:
                for binding_id, binding in list(self.bindings.items()):
                    if binding.chat_context_id == chat_context_id and binding.is_default:
                        self.bindings[binding_id] = binding.model_copy(update={"is_default": False})
            binding = ProjectBinding(
                id=new_id("pbind"),
                chat_context_id=chat_context_id,
                project_id=project_id,
                alias_in_chat=normalized_alias,
                is_default=is_default,
            )
            self.bindings[binding.id] = binding
            if is_default:
                self.update_active_project(chat_context_id, project_id, expected_version=None)
            return binding

    def list_project_bindings(self, chat_context_id: str) -> list[ProjectBinding]:
        with self._lock:
            self.get_chat_context(chat_context_id)
            return sorted(
                [
                    binding
                    for binding in self.bindings.values()
                    if binding.chat_context_id == chat_context_id
                ],
                key=lambda binding: binding.created_at,
            )

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

    def set_preferred_agent(
        self, chat_context_id: str, agent_type: AgentType | None
    ) -> ChatContext:
        """设置/清除该聊天上下文锁定的 agent（不动会话指针，不改 pointer_version）。"""
        with self._lock:
            context = self.get_chat_context(chat_context_id)
            if context.preferred_agent == agent_type:
                return context
            updated = context.model_copy(update={"preferred_agent": agent_type})
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
            active_session_count = self._count_active_project_sessions(project_id)
            if active_session_count >= project.max_active_sessions:
                raise AgentBridgeError(
                    ErrorCode.QUOTA_EXCEEDED,
                    "项目活跃会话数已达到配额上限。",
                    next_step="请关闭不再使用的 Session，或提高项目 max_active_sessions 配额。",
                    status_code=409,
                    details={
                        "project_id": project_id,
                        "active_sessions": active_session_count,
                        "max_active_sessions": project.max_active_sessions,
                    },
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

    def _count_active_project_sessions(self, project_id: str) -> int:
        inactive_statuses = {
            SessionStatus.CLOSED,
            SessionStatus.ARCHIVED,
            SessionStatus.ERROR,
        }
        return sum(
            1
            for session in self.sessions.values()
            if session.project_id == project_id and session.status not in inactive_statuses
        )

    def _count_project_queued_turns(self, project_id: str) -> int:
        session_ids = {
            session.id for session in self.sessions.values() if session.project_id == project_id
        }
        return sum(
            1
            for turn in self.turns.values()
            if turn.session_id in session_ids and turn.status == TurnStatus.QUEUED
        )

    def _count_project_running_turns(self, project_id: str) -> int:
        session_ids = {
            session.id for session in self.sessions.values() if session.project_id == project_id
        }
        return sum(
            1
            for turn in self.turns.values()
            if turn.session_id in session_ids and turn.status == TurnStatus.RUNNING
        )

    def _count_project_daily_turns(self, *, project_id: str, actor_id: str) -> int:
        today = utc_now().date()
        session_ids = {
            session.id for session in self.sessions.values() if session.project_id == project_id
        }
        return sum(
            1
            for turn in self.turns.values()
            if turn.session_id in session_ids
            and turn.actor_id == actor_id
            and turn.queued_at.date() == today
        )

    def _sorted_queue_locked(self, session_id: str) -> list[Turn]:
        return sorted(
            [
                turn
                for turn in self.turns.values()
                if turn.session_id == session_id and turn.status == TurnStatus.QUEUED
            ],
            key=lambda turn: (turn.queue_order, turn.queued_at, turn.id),
        )

    @staticmethod
    def _queue_version_for_state(*, turns: list[Turn], queue_paused: bool) -> str:
        serialized = "\n".join(
            [f"paused={int(queue_paused)}", *[turn.id for turn in turns]]
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
        return f"qv_{digest}"

    def _queue_version_locked(self, session_id: str) -> str:
        session = self.get_session(session_id)
        return self._queue_version_for_state(
            turns=self._sorted_queue_locked(session_id),
            queue_paused=session.queue_paused,
        )

    def _validate_queue_version_locked(
        self,
        *,
        session_id: str,
        expected_queue_version: str | None,
    ) -> None:
        if expected_queue_version is None:
            return
        current_version = self._queue_version_locked(session_id)
        if expected_queue_version != current_version:
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "队列版本已变化，拒绝覆盖并发修改。",
                next_step="请重新执行 /agent queue list 获取最新 queue_version 后重试。",
                status_code=409,
                details={
                    "session_id": session_id,
                    "expected_queue_version": expected_queue_version,
                    "current_queue_version": current_version,
                },
            )

    def _next_session_queue_order_locked(self, session_id: str) -> int:
        return (
            max(
                (
                    turn.queue_order
                    for turn in self.turns.values()
                    if turn.session_id == session_id
                ),
                default=0,
            )
            + 1
        )

    def _next_utc_day_start(self) -> datetime:
        now = utc_now()
        return datetime.combine(
            now.date() + timedelta(days=1),
            datetime.min.time(),
            tzinfo=now.tzinfo,
        )

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

    def queue_terminal_input(self, session_id: str, text: str) -> None:
        """登记一条"立刻追加"到运行中终端的输入（仅内存，由终端服务冲刷）。"""
        with self._lock:
            self.pending_terminal_inputs.setdefault(session_id, []).append(text)

    def drain_terminal_inputs(self, session_id: str) -> list[str]:
        """取出并清空某会话待追加的输入（FIFO）。"""
        with self._lock:
            return self.pending_terminal_inputs.pop(session_id, [])

    def set_terminal_title(self, session_id: str, title: str | None) -> None:
        """更新会话的终端标题（瞬态运行状态，仅内存；不触发持久化，重启后由监控重抓）。"""
        with self._lock:
            session = self.sessions.get(session_id)
            if session is None or session.terminal_title == title:
                return
            self.sessions[session_id] = session.model_copy(
                update={"terminal_title": title}
            )

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

    def enqueue_turn(
        self,
        *,
        session_id: str,
        prompt: str,
        actor: Actor,
        queue_reason: str | None = None,
    ) -> Turn:
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
            project = self.get_project(session.project_id)
            queued_turn_count = self._count_project_queued_turns(project.id)
            if queued_turn_count >= project.max_queued_turns:
                raise AgentBridgeError(
                    ErrorCode.QUOTA_EXCEEDED,
                    "项目排队任务数已达到配额上限。",
                    next_step="请等待已有 Turn 完成，或提高项目 max_queued_turns 配额。",
                    status_code=409,
                    details={
                        "project_id": project.id,
                        "queued_turns": queued_turn_count,
                        "max_queued_turns": project.max_queued_turns,
                        "queue_position": queued_turn_count + 1,
                    },
                )
            daily_turn_count = self._count_project_daily_turns(
                project_id=project.id,
                actor_id=actor.id,
            )
            if daily_turn_count >= project.daily_turns_per_user:
                reset_at = self._next_utc_day_start().isoformat()
                raise AgentBridgeError(
                    ErrorCode.QUOTA_EXCEEDED,
                    "项目每日每用户任务数已达到配额上限。",
                    next_step="请等待每日配额重置，或提高项目 daily_turns_per_user 配额。",
                    status_code=409,
                    details={
                        "project_id": project.id,
                        "actor_id": actor.id,
                        "daily_turns": daily_turn_count,
                        "daily_turns_per_user": project.daily_turns_per_user,
                        "reset_at": reset_at,
                    },
                )
            turn = Turn(
                id=new_id("turn"),
                session_id=session_id,
                prompt=prompt.strip(),
                actor_id=actor.id,
                queue_order=self._next_session_queue_order_locked(session_id),
                queue_reason=queue_reason,
            )
            self.turns[turn.id] = turn
            return turn

    def list_turns(self, session_id: str) -> list[Turn]:
        with self._lock:
            return [turn for turn in self.turns.values() if turn.session_id == session_id]

    def list_queue(self, session_id: str) -> list[Turn]:
        with self._lock:
            self.get_session(session_id)
            return self._sorted_queue_locked(session_id)

    def queue_version(self, session_id: str) -> str:
        with self._lock:
            self.get_session(session_id)
            return self._queue_version_locked(session_id)

    def queue_snapshot(self, session_id: str) -> tuple[list[Turn], str, bool]:
        with self._lock:
            session = self.get_session(session_id)
            turns = self._sorted_queue_locked(session_id)
            return (
                turns,
                self._queue_version_for_state(
                    turns=turns,
                    queue_paused=session.queue_paused,
                ),
                session.queue_paused,
            )

    def get_turn(self, turn_id: str) -> Turn:
        with self._lock:
            turn = self.turns.get(turn_id)
            if not turn:
                raise AgentBridgeError(
                    ErrorCode.NOT_FOUND,
                    f"Turn 不存在：{turn_id}",
                    next_step="请执行 /agent queue list 查看可操作的 Turn。",
                    status_code=404,
                    details={"turn_id": turn_id},
                )
            return turn

    def cancel_queued_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        expected_queue_version: str | None = None,
    ) -> Turn:
        with self._lock:
            self._validate_queue_version_locked(
                session_id=session_id,
                expected_queue_version=expected_queue_version,
            )
            turn = self._get_session_turn(session_id=session_id, turn_id=turn_id)
            if turn.status != TurnStatus.QUEUED:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    f"只能移除尚未开始的 queued Turn，当前状态：{turn.status.value}",
                    next_step="已进入执行的 Turn 请使用 stop/interrupt 控制流。",
                    status_code=409,
                    details={"turn_id": turn_id, "status": turn.status.value},
                )
            updated = turn.model_copy(
                update={"status": TurnStatus.CANCELLED, "completed_at": utc_now()}
            )
            self.turns[turn.id] = updated
            return updated

    def clear_queued_turns(
        self,
        session_id: str,
        *,
        expected_queue_version: str | None = None,
        confirmed_count: int | None = None,
    ) -> list[Turn]:
        with self._lock:
            self.get_session(session_id)
            current_queue = self._sorted_queue_locked(session_id)
            current_queue_version = self._queue_version_locked(session_id)
            if expected_queue_version is None:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "清空队列需要携带 queue_version。",
                    next_step=(
                        "请先执行 /agent queue list，再使用 "
                        f"/agent queue clear --version {current_queue_version} "
                        f"--confirm {len(current_queue)}。"
                    ),
                    status_code=400,
                    details={
                        "session_id": session_id,
                        "current_queue_version": current_queue_version,
                        "current_count": len(current_queue),
                    },
                )
            self._validate_queue_version_locked(
                session_id=session_id,
                expected_queue_version=expected_queue_version,
            )
            if confirmed_count is None:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "清空队列需要确认受影响 Turn 数量。",
                    next_step=(
                        "请先执行 /agent queue list 确认队列，再使用 "
                        f"/agent queue clear --version {current_queue_version} "
                        f"--confirm {len(current_queue)}。"
                    ),
                    status_code=400,
                    details={
                        "session_id": session_id,
                        "current_queue_version": current_queue_version,
                        "current_count": len(current_queue),
                    },
                )
            if confirmed_count != len(current_queue):
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    "清空队列确认数量与当前队列数量不一致。",
                    next_step="请重新执行 /agent queue list 获取最新数量和 queue_version 后重试。",
                    status_code=409,
                    details={
                        "session_id": session_id,
                        "confirmed_count": confirmed_count,
                        "current_count": len(current_queue),
                        "current_queue_version": current_queue_version,
                    },
                )
            cancelled: list[Turn] = []
            for turn in current_queue:
                updated = turn.model_copy(
                    update={"status": TurnStatus.CANCELLED, "completed_at": utc_now()}
                )
                self.turns[turn.id] = updated
                cancelled.append(updated)
            return cancelled

    def reorder_queued_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        before_turn_id: str,
        expected_queue_version: str,
    ) -> list[Turn]:
        with self._lock:
            self.get_session(session_id)
            self._validate_queue_version_locked(
                session_id=session_id,
                expected_queue_version=expected_queue_version,
            )
            turns = self._sorted_queue_locked(session_id)
            turn_ids = [turn.id for turn in turns]
            if turn_id not in turn_ids:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    "只能重排尚未开始的 queued Turn。",
                    next_step="请执行 /agent queue list 查看可重排的 Turn。",
                    status_code=409,
                    details={"turn_id": turn_id, "session_id": session_id},
                )
            if before_turn_id not in turn_ids:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    "目标位置 Turn 不在当前队列中。",
                    next_step="请确认 --before 指向同一 Session 中尚未开始的 Turn。",
                    status_code=409,
                    details={
                        "turn_id": turn_id,
                        "before_turn_id": before_turn_id,
                        "session_id": session_id,
                    },
                )
            if turn_id == before_turn_id:
                return turns

            moving = self.turns[turn_id]
            reordered = [turn for turn in turns if turn.id != turn_id]
            before_index = next(
                index for index, turn in enumerate(reordered) if turn.id == before_turn_id
            )
            reordered.insert(before_index, moving)
            updated_turns: list[Turn] = []
            for index, turn in enumerate(reordered, start=1):
                updated = turn.model_copy(update={"queue_order": index})
                self.turns[turn.id] = updated
                updated_turns.append(updated)
            return updated_turns

    def set_turn_queue_paused(
        self,
        *,
        session_id: str,
        paused: bool,
        expected_queue_version: str,
    ) -> AgentSession:
        with self._lock:
            session = self.get_session(session_id)
            self._validate_queue_version_locked(
                session_id=session_id,
                expected_queue_version=expected_queue_version,
            )
            if session.queue_paused == paused:
                return session
            updated = session.model_copy(
                update={"queue_paused": paused, "updated_at": utc_now()}
            )
            self.sessions[session_id] = updated
            return updated

    def start_turn(self, *, session_id: str, turn_id: str) -> Turn:
        with self._lock:
            session = self.get_session(session_id)
            turn = self._get_session_turn(session_id=session_id, turn_id=turn_id)
            if turn.status == TurnStatus.RUNNING:
                return turn
            if session.status == SessionStatus.RECOVERING and turn.status == TurnStatus.QUEUED:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    "Terminal Agent 处于离线保护，不能开始 queued Turn。",
                    next_step="请等待 Terminal Agent 重连并退出离线保护后再领取 Turn。",
                    status_code=409,
                    details={
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "offline_protection": True,
                        "session_status": session.status.value,
                    },
                )
            if session.queue_paused and turn.status == TurnStatus.QUEUED:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    "当前 Session 队列已暂停，不能开始新的 queued Turn。",
                    next_step="请执行 /agent queue resume 恢复队列后再开始 Turn。",
                    status_code=409,
                    details={
                        "session_id": session_id,
                        "turn_id": turn_id,
                        "queue_paused": True,
                    },
                )
            if turn.status in {
                TurnStatus.COMPLETED,
                TurnStatus.FAILED,
                TurnStatus.CANCELLED,
            }:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    f"Turn 已处于终态，不能开始运行：{turn.status.value}",
                    next_step="请创建新的 Turn，或检查 Terminal Agent 事件顺序。",
                    status_code=409,
                    details={"turn_id": turn_id, "status": turn.status.value},
                )
            if session.active_turn_id and session.active_turn_id != turn_id:
                active_turn = self.turns.get(session.active_turn_id)
                if active_turn and active_turn.status == TurnStatus.RUNNING:
                    raise AgentBridgeError(
                        ErrorCode.RESOURCE_CONFLICT,
                        "当前 Session 已有运行中的 Turn。",
                        next_step="请等待当前 Turn 结束后再开始下一个 Turn。",
                        status_code=409,
                        details={
                            "session_id": session_id,
                            "active_turn_id": session.active_turn_id,
                        },
                    )
            project = self.get_project(session.project_id)
            running_turn_count = self._count_project_running_turns(project.id)
            if running_turn_count >= project.max_running_turns:
                raise AgentBridgeError(
                    ErrorCode.QUOTA_EXCEEDED,
                    "项目运行中任务数已达到配额上限。",
                    next_step="请等待运行中的 Turn 完成，或提高项目 max_running_turns 配额。",
                    status_code=409,
                    details={
                        "project_id": project.id,
                        "running_turns": running_turn_count,
                        "max_running_turns": project.max_running_turns,
                    },
                )
            now = utc_now()
            updated_turn = turn.model_copy(
                update={
                    "status": TurnStatus.RUNNING,
                    "started_at": turn.started_at or now,
                }
            )
            self.turns[turn.id] = updated_turn
            self.sessions[session.id] = session.model_copy(
                update={
                    "active_turn_id": turn.id,
                    "status": SessionStatus.RUNNING,
                    "updated_at": now,
                }
            )
            return updated_turn

    def start_next_turn(
        self,
        *,
        session_id: str,
        expected_queue_version: str | None = None,
    ) -> Turn | None:
        with self._lock:
            self.get_session(session_id)
            self._validate_queue_version_locked(
                session_id=session_id,
                expected_queue_version=expected_queue_version,
            )
            queued_turns = self._sorted_queue_locked(session_id)
            if not queued_turns:
                return None
            return self.start_turn(session_id=session_id, turn_id=queued_turns[0].id)

    def finish_turn(self, *, session_id: str, turn_id: str, status: TurnStatus) -> Turn:
        with self._lock:
            session = self.get_session(session_id)
            turn = self._get_session_turn(session_id=session_id, turn_id=turn_id)
            if status not in {
                TurnStatus.COMPLETED,
                TurnStatus.FAILED,
                TurnStatus.CANCELLED,
            }:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    f"Turn 结束状态无效：{status.value}",
                    next_step="请使用 completed、failed 或 cancelled。",
                    status_code=400,
                    details={"turn_id": turn_id, "status": status.value},
                )
            if turn.status == status:
                return turn
            if turn.status in {
                TurnStatus.COMPLETED,
                TurnStatus.FAILED,
                TurnStatus.CANCELLED,
            }:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    f"Turn 已处于终态，不能改写为：{status.value}",
                    next_step="请检查 Terminal Agent 事件顺序或幂等键。",
                    status_code=409,
                    details={
                        "turn_id": turn_id,
                        "current_status": turn.status.value,
                        "requested_status": status.value,
                    },
                )
            now = utc_now()
            updated_turn = turn.model_copy(
                update={
                    "status": status,
                    "completed_at": turn.completed_at or now,
                }
            )
            self.turns[turn.id] = updated_turn
            if session.active_turn_id == turn.id:
                self.sessions[session.id] = session.model_copy(
                    update={
                        "active_turn_id": None,
                        "status": SessionStatus.IDLE,
                        "updated_at": now,
                    }
                )
            return updated_turn

    def _get_session_turn(self, *, session_id: str, turn_id: str) -> Turn:
        turn = self.turns.get(turn_id)
        if not turn:
            raise AgentBridgeError(
                ErrorCode.NOT_FOUND,
                f"Turn 不存在：{turn_id}",
                next_step="请确认 Terminal Agent 事件携带的是已入队 Turn ID。",
                status_code=404,
                details={"turn_id": turn_id},
            )
        if turn.session_id != session_id:
            raise AgentBridgeError(
                ErrorCode.RESOURCE_CONFLICT,
                "Turn 不属于目标 Session。",
                next_step="请检查 Terminal Agent 事件中的 session_id 与 turn_id。",
                status_code=409,
                details={
                    "turn_id": turn_id,
                    "turn_session_id": turn.session_id,
                    "session_id": session_id,
                },
            )
        return turn

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
            if (
                session.status == SessionStatus.RECOVERING
                and owner_type != LeaseOwnerType.HUMAN
            ):
                raise AgentBridgeError(
                    ErrorCode.LEASE_CONFLICT,
                    "Terminal Agent 处于离线保护，只允许本地 Human 获取写入租约。",
                    next_step="请等待 Terminal Agent 重连，或由本地用户接管后手动操作。",
                    status_code=409,
                    details={
                        "session_id": session_id,
                        "owner_type": owner_type.value,
                        "offline_protection": True,
                        "session_status": session.status.value,
                    },
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
            self._require_workspace_write_capacity(session)
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
                status = (
                    SessionStatus.RECOVERING
                    if session.status == SessionStatus.RECOVERING
                    else SessionStatus.HUMAN_CONTROLLED
                )
                self.sessions[session.id] = session.model_copy(
                    update={"status": status, "updated_at": utc_now()}
                )
            return lease

    def set_terminal_agent_offline_protection(
        self,
        *,
        session_id: str,
        offline: bool,
    ) -> tuple[AgentSession, WriterLease | None, int]:
        with self._lock:
            session = self.get_session(session_id)
            if session.status in {SessionStatus.CLOSED, SessionStatus.ARCHIVED}:
                raise AgentBridgeError(
                    ErrorCode.RESOURCE_CONFLICT,
                    "已关闭或归档的会话不能切换 Terminal Agent 离线保护。",
                    next_step="请恢复会话或创建新会话。",
                    status_code=409,
                    details={"session_id": session_id, "status": session.status.value},
                )
            removed_lease: WriterLease | None = None
            next_epoch = self.lease_epochs.get(session_id, 0)
            current = self.leases.get(session_id)
            if offline:
                if current is not None and current.owner_type != LeaseOwnerType.HUMAN:
                    removed_lease = current
                    next_epoch = max(next_epoch, current.epoch) + 1
                    self.lease_epochs[session_id] = next_epoch
                    self.leases.pop(session_id, None)
                updated = session.model_copy(
                    update={
                        "status": SessionStatus.RECOVERING,
                        "updated_at": utc_now(),
                    }
                )
                self.sessions[session_id] = updated
                return updated, removed_lease, next_epoch

            active_lease = self.current_lease(session_id)
            target_status = (
                SessionStatus.HUMAN_CONTROLLED
                if active_lease is not None
                and active_lease.owner_type == LeaseOwnerType.HUMAN
                else SessionStatus.IDLE
            )
            if session.status == target_status:
                return session, removed_lease, next_epoch
            updated = session.model_copy(
                update={"status": target_status, "updated_at": utc_now()}
            )
            self.sessions[session_id] = updated
            return updated, removed_lease, next_epoch

    def _require_workspace_write_capacity(self, session: AgentSession) -> None:
        workspace = self.get_workspace(session.workspace_id)
        if not workspace.is_writable:
            raise AgentBridgeError(
                ErrorCode.LEASE_CONFLICT,
                "Workspace 当前不允许写入租约。",
                next_step="请选择可写 Workspace，或仅以只读方式查看会话。",
                status_code=409,
                details={"workspace_id": workspace.id},
            )
        max_write_sessions = max(0, workspace.max_write_sessions)
        active_write_sessions = [
            lease.session_id
            for lease in self.leases.values()
            if lease.session_id != session.id
            and lease.is_active()
            and self.sessions.get(lease.session_id) is not None
            and self.sessions[lease.session_id].workspace_id == workspace.id
        ]
        if len(active_write_sessions) >= max_write_sessions:
            raise AgentBridgeError(
                ErrorCode.LEASE_CONFLICT,
                "Workspace 写入租约已达到并发上限。",
                next_step="等待其他会话释放写入租约，或为并行任务使用独立 Workspace。",
                status_code=409,
                details={
                    "workspace_id": workspace.id,
                    "max_write_sessions": max_write_sessions,
                    "active_write_sessions": len(active_write_sessions),
                    "active_session_ids": sorted(active_write_sessions),
                },
            )

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
        expires_at: datetime | None = None,
        risk_level: RiskLevel = RiskLevel.MEDIUM,
        requested_by: str | None = None,
        policy_snapshot: dict[str, object] | None = None,
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
                risk_level=risk_level,
                required_votes=required_votes,
                requested_by=requested_by,
                policy_snapshot=policy_snapshot or {},
                expires_at=expires_at,
            )
            self.interactions[interaction.id] = interaction
            return interaction

    def list_interactions(
        self,
        *,
        session_id: str | None = None,
        status: InteractionStatus | None = None,
    ) -> list[Interaction]:
        with self._lock:
            interactions = list(self.interactions.values())
            if session_id:
                interactions = [
                    interaction
                    for interaction in interactions
                    if interaction.session_id == session_id
                ]
            if status:
                interactions = [
                    interaction for interaction in interactions if interaction.status == status
                ]
            return sorted(interactions, key=lambda interaction: interaction.created_at)

    def get_interaction(self, interaction_id: str) -> Interaction:
        with self._lock:
            interaction = self.interactions.get(interaction_id)
            if not interaction:
                raise AgentBridgeError(
                    ErrorCode.NOT_FOUND,
                    f"Interaction 不存在：{interaction_id}",
                    next_step="请执行 /agent approvals 查看待处理交互。",
                    status_code=404,
                )
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

    def cancel_interaction(self, interaction_id: str, reason: str | None = None) -> Interaction:
        with self._lock:
            interaction = self._get_pending_interaction(interaction_id)
            updated = interaction.model_copy(
                update={
                    "status": InteractionStatus.CANCELLED,
                    "answer": reason,
                    "resolved_at": utc_now(),
                    "version": interaction.version + 1,
                }
            )
            self.interactions[interaction_id] = updated
            return updated

    def expire_due_interactions(self, now: datetime | None = None) -> list[Interaction]:
        now = now or utc_now()
        expired: list[Interaction] = []
        with self._lock:
            for interaction in list(self.interactions.values()):
                if interaction.status not in {
                    InteractionStatus.PENDING,
                    InteractionStatus.PARTIALLY_APPROVED,
                }:
                    continue
                if interaction.expires_at is None or interaction.expires_at > now:
                    continue
                updated = interaction.model_copy(
                    update={
                        "status": InteractionStatus.EXPIRED,
                        "resolved_at": now,
                        "version": interaction.version + 1,
                    }
                )
                self.interactions[interaction.id] = updated
                expired.append(updated)
        return expired

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
            expired = interaction.model_copy(
                update={
                    "status": InteractionStatus.EXPIRED,
                    "resolved_at": utc_now(),
                    "version": interaction.version + 1,
                }
            )
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
        details_field: str | None = None,
        details_value: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        max_results = self._clamp_audit_limit(limit)
        with self._lock:
            events: list[AuditEvent] = []
            for event in reversed(self.audit_events):
                if (
                    (actor_id is None or event.actor_id == actor_id)
                    and (action is None or event.action == action)
                    and (project_id is None or event.project_id == project_id)
                    and (session_id is None or event.session_id == session_id)
                    and (interaction_id is None or event.interaction_id == interaction_id)
                    and (trace_id is None or event.trace_id == trace_id)
                    and payload_contains_query(event.details, payload_query)
                    and payload_field_matches(
                        event.details,
                        details_field,
                        details_value,
                    )
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

    @staticmethod
    def _clamp_audit_limit(limit: int) -> int:
        return max(1, min(limit, 500))

    def append_event(
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
        with self._lock:
            if idempotency_key and idempotency_key in self.event_idempotency:
                return self.event_idempotency[idempotency_key]
            stream_id = self._event_stream_id(project_id=project_id, session_id=session_id)
            seq = self.event_stream_seq.get(stream_id, 0) + 1
            event = SemanticEvent(
                id=new_id("evt"),
                stream_id=stream_id,
                seq=seq,
                type=event_type,
                source=source,
                trace_id=trace_id,
                idempotency_key=idempotency_key,
                project_id=project_id,
                session_id=session_id,
                turn_id=turn_id,
                interaction_id=interaction_id,
                payload=payload or {},
            )
            self.event_stream_seq[stream_id] = seq
            self.semantic_events.append(event)
            if idempotency_key:
                self.event_idempotency[idempotency_key] = event
            return event

    def get_event_by_idempotency_key(self, idempotency_key: str) -> SemanticEvent | None:
        with self._lock:
            return self.event_idempotency.get(idempotency_key)

    def get_semantic_event(self, event_id: str) -> SemanticEvent | None:
        with self._lock:
            for event in reversed(self.semantic_events):
                if event.id == event_id:
                    return event
            return None

    def list_events(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
        after_seq: int | None = None,
        limit: int = 100,
    ) -> list[SemanticEvent]:
        with self._lock:
            stream_id = self._event_stream_id(project_id=project_id, session_id=session_id)
            events = [event for event in self.semantic_events if event.stream_id == stream_id]
            if after_seq is not None:
                events = [event for event in events if event.seq > after_seq]
            return events[:limit]

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
        payload_field: str | None = None,
        payload_value: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 100,
    ) -> list[SemanticEvent]:
        max_results = self._clamp_event_search_limit(limit)
        with self._lock:
            events: list[SemanticEvent] = []
            for event in reversed(self.semantic_events):
                if (
                    (project_id is None or event.project_id == project_id)
                    and (session_id is None or event.session_id == session_id)
                    and (turn_id is None or event.turn_id == turn_id)
                    and (interaction_id is None or event.interaction_id == interaction_id)
                    and (event_type is None or event.type == event_type)
                    and (source is None or event.source == source)
                    and (trace_id is None or event.trace_id == trace_id)
                    and payload_contains_query(event.payload, payload_query)
                    and payload_field_matches(
                        event.payload,
                        payload_field,
                        payload_value,
                    )
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

    def list_semantic_events_chronological(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
        event_type: str | None = None,
        after_seq: int | None = None,
        after_event_id: str | None = None,
        limit: int = 100,
    ) -> list[SemanticEvent]:
        max_results = self._clamp_event_search_limit(limit)
        with self._lock:
            events: list[SemanticEvent] = []
            found_after_event = after_event_id is None
            for event in self.semantic_events:
                if not found_after_event:
                    if event.id == after_event_id:
                        found_after_event = True
                    continue
                if (
                    (project_id is None or event.project_id == project_id)
                    and (session_id is None or event.session_id == session_id)
                    and (event_type is None or event.type == event_type)
                    and (after_seq is None or event.seq > after_seq)
                ):
                    events.append(event)
                    if len(events) >= max_results:
                        break
            return events

    @staticmethod
    def _clamp_event_search_limit(limit: int) -> int:
        return max(1, min(limit, 1000))

    @staticmethod
    def _event_stream_id(project_id: str | None, session_id: str | None) -> str:
        if session_id:
            return f"session:{session_id}"
        if project_id:
            return f"project:{project_id}"
        return "system"

    def get_event_consumer_offset(
        self,
        *,
        session_id: str,
        consumer_id: str,
    ) -> EventConsumerOffset | None:
        with self._lock:
            self.get_session(session_id)
            consumer_id = self._require_event_consumer_id(consumer_id)
            stream_id = self._event_stream_id(project_id=None, session_id=session_id)
            return self.event_consumer_offsets.get((stream_id, consumer_id))

    def ack_event_consumer(
        self,
        *,
        session_id: str,
        consumer_id: str,
        seq: int,
    ) -> EventConsumerOffset:
        with self._lock:
            self.get_session(session_id)
            consumer_id = self._require_event_consumer_id(consumer_id)
            stream_id = self._event_stream_id(project_id=None, session_id=session_id)
            latest_seq = self.event_stream_seq.get(stream_id, 0)
            if seq < 0:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "ACK seq 必须是非负整数。",
                    next_step="请使用已收到事件的 seq 作为 ACK 游标。",
                    details={"seq": seq},
                )
            if seq > latest_seq:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "ACK seq 不能超过当前事件流最新 seq。",
                    next_step="请先回放并处理已存在的事件，再确认对应 seq。",
                    details={"seq": seq, "latest_seq": latest_seq},
                )
            key = (stream_id, consumer_id)
            existing = self.event_consumer_offsets.get(key)
            acknowledged_seq = max(seq, existing.last_seq if existing else 0)
            offset = EventConsumerOffset(
                id=existing.id if existing else new_id("eoff"),
                stream_id=stream_id,
                session_id=session_id,
                consumer_id=consumer_id,
                last_seq=acknowledged_seq,
            )
            self.event_consumer_offsets[key] = offset
            return offset

    @staticmethod
    def _require_event_consumer_id(consumer_id: str) -> str:
        normalized = consumer_id.strip()
        if not normalized:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "consumer_id 必须是非空字符串。",
                next_step="请为事件消费者提供稳定的非空 consumer_id。",
            )
        return normalized

    def get_bot_delivery_record(self, idempotency_key: str) -> BotDeliveryRecord | None:
        with self._lock:
            return self.bot_delivery_records.get(idempotency_key)

    def store_bot_delivery_record(self, record: BotDeliveryRecord) -> None:
        with self._lock:
            self.bot_delivery_records[record.idempotency_key] = record

    def list_bot_delivery_records(
        self,
        chat_context_id: str | None = None,
        status: BotDeliveryStatus | None = None,
    ) -> list[BotDeliveryRecord]:
        with self._lock:
            records = list(self.bot_delivery_records.values())
            if chat_context_id:
                records = [
                    record for record in records if record.chat_context_id == chat_context_id
                ]
            if status:
                records = [record for record in records if record.status == status]
            return sorted(records, key=lambda record: record.created_at)
