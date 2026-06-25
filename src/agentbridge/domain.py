from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


class ErrorCode(StrEnum):
    COMMAND_UNKNOWN = "COMMAND_UNKNOWN"
    COMMAND_ARGUMENT_INVALID = "COMMAND_ARGUMENT_INVALID"
    TARGET_PROJECT_AMBIGUOUS = "TARGET_PROJECT_AMBIGUOUS"
    TARGET_PROJECT_REQUIRED = "TARGET_PROJECT_REQUIRED"
    TARGET_SESSION_REQUIRED = "TARGET_SESSION_REQUIRED"
    TARGET_SESSION_AMBIGUOUS = "TARGET_SESSION_AMBIGUOUS"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
    INTERACTION_EXPIRED = "INTERACTION_EXPIRED"
    LEASE_CONFLICT = "LEASE_CONFLICT"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    PLATFORM_CAPABILITY_MISSING = "PLATFORM_CAPABILITY_MISSING"
    NOT_FOUND = "NOT_FOUND"
    WORKSPACE_PATH_DENIED = "WORKSPACE_PATH_DENIED"


class AgentBridgeError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        next_step: str | None = None,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_step = next_step or "检查命令参数后重试。"
        self.status_code = status_code
        self.details = details or {}

    def to_payload(self, trace_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error_code": self.code.value,
            "message": self.message,
            "side_effect": "未执行副作用。",
            "next_step": self.next_step,
        }
        if trace_id:
            payload["trace_id"] = trace_id
        if self.details:
            payload["details"] = self.details
        return payload


class ProjectStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    READ_ONLY = "read_only"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class WorkspaceType(StrEnum):
    SHARED = "shared"
    EXCLUSIVE = "exclusive"
    GIT_WORKTREE = "git_worktree"
    EPHEMERAL_COPY = "ephemeral_copy"
    READ_ONLY = "read_only"


class AgentType(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    GENERIC_TUI = "generic_tui"


class Visibility(StrEnum):
    PRIVATE = "private"
    THREAD = "thread"
    GROUP = "group"
    PROJECT = "project"
    ORGANIZATION = "organization"


class SessionStatus(StrEnum):
    CREATING = "creating"
    STARTING = "starting"
    IDLE = "idle"
    RUNNING = "running"
    WAITING_INTERACTION = "waiting_interaction"
    HUMAN_CONTROLLED = "human_controlled"
    SUSPENDED = "suspended"
    RECOVERING = "recovering"
    ERROR = "error"
    CLOSING = "closing"
    CLOSED = "closed"
    ARCHIVED = "archived"


class TurnStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_INTERACTION = "waiting_interaction"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InteractionType(StrEnum):
    QUESTION = "question"
    APPROVAL = "approval"
    PLAN = "plan"


class InteractionStatus(StrEnum):
    PENDING = "pending"
    PARTIALLY_APPROVED = "partially_approved"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class LeaseOwnerType(StrEnum):
    BOT = "bot"
    HUMAN = "human"
    WEB_ADMIN = "web_admin"
    SYSTEM = "system"


class AuditOutcome(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    FAILED = "failed"


class BotPlatform(StrEnum):
    ONEBOT_V11 = "onebot.v11"
    PLAIN_TEXT = "plain_text"


class BotDeliveryStatus(StrEnum):
    SENT = "sent"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    FAILED = "failed"
    RETRYING = "retrying"


class BotDeliveryPlatformState(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    EDITED = "edited"
    DELETED = "deleted"


class BotDeliveryResultAction(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    EDIT = "edit"
    DELETE = "delete"


class PolicyScope(StrEnum):
    PROJECT = "project"
    CHAT_CONTEXT = "chat_context"


class AccessPolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class DeviceIdentityStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class SemanticEventSource(StrEnum):
    CONTROL_PLANE = "control_plane"
    TERMINAL_AGENT = "terminal_agent"
    BOT_GATEWAY = "bot_gateway"
    ADMIN_WEB = "admin_web"


class Actor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    roles: set[str] = Field(default_factory=lambda: {"member"})


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    slug: str
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    default_agent: AgentType = AgentType.CLAUDE
    policy_id: str | None = None
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("slug must not be empty")
        return normalized


class Workspace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    machine_id: str
    path: str
    allowed_root: str
    type: WorkspaceType = WorkspaceType.SHARED
    is_writable: bool = True
    max_write_sessions: int = 1
    created_at: datetime = Field(default_factory=utc_now)


class ProjectBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    chat_context_id: str
    project_id: str
    alias_in_chat: str | None = None
    is_default: bool = False
    visibility: Visibility = Visibility.GROUP
    created_at: datetime = Field(default_factory=utc_now)


class ChatContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    bot_instance_id: str
    platform: str
    chat_space_id: str
    thread_id: str | None = None
    user_id: str | None = None
    active_project_id: str | None = None
    active_session_id: str | None = None
    pointer_version: int = 0


class GroupRoleBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    chat_context_id: str
    actor_id: str
    roles: set[str]
    granted_by: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ApprovalPolicyOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    scope_type: PolicyScope
    scope_id: str
    quorum_by_risk: dict[RiskLevel, int] = Field(default_factory=dict)
    updated_by: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("quorum_by_risk")
    @classmethod
    def validate_quorum_by_risk(
        cls, value: dict[RiskLevel, int]
    ) -> dict[RiskLevel, int]:
        for risk_level, quorum in value.items():
            if not isinstance(risk_level, RiskLevel):
                raise ValueError("quorum risk keys must be valid risk levels")
            if quorum < 1:
                raise ValueError("quorum must be >= 1")
        return value


class AccessPolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    effect: AccessPolicyEffect
    action: str
    resource_type: str = "*"
    resource_id: str | None = None
    actor_ids: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    priority: int = 100
    enabled: bool = True
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("action", "resource_type")
    @classmethod
    def validate_non_empty_pattern(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("policy pattern must not be empty")
        return normalized

    @field_validator("actor_ids", "roles")
    @classmethod
    def validate_non_empty_items(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class DeviceIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    device_id: str
    display_name: str | None = None
    key_hash: str
    key_salt: str
    key_iterations: int = 210000
    status: DeviceIdentityStatus = DeviceIdentityStatus.ACTIVE
    created_by: str
    created_at: datetime = Field(default_factory=utc_now)
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None

    @field_validator("device_id")
    @classmethod
    def validate_device_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("device_id must not be empty")
        return normalized


class AgentSession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    short_code: str
    name: str
    project_id: str
    workspace_id: str
    agent_type: AgentType
    visibility: Visibility
    status: SessionStatus = SessionStatus.IDLE
    created_by: str
    active_turn_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    prompt: str
    actor_id: str
    status: TurnStatus = TurnStatus.QUEUED
    queued_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class WriterLease(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    owner_type: LeaseOwnerType
    owner_id: str
    epoch: int
    issued_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    renewable: bool = True

    @classmethod
    def issue(
        cls,
        *,
        session_id: str,
        owner_type: LeaseOwnerType,
        owner_id: str,
        epoch: int,
        ttl_seconds: int,
    ) -> WriterLease:
        now = utc_now()
        return cls(
            session_id=session_id,
            owner_type=owner_type,
            owner_id=owner_id,
            epoch=epoch,
            issued_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )

    def is_active(self, now: datetime | None = None) -> bool:
        return (now or utc_now()) < self.expires_at


class Interaction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    turn_id: str | None = None
    type: InteractionType
    status: InteractionStatus = InteractionStatus.PENDING
    prompt: str
    options: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    required_votes: int = 1
    votes: dict[str, bool] = Field(default_factory=dict)
    answer: str | None = None
    requested_by: str | None = None
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    version: int = 1
    expires_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class CommandInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    trace_id: str
    idempotency_key: str
    raw_text: str
    canonical_command: str
    args: dict[str, Any] = Field(default_factory=dict)
    actor: Actor
    chat_context_id: str
    created_at: datetime = Field(default_factory=utc_now)


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invocation_id: str
    trace_id: str
    canonical_command: str
    title: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    audit_id: str | None = None


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    action: str
    actor_id: str
    outcome: AuditOutcome
    trace_id: str
    chat_context_id: str | None = None
    project_id: str | None = None
    session_id: str | None = None
    interaction_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    previous_hash: str | None = None
    entry_hash: str


class SemanticEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    stream_id: str
    seq: int
    type: str
    source: SemanticEventSource
    trace_id: str
    idempotency_key: str | None = None
    project_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    interaction_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class BotDeliveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    idempotency_key: str
    platform: BotPlatform
    chat_context_id: str
    event_id: str
    event_seq: int
    message_index: int
    platform_message_id: str | None = None
    text: str
    status: BotDeliveryStatus
    platform_state: BotDeliveryPlatformState = BotDeliveryPlatformState.PENDING
    attempt_count: int = 1
    last_error: str | None = None
    next_retry_at: datetime | None = None
    acknowledged_at: datetime | None = None
    edited_at: datetime | None = None
    deleted_at: datetime | None = None
    edit_revision: int = 0
    platform_payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
