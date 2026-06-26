from __future__ import annotations

import asyncio
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import shlex
import subprocess
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

import uvicorn
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from agentbridge.admin_ui import (
    ACCESS_POLICY_ADMIN_HTML,
    ADMIN_AUTH_REQUIRED_HTML,
    ADMIN_HOME_HTML,
    AUDIT_EVENTS_ADMIN_HTML,
    BOT_DELIVERY_ADMIN_HTML,
    DEVICE_IDENTITY_ADMIN_HTML,
    INTERACTION_ADMIN_HTML,
    PROJECT_SESSION_ADMIN_HTML,
    SYSTEM_HEALTH_ADMIN_HTML,
    TERMINAL_LIFECYCLE_ADMIN_HTML,
)
from agentbridge.bot_gateway import (
    BotDeliveryRateLimiter,
    BotDeliveryRetryWorker,
    BotGatewayService,
    BotPlatform,
    BotRateLimitPolicy,
    InMemoryBotTransport,
)
from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.device_auth import normalize_certificate_fingerprint, verify_device_key
from agentbridge.device_certificate_health import (
    device_identity_certificate_health,
    managed_device_certificate_active,
)
from agentbridge.device_certificate_scan import DeviceCertificateScanWorker
from agentbridge.device_certificates import (
    DeviceCertificateIssuer,
    ExternalDeviceCertificateIssuer,
)
from agentbridge.domain import (
    AccessPolicyEffect,
    Actor,
    AgentBridgeError,
    AgentType,
    AuditEvent,
    BotDeliveryResultAction,
    BotDeliveryStatus,
    DeviceIdentity,
    DeviceIdentityScope,
    DeviceIdentityStatus,
    ErrorCode,
    InteractionStatus,
    InteractionType,
    LeaseOwnerType,
    PolicyScope,
    RiskLevel,
    SemanticEvent,
    SemanticEventSource,
    Visibility,
    WorkspaceType,
    utc_now,
)
from agentbridge.onebot import (
    OneBotInboundAdapter,
    OneBotV11HTTPTransport,
    execute_onebot_inbound_command,
)
from agentbridge.persistence import SQLAlchemyRepository
from agentbridge.policy import ApprovalPolicy, Permission
from agentbridge.pty_host import (
    PtyHostSupervisor,
    PtyHostSupervisorConfig,
    PtyHostTerminalBackend,
)
from agentbridge.renderer import (
    OneBotV11TextRenderer,
    RenderDocument,
    document_from_event,
    render_action_descriptors,
)
from agentbridge.storage import InMemoryRepository
from agentbridge.terminal_agent import (
    DEFAULT_PTY_OUTPUT_LIMIT_CHARS,
    FakeTerminalBackend,
    PtyTerminalBackend,
    TerminalAgentService,
    TerminalInputKind,
    TerminalLifecyclePolicy,
    TmuxTerminalBackend,
)


class ActorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = "system"
    roles: set[str] = Field(default_factory=lambda: {"admin"})

    def to_actor(self) -> Actor:
        return Actor(id=self.id, roles=self.roles)


class ChatContextPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_instance_id: str = "bot-local"
    platform: str = "onebot.v11"
    chat_space_id: str = "local"
    thread_id: str | None = None
    user_id: str | None = None


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    name: str
    slug: str | None = None
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    default_agent: AgentType = AgentType.CLAUDE
    max_active_sessions: int = Field(default=10, ge=0)
    max_running_turns: int = Field(default=4, ge=0)
    max_queued_turns: int = Field(default=100, ge=0)
    daily_turns_per_user: int = Field(default=50, ge=0)
    trace_id: str = "api"


class CreateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    machine_id: str = "local"
    path: str
    allowed_root: str
    workspace_type: WorkspaceType = WorkspaceType.SHARED
    is_writable: bool = True
    max_write_sessions: int = Field(default=1, ge=0)
    trace_id: str = "api"


class CreateProjectBindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    project_id: str
    alias_in_chat: str | None = None
    is_default: bool = False
    trace_id: str = "api"


class UpdateActiveProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    project: str
    expected_version: int | None = None
    trace_id: str = "api"


class UpdateActiveSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    session: str
    expected_version: int | None = None
    trace_id: str = "api"


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    project_id: str
    workspace_id: str | None = None
    name: str = "AgentBridge Session"
    agent_type: AgentType = AgentType.CLAUDE
    visibility: Visibility = Visibility.GROUP
    trace_id: str = "api"


class CreateTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    prompt: str
    trace_id: str = "api"


class QueueMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    expected_queue_version: str | None = None
    confirm_count: int | None = Field(default=None, ge=0)
    trace_id: str = "api"


class QueueReorderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    turn_id: str
    before_turn_id: str
    expected_queue_version: str
    trace_id: str = "api"


class QueueStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    expected_queue_version: str
    trace_id: str = "api"


class AcquireLeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    owner_type: LeaseOwnerType
    owner_id: str
    ttl_seconds: int = 300
    trace_id: str = "api"


class ReleaseLeaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    epoch: int
    trace_id: str = "api"


class IngestSessionEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    source: SemanticEventSource = SemanticEventSource.TERMINAL_AGENT
    trace_id: str = "api"
    idempotency_key: str | None = None
    turn_id: str | None = None
    interaction_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class CreateInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    type: InteractionType
    prompt: str
    turn_id: str | None = None
    options: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    required_votes: int | None = None
    expires_at: datetime | None = None
    ttl_seconds: int | None = None
    chat_context_id: str | None = None
    trace_id: str = "api"


class AnswerInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    answer: str
    chat_context_id: str | None = None
    trace_id: str = "api"


class VoteInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    approve: bool
    reason: str | None = None
    chat_context_id: str | None = None
    trace_id: str = "api"


class CancelInteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    reason: str | None = None
    chat_context_id: str | None = None
    trace_id: str = "api"


class StartTerminalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    command: str | None = None
    trace_id: str = "api"


class RestartTerminalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    command: str | None = None
    trace_id: str = "api"


class TerminalInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    epoch: int
    owner_type: LeaseOwnerType
    owner_id: str
    type: TerminalInputKind = TerminalInputKind.TEXT
    data: str
    request_id: str | None = None
    cols: int | None = None
    rows: int | None = None
    trace_id: str = "api"


class TerminalLifecycleRunOnceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    trace_id: str = "terminal-lifecycle-api"


class AgentLaunchProbeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    agent_types: list[AgentType] | None = None
    timeout_seconds: float = Field(default=2.0, ge=0.1, le=10.0)
    trace_id: str = "terminal-agent-launch-probe-api"


class AgentAdapterDetectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    agent_types: list[AgentType] | None = None
    timeout_seconds: float = Field(default=2.0, ge=0.1, le=10.0)
    trace_id: str = "terminal-agent-adapter-detect-api"


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str
    actor: ActorPayload = Field(default_factory=ActorPayload)
    chat_context_id: str | None = None
    chat: ChatContextPayload = Field(default_factory=ChatContextPayload)
    idempotency_key: str | None = None
    trace_id: str | None = None


class DeliverSessionEventsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    chat_context_id: str
    platform: BotPlatform = BotPlatform.ONEBOT_V11
    after_seq: int | None = None
    limit: int = 100


class DeliverEventsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_context_id: str
    platform: BotPlatform = BotPlatform.ONEBOT_V11
    project_id: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    interaction_id: str | None = None
    event_type: str | None = None
    source: SemanticEventSource | None = None
    trace_id: str | None = None
    q: str | None = None
    payload_field: str | None = None
    payload_value: str | None = None
    limit: int = 100


class RetryBotDeliveriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_context_id: str | None = None
    limit: int = 100


class BotDeliveryResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str
    action: BotDeliveryResultAction
    platform_message_id: str | None = None
    text: str | None = None
    error: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)
    occurred_at: datetime | None = None


class EditBotDeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str
    text: str
    payload: dict[str, object] = Field(default_factory=dict)
    occurred_at: datetime | None = None


class DeleteBotDeliveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str
    payload: dict[str, object] = Field(default_factory=dict)
    occurred_at: datetime | None = None


class RetryWorkerRunOnceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_context_id: str | None = None
    limit: int | None = None


class OneBotEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: dict[str, object]
    bot_instance_id: str = "onebot-http"
    default_roles: set[str] = Field(default_factory=lambda: {"member"})


class DeviceIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    device_id: str
    display_name: str | None = None
    device_key: str | None = None
    allowed_scopes: set[DeviceIdentityScope] | None = None
    allowed_resource_ids: set[str] | None = None
    certificate_fingerprints: set[str] | None = None
    trace_id: str = "device-identity-api"


class RevokeDeviceIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    trace_id: str = "device-identity-api"


class RotateDeviceCertificateFingerprintsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    add_fingerprints: set[str] = Field(default_factory=set)
    remove_fingerprints: set[str] = Field(default_factory=set)
    trace_id: str = "device-certificate-rotation-api"


class IssueDeviceCertificateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    csr_pem: str
    validity_days: int | None = Field(default=None, ge=1)
    trace_id: str = "device-certificate-issue-api"


class RenewDeviceCertificateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    csr_pem: str
    validity_days: int | None = Field(default=None, ge=1)
    trace_id: str = "device-certificate-renew-api"


class ScanDeviceCertificatesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    warning_days: int | None = Field(default=None, ge=1)
    include_revoked: bool = False
    trace_id: str = "device-certificate-scan-api"


class CertificateScanWorkerRunOnceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    warning_days: int | None = Field(default=None, ge=1)
    include_revoked: bool | None = None
    trace_id: str = "device-certificate-scan-worker-run-once-api"


class GroupRoleChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    target_actor_id: str
    roles: set[str]
    trace_id: str = "api"


class ApprovalPolicyOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    quorum_by_risk: dict[RiskLevel, int]
    trace_id: str = "api"


class AccessPolicyRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    rule_id: str | None = None
    effect: AccessPolicyEffect
    action: str
    resource_type: str = "*"
    resource_id: str | None = None
    actor_ids: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    attributes: dict[str, object] = Field(default_factory=dict)
    description: str | None = None
    priority: int = 100
    enabled: bool = True
    trace_id: str = "api"
    chat_context_id: str | None = None


class AccessPolicyDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    trace_id: str = "api"
    chat_context_id: str | None = None


class AccessPolicySimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    target_actor: ActorPayload
    action: str
    resource_type: str = "*"
    resource_id: str | None = None
    attributes: dict[str, object] = Field(default_factory=dict)
    chat_context_id: str | None = None


def create_app(control_plane: ControlPlane | None = None) -> FastAPI:
    control = control_plane or ControlPlane(
        repository=create_repository_from_env(),
        approval_policy=create_approval_policy_from_env(),
    )
    commands = CommandService(control)
    terminal = TerminalAgentService(
        control,
        backend=create_terminal_backend_from_env(),
        lifecycle_policy=create_terminal_lifecycle_policy_from_env(),
    )
    bot_gateway = BotGatewayService(
        control,
        transport=create_bot_transport_from_env(),
        rate_limiter=create_bot_rate_limiter_from_env(),
    )
    bot_retry_worker = create_bot_retry_worker_from_env(bot_gateway)
    certificate_scan_worker = create_certificate_scan_worker_from_env(
        control,
        bot_gateway,
    )
    terminal_lifecycle_monitor_enabled = env_bool(
        "AGENTBRIDGE_TERMINAL_LIFECYCLE_MONITOR_ENABLED",
        default=False,
    )
    terminal_lifecycle_poll_interval_seconds = env_float(
        "AGENTBRIDGE_TERMINAL_LIFECYCLE_POLL_INTERVAL_SECONDS",
        default=1.0,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        start_terminal_backend_supervision(terminal)
        if terminal_lifecycle_monitor_enabled:
            terminal.start_lifecycle_monitor(
                interval_seconds=terminal_lifecycle_poll_interval_seconds
            )
        if bot_retry_worker.enabled:
            bot_retry_worker.start()
        if certificate_scan_worker.enabled:
            certificate_scan_worker.start()
        try:
            yield
        finally:
            terminal.stop_lifecycle_monitor()
            stop_terminal_backend_supervision(terminal)
            bot_retry_worker.stop()
            certificate_scan_worker.stop()

    app = FastAPI(
        title="AgentBridge Control Plane",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.control = control
    app.state.commands = commands
    app.state.terminal = terminal
    app.state.bot_gateway = bot_gateway
    app.state.bot_retry_worker = bot_retry_worker
    app.state.certificate_scan_worker = certificate_scan_worker
    app.state.terminal_lifecycle_monitor_enabled = terminal_lifecycle_monitor_enabled

    @app.exception_handler(AgentBridgeError)
    async def agentbridge_error_handler(_, exc: AgentBridgeError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.middleware("http")
    async def api_token_gate(request: Request, call_next):
        if not await http_api_request_authorized(request, control=app.state.control):
            return http_api_auth_error_response()
        return await call_next(request)

    @app.get("/admin", response_class=HTMLResponse)
    def admin_home_ui(request: Request):
        return admin_html_response(request, ADMIN_HOME_HTML)

    @app.get("/admin/access-policy", response_class=HTMLResponse)
    def access_policy_admin_ui(request: Request):
        return admin_html_response(request, ACCESS_POLICY_ADMIN_HTML)

    @app.get("/admin/system", response_class=HTMLResponse)
    def system_health_admin_ui(request: Request):
        return admin_html_response(request, SYSTEM_HEALTH_ADMIN_HTML)

    @app.get("/admin/projects", response_class=HTMLResponse)
    def project_session_admin_ui(request: Request):
        return admin_html_response(request, PROJECT_SESSION_ADMIN_HTML)

    @app.get("/admin/interactions", response_class=HTMLResponse)
    def interaction_admin_ui(request: Request):
        return admin_html_response(request, INTERACTION_ADMIN_HTML)

    @app.get("/admin/audit", response_class=HTMLResponse)
    def audit_events_admin_ui(request: Request):
        return admin_html_response(request, AUDIT_EVENTS_ADMIN_HTML)

    @app.get("/admin/terminal-lifecycle", response_class=HTMLResponse)
    def terminal_lifecycle_admin_ui(request: Request):
        return admin_html_response(request, TERMINAL_LIFECYCLE_ADMIN_HTML)

    @app.get("/admin/device-identities", response_class=HTMLResponse)
    def device_identity_admin_ui(request: Request):
        return admin_html_response(request, DEVICE_IDENTITY_ADMIN_HTML)

    @app.get("/admin/bot-delivery", response_class=HTMLResponse)
    def bot_delivery_admin_ui(request: Request):
        return admin_html_response(request, BOT_DELIVERY_ADMIN_HTML)

    def get_control() -> ControlPlane:
        return app.state.control

    def get_commands() -> CommandService:
        return app.state.commands

    def get_terminal() -> TerminalAgentService:
        return app.state.terminal

    def get_bot_gateway() -> BotGatewayService:
        return app.state.bot_gateway

    def get_bot_retry_worker() -> BotDeliveryRetryWorker:
        return app.state.bot_retry_worker

    def get_certificate_scan_worker() -> DeviceCertificateScanWorker:
        return app.state.certificate_scan_worker

    @app.get("/api/v1/health")
    def health(control: ControlPlane = Depends(get_control)):
        return control.health()

    @app.post("/api/v1/chat-contexts")
    def create_chat_context(
        payload: ChatContextPayload, control: ControlPlane = Depends(get_control)
    ):
        context = control.get_or_create_chat_context(**payload.model_dump())
        return context.model_dump(mode="json")

    @app.get("/api/v1/projects")
    def list_projects(control: ControlPlane = Depends(get_control)):
        actor = Actor(id="api", roles={"admin"})
        return [project.model_dump(mode="json") for project in control.list_projects(actor)]

    @app.post("/api/v1/projects")
    def create_project(
        payload: CreateProjectRequest, control: ControlPlane = Depends(get_control)
    ):
        project = control.create_project(
            actor=payload.actor.to_actor(),
            name=payload.name,
            slug=payload.slug,
            aliases=payload.aliases,
            description=payload.description,
            default_agent=payload.default_agent,
            max_active_sessions=payload.max_active_sessions,
            max_running_turns=payload.max_running_turns,
            max_queued_turns=payload.max_queued_turns,
            daily_turns_per_user=payload.daily_turns_per_user,
            trace_id=payload.trace_id,
        )
        return project.model_dump(mode="json")

    @app.get("/api/v1/projects/{project_id}")
    def get_project(project_id: str, control: ControlPlane = Depends(get_control)):
        return control.repository.get_project(project_id).model_dump(mode="json")

    @app.get("/api/v1/projects/{project_id}/workspaces")
    def list_workspaces(project_id: str, control: ControlPlane = Depends(get_control)):
        return [
            workspace.model_dump(mode="json")
            for workspace in control.repository.list_workspaces(project_id)
        ]

    @app.post("/api/v1/projects/{project_id}/workspaces")
    def add_workspace(
        project_id: str,
        payload: CreateWorkspaceRequest,
        control: ControlPlane = Depends(get_control),
    ):
        workspace = control.add_workspace(
            actor=payload.actor.to_actor(),
            project_id=project_id,
            machine_id=payload.machine_id,
            path=payload.path,
            allowed_root=payload.allowed_root,
            workspace_type=payload.workspace_type,
            is_writable=payload.is_writable,
            max_write_sessions=payload.max_write_sessions,
            trace_id=payload.trace_id,
        )
        return workspace.model_dump(mode="json")

    @app.post("/api/v1/chat-spaces/{chat_context_id}/project-bindings")
    def bind_project(
        chat_context_id: str,
        payload: CreateProjectBindingRequest,
        control: ControlPlane = Depends(get_control),
    ):
        control.bind_project(
            actor=payload.actor.to_actor(),
            chat_context_id=chat_context_id,
            project_id=payload.project_id,
            alias_in_chat=payload.alias_in_chat,
            is_default=payload.is_default,
            trace_id=payload.trace_id,
        )
        return {"status": "ok"}

    @app.put("/api/v1/chat-contexts/{chat_context_id}/active-project")
    def update_active_project(
        chat_context_id: str,
        payload: UpdateActiveProjectRequest,
        control: ControlPlane = Depends(get_control),
    ):
        context = control.use_project(
            actor=payload.actor.to_actor(),
            chat_context_id=chat_context_id,
            project_token=payload.project,
            expected_version=payload.expected_version,
            trace_id=payload.trace_id,
        )
        return context.model_dump(mode="json")

    @app.put("/api/v1/chat-contexts/{chat_context_id}/active-session")
    def update_active_session(
        chat_context_id: str,
        payload: UpdateActiveSessionRequest,
        control: ControlPlane = Depends(get_control),
    ):
        context = control.use_session(
            actor=payload.actor.to_actor(),
            chat_context_id=chat_context_id,
            session_token=payload.session,
            expected_version=payload.expected_version,
            trace_id=payload.trace_id,
        )
        return context.model_dump(mode="json")

    @app.get("/api/v1/chat-contexts/{chat_context_id}/roles")
    def list_group_roles(
        chat_context_id: str,
        control: ControlPlane = Depends(get_control),
    ):
        actor = Actor(id="api", roles={"admin"})
        return [
            binding.model_dump(mode="json")
            for binding in control.list_group_role_bindings(
                actor=actor,
                chat_context_id=chat_context_id,
            )
        ]

    @app.post("/api/v1/chat-contexts/{chat_context_id}/roles/grant")
    def grant_group_roles(
        chat_context_id: str,
        payload: GroupRoleChangeRequest,
        control: ControlPlane = Depends(get_control),
    ):
        binding = control.grant_group_roles(
            actor=payload.actor.to_actor(),
            chat_context_id=chat_context_id,
            target_actor_id=payload.target_actor_id,
            roles=payload.roles,
            trace_id=payload.trace_id,
        )
        return binding.model_dump(mode="json")

    @app.post("/api/v1/chat-contexts/{chat_context_id}/roles/revoke")
    def revoke_group_roles(
        chat_context_id: str,
        payload: GroupRoleChangeRequest,
        control: ControlPlane = Depends(get_control),
    ):
        binding = control.revoke_group_roles(
            actor=payload.actor.to_actor(),
            chat_context_id=chat_context_id,
            target_actor_id=payload.target_actor_id,
            roles=payload.roles,
            trace_id=payload.trace_id,
        )
        return binding.model_dump(mode="json") if binding else None

    @app.get("/api/v1/projects/{project_id}/approval-policy")
    def get_project_approval_policy(
        project_id: str,
        control: ControlPlane = Depends(get_control),
    ):
        actor = Actor(id="api", roles={"admin"})
        return control.get_approval_policy_state(
            actor=actor,
            scope_type=PolicyScope.PROJECT,
            scope_id=project_id,
        )

    @app.put("/api/v1/projects/{project_id}/approval-policy")
    def update_project_approval_policy(
        project_id: str,
        payload: ApprovalPolicyOverrideRequest,
        control: ControlPlane = Depends(get_control),
    ):
        override = control.set_approval_policy_override(
            actor=payload.actor.to_actor(),
            scope_type=PolicyScope.PROJECT,
            scope_id=project_id,
            quorum_by_risk=payload.quorum_by_risk,
            trace_id=payload.trace_id,
        )
        return override.model_dump(mode="json")

    @app.get("/api/v1/chat-contexts/{chat_context_id}/approval-policy")
    def get_chat_context_approval_policy(
        chat_context_id: str,
        control: ControlPlane = Depends(get_control),
    ):
        actor = Actor(id="api", roles={"admin"})
        return control.get_approval_policy_state(
            actor=actor,
            scope_type=PolicyScope.CHAT_CONTEXT,
            scope_id=chat_context_id,
            chat_context_id=chat_context_id,
        )

    @app.put("/api/v1/chat-contexts/{chat_context_id}/approval-policy")
    def update_chat_context_approval_policy(
        chat_context_id: str,
        payload: ApprovalPolicyOverrideRequest,
        control: ControlPlane = Depends(get_control),
    ):
        override = control.set_approval_policy_override(
            actor=payload.actor.to_actor(),
            scope_type=PolicyScope.CHAT_CONTEXT,
            scope_id=chat_context_id,
            quorum_by_risk=payload.quorum_by_risk,
            trace_id=payload.trace_id,
            chat_context_id=chat_context_id,
        )
        return override.model_dump(mode="json")

    @app.get("/api/v1/access-policy/rules")
    def list_access_policy_rules(
        enabled: bool | None = None,
        control: ControlPlane = Depends(get_control),
    ):
        actor = Actor(id="api", roles={"admin"})
        return [
            rule.model_dump(mode="json")
            for rule in control.list_access_policy_rules(actor=actor, enabled=enabled)
        ]

    @app.post("/api/v1/access-policy/rules")
    def create_access_policy_rule(
        payload: AccessPolicyRuleRequest,
        control: ControlPlane = Depends(get_control),
    ):
        rule = control.set_access_policy_rule(
            actor=payload.actor.to_actor(),
            rule_id=payload.rule_id,
            effect=payload.effect,
            action=payload.action,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            actor_ids=payload.actor_ids,
            roles=payload.roles,
            attributes=payload.attributes,
            description=payload.description,
            priority=payload.priority,
            enabled=payload.enabled,
            trace_id=payload.trace_id,
            chat_context_id=payload.chat_context_id,
        )
        return rule.model_dump(mode="json")

    @app.put("/api/v1/access-policy/rules/{rule_id}")
    def update_access_policy_rule(
        rule_id: str,
        payload: AccessPolicyRuleRequest,
        control: ControlPlane = Depends(get_control),
    ):
        rule = control.set_access_policy_rule(
            actor=payload.actor.to_actor(),
            rule_id=rule_id,
            effect=payload.effect,
            action=payload.action,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            actor_ids=payload.actor_ids,
            roles=payload.roles,
            attributes=payload.attributes,
            description=payload.description,
            priority=payload.priority,
            enabled=payload.enabled,
            trace_id=payload.trace_id,
            chat_context_id=payload.chat_context_id,
        )
        return rule.model_dump(mode="json")

    @app.post("/api/v1/access-policy/rules/{rule_id}/delete")
    def delete_access_policy_rule(
        rule_id: str,
        payload: AccessPolicyDeleteRequest,
        control: ControlPlane = Depends(get_control),
    ):
        rule = control.delete_access_policy_rule(
            actor=payload.actor.to_actor(),
            rule_id=rule_id,
            trace_id=payload.trace_id,
            chat_context_id=payload.chat_context_id,
        )
        return rule.model_dump(mode="json")

    @app.post("/api/v1/access-policy/simulate")
    def simulate_access_policy(
        payload: AccessPolicySimulationRequest,
        control: ControlPlane = Depends(get_control),
    ):
        return control.simulate_access_policy(
            actor=payload.actor.to_actor(),
            target_actor=payload.target_actor.to_actor(),
            action=payload.action,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            attributes=payload.attributes,
            chat_context_id=payload.chat_context_id,
        )

    @app.get("/api/v1/device-identities")
    def list_device_identities(
        include_revoked: bool = False,
        control: ControlPlane = Depends(get_control),
    ):
        actor = Actor(id="api", roles={"admin"})
        return [
            device_identity_public_payload(identity)
            for identity in control.list_device_identities(
                actor=actor,
                include_revoked=include_revoked,
            )
        ]

    @app.post("/api/v1/device-identities")
    def upsert_device_identity(
        payload: DeviceIdentityRequest,
        control: ControlPlane = Depends(get_control),
    ):
        identity, device_key = control.upsert_device_identity(
            actor=payload.actor.to_actor(),
            device_id=payload.device_id,
            display_name=payload.display_name,
            device_key=payload.device_key,
            allowed_scopes=payload.allowed_scopes,
            allowed_resource_ids=payload.allowed_resource_ids,
            certificate_fingerprints=payload.certificate_fingerprints,
            trace_id=payload.trace_id,
        )
        response = device_identity_public_payload(identity)
        if device_key is not None:
            response["device_key"] = device_key
        return response

    @app.post("/api/v1/device-identities/certificates/scan")
    def scan_device_certificates(
        payload: ScanDeviceCertificatesRequest,
        control: ControlPlane = Depends(get_control),
    ):
        return control.scan_device_identity_certificates(
            actor=payload.actor.to_actor(),
            warning_days=(
                payload.warning_days
                if payload.warning_days is not None
                else device_certificate_expiry_warning_days_from_env()
            ),
            include_revoked=payload.include_revoked,
            trace_id=payload.trace_id,
        )

    @app.get("/api/v1/device-identities/certificates/scan-worker")
    def certificate_scan_worker_status(
        worker: DeviceCertificateScanWorker = Depends(get_certificate_scan_worker),
    ):
        return worker.status()

    @app.post("/api/v1/device-identities/certificates/scan-worker/run-once")
    def run_certificate_scan_worker_once(
        payload: CertificateScanWorkerRunOnceRequest,
        worker: DeviceCertificateScanWorker = Depends(get_certificate_scan_worker),
    ):
        result = worker.run_once(
            actor=payload.actor.to_actor(),
            warning_days=payload.warning_days,
            include_revoked=payload.include_revoked,
            trace_id=payload.trace_id,
        )
        return {
            "worker": worker.status(),
            "result": result,
        }

    @app.post("/api/v1/device-identities/{device_id}/revoke")
    def revoke_device_identity(
        device_id: str,
        payload: RevokeDeviceIdentityRequest,
        control: ControlPlane = Depends(get_control),
    ):
        identity = control.revoke_device_identity(
            actor=payload.actor.to_actor(),
            device_id=device_id,
            trace_id=payload.trace_id,
        )
        return device_identity_public_payload(identity)

    @app.post("/api/v1/device-identities/{device_id}/certificate-fingerprints/rotate")
    def rotate_device_certificate_fingerprints(
        device_id: str,
        payload: RotateDeviceCertificateFingerprintsRequest,
        control: ControlPlane = Depends(get_control),
    ):
        identity = control.rotate_device_identity_certificate_fingerprints(
            actor=payload.actor.to_actor(),
            device_id=device_id,
            add_fingerprints=payload.add_fingerprints,
            remove_fingerprints=payload.remove_fingerprints,
            trace_id=payload.trace_id,
        )
        return device_identity_public_payload(identity)

    @app.post("/api/v1/device-identities/{device_id}/certificates/issue")
    def issue_device_certificate(
        device_id: str,
        payload: IssueDeviceCertificateRequest,
        control: ControlPlane = Depends(get_control),
    ):
        identity, issued_certificate = control.issue_device_identity_certificate(
            actor=payload.actor.to_actor(),
            device_id=device_id,
            csr_pem=payload.csr_pem,
            issuer=create_device_certificate_issuer_from_env(),
            validity_days=payload.validity_days,
            trace_id=payload.trace_id,
        )
        response = {
            "device_identity": device_identity_public_payload(identity),
            **issued_certificate.to_payload(),
        }
        return response

    @app.post("/api/v1/device-identities/{device_id}/certificates/renew")
    def renew_device_certificate(
        device_id: str,
        payload: RenewDeviceCertificateRequest,
        control: ControlPlane = Depends(get_control),
    ):
        (
            identity,
            issued_certificate,
            replaced_fingerprints,
        ) = control.renew_device_identity_certificate(
            actor=payload.actor.to_actor(),
            device_id=device_id,
            csr_pem=payload.csr_pem,
            issuer=create_device_certificate_issuer_from_env(),
            validity_days=payload.validity_days,
            trace_id=payload.trace_id,
        )
        response = {
            "device_identity": device_identity_public_payload(identity),
            "replaced_certificate_fingerprints": replaced_fingerprints,
            **issued_certificate.to_payload(),
        }
        return response

    @app.get("/api/v1/sessions")
    def list_sessions(
        control: ControlPlane = Depends(get_control), project_id: str | None = None
    ):
        actor = Actor(id="api", roles={"admin"})
        return [
            session.model_dump(mode="json")
            for session in control.list_sessions(actor, project_id=project_id)
        ]

    @app.post("/api/v1/sessions")
    def create_session(
        payload: CreateSessionRequest, control: ControlPlane = Depends(get_control)
    ):
        session = control.create_session(
            actor=payload.actor.to_actor(),
            project_id=payload.project_id,
            workspace_id=payload.workspace_id,
            name=payload.name,
            agent_type=payload.agent_type,
            visibility=payload.visibility,
            trace_id=payload.trace_id,
        )
        return session.model_dump(mode="json")

    @app.get("/api/v1/sessions/{session_id}")
    def get_session(session_id: str, control: ControlPlane = Depends(get_control)):
        return control.repository.get_session(session_id).model_dump(mode="json")

    @app.post("/api/v1/sessions/{session_id}/turns")
    def enqueue_turn(
        session_id: str,
        payload: CreateTurnRequest,
        control: ControlPlane = Depends(get_control),
    ):
        turn = control.enqueue_turn(
            actor=payload.actor.to_actor(),
            session_id=session_id,
            prompt=payload.prompt,
            trace_id=payload.trace_id,
        )
        return turn.model_dump(mode="json")

    @app.get("/api/v1/sessions/{session_id}/queue")
    def list_turn_queue(session_id: str, control: ControlPlane = Depends(get_control)):
        turns, queue_version, queue_paused = control.list_turn_queue(
            actor=Actor(id="api", roles={"admin"}),
            session_id=session_id,
        )
        return {
            "queue_version": queue_version,
            "queue_paused": queue_paused,
            "turns": [turn.model_dump(mode="json") for turn in turns],
        }

    @app.get("/api/v1/sessions/{session_id}/lease")
    def get_session_lease(session_id: str, control: ControlPlane = Depends(get_control)):
        lease = control.get_session_lease(
            actor=Actor(id="api", roles={"admin"}),
            session_id=session_id,
        )
        return lease.model_dump(mode="json") if lease else None

    @app.post("/api/v1/sessions/{session_id}/queue/reorder")
    def reorder_turn_queue(
        session_id: str,
        payload: QueueReorderRequest,
        control: ControlPlane = Depends(get_control),
    ):
        turns, queue_version = control.reorder_turn_queue(
            actor=payload.actor.to_actor(),
            session_id=session_id,
            turn_id=payload.turn_id,
            before_turn_id=payload.before_turn_id,
            expected_queue_version=payload.expected_queue_version,
            trace_id=payload.trace_id,
        )
        return {
            "queue_version": queue_version,
            "turns": [turn.model_dump(mode="json") for turn in turns],
        }

    @app.post("/api/v1/sessions/{session_id}/queue/pause")
    def pause_turn_queue(
        session_id: str,
        payload: QueueStateRequest,
        control: ControlPlane = Depends(get_control),
    ):
        session, queue_version = control.set_turn_queue_paused(
            actor=payload.actor.to_actor(),
            session_id=session_id,
            paused=True,
            expected_queue_version=payload.expected_queue_version,
            trace_id=payload.trace_id,
        )
        return {
            "queue_version": queue_version,
            "queue_paused": session.queue_paused,
            "session": session.model_dump(mode="json"),
        }

    @app.post("/api/v1/sessions/{session_id}/queue/resume")
    def resume_turn_queue(
        session_id: str,
        payload: QueueStateRequest,
        control: ControlPlane = Depends(get_control),
    ):
        session, queue_version = control.set_turn_queue_paused(
            actor=payload.actor.to_actor(),
            session_id=session_id,
            paused=False,
            expected_queue_version=payload.expected_queue_version,
            trace_id=payload.trace_id,
        )
        return {
            "queue_version": queue_version,
            "queue_paused": session.queue_paused,
            "session": session.model_dump(mode="json"),
        }

    @app.delete("/api/v1/sessions/{session_id}/queue/{turn_id}")
    def remove_queued_turn(
        session_id: str,
        turn_id: str,
        payload: QueueMutationRequest,
        control: ControlPlane = Depends(get_control),
    ):
        turn, queue_version = control.remove_queued_turn(
            actor=payload.actor.to_actor(),
            session_id=session_id,
            turn_id=turn_id,
            trace_id=payload.trace_id,
            expected_queue_version=payload.expected_queue_version,
        )
        return {
            "queue_version": queue_version,
            "turn": turn.model_dump(mode="json"),
        }

    @app.post("/api/v1/sessions/{session_id}/queue/clear")
    def clear_turn_queue(
        session_id: str,
        payload: QueueMutationRequest,
        control: ControlPlane = Depends(get_control),
    ):
        turns, queue_version = control.clear_turn_queue(
            actor=payload.actor.to_actor(),
            session_id=session_id,
            trace_id=payload.trace_id,
            expected_queue_version=payload.expected_queue_version,
            confirmed_count=payload.confirm_count,
        )
        return {
            "queue_version": queue_version,
            "count": len(turns),
            "turns": [turn.model_dump(mode="json") for turn in turns],
        }

    @app.post("/api/v1/sessions/{session_id}/close")
    def close_session(
        session_id: str,
        payload: ActorPayload,
        control: ControlPlane = Depends(get_control),
    ):
        session = control.close_session(
            actor=payload.to_actor(),
            session_id=session_id,
            trace_id="api",
        )
        return session.model_dump(mode="json")

    @app.post("/api/v1/sessions/{session_id}/lease/acquire")
    def acquire_lease(
        session_id: str,
        payload: AcquireLeaseRequest,
        control: ControlPlane = Depends(get_control),
    ):
        lease = control.acquire_lease(
            actor=payload.actor.to_actor(),
            session_id=session_id,
            owner_type=payload.owner_type,
            owner_id=payload.owner_id,
            ttl_seconds=payload.ttl_seconds,
            trace_id=payload.trace_id,
        )
        return lease.model_dump(mode="json")

    @app.post("/api/v1/sessions/{session_id}/lease/release")
    def release_lease(
        session_id: str,
        payload: ReleaseLeaseRequest,
        control: ControlPlane = Depends(get_control),
    ):
        next_epoch = control.release_lease(
            actor=payload.actor.to_actor(),
            session_id=session_id,
            epoch=payload.epoch,
            trace_id=payload.trace_id,
        )
        return {"next_epoch": next_epoch}

    @app.get("/api/v1/sessions/{session_id}/events")
    def list_events(
        session_id: str,
        control: ControlPlane = Depends(get_control),
        after_seq: int | None = None,
        limit: int = 100,
    ):
        control.repository.get_session(session_id)
        events = control.repository.list_events(
            session_id=session_id,
            after_seq=after_seq,
            limit=limit,
        )
        return [event.model_dump(mode="json") for event in events]

    @app.get("/api/v1/events")
    def search_events(
        control: ControlPlane = Depends(get_control),
        project_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        interaction_id: str | None = None,
        event_type: str | None = None,
        source: SemanticEventSource | None = None,
        trace_id: str | None = None,
        q: str | None = None,
        payload_field: str | None = None,
        payload_value: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 100,
    ):
        if session_id is not None:
            control.repository.get_session(session_id)
        elif project_id is not None:
            control.repository.get_project(project_id)
        events = control.repository.list_semantic_events(
            project_id=project_id,
            session_id=session_id,
            turn_id=turn_id,
            interaction_id=interaction_id,
            event_type=event_type,
            source=source,
            trace_id=trace_id,
            payload_query=q,
            payload_field=payload_field,
            payload_value=payload_value,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
        )
        return [event.model_dump(mode="json") for event in events]

    @app.get("/api/v1/events/rendered")
    def search_rendered_events(
        control: ControlPlane = Depends(get_control),
        project_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        interaction_id: str | None = None,
        event_type: str | None = None,
        source: SemanticEventSource | None = None,
        trace_id: str | None = None,
        q: str | None = None,
        payload_field: str | None = None,
        payload_value: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 100,
    ):
        if session_id is not None:
            control.repository.get_session(session_id)
        elif project_id is not None:
            control.repository.get_project(project_id)
        events = control.repository.list_semantic_events(
            project_id=project_id,
            session_id=session_id,
            turn_id=turn_id,
            interaction_id=interaction_id,
            event_type=event_type,
            source=source,
            trace_id=trace_id,
            payload_query=q,
            payload_field=payload_field,
            payload_value=payload_value,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
        )
        renderer = OneBotV11TextRenderer()
        return [
            rendered_event_payload(event=event, renderer=renderer)
            for event in events
        ]

    @app.get("/api/v1/sessions/{session_id}/rendered-events")
    def list_rendered_events(
        session_id: str,
        control: ControlPlane = Depends(get_control),
        after_seq: int | None = None,
        limit: int = 100,
    ):
        control.repository.get_session(session_id)
        events = control.repository.list_events(
            session_id=session_id,
            after_seq=after_seq,
            limit=limit,
        )
        renderer = OneBotV11TextRenderer()
        rendered = []
        for event in events:
            rendered.append(rendered_event_payload(event=event, renderer=renderer))
        return rendered

    @app.websocket("/api/v1/sessions/{session_id}/events/ws")
    async def stream_events(
        websocket: WebSocket,
        session_id: str,
        control: ControlPlane = Depends(get_control),
        after_seq: int | None = None,
        limit: int = 100,
        poll_interval_seconds: float = 0.25,
        idle_timeout_seconds: float | None = None,
        token: str | None = None,
    ):
        await stream_session_events(
            websocket=websocket,
            control=control,
            session_id=session_id,
            after_seq=after_seq,
            limit=limit,
            poll_interval_seconds=poll_interval_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
            rendered=False,
            token=token,
        )

    @app.websocket("/api/v1/sessions/{session_id}/rendered-events/ws")
    async def stream_rendered_events(
        websocket: WebSocket,
        session_id: str,
        control: ControlPlane = Depends(get_control),
        after_seq: int | None = None,
        limit: int = 100,
        poll_interval_seconds: float = 0.25,
        idle_timeout_seconds: float | None = None,
        token: str | None = None,
    ):
        await stream_session_events(
            websocket=websocket,
            control=control,
            session_id=session_id,
            after_seq=after_seq,
            limit=limit,
            poll_interval_seconds=poll_interval_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
            rendered=True,
            token=token,
        )

    @app.post("/api/v1/sessions/{session_id}/events")
    def ingest_session_event(
        session_id: str,
        payload: IngestSessionEventRequest,
        control: ControlPlane = Depends(get_control),
    ):
        event = control.ingest_session_event(
            session_id=session_id,
            event_type=payload.type,
            source=payload.source,
            trace_id=payload.trace_id,
            turn_id=payload.turn_id,
            interaction_id=payload.interaction_id,
            payload=payload.payload,
            idempotency_key=payload.idempotency_key,
        )
        return event.model_dump(mode="json")

    @app.post("/api/v1/sessions/{session_id}/interactions")
    def create_interaction(
        session_id: str,
        payload: CreateInteractionRequest,
        control: ControlPlane = Depends(get_control),
    ):
        expires_at = payload.expires_at
        if expires_at is None and payload.ttl_seconds is not None:
            expires_at = utc_now() + timedelta(seconds=payload.ttl_seconds)
        interaction = control.create_interaction(
            actor=payload.actor.to_actor(),
            session_id=session_id,
            interaction_type=payload.type,
            prompt=payload.prompt,
            turn_id=payload.turn_id,
            options=payload.options,
            required_votes=payload.required_votes,
            risk_level=payload.risk_level,
            expires_at=expires_at,
            trace_id=payload.trace_id,
            chat_context_id=payload.chat_context_id,
        )
        return interaction.model_dump(mode="json")

    @app.get("/api/v1/interactions")
    def list_interactions(
        control: ControlPlane = Depends(get_control),
        session_id: str | None = None,
        status: InteractionStatus | None = None,
    ):
        actor = Actor(id="api", roles={"admin"})
        return [
            interaction.model_dump(mode="json")
            for interaction in control.list_interactions(
                actor=actor,
                session_id=session_id,
                status=status,
            )
        ]

    @app.get("/api/v1/interactions/{interaction_id}")
    def get_interaction(
        interaction_id: str,
        control: ControlPlane = Depends(get_control),
    ):
        actor = Actor(id="api", roles={"admin"})
        interaction = control.get_interaction(actor=actor, interaction_id=interaction_id)
        return interaction.model_dump(mode="json")

    @app.post("/api/v1/interactions/{interaction_id}/answer")
    def answer_interaction(
        interaction_id: str,
        payload: AnswerInteractionRequest,
        control: ControlPlane = Depends(get_control),
    ):
        interaction = control.answer_interaction(
            actor=payload.actor.to_actor(),
            interaction_id=interaction_id,
            answer=payload.answer,
            trace_id=payload.trace_id,
            chat_context_id=payload.chat_context_id,
        )
        return interaction.model_dump(mode="json")

    @app.post("/api/v1/interactions/{interaction_id}/cancel")
    def cancel_interaction(
        interaction_id: str,
        payload: CancelInteractionRequest,
        control: ControlPlane = Depends(get_control),
    ):
        interaction = control.cancel_interaction(
            actor=payload.actor.to_actor(),
            interaction_id=interaction_id,
            reason=payload.reason,
            trace_id=payload.trace_id,
            chat_context_id=payload.chat_context_id,
        )
        return interaction.model_dump(mode="json")

    @app.post("/api/v1/interactions/{interaction_id}/vote")
    def vote_interaction(
        interaction_id: str,
        payload: VoteInteractionRequest,
        control: ControlPlane = Depends(get_control),
    ):
        interaction = control.vote_interaction(
            actor=payload.actor.to_actor(),
            interaction_id=interaction_id,
            approve=payload.approve,
            reason=payload.reason,
            trace_id=payload.trace_id,
            chat_context_id=payload.chat_context_id,
        )
        return interaction.model_dump(mode="json")

    @app.post("/api/v1/sessions/{session_id}/terminal/start")
    def start_terminal(
        session_id: str,
        payload: StartTerminalRequest,
        control: ControlPlane = Depends(get_control),
        terminal_service: TerminalAgentService = Depends(get_terminal),
    ):
        actor = payload.actor.to_actor()
        launch_profile = terminal_service.resolve_start_command(
            session_id=session_id,
            command=payload.command,
        )
        control.require_terminal_control(
            actor,
            session_id=session_id,
            attributes={
                "operation": "terminal_start",
                "command": launch_profile.command,
                "command_source": launch_profile.source,
                "agent_type": launch_profile.agent_type.value,
            },
        )
        terminal_service.start_session(
            session_id=session_id,
            command=payload.command,
            trace_id=payload.trace_id,
        )
        return {"status": "started"}

    @app.post("/api/v1/sessions/{session_id}/terminal/restart")
    def restart_terminal(
        session_id: str,
        payload: RestartTerminalRequest,
        control: ControlPlane = Depends(get_control),
        terminal_service: TerminalAgentService = Depends(get_terminal),
    ):
        actor = payload.actor.to_actor()
        command = terminal_service.resolve_restart_command(
            session_id=session_id,
            command=payload.command,
        )
        control.require_terminal_control(
            actor,
            session_id=session_id,
            attributes={
                "operation": "terminal_restart",
                "command": command,
                "uses_previous_command": payload.command is None,
            },
        )
        return terminal_service.restart_session(
            session_id=session_id,
            command=command,
            trace_id=payload.trace_id,
        ).to_payload()

    @app.post("/api/v1/sessions/{session_id}/terminal/input")
    def submit_terminal_input(
        session_id: str,
        payload: TerminalInputRequest,
        control: ControlPlane = Depends(get_control),
        terminal_service: TerminalAgentService = Depends(get_terminal),
    ):
        actor = payload.actor.to_actor()
        control.require_terminal_control(
            actor,
            session_id=session_id,
            attributes={
                "operation": "terminal_input",
                "owner_type": payload.owner_type.value,
                "owner_id": payload.owner_id,
                "input_type": payload.type.value,
            },
        )
        request_id = terminal_service.submit_input(
            session_id=session_id,
            epoch=payload.epoch,
            owner_type=payload.owner_type,
            owner_id=payload.owner_id,
            kind=payload.type,
            data=payload.data,
            trace_id=payload.trace_id,
            request_id=payload.request_id,
            cols=payload.cols,
            rows=payload.rows,
        )
        return {"request_id": request_id}

    @app.get("/api/v1/sessions/{session_id}/terminal/snapshot")
    def terminal_snapshot(
        session_id: str,
        control: ControlPlane = Depends(get_control),
        terminal_service: TerminalAgentService = Depends(get_terminal),
    ):
        actor = Actor(id="api", roles={"admin"})
        control.require_session_permission(
            actor,
            Permission.SESSION_VIEW,
            session_id=session_id,
            attributes={"operation": "terminal_snapshot"},
        )
        return {"snapshot": terminal_service.snapshot(session_id=session_id)}

    @app.get("/api/v1/sessions/{session_id}/terminal/status")
    def terminal_status(
        session_id: str,
        control: ControlPlane = Depends(get_control),
        terminal_service: TerminalAgentService = Depends(get_terminal),
    ):
        actor = Actor(id="api", roles={"admin"})
        control.require_session_permission(
            actor,
            Permission.SESSION_VIEW,
            session_id=session_id,
            attributes={"operation": "terminal_status"},
        )
        return terminal_service.status(
            session_id=session_id,
            trace_id="terminal-status",
        ).to_payload()

    @app.get("/api/v1/terminal/lifecycle-monitor")
    def terminal_lifecycle_monitor_status(
        control: ControlPlane = Depends(get_control),
        terminal_service: TerminalAgentService = Depends(get_terminal),
    ):
        actor = Actor(id="api", roles={"admin"})
        control.require_collection_permission(
            actor,
            Permission.AUDIT_VIEW,
            resource_type="terminal_lifecycle",
            attributes={"operation": "terminal_lifecycle_status"},
        )
        return terminal_service.lifecycle_monitor_status()

    @app.post("/api/v1/terminal/lifecycle-monitor/run-once")
    def run_terminal_lifecycle_monitor_once(
        payload: TerminalLifecycleRunOnceRequest,
        control: ControlPlane = Depends(get_control),
        terminal_service: TerminalAgentService = Depends(get_terminal),
    ):
        control.require_collection_permission(
            payload.actor.to_actor(),
            Permission.TERMINAL_CONTROL,
            resource_type="terminal_lifecycle",
            attributes={"operation": "terminal_lifecycle_run_once"},
        )
        observed = terminal_service.run_lifecycle_monitor_once(trace_id=payload.trace_id)
        return {
            "monitor": terminal_service.lifecycle_monitor_status(),
            "observed": {
                session_id: status.to_payload()
                for session_id, status in observed.items()
            },
        }

    @app.post("/api/v1/terminal/agent-launch/probe")
    def probe_terminal_agent_launch_versions(
        payload: AgentLaunchProbeRequest,
        control: ControlPlane = Depends(get_control),
        terminal_service: TerminalAgentService = Depends(get_terminal),
    ):
        control.require_collection_permission(
            payload.actor.to_actor(),
            Permission.TERMINAL_CONTROL,
            resource_type="terminal_lifecycle",
            attributes={
                "operation": "terminal_agent_launch_probe",
                "agent_types": [
                    agent_type.value for agent_type in (payload.agent_types or [])
                ],
            },
        )
        return {
            "profiles": terminal_service.probe_agent_launch_versions(
                agent_types=payload.agent_types,
                timeout_seconds=payload.timeout_seconds,
            )
        }

    @app.post("/api/v1/terminal/agent-adapters/detect")
    def detect_terminal_agent_adapter_capabilities(
        payload: AgentAdapterDetectRequest,
        control: ControlPlane = Depends(get_control),
        terminal_service: TerminalAgentService = Depends(get_terminal),
    ):
        control.require_collection_permission(
            payload.actor.to_actor(),
            Permission.TERMINAL_CONTROL,
            resource_type="terminal_lifecycle",
            attributes={
                "operation": "terminal_agent_adapter_detect",
                "agent_types": [
                    agent_type.value for agent_type in (payload.agent_types or [])
                ],
            },
        )
        return {
            "adapters": terminal_service.detect_agent_adapter_capabilities(
                agent_types=payload.agent_types,
                timeout_seconds=payload.timeout_seconds,
            )
        }

    @app.websocket("/api/v1/sessions/{session_id}/terminal/ws")
    async def terminal_command_websocket(
        websocket: WebSocket,
        session_id: str,
        control: ControlPlane = Depends(get_control),
        terminal_service: TerminalAgentService = Depends(get_terminal),
        token: str | None = None,
    ):
        await stream_terminal_commands(
            websocket=websocket,
            control=control,
            terminal_service=terminal_service,
            session_id=session_id,
            token=token,
        )

    @app.post("/api/v1/commands/parse")
    def parse_command(
        payload: CommandRequest,
        command_service: CommandService = Depends(get_commands),
        control: ControlPlane = Depends(get_control),
    ):
        context_id = ensure_chat_context_id(payload, control)
        invocation = command_service.parse(
            raw_text=payload.raw_text,
            actor=payload.actor.to_actor(),
            chat_context_id=context_id,
            idempotency_key=payload.idempotency_key,
            trace_id=payload.trace_id,
        )
        return invocation.model_dump(mode="json")

    @app.post("/api/v1/commands/execute")
    def execute_command(
        payload: CommandRequest,
        command_service: CommandService = Depends(get_commands),
        control: ControlPlane = Depends(get_control),
    ):
        context_id = ensure_chat_context_id(payload, control)
        invocation = command_service.parse(
            raw_text=payload.raw_text,
            actor=payload.actor.to_actor(),
            chat_context_id=context_id,
            idempotency_key=payload.idempotency_key,
            trace_id=payload.trace_id,
        )
        result = command_service.execute(invocation)
        return result.model_dump(mode="json")

    @app.get("/api/v1/commands")
    def list_commands():
        return {
            "commands": [
                "help",
                "health",
                "project list/info/use/create",
                "session list/new/use/info/close",
                "ask/send",
                "control status/takeover/release",
                "role list/grant/revoke",
                "policy show/set",
                "approvals/approval show/approval cancel/approve/deny/answer",
            ]
        }

    @app.post("/api/v1/bot-gateway/deliver-session-events")
    def deliver_session_events(
        payload: DeliverSessionEventsRequest,
        bot_gateway_service: BotGatewayService = Depends(get_bot_gateway),
    ):
        records = bot_gateway_service.deliver_session_events(
            session_id=payload.session_id,
            chat_context_id=payload.chat_context_id,
            platform=payload.platform,
            after_seq=payload.after_seq,
            limit=payload.limit,
        )
        return [record.model_dump(mode="json") for record in records]

    @app.post("/api/v1/bot-gateway/deliver-events")
    def deliver_events(
        payload: DeliverEventsRequest,
        bot_gateway_service: BotGatewayService = Depends(get_bot_gateway),
    ):
        records = bot_gateway_service.deliver_events(
            chat_context_id=payload.chat_context_id,
            platform=payload.platform,
            project_id=payload.project_id,
            session_id=payload.session_id,
            turn_id=payload.turn_id,
            interaction_id=payload.interaction_id,
            event_type=payload.event_type,
            source=payload.source,
            trace_id=payload.trace_id,
            payload_query=payload.q,
            payload_field=payload.payload_field,
            payload_value=payload.payload_value,
            limit=payload.limit,
        )
        return [record.model_dump(mode="json") for record in records]

    @app.websocket("/api/v1/bot-gateway/session-events/ws")
    async def stream_bot_gateway_session_events(
        websocket: WebSocket,
        session_id: str,
        chat_context_id: str,
        control: ControlPlane = Depends(get_control),
        bot_gateway_service: BotGatewayService = Depends(get_bot_gateway),
        platform: BotPlatform = BotPlatform.ONEBOT_V11,
        after_seq: int | None = None,
        limit: int = 100,
        poll_interval_seconds: float = 0.25,
        idle_timeout_seconds: float | None = None,
        token: str | None = None,
    ):
        await stream_bot_gateway_events(
            websocket=websocket,
            control=control,
            bot_gateway_service=bot_gateway_service,
            session_id=session_id,
            chat_context_id=chat_context_id,
            platform=platform,
            after_seq=after_seq,
            limit=limit,
            poll_interval_seconds=poll_interval_seconds,
            idle_timeout_seconds=idle_timeout_seconds,
            token=token,
        )

    @app.post("/api/v1/bot-gateway/retry-failed-deliveries")
    def retry_failed_deliveries(
        payload: RetryBotDeliveriesRequest,
        bot_gateway_service: BotGatewayService = Depends(get_bot_gateway),
    ):
        records = bot_gateway_service.retry_failed_deliveries(
            chat_context_id=payload.chat_context_id,
            limit=payload.limit,
        )
        return [record.model_dump(mode="json") for record in records]

    @app.post("/api/v1/bot-gateway/delivery-results")
    def record_bot_delivery_result(
        payload: BotDeliveryResultRequest,
        bot_gateway_service: BotGatewayService = Depends(get_bot_gateway),
    ):
        record = bot_gateway_service.record_delivery_result(
            idempotency_key=payload.idempotency_key,
            action=payload.action,
            platform_message_id=payload.platform_message_id,
            text=payload.text,
            error=payload.error,
            payload=payload.payload,
            occurred_at=payload.occurred_at,
        )
        return record.model_dump(mode="json")

    @app.post("/api/v1/bot-gateway/deliveries/edit")
    def edit_bot_delivery(
        payload: EditBotDeliveryRequest,
        bot_gateway_service: BotGatewayService = Depends(get_bot_gateway),
    ):
        record = bot_gateway_service.edit_delivery(
            idempotency_key=payload.idempotency_key,
            text=payload.text,
            payload=payload.payload,
            occurred_at=payload.occurred_at,
        )
        return record.model_dump(mode="json")

    @app.post("/api/v1/bot-gateway/deliveries/delete")
    def delete_bot_delivery(
        payload: DeleteBotDeliveryRequest,
        bot_gateway_service: BotGatewayService = Depends(get_bot_gateway),
    ):
        record = bot_gateway_service.delete_delivery(
            idempotency_key=payload.idempotency_key,
            payload=payload.payload,
            occurred_at=payload.occurred_at,
        )
        return record.model_dump(mode="json")

    @app.get("/api/v1/bot-gateway/deliveries")
    def list_bot_deliveries(
        bot_gateway_service: BotGatewayService = Depends(get_bot_gateway),
        chat_context_id: str | None = None,
        status: BotDeliveryStatus | None = None,
    ):
        return [
            record.model_dump(mode="json")
            for record in bot_gateway_service.list_records(chat_context_id, status=status)
        ]

    @app.get("/api/v1/bot-gateway/rate-limits")
    def list_bot_rate_limits(
        bot_gateway_service: BotGatewayService = Depends(get_bot_gateway),
    ):
        return {"policies": bot_gateway_service.rate_limiter.describe()}

    @app.get("/api/v1/bot-gateway/retry-worker")
    def bot_retry_worker_status(
        retry_worker: BotDeliveryRetryWorker = Depends(get_bot_retry_worker),
    ):
        return retry_worker.status()

    @app.post("/api/v1/bot-gateway/retry-worker/run-once")
    def run_bot_retry_worker_once(
        payload: RetryWorkerRunOnceRequest,
        retry_worker: BotDeliveryRetryWorker = Depends(get_bot_retry_worker),
    ):
        records = retry_worker.run_once(
            chat_context_id=payload.chat_context_id,
            limit=payload.limit,
        )
        return {
            "worker": retry_worker.status(),
            "records": [record.model_dump(mode="json") for record in records],
        }

    @app.post("/api/v1/onebot/events")
    def receive_onebot_event(
        payload: OneBotEventRequest,
        command_service: CommandService = Depends(get_commands),
        control: ControlPlane = Depends(get_control),
    ):
        adapter = OneBotInboundAdapter(
            bot_instance_id=payload.bot_instance_id,
            default_roles=payload.default_roles,
        )
        inbound = adapter.command_from_event(dict(payload.event))
        if inbound is None:
            return {"handled": False}
        return execute_onebot_inbound_command(
            inbound,
            command_service=command_service,
            control=control,
        )

    @app.get("/api/v1/audit")
    def list_audit(
        control: ControlPlane = Depends(get_control),
        actor_id: str | None = None,
        action: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        interaction_id: str | None = None,
        trace_id: str | None = None,
        q: str | None = None,
        details_field: str | None = None,
        details_value: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 100,
    ):
        events = list_audit_events_for_export(
            control,
            actor_id=actor_id,
            action=action,
            project_id=project_id,
            session_id=session_id,
            interaction_id=interaction_id,
            trace_id=trace_id,
            payload_query=q,
            details_field=details_field,
            details_value=details_value,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
        )
        return [event.model_dump(mode="json") for event in events]

    @app.get("/api/v1/audit/export")
    def export_audit(
        control: ControlPlane = Depends(get_control),
        actor_id: str | None = None,
        action: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        interaction_id: str | None = None,
        trace_id: str | None = None,
        q: str | None = None,
        details_field: str | None = None,
        details_value: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 100,
        format: str = "json",
    ):
        filters = audit_export_filter_payload(
            actor_id=actor_id,
            action=action,
            project_id=project_id,
            session_id=session_id,
            interaction_id=interaction_id,
            trace_id=trace_id,
            q=q,
            details_field=details_field,
            details_value=details_value,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
        )
        events = list_audit_events_for_export(
            control,
            actor_id=actor_id,
            action=action,
            project_id=project_id,
            session_id=session_id,
            interaction_id=interaction_id,
            trace_id=trace_id,
            payload_query=q,
            details_field=details_field,
            details_value=details_value,
            created_from=created_from,
            created_to=created_to,
            limit=limit,
        )
        normalized_format = format.strip().lower()
        if normalized_format == "json":
            return JSONResponse(
                content={
                    "format": "json",
                    "count": len(events),
                    "records": [event.model_dump(mode="json") for event in events],
                },
                headers={
                    "content-disposition": 'attachment; filename="agentbridge-audit.json"'
                },
            )
        if normalized_format == "csv":
            return Response(
                content=audit_events_to_csv(events),
                media_type="text/csv; charset=utf-8",
                headers={
                    "content-disposition": 'attachment; filename="agentbridge-audit.csv"'
                },
            )
        if normalized_format in {"archive", "signed_archive"}:
            return JSONResponse(
                content=signed_audit_archive(events, filters=filters),
                headers={
                    "content-disposition": (
                        'attachment; filename="agentbridge-audit-archive.json"'
                    )
                },
            )
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "Unsupported audit export format.",
            next_step="Use format=json, format=csv, or format=archive.",
            status_code=400,
        )

    return app


AUDIT_EXPORT_COLUMNS = [
    "id",
    "created_at",
    "action",
    "actor_id",
    "outcome",
    "trace_id",
    "chat_context_id",
    "project_id",
    "session_id",
    "interaction_id",
    "previous_hash",
    "entry_hash",
    "details",
]


def audit_export_filter_payload(
    *,
    actor_id: str | None,
    action: str | None,
    project_id: str | None,
    session_id: str | None,
    interaction_id: str | None,
    trace_id: str | None,
    q: str | None,
    details_field: str | None,
    details_value: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
    limit: int,
) -> dict[str, object]:
    return {
        "actor_id": actor_id,
        "action": action,
        "project_id": project_id,
        "session_id": session_id,
        "interaction_id": interaction_id,
        "trace_id": trace_id,
        "q": q,
        "details_field": details_field,
        "details_value": details_value,
        "created_from": created_from.isoformat() if created_from else None,
        "created_to": created_to.isoformat() if created_to else None,
        "limit": limit,
    }


def list_audit_events_for_export(
    control: ControlPlane,
    *,
    actor_id: str | None,
    action: str | None,
    project_id: str | None,
    session_id: str | None,
    interaction_id: str | None,
    trace_id: str | None,
    payload_query: str | None,
    details_field: str | None,
    details_value: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
    limit: int,
) -> list[AuditEvent]:
    return control.repository.list_audit_events(
        actor_id=actor_id,
        action=action,
        project_id=project_id,
        session_id=session_id,
        interaction_id=interaction_id,
        trace_id=trace_id,
        payload_query=payload_query,
        details_field=details_field,
        details_value=details_value,
        created_from=created_from,
        created_to=created_to,
        limit=limit,
    )


def audit_event_export_row(event: AuditEvent) -> dict[str, str]:
    data = event.model_dump(mode="json")
    return {
        "id": data["id"],
        "created_at": data["created_at"],
        "action": data["action"],
        "actor_id": data["actor_id"],
        "outcome": data["outcome"],
        "trace_id": data["trace_id"],
        "chat_context_id": data.get("chat_context_id") or "",
        "project_id": data.get("project_id") or "",
        "session_id": data.get("session_id") or "",
        "interaction_id": data.get("interaction_id") or "",
        "previous_hash": data.get("previous_hash") or "",
        "entry_hash": data["entry_hash"],
        "details": json.dumps(
            data.get("details") or {},
            ensure_ascii=False,
            sort_keys=True,
        ),
    }


def audit_events_to_csv(events: list[AuditEvent]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=AUDIT_EXPORT_COLUMNS)
    writer.writeheader()
    for event in events:
        writer.writerow(audit_event_export_row(event))
    return output.getvalue()


def canonical_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class AuditArchiveSigner:
    algorithm: str
    key_id: str
    hmac_key: str | None = None
    private_key: Any | None = None
    external_command: tuple[str, ...] | None = None
    external_timeout_seconds: float = 10.0
    public_key_sha256: str | None = None

    def sign(self, canonical_archive: str) -> dict[str, object]:
        data = canonical_archive.encode("utf-8")
        if self.external_command is not None:
            return self._sign_with_external_command(data)
        if self.algorithm == "HMAC-SHA256":
            if self.hmac_key is None:
                raise RuntimeError("hmac signer missing key")
            return {
                "encoding": "hex",
                "value": hmac.new(
                    self.hmac_key.encode("utf-8"),
                    data,
                    hashlib.sha256,
                ).hexdigest(),
            }
        if isinstance(self.private_key, ed25519.Ed25519PrivateKey):
            signature = self.private_key.sign(data)
        elif isinstance(self.private_key, rsa.RSAPrivateKey):
            signature = self.private_key.sign(
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
        elif isinstance(self.private_key, ec.EllipticCurvePrivateKey):
            signature = self.private_key.sign(data, ec.ECDSA(hashes.SHA256()))
        else:
            raise RuntimeError("unsupported audit archive signer")
        payload: dict[str, object] = {
            "encoding": "base64",
            "value": base64.b64encode(signature).decode("ascii"),
        }
        if self.public_key_sha256:
            payload["public_key_sha256"] = self.public_key_sha256
        return payload

    def _sign_with_external_command(self, data: bytes) -> dict[str, object]:
        if not self.external_command:
            raise RuntimeError("external signer missing command")
        archive_sha256 = hashlib.sha256(data).hexdigest()
        env = os.environ.copy()
        env["AGENTBRIDGE_AUDIT_ARCHIVE_SHA256"] = archive_sha256
        env["AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_ALGORITHM"] = self.algorithm
        env["AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_ID"] = self.key_id
        try:
            completed = subprocess.run(
                self.external_command,
                input=data,
                capture_output=True,
                env=env,
                check=False,
                timeout=self.external_timeout_seconds,
            )
        except OSError as exc:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "Audit archive external signing command could not be started.",
                next_step=(
                    "Check AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND "
                    "and executable permissions."
                ),
                status_code=503,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "Audit archive external signing command timed out.",
                next_step=(
                    "Check signer health or increase "
                    "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND_TIMEOUT_SECONDS."
                ),
                status_code=503,
            ) from exc
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "Audit archive external signing command failed.",
                next_step=stderr[:500] or "Check signer logs and KMS/HSM permissions.",
                status_code=503,
            )
        try:
            output = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "Audit archive external signing command returned invalid JSON.",
                next_step=(
                    "Return a JSON object with signature fields "
                    "`encoding` and `value`."
                ),
                status_code=503,
            ) from exc
        if not isinstance(output, dict):
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "Audit archive external signing command returned invalid JSON.",
                next_step="Return a JSON object, not an array or scalar value.",
                status_code=503,
            )
        encoding = str(output.get("encoding") or "base64")
        value = output.get("value")
        if encoding not in {"base64", "hex"} or not isinstance(value, str) or not value:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "Audit archive external signing command returned invalid signature.",
                next_step=(
                    "Return a non-empty `value` with `encoding` set to "
                    "`base64` or `hex`."
                ),
                status_code=503,
            )
        payload: dict[str, object] = {"encoding": encoding, "value": value}
        for key in (
            "public_key_sha256",
            "signing_certificate_sha256",
            "kms_key_version",
            "signature_id",
        ):
            optional_value = output.get(key)
            if isinstance(optional_value, str) and optional_value.strip():
                payload[key] = optional_value.strip()
        metadata = output.get("metadata")
        if isinstance(metadata, dict):
            payload["metadata"] = {
                str(key): value
                for key, value in metadata.items()
                if isinstance(key, str)
                and isinstance(value, str | int | float | bool)
            }
        return payload


def audit_archive_hmac_signing_key() -> tuple[str, str]:
    keys, configured = tokens_from_env("AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY")
    if keys:
        key_id = os.environ.get("AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_ID", "").strip()
        return keys[0], key_id or "default"
    next_step = (
        "Set AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND, "
        "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_PRIVATE_KEY_FILE, "
        "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY, or "
        "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_FILE."
    )
    if configured:
        next_step = "Check that the configured audit archive signing key file is readable."
    raise AgentBridgeError(
        ErrorCode.COMMAND_ARGUMENT_INVALID,
        "Audit archive signing key is not configured.",
        next_step=next_step,
        status_code=400,
    )


def audit_archive_signer() -> AuditArchiveSigner:
    external_signer = audit_archive_external_signer()
    if external_signer is not None:
        return external_signer
    asymmetric_signer = audit_archive_asymmetric_signer()
    if asymmetric_signer is not None:
        return asymmetric_signer
    signing_key, key_id = audit_archive_hmac_signing_key()
    return AuditArchiveSigner(
        algorithm="HMAC-SHA256",
        key_id=key_id,
        hmac_key=signing_key,
    )


def audit_archive_external_signer() -> AuditArchiveSigner | None:
    command = os.environ.get("AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND", "").strip()
    if not command:
        return None
    try:
        command_parts = tuple(shlex.split(command))
    except ValueError as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "Audit archive external signing command is invalid.",
            next_step=(
                "Check shell-style quoting in "
                "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND."
            ),
            status_code=400,
        ) from exc
    if not command_parts:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "Audit archive external signing command is empty.",
            next_step="Set AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND to an executable.",
            status_code=400,
        )
    algorithm = os.environ.get(
        "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND_ALGORITHM",
        "",
    ).strip()
    key_id = os.environ.get("AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_ID", "").strip()
    return AuditArchiveSigner(
        algorithm=algorithm or "EXTERNAL-SHA256",
        key_id=key_id or "external",
        external_command=command_parts,
        external_timeout_seconds=audit_archive_external_signing_timeout_seconds(),
    )


def audit_archive_external_signing_timeout_seconds() -> float:
    value = os.environ.get(
        "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND_TIMEOUT_SECONDS", ""
    ).strip()
    if not value:
        return 10.0
    try:
        timeout = float(value)
    except ValueError as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "Audit archive external signing timeout is invalid.",
            next_step=(
                "Set AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND_TIMEOUT_SECONDS "
                "to a positive number."
            ),
            status_code=400,
        ) from exc
    if timeout <= 0:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "Audit archive external signing timeout is invalid.",
            next_step=(
                "Set AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND_TIMEOUT_SECONDS "
                "to a positive number."
            ),
            status_code=400,
        )
    return min(timeout, 120.0)


def audit_archive_asymmetric_signer() -> AuditArchiveSigner | None:
    private_key_file = os.environ.get(
        "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_PRIVATE_KEY_FILE", ""
    ).strip()
    if not private_key_file:
        return None
    key_id = os.environ.get("AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_ID", "").strip()
    try:
        private_key_pem = Path(private_key_file).expanduser().read_bytes()
    except OSError as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "Audit archive signing private key file is not readable.",
            next_step=(
                "Check AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_PRIVATE_KEY_FILE "
                "and file permissions."
            ),
            status_code=400,
        ) from exc
    try:
        private_key = serialization.load_pem_private_key(
            private_key_pem,
            password=audit_archive_private_key_password(),
        )
    except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "Audit archive signing private key could not be loaded.",
            next_step=(
                "Use an unencrypted PEM private key or configure "
                "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_PRIVATE_KEY_PASSWORD(_FILE)."
            ),
            status_code=400,
        ) from exc
    algorithm = audit_archive_private_key_algorithm(private_key)
    public_key_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return AuditArchiveSigner(
        algorithm=algorithm,
        key_id=key_id or "default-asymmetric",
        private_key=private_key,
        public_key_sha256=hashlib.sha256(public_key_der).hexdigest(),
    )


def audit_archive_private_key_password() -> bytes | None:
    password = os.environ.get("AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_PRIVATE_KEY_PASSWORD", "")
    if password:
        return password.encode("utf-8")
    password_file = os.environ.get(
        "AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_PRIVATE_KEY_PASSWORD_FILE", ""
    ).strip()
    if not password_file:
        return None
    try:
        value = Path(password_file).expanduser().read_text(encoding="utf-8").strip()
        return value.encode("utf-8") if value else None
    except OSError as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "Audit archive signing private key password file is not readable.",
            next_step=(
                "Check AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_PRIVATE_KEY_PASSWORD_FILE "
                "and file permissions."
            ),
            status_code=400,
        ) from exc


def audit_archive_private_key_algorithm(private_key: Any) -> str:
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        return "Ed25519"
    if isinstance(private_key, rsa.RSAPrivateKey):
        return "RSA-PSS-SHA256"
    if isinstance(private_key, ec.EllipticCurvePrivateKey):
        return "ECDSA-SHA256"
    raise AgentBridgeError(
        ErrorCode.COMMAND_ARGUMENT_INVALID,
        "Audit archive signing private key type is not supported.",
        next_step="Use an Ed25519, RSA, or ECDSA PEM private key.",
        status_code=400,
    )


def signed_audit_archive(
    events: list[AuditEvent], *, filters: dict[str, object]
) -> dict[str, object]:
    signer = audit_archive_signer()
    records = [event.model_dump(mode="json") for event in events]
    archive = {
        "format": "signed_audit_archive",
        "version": 1,
        "algorithm": signer.algorithm,
        "exported_at": utc_now().isoformat(),
        "filters": filters,
        "record_count": len(records),
        "newest_entry_hash": records[0]["entry_hash"] if records else None,
        "oldest_entry_hash": records[-1]["entry_hash"] if records else None,
        "records": records,
    }
    canonical_archive = canonical_json(archive)
    archive_sha256 = hashlib.sha256(canonical_archive.encode("utf-8")).hexdigest()
    signature = signer.sign(canonical_archive)
    return {
        "archive": archive,
        "signature": {
            "algorithm": signer.algorithm,
            "key_id": signer.key_id,
            "archive_sha256": archive_sha256,
            **signature,
        },
    }


def ensure_chat_context_id(payload: CommandRequest, control: ControlPlane) -> str:
    if payload.chat_context_id:
        control.repository.get_chat_context(payload.chat_context_id)
        return payload.chat_context_id
    context = control.get_or_create_chat_context(**payload.chat.model_dump())
    return context.id


def rendered_event_payload(
    *,
    event: SemanticEvent,
    renderer: OneBotV11TextRenderer,
) -> dict[str, object]:
    document = document_from_event(event)
    return {
        "event_id": event.id,
        "seq": event.seq,
        "document": document.model_dump(mode="json"),
        "text_messages": renderer.render(document),
    }


def device_identity_public_payload(identity: DeviceIdentity) -> dict[str, object]:
    return {
        "id": identity.id,
        "device_id": identity.device_id,
        "display_name": identity.display_name,
        "status": identity.status.value,
        "allowed_scopes": sorted(scope.value for scope in identity.allowed_scopes),
        "allowed_resource_ids": sorted(identity.allowed_resource_ids),
        "certificate_fingerprints": sorted(identity.certificate_fingerprints),
        "certificate_records": [
            record.model_dump(mode="json") for record in identity.certificate_records
        ],
        "certificate_health": device_identity_certificate_health(
            identity,
            warning_days=device_certificate_expiry_warning_days_from_env(),
        ),
        "created_by": identity.created_by,
        "created_at": identity.created_at.isoformat(),
        "revoked_at": identity.revoked_at.isoformat() if identity.revoked_at else None,
        "last_used_at": (
            identity.last_used_at.isoformat() if identity.last_used_at else None
        ),
    }


def device_certificate_expiry_warning_days_from_env() -> int:
    return max(
        1,
        env_int(
            "AGENTBRIDGE_DEVICE_CERT_EXPIRY_WARNING_DAYS",
            default=14,
        ),
    )


async def stream_session_events(
    *,
    websocket: WebSocket,
    control: ControlPlane,
    session_id: str,
    after_seq: int | None,
    limit: int,
    poll_interval_seconds: float,
    idle_timeout_seconds: float | None,
    rendered: bool,
    token: str | None,
) -> None:
    required_scope = (
        DeviceIdentityScope.RENDERED_EVENTS_WS
        if rendered
        else DeviceIdentityScope.SESSION_EVENTS_WS
    )
    if not await accept_authenticated_websocket(
        websocket,
        token=token,
        control=control,
        required_scope=required_scope,
        resource_ids={session_id},
    ):
        return
    try:
        control.repository.get_session(session_id)
    except AgentBridgeError as exc:
        await websocket.send_json({"type": "error", "error": exc.to_payload()})
        await websocket.close(code=1008)
        return

    batch_limit = clamp_stream_limit(limit)
    poll_interval = clamp_poll_interval(poll_interval_seconds)
    idle_timeout = normalize_idle_timeout(idle_timeout_seconds)
    last_seq = max(after_seq or 0, 0)
    idle_started_at = monotonic()
    renderer = OneBotV11TextRenderer() if rendered else None

    try:
        while True:
            events = control.repository.list_events(
                session_id=session_id,
                after_seq=last_seq,
                limit=batch_limit,
            )
            if events:
                idle_started_at = monotonic()
                for event in events:
                    last_seq = max(last_seq, event.seq)
                    if rendered:
                        document = document_from_event(event)
                        await websocket.send_json(
                            {
                                "type": "rendered_event",
                                "event_id": event.id,
                                "seq": event.seq,
                                "event": event.model_dump(mode="json"),
                                "document": document.model_dump(mode="json"),
                                "text_messages": renderer.render(document) if renderer else [],
                            }
                        )
                    else:
                        await websocket.send_json(
                            {
                                "type": "semantic_event",
                                "event": event.model_dump(mode="json"),
                            }
                        )
                continue

            if idle_timeout is not None and monotonic() - idle_started_at >= idle_timeout:
                await websocket.send_json({"type": "idle_timeout", "last_seq": last_seq})
                await websocket.close(code=1000)
                return

            await asyncio.sleep(poll_interval)
    except WebSocketDisconnect:
        return


async def stream_bot_gateway_events(
    *,
    websocket: WebSocket,
    control: ControlPlane,
    bot_gateway_service: BotGatewayService,
    session_id: str,
    chat_context_id: str,
    platform: BotPlatform,
    after_seq: int | None,
    limit: int,
    poll_interval_seconds: float,
    idle_timeout_seconds: float | None,
    token: str | None,
) -> None:
    if not await accept_authenticated_websocket(
        websocket,
        token=token,
        control=control,
        required_scope=DeviceIdentityScope.BOT_GATEWAY_WS,
        resource_ids={session_id, chat_context_id},
    ):
        return
    try:
        control.repository.get_session(session_id)
        chat_context = control.repository.get_chat_context(chat_context_id)
    except AgentBridgeError as exc:
        await websocket.send_json({"type": "error", "error": exc.to_payload()})
        await websocket.close(code=1008)
        return

    batch_limit = clamp_stream_limit(limit)
    poll_interval = clamp_poll_interval(poll_interval_seconds)
    idle_timeout = normalize_idle_timeout(idle_timeout_seconds)
    last_seq = max(after_seq or 0, 0)
    idle_started_at = monotonic()

    try:
        while True:
            events = control.repository.list_events(
                session_id=session_id,
                after_seq=last_seq,
                limit=batch_limit,
            )
            if events:
                idle_started_at = monotonic()
                for event in events:
                    last_seq = max(last_seq, event.seq)
                    document = document_from_event(event)
                    text_messages = bot_gateway_service.renderer.render(document)
                    await websocket.send_json(
                        bot_gateway_render_frame(
                            session_id=session_id,
                            chat_context=chat_context,
                            platform=platform,
                            event=event,
                            document=document,
                            text_messages=text_messages,
                        )
                    )
                continue

            if idle_timeout is not None and monotonic() - idle_started_at >= idle_timeout:
                await websocket.send_json({"type": "idle_timeout", "last_seq": last_seq})
                await websocket.close(code=1000)
                return

            await asyncio.sleep(poll_interval)
    except WebSocketDisconnect:
        return


def bot_gateway_render_frame(
    *,
    session_id: str,
    chat_context,
    platform: BotPlatform,
    event,
    document: RenderDocument,
    text_messages: list[str],
) -> dict[str, object]:
    return {
        "type": "bot.render.create",
        "event_id": event.id,
        "seq": event.seq,
        "session_id": session_id,
        "chat_context_id": chat_context.id,
        "platform": platform.value,
        "chat": chat_context.model_dump(mode="json"),
        "event": event.model_dump(mode="json"),
        "document": document.model_dump(mode="json"),
        "actions": render_action_descriptors(document.actions),
        "messages": [
            {
                "index": index,
                "idempotency_key": bot_render_idempotency_key(
                    platform=platform,
                    chat_context_id=chat_context.id,
                    event_id=event.id,
                    message_index=index,
                ),
                "text": text,
            }
            for index, text in enumerate(text_messages)
        ],
    }


def bot_render_idempotency_key(
    *,
    platform: BotPlatform,
    chat_context_id: str,
    event_id: str,
    message_index: int,
) -> str:
    return f"{platform.value}:{chat_context_id}:{event_id}:{message_index}"


async def stream_terminal_commands(
    *,
    websocket: WebSocket,
    control: ControlPlane,
    terminal_service: TerminalAgentService,
    session_id: str,
    token: str | None,
) -> None:
    if not await accept_authenticated_websocket(
        websocket,
        token=token,
        control=control,
        required_scope=DeviceIdentityScope.TERMINAL_WS,
        resource_ids={session_id},
    ):
        return
    try:
        control.repository.get_session(session_id)
    except AgentBridgeError as exc:
        await websocket.send_json({"type": "error", "error": exc.to_payload()})
        await websocket.close(code=1008)
        return

    try:
        while True:
            try:
                frame = await websocket.receive_json()
            except ValueError as exc:
                await websocket.send_json(
                    terminal_error_frame(None, invalid_terminal_frame(str(exc)))
                )
                continue
            if not isinstance(frame, dict):
                await websocket.send_json(
                    terminal_error_frame(
                        None,
                        invalid_terminal_frame("WebSocket frame must be a JSON object."),
                    )
                )
                continue
            request_id = frame.get("id")
            action = str(frame.get("type") or frame.get("action") or "")
            try:
                data = handle_terminal_ws_action(
                    control=control,
                    terminal_service=terminal_service,
                    session_id=session_id,
                    action=action,
                    payload=terminal_ws_payload(frame),
                )
                await websocket.send_json(
                    {
                        "type": "terminal.result",
                        "id": request_id,
                        "action": action,
                        "ok": True,
                        "data": data,
                    }
                )
            except AgentBridgeError as exc:
                await websocket.send_json(terminal_error_frame(request_id, exc))
            except (KeyError, TypeError, ValueError) as exc:
                await websocket.send_json(
                    terminal_error_frame(request_id, invalid_terminal_frame(str(exc)))
                )
    except WebSocketDisconnect:
        return


def handle_terminal_ws_action(
    *,
    control: ControlPlane,
    terminal_service: TerminalAgentService,
    session_id: str,
    action: str,
    payload: dict[str, object],
) -> dict[str, object]:
    if action == "health":
        return control.health()
    if action == "start_session":
        actor = actor_from_terminal_ws_payload(payload)
        command_payload = payload.get("command")
        if command_payload is not None and not isinstance(command_payload, str):
            raise TypeError("command must be a string")
        launch_profile = terminal_service.resolve_start_command(
            session_id=session_id,
            command=command_payload,
        )
        control.require_terminal_control(
            actor,
            session_id=session_id,
            attributes={
                "operation": "terminal_ws_start",
                "command": launch_profile.command,
                "command_source": launch_profile.source,
                "agent_type": launch_profile.agent_type.value,
            },
        )
        terminal_service.start_session(
            session_id=session_id,
            command=command_payload,
            trace_id=str(payload.get("trace_id") or "terminal-ws"),
        )
        return {"status": "started"}
    if action == "restart_session":
        actor = actor_from_terminal_ws_payload(payload)
        command_payload = payload.get("command")
        if command_payload is not None and not isinstance(command_payload, str):
            raise TypeError("command must be a string")
        command = terminal_service.resolve_restart_command(
            session_id=session_id,
            command=command_payload,
        )
        control.require_terminal_control(
            actor,
            session_id=session_id,
            attributes={
                "operation": "terminal_ws_restart",
                "command": command,
                "uses_previous_command": command_payload is None,
            },
        )
        return terminal_service.restart_session(
            session_id=session_id,
            command=command,
            trace_id=str(payload.get("trace_id") or "terminal-ws"),
        ).to_payload()
    if action == "acquire_lease":
        actor = actor_from_terminal_ws_payload(payload)
        lease = control.acquire_lease(
            actor=actor,
            session_id=session_id,
            owner_type=LeaseOwnerType(
                str(payload.get("owner_type") or LeaseOwnerType.BOT.value)
            ),
            owner_id=required_ws_str(payload, "owner_id"),
            ttl_seconds=int(payload.get("ttl_seconds") or 300),
            trace_id=str(payload.get("trace_id") or "terminal-ws"),
        )
        return {"lease": lease.model_dump(mode="json")}
    if action == "release_lease":
        actor = actor_from_terminal_ws_payload(payload)
        next_epoch = control.release_lease(
            actor=actor,
            session_id=session_id,
            epoch=int(payload["epoch"]),
            trace_id=str(payload.get("trace_id") or "terminal-ws"),
        )
        return {"next_epoch": next_epoch}
    if action == "submit_input":
        actor = actor_from_terminal_ws_payload(payload)
        control.require_terminal_control(
            actor,
            session_id=session_id,
            attributes={
                "operation": "terminal_ws_input",
                "owner_type": str(payload["owner_type"]),
                "owner_id": required_ws_str(payload, "owner_id"),
                "input_type": str(
                    payload.get("input_type") or payload.get("type") or "text"
                ),
            },
        )
        request_id = payload.get("request_id")
        submitted_id = terminal_service.submit_input(
            session_id=session_id,
            epoch=int(payload["epoch"]),
            owner_type=LeaseOwnerType(str(payload["owner_type"])),
            owner_id=required_ws_str(payload, "owner_id"),
            kind=TerminalInputKind(
                str(payload.get("input_type") or payload.get("type") or "text")
            ),
            data=str(payload.get("data") or ""),
            trace_id=str(payload.get("trace_id") or "terminal-ws"),
            request_id=request_id if isinstance(request_id, str) else None,
            cols=int(payload["cols"]) if payload.get("cols") is not None else None,
            rows=int(payload["rows"]) if payload.get("rows") is not None else None,
        )
        return {"request_id": submitted_id}
    if action == "snapshot":
        actor = actor_from_terminal_ws_payload(payload)
        control.require_session_permission(
            actor,
            Permission.SESSION_VIEW,
            session_id=session_id,
            attributes={"operation": "terminal_ws_snapshot"},
        )
        return {"snapshot": terminal_service.snapshot(session_id=session_id)}
    if action == "status":
        actor = actor_from_terminal_ws_payload(payload)
        control.require_session_permission(
            actor,
            Permission.SESSION_VIEW,
            session_id=session_id,
            attributes={"operation": "terminal_ws_status"},
        )
        return terminal_service.status(
            session_id=session_id,
            trace_id=str(payload.get("trace_id") or "terminal-ws"),
        ).to_payload()
    raise AgentBridgeError(
        ErrorCode.COMMAND_UNKNOWN,
        f"未知 Terminal WebSocket action：{action}",
        next_step=(
            "请使用 health、start_session、restart_session、acquire_lease、release_lease、"
            "submit_input、snapshot 或 status。"
        ),
    )


def terminal_ws_payload(frame: dict[object, object]) -> dict[str, object]:
    payload = frame.get("payload")
    if payload is None:
        payload = {
            key: value for key, value in frame.items() if key not in {"id", "type", "action"}
        }
    if not isinstance(payload, dict):
        raise TypeError("payload must be an object")
    return {str(key): value for key, value in payload.items()}


def actor_from_terminal_ws_payload(payload: dict[str, object]) -> Actor:
    actor_payload = payload.get("actor") or {}
    if not isinstance(actor_payload, dict):
        raise TypeError("actor must be an object")
    return ActorPayload.model_validate(actor_payload).to_actor()


def required_ws_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            f"缺少必需字段：{key}",
            next_step=f"请在 payload 中提供 {key}。",
        )
    return value


def terminal_error_frame(request_id: object, exc: AgentBridgeError) -> dict[str, object]:
    return {
        "type": "terminal.error",
        "id": request_id,
        "ok": False,
        "error": exc.to_payload(),
    }


ADMIN_AUTH_COOKIE_NAME = "agentbridge_admin_token"
ADMIN_AUTH_QUERY_PARAM = "admin_token"


def admin_html_response(request: Request, html: str):
    expected_tokens, token_configured = admin_expected_token_config()
    certificate_configured = client_certificate_fingerprints_configured()
    if not token_configured and not certificate_configured:
        return HTMLResponse(html)

    if client_certificate_authorized(request.headers):
        return HTMLResponse(html)

    presented_token = admin_presented_token(request)
    matched_token = matching_token(presented_token, expected_tokens)
    if matched_token:
        query_token = request.query_params.get(ADMIN_AUTH_QUERY_PARAM)
        response = (
            RedirectResponse(url=request.url.path, status_code=303)
            if query_token
            else HTMLResponse(html)
        )
        response.set_cookie(
            ADMIN_AUTH_COOKIE_NAME,
            matched_token,
            max_age=admin_cookie_max_age_seconds(),
            httponly=True,
            secure=admin_cookie_secure(request),
            samesite="strict",
        )
        return response

    return HTMLResponse(ADMIN_AUTH_REQUIRED_HTML, status_code=401)


def admin_expected_tokens() -> list[str]:
    tokens, _configured = admin_expected_token_config()
    return tokens


def admin_expected_token_config() -> tuple[list[str], bool]:
    return tokens_from_env(
        "AGENTBRIDGE_ADMIN_TOKEN",
        "AGENTBRIDGE_API_TOKEN",
    )


def admin_presented_token(request: Request) -> str | None:
    query_token = request.query_params.get(ADMIN_AUTH_QUERY_PARAM)
    if query_token:
        return query_token
    cookie_token = request.cookies.get(ADMIN_AUTH_COOKIE_NAME)
    if cookie_token:
        return cookie_token
    header_token = request.headers.get("x-agentbridge-admin-token")
    if header_token:
        return header_token.strip()
    authorization = request.headers.get("authorization")
    if not authorization:
        return None
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix) :].strip()
    return authorization.strip()


def admin_cookie_max_age_seconds() -> int:
    raw_value = os.environ.get("AGENTBRIDGE_ADMIN_COOKIE_MAX_AGE_SECONDS", "43200").strip()
    try:
        return max(60, int(raw_value))
    except ValueError:
        return 43200


def admin_cookie_secure(request: Request) -> bool:
    override = os.environ.get("AGENTBRIDGE_ADMIN_COOKIE_SECURE", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    return request.url.scheme == "https"


async def http_api_request_authorized(
    request: Request,
    *,
    control: ControlPlane | None = None,
) -> bool:
    if not request.url.path.startswith("/api/"):
        return True
    if request.url.path == "/api/v1/health":
        return True
    expected_tokens, token_configured = http_api_expected_token_config()
    device_keys = configured_device_keys()
    if (
        not token_configured
        and not device_keys_configured()
        and not managed_device_identities_configured(control)
        and not client_certificate_fingerprints_configured()
    ):
        return True
    required_scope = http_api_required_device_scope(request)
    resource_ids = await http_api_resource_ids(request)
    presented_tokens = http_api_presented_tokens(request)
    return (
        client_certificate_authorized(
            request.headers,
            control=control,
            required_scope=required_scope,
            resource_ids=resource_ids,
        )
        or http_device_key_authorized(
            request,
            device_keys,
            control=control,
            required_scope=required_scope,
            resource_ids=resource_ids,
        )
        or any(
            matching_token(presented_token, expected_tokens)
            for presented_token in presented_tokens
        )
    )


async def http_api_resource_ids(request: Request) -> set[str]:
    path_segments = request.url.path.rstrip("/").split("/")
    resource_ids: set[str] = set()
    resource_collections = {
        "chat-contexts",
        "device-identities",
        "interactions",
        "projects",
        "sessions",
    }
    if (
        len(path_segments) >= 5
        and path_segments[:3] == ["", "api", "v1"]
        and path_segments[3] in resource_collections
        and path_segments[4]
    ):
        resource_ids.add(path_segments[4])
    for param_name in (
        "chat_context_id",
        "device_id",
        "interaction_id",
        "project_id",
        "session_id",
    ):
        value = request.query_params.get(param_name)
        if value and value.strip():
            resource_ids.add(value.strip())
    resource_ids.update(await http_api_body_resource_ids(request))
    return resource_ids


async def http_api_body_resource_ids(request: Request) -> set[str]:
    if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return set()
    if not http_request_has_json_body(request):
        return set()
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > http_api_resource_body_limit_bytes():
                return set()
        except ValueError:
            return set()
    try:
        body = await request.body()
    except RuntimeError:
        return set()
    if not body or len(body) > http_api_resource_body_limit_bytes():
        return set()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return set()
    if not isinstance(payload, dict):
        return set()
    resource_ids: set[str] = set()
    for field_name in (
        "chat_context_id",
        "device_id",
        "interaction_id",
        "project_id",
        "resource_id",
        "session_id",
    ):
        collect_http_resource_id_value(payload.get(field_name), resource_ids)
    return resource_ids


def http_request_has_json_body(request: Request) -> bool:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    return content_type == "application/json" or content_type.endswith("+json")


def http_api_resource_body_limit_bytes() -> int:
    return max(
        0,
        env_int(
            "AGENTBRIDGE_DEVICE_AUTH_RESOURCE_BODY_LIMIT_BYTES",
            default=1048576,
        ),
    )


def collect_http_resource_id_value(value: object, resource_ids: set[str]) -> None:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized:
            resource_ids.add(normalized)
        return
    if isinstance(value, list | tuple | set):
        for item in value:
            collect_http_resource_id_value(item, resource_ids)


def http_api_required_device_scope(request: Request) -> DeviceIdentityScope:
    path = request.url.path.rstrip("/")
    path_segments = path.split("/")
    method = request.method.upper()
    if method == "POST" and path.startswith("/api/v1/bot-gateway/"):
        return DeviceIdentityScope.BOT_GATEWAY_MANAGE
    if method == "GET" and path.startswith("/api/v1/bot-gateway/"):
        return DeviceIdentityScope.BOT_GATEWAY_READ
    if method == "POST" and path == "/api/v1/onebot/events":
        return DeviceIdentityScope.ONEBOT_EVENT_INGEST
    if method == "POST" and path == "/api/v1/commands/parse":
        return DeviceIdentityScope.COMMAND_PARSE
    if method == "POST" and path == "/api/v1/commands/execute":
        return DeviceIdentityScope.COMMAND_EXECUTE
    if method == "POST" and path == "/api/v1/chat-contexts":
        return DeviceIdentityScope.CHAT_CONTEXT_MANAGE
    if (
        method == "PUT"
        and len(path_segments) >= 6
        and path_segments[:4] == ["", "api", "v1", "chat-contexts"]
        and path_segments[5] in {"active-project", "active-session"}
    ):
        return DeviceIdentityScope.CHAT_CONTEXT_MANAGE
    if method == "POST" and path == "/api/v1/projects":
        return DeviceIdentityScope.PROJECT_MANAGE
    if (
        method == "POST"
        and len(path_segments) >= 6
        and path_segments[:4] == ["", "api", "v1", "projects"]
        and path_segments[5] == "workspaces"
    ):
        return DeviceIdentityScope.PROJECT_MANAGE
    if (
        method == "POST"
        and len(path_segments) >= 6
        and path_segments[:4] == ["", "api", "v1", "chat-spaces"]
        and path_segments[5] == "project-bindings"
    ):
        return DeviceIdentityScope.PROJECT_MANAGE
    if method == "GET" and (
        path == "/api/v1/projects"
        or (
            len(path_segments) == 5
            and path_segments[:4] == ["", "api", "v1", "projects"]
        )
        or (
            len(path_segments) == 6
            and path_segments[:4] == ["", "api", "v1", "projects"]
            and path_segments[5] == "workspaces"
        )
    ):
        return DeviceIdentityScope.PROJECT_READ
    if method == "GET" and (
        path == "/api/v1/sessions"
        or (
            len(path_segments) == 5
            and path_segments[:4] == ["", "api", "v1", "sessions"]
        )
        or (
            len(path_segments) == 6
            and path_segments[:4] == ["", "api", "v1", "sessions"]
            and path_segments[5] in {"queue", "lease"}
        )
    ):
        return DeviceIdentityScope.SESSION_READ
    if method == "POST" and path == "/api/v1/sessions":
        return DeviceIdentityScope.SESSION_MANAGE
    if (
        method == "POST"
        and len(path_segments) >= 6
        and path_segments[:4] == ["", "api", "v1", "sessions"]
        and path_segments[5] == "turns"
    ):
        return DeviceIdentityScope.SESSION_SEND
    if (
        method == "DELETE"
        and len(path_segments) == 7
        and path_segments[:4] == ["", "api", "v1", "sessions"]
        and path_segments[5] == "queue"
    ):
        return DeviceIdentityScope.SESSION_SEND
    if (
        method == "POST"
        and len(path_segments) == 7
        and path_segments[:4] == ["", "api", "v1", "sessions"]
        and path_segments[5] == "queue"
        and path_segments[6] in {"clear", "reorder", "pause", "resume"}
    ):
        return DeviceIdentityScope.SESSION_MANAGE
    if (
        method == "POST"
        and len(path_segments) >= 6
        and path_segments[:4] == ["", "api", "v1", "sessions"]
        and path_segments[5] == "events"
    ):
        return DeviceIdentityScope.SESSION_EVENT_INGEST
    if (
        method == "POST"
        and len(path_segments) >= 6
        and path_segments[:4] == ["", "api", "v1", "sessions"]
        and (
            path_segments[5] == "close"
            or (
                len(path_segments) >= 7
                and path_segments[5] == "lease"
                and path_segments[6] in {"acquire", "release"}
            )
        )
    ):
        return DeviceIdentityScope.SESSION_MANAGE
    if (
        method == "POST"
        and len(path_segments) >= 6
        and path_segments[:4] == ["", "api", "v1", "sessions"]
        and path_segments[5] == "interactions"
    ):
        return DeviceIdentityScope.INTERACTION_MANAGE
    if (
        method == "POST"
        and len(path_segments) >= 6
        and path_segments[:4] == ["", "api", "v1", "interactions"]
        and path_segments[5] in {"answer", "cancel", "vote"}
    ):
        return DeviceIdentityScope.INTERACTION_MANAGE
    if method == "GET" and (
        path == "/api/v1/interactions"
        or (
            len(path_segments) == 5
            and path_segments[:4] == ["", "api", "v1", "interactions"]
        )
    ):
        return DeviceIdentityScope.INTERACTION_READ
    if method == "GET" and path in {
        "/api/v1/audit",
        "/api/v1/audit/export",
        "/api/v1/events",
        "/api/v1/events/rendered",
    }:
        return DeviceIdentityScope.AUDIT_READ
    if (
        method == "GET"
        and len(path_segments) >= 6
        and path_segments[:4] == ["", "api", "v1", "sessions"]
        and path_segments[5] in {"events", "rendered-events"}
    ):
        return DeviceIdentityScope.AUDIT_READ
    if path == "/api/v1/terminal/lifecycle-monitor":
        return DeviceIdentityScope.TERMINAL_READ
    if (
        len(path_segments) >= 7
        and path_segments[:4] == ["", "api", "v1", "sessions"]
        and path_segments[5] == "terminal"
        and path_segments[6] in {"snapshot", "status"}
    ):
        return DeviceIdentityScope.TERMINAL_READ
    if path in {
        "/api/v1/terminal/lifecycle-monitor/run-once",
        "/api/v1/terminal/agent-launch/probe",
        "/api/v1/terminal/agent-adapters/detect",
    }:
        return DeviceIdentityScope.TERMINAL_CONTROL
    if (
        len(path_segments) >= 7
        and path_segments[:4] == ["", "api", "v1", "sessions"]
        and path_segments[5] == "terminal"
        and path_segments[6] in {"start", "restart", "input"}
    ):
        return DeviceIdentityScope.TERMINAL_CONTROL
    if method == "GET" and path.endswith("/approval-policy"):
        return DeviceIdentityScope.POLICY_READ
    if method == "GET" and path == "/api/v1/access-policy/rules":
        return DeviceIdentityScope.POLICY_READ
    if method == "POST" and path == "/api/v1/access-policy/simulate":
        return DeviceIdentityScope.POLICY_READ
    if path == "/api/v1/access-policy" or path.startswith("/api/v1/access-policy/"):
        return DeviceIdentityScope.POLICY_MANAGE
    if path.endswith("/approval-policy"):
        return DeviceIdentityScope.POLICY_MANAGE
    if (
        method == "GET"
        and len(path_segments) >= 6
        and path_segments[:4] == ["", "api", "v1", "chat-contexts"]
        and path_segments[5] == "roles"
    ):
        return DeviceIdentityScope.GROUP_ROLE_READ
    if (
        len(path_segments) >= 6
        and path_segments[:4] == ["", "api", "v1", "chat-contexts"]
        and path_segments[5] == "roles"
    ):
        return DeviceIdentityScope.GROUP_ROLE_MANAGE
    if path == "/api/v1/device-identities" or path.startswith(
        "/api/v1/device-identities/"
    ):
        return DeviceIdentityScope.DEVICE_MANAGE
    return DeviceIdentityScope.HTTP_API


def http_api_expected_tokens() -> list[str]:
    tokens, _configured = http_api_expected_token_config()
    return tokens


def http_api_expected_token_config() -> tuple[list[str], bool]:
    return tokens_from_env(
        "AGENTBRIDGE_API_TOKEN",
        "AGENTBRIDGE_ADMIN_TOKEN",
    )


def http_api_presented_tokens(request: Request) -> list[str]:
    tokens: list[str] = []
    header_token = request.headers.get("x-agentbridge-api-token")
    if header_token:
        tokens.append(header_token.strip())
    admin_header_token = request.headers.get("x-agentbridge-admin-token")
    if admin_header_token:
        tokens.append(admin_header_token.strip())
    authorization = request.headers.get("authorization")
    if authorization:
        prefix = "Bearer "
        tokens.append(
            authorization[len(prefix) :].strip()
            if authorization.startswith(prefix)
            else authorization.strip()
        )
    admin_cookie_token = request.cookies.get(ADMIN_AUTH_COOKIE_NAME)
    if admin_cookie_token:
        tokens.append(admin_cookie_token)
    return [token for token in tokens if token]


def http_device_key_authorized(
    request: Request,
    device_keys: dict[str, str],
    *,
    control: ControlPlane | None = None,
    required_scope: DeviceIdentityScope,
    resource_ids: set[str] | None = None,
) -> bool:
    device_id = request.headers.get("x-agentbridge-device-id")
    presented_key = request.headers.get("x-agentbridge-device-key")
    if not presented_key:
        authorization = request.headers.get("authorization")
        if authorization:
            prefix = "Bearer "
            presented_key = (
                authorization[len(prefix) :].strip()
                if authorization.startswith(prefix)
                else authorization.strip()
            )
    return matching_device_key(
        device_id,
        presented_key,
        device_keys,
    ) or matching_managed_device_key(
        device_id,
        presented_key,
        control,
        required_scope=required_scope,
        resource_ids=resource_ids,
    )


def http_api_auth_error_response() -> JSONResponse:
    error = AgentBridgeError(
        ErrorCode.PERMISSION_DENIED,
        "HTTP API token 无效。",
        next_step=(
            "请设置 Authorization: Bearer <token>、X-AgentBridge-API-Token，"
            "X-AgentBridge-Device-ID/X-AgentBridge-Device-Key，"
            "可信代理传入的客户端证书指纹，"
            "或使用已解锁的 Admin Web cookie。"
        ),
        status_code=403,
    )
    return JSONResponse(status_code=error.status_code, content=error.to_payload())


def matching_token(presented_token: str | None, expected_tokens: list[str]) -> str | None:
    if not presented_token:
        return None
    for expected_token in expected_tokens:
        if hmac.compare_digest(presented_token, expected_token):
            return expected_token
    return None


def invalid_terminal_frame(reason: str) -> AgentBridgeError:
    return AgentBridgeError(
        ErrorCode.COMMAND_ARGUMENT_INVALID,
        "Terminal WebSocket 请求格式无效。",
        next_step="请发送包含 type/action 和 payload 的 JSON 对象。",
        details={"reason": reason},
    )


async def accept_authenticated_websocket(
    websocket: WebSocket,
    *,
    token: str | None,
    control: ControlPlane | None = None,
    required_scope: DeviceIdentityScope,
    resource_ids: set[str] | None = None,
) -> bool:
    await websocket.accept()
    expected_tokens, token_configured = websocket_expected_token_config()
    device_keys = configured_device_keys()
    if (
        not token_configured
        and not device_keys_configured()
        and not managed_device_identities_configured(control)
        and not client_certificate_fingerprints_configured()
    ):
        return True
    presented_token = websocket_presented_token(websocket, token)
    if matching_token(presented_token, expected_tokens):
        return True
    if client_certificate_authorized(
        websocket.headers,
        control=control,
        required_scope=required_scope,
        resource_ids=resource_ids,
    ):
        return True
    if websocket_device_key_authorized(
        websocket,
        device_keys,
        control=control,
        required_scope=required_scope,
        resource_ids=resource_ids,
    ):
        return True
    if websocket_admin_cookie_authorized(websocket):
        return True
    error = AgentBridgeError(
        ErrorCode.PERMISSION_DENIED,
        "WebSocket token 无效。",
        next_step=(
            "请使用当前 AGENTBRIDGE_WS_TOKEN/AGENTBRIDGE_WS_TOKEN_FILE，"
            "通过 device_id/device_key 重新连接，使用可信代理传入的客户端证书指纹，"
            "或先解锁 Admin Web。"
        ),
        status_code=403,
    )
    await websocket.send_json({"type": "error", "error": error.to_payload()})
    await websocket.close(code=1008)
    return False


def websocket_presented_token(websocket: WebSocket, token: str | None) -> str | None:
    if token:
        return token
    authorization = websocket.headers.get("authorization")
    if not authorization:
        return None
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix) :].strip()
    return authorization.strip()


def websocket_expected_tokens() -> list[str]:
    tokens, _configured = websocket_expected_token_config()
    return tokens


def websocket_expected_token_config() -> tuple[list[str], bool]:
    return tokens_from_env("AGENTBRIDGE_WS_TOKEN")


def tokens_from_env(*token_env_names: str) -> tuple[list[str], bool]:
    tokens: list[str] = []
    configured = False
    for token_env_name in token_env_names:
        token = os.environ.get(token_env_name, "").strip()
        if token:
            configured = True
            tokens.append(token)
        token_file = os.environ.get(f"{token_env_name}_FILE", "").strip()
        if not token_file:
            continue
        configured = True
        file_token = token_from_file(token_file)
        if file_token:
            tokens.append(file_token)
    return tokens, configured


def token_from_file(raw_path: str) -> str | None:
    try:
        return Path(raw_path).expanduser().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def websocket_device_key_authorized(
    websocket: WebSocket,
    device_keys: dict[str, str],
    *,
    control: ControlPlane | None = None,
    required_scope: DeviceIdentityScope,
    resource_ids: set[str] | None = None,
) -> bool:
    device_id = websocket.query_params.get("device_id")
    presented_key = websocket.query_params.get("device_key")
    return matching_device_key(
        device_id,
        presented_key,
        device_keys,
    ) or matching_managed_device_key(
        device_id,
        presented_key,
        control,
        required_scope=required_scope,
        resource_ids=resource_ids,
    )


def websocket_admin_cookie_authorized(websocket: WebSocket) -> bool:
    path = websocket.url.path
    if not (
        path.endswith("/events/ws")
        or path.endswith("/rendered-events/ws")
        or path == "/api/v1/bot-gateway/session-events/ws"
    ):
        return False
    return bool(
        matching_token(
            websocket.cookies.get(ADMIN_AUTH_COOKIE_NAME),
            admin_expected_tokens(),
        )
    )


def client_certificate_fingerprints_configured() -> bool:
    return bool(
        os.environ.get("AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS", "").strip()
        or os.environ.get("AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS_FILE", "").strip()
    )


def configured_client_certificate_fingerprints() -> set[str]:
    raw_values: list[str] = []
    raw_env_value = os.environ.get("AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS", "")
    raw_values.extend(split_client_certificate_fingerprints(raw_env_value))
    raw_file_path = os.environ.get("AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS_FILE", "").strip()
    if raw_file_path:
        raw_values.extend(
            split_client_certificate_fingerprints(
                client_certificate_fingerprints_from_file(raw_file_path)
            )
        )
    return {
        fingerprint
        for value in raw_values
        if (fingerprint := normalize_certificate_fingerprint(value))
    }


def split_client_certificate_fingerprints(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    values: list[str] = []
    for line in raw_value.splitlines():
        values.extend(part.strip() for part in line.split(","))
    return [value for value in values if value]


def client_certificate_fingerprints_from_file(raw_path: str) -> str:
    try:
        return Path(raw_path).expanduser().read_text(encoding="utf-8")
    except OSError:
        return ""


def client_certificate_fingerprint_header_name() -> str:
    return (
        os.environ.get(
            "AGENTBRIDGE_CLIENT_CERT_FINGERPRINT_HEADER",
            "x-agentbridge-client-cert-fingerprint",
        ).strip()
        or "x-agentbridge-client-cert-fingerprint"
    )


def client_certificate_authorized(
    headers,
    *,
    control: ControlPlane | None = None,
    required_scope: DeviceIdentityScope | None = None,
    resource_ids: set[str] | None = None,
) -> bool:
    global_fingerprints = configured_client_certificate_fingerprints()
    if not global_fingerprints and (control is None or required_scope is None):
        return False
    presented_fingerprint = headers.get(client_certificate_fingerprint_header_name())
    if not presented_fingerprint:
        return False
    normalized_fingerprint = normalize_certificate_fingerprint(presented_fingerprint)
    if not normalized_fingerprint:
        return False
    if normalized_fingerprint in global_fingerprints:
        return True
    if control is None or required_scope is None:
        return False
    return matching_managed_device_certificate_fingerprint(
        normalized_fingerprint,
        control,
        required_scope=required_scope,
        resource_ids=resource_ids,
    )


def device_keys_configured() -> bool:
    return bool(os.environ.get("AGENTBRIDGE_DEVICE_KEYS", "").strip())


def configured_device_keys() -> dict[str, str]:
    raw_value = os.environ.get("AGENTBRIDGE_DEVICE_KEYS", "").strip()
    if not raw_value:
        return {}
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    keys: dict[str, str] = {}
    for device_id, device_key in payload.items():
        if isinstance(device_id, str) and isinstance(device_key, str):
            normalized_device_id = device_id.strip()
            normalized_device_key = device_key.strip()
            if normalized_device_id and normalized_device_key:
                keys[normalized_device_id] = normalized_device_key
    return keys


def matching_device_key(
    device_id: str | None,
    presented_key: str | None,
    device_keys: dict[str, str],
) -> bool:
    if not device_id or not presented_key:
        return False
    expected_key = device_keys.get(device_id.strip())
    if not expected_key:
        return False
    return hmac.compare_digest(presented_key.strip(), expected_key)


def managed_device_identities_configured(control: ControlPlane | None) -> bool:
    if control is None:
        return False
    return bool(control.repository.list_device_identities(include_revoked=True))


def matching_managed_device_key(
    device_id: str | None,
    presented_key: str | None,
    control: ControlPlane | None,
    *,
    required_scope: DeviceIdentityScope,
    resource_ids: set[str] | None = None,
) -> bool:
    if control is None or not device_id or not presented_key:
        return False
    try:
        identity = control.repository.get_device_identity(device_id.strip())
    except AgentBridgeError:
        return False
    if identity.status != DeviceIdentityStatus.ACTIVE:
        return False
    if required_scope not in identity.allowed_scopes:
        return False
    if not device_identity_resource_allowed(identity, resource_ids):
        return False
    if not identity.key_hash or not identity.key_salt:
        return False
    try:
        verified = verify_device_key(
            presented_key.strip(),
            expected_hash=identity.key_hash,
            salt=identity.key_salt,
            iterations=identity.key_iterations,
        )
    except ValueError:
        return False
    if not verified:
        return False
    control.repository.mark_device_identity_used(identity.device_id)
    return True


def matching_managed_device_certificate_fingerprint(
    presented_fingerprint: str,
    control: ControlPlane,
    *,
    required_scope: DeviceIdentityScope,
    resource_ids: set[str] | None = None,
) -> bool:
    for identity in control.repository.list_device_identities():
        if identity.status != DeviceIdentityStatus.ACTIVE:
            continue
        if required_scope not in identity.allowed_scopes:
            continue
        if not device_identity_resource_allowed(identity, resource_ids):
            continue
        if managed_device_certificate_active(identity, presented_fingerprint):
            control.repository.mark_device_identity_used(identity.device_id)
            return True
    return False


def device_identity_resource_allowed(
    identity: DeviceIdentity,
    resource_ids: set[str] | None,
) -> bool:
    allowed_resource_ids = {
        resource_id
        for value in identity.allowed_resource_ids
        if (resource_id := value.strip())
    }
    if not allowed_resource_ids or "*" in allowed_resource_ids:
        return True
    requested_resource_ids = {
        resource_id for value in (resource_ids or set()) if (resource_id := value.strip())
    }
    if not requested_resource_ids:
        return False
    return requested_resource_ids <= allowed_resource_ids


def clamp_stream_limit(limit: int) -> int:
    return max(1, min(limit, 1000))


def clamp_poll_interval(poll_interval_seconds: float) -> float:
    return max(0.05, min(poll_interval_seconds, 5.0))


def normalize_idle_timeout(idle_timeout_seconds: float | None) -> float | None:
    if idle_timeout_seconds is None:
        return None
    return max(idle_timeout_seconds, 0.0)


def create_repository_from_env() -> InMemoryRepository:
    database_url = os.environ.get("AGENTBRIDGE_DATABASE_URL")
    if not database_url:
        return InMemoryRepository()
    auto_create_schema = os.environ.get("AGENTBRIDGE_AUTO_CREATE_SCHEMA", "false").lower()
    return SQLAlchemyRepository(
        database_url,
        create_schema=auto_create_schema in {"1", "true", "yes", "on"},
        engine_options=database_engine_options_from_env(),
    )


def database_engine_options_from_env() -> dict[str, object]:
    options: dict[str, object] = {}
    optional_int_options = {
        "AGENTBRIDGE_DATABASE_POOL_SIZE": "pool_size",
        "AGENTBRIDGE_DATABASE_MAX_OVERFLOW": "max_overflow",
        "AGENTBRIDGE_DATABASE_POOL_TIMEOUT_SECONDS": "pool_timeout",
        "AGENTBRIDGE_DATABASE_POOL_RECYCLE_SECONDS": "pool_recycle",
    }
    for env_name, option_name in optional_int_options.items():
        raw_value = os.environ.get(env_name)
        if raw_value is not None and raw_value.strip():
            options[option_name] = env_int(env_name, default=0)

    pool_pre_ping = os.environ.get("AGENTBRIDGE_DATABASE_POOL_PRE_PING")
    if pool_pre_ping is not None:
        options["pool_pre_ping"] = env_bool(
            "AGENTBRIDGE_DATABASE_POOL_PRE_PING",
            default=False,
        )

    echo = os.environ.get("AGENTBRIDGE_DATABASE_ECHO")
    if echo is not None:
        options["echo"] = env_bool("AGENTBRIDGE_DATABASE_ECHO", default=False)
    return options


def create_approval_policy_from_env() -> ApprovalPolicy:
    config = os.environ.get("AGENTBRIDGE_APPROVAL_QUORUMS", "").strip()
    if not config:
        return ApprovalPolicy.default()
    policy = ApprovalPolicy.default()
    quorum_by_risk = dict(policy.quorum_by_risk)
    for item in config.split(","):
        if not item.strip():
            continue
        try:
            risk_value, quorum_value = item.split("=", maxsplit=1)
            quorum_by_risk[RiskLevel(risk_value.strip())] = int(quorum_value.strip())
        except ValueError as exc:
            raise RuntimeError(
                "AGENTBRIDGE_APPROVAL_QUORUMS must use entries like "
                "low=1,medium=1,high=1,critical=2"
            ) from exc
    return ApprovalPolicy(quorum_by_risk=quorum_by_risk)


def create_device_certificate_issuer_from_env() -> (
    DeviceCertificateIssuer | ExternalDeviceCertificateIssuer
):
    external_issuer_command = os.environ.get(
        "AGENTBRIDGE_DEVICE_CERT_ISSUER_COMMAND", ""
    )
    if external_issuer_command.strip():
        return ExternalDeviceCertificateIssuer.from_command(
            command=external_issuer_command,
            default_validity_days=device_certificate_default_validity_days_from_env(),
            timeout_seconds=min(
                120.0,
                max(
                    0.1,
                    env_float(
                        "AGENTBRIDGE_DEVICE_CERT_ISSUER_COMMAND_TIMEOUT_SECONDS",
                        default=10.0,
                    ),
                ),
            ),
        )
    ca_certificate_file = os.environ.get("AGENTBRIDGE_DEVICE_CERT_CA_CERT_FILE", "")
    ca_private_key_file = os.environ.get("AGENTBRIDGE_DEVICE_CERT_CA_KEY_FILE", "")
    if not ca_certificate_file.strip() or not ca_private_key_file.strip():
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "设备证书签发 CA 未配置。",
            next_step=(
                "请设置 AGENTBRIDGE_DEVICE_CERT_ISSUER_COMMAND，或设置 "
                "AGENTBRIDGE_DEVICE_CERT_CA_CERT_FILE 和 "
                "AGENTBRIDGE_DEVICE_CERT_CA_KEY_FILE。"
            ),
            status_code=503,
        )
    return DeviceCertificateIssuer.from_files(
        ca_certificate_path=Path(ca_certificate_file).expanduser(),
        ca_private_key_path=Path(ca_private_key_file).expanduser(),
        ca_private_key_password=device_certificate_ca_key_password_from_env(),
        default_validity_days=device_certificate_default_validity_days_from_env(),
    )


def device_certificate_ca_key_password_from_env() -> str | None:
    password = os.environ.get("AGENTBRIDGE_DEVICE_CERT_CA_KEY_PASSWORD", "")
    password_file = os.environ.get("AGENTBRIDGE_DEVICE_CERT_CA_KEY_PASSWORD_FILE", "")
    if password:
        return password
    if not password_file.strip():
        return None
    try:
        return Path(password_file).expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "设备证书 CA 私钥密码文件不可读。",
            next_step="请检查 AGENTBRIDGE_DEVICE_CERT_CA_KEY_PASSWORD_FILE 路径和权限。",
            status_code=503,
        ) from exc


def device_certificate_default_validity_days_from_env() -> int:
    return max(
        1,
        env_int(
            "AGENTBRIDGE_DEVICE_CERT_DEFAULT_VALIDITY_DAYS",
            default=30,
        ),
    )


def create_terminal_backend_from_env():
    backend = os.environ.get("AGENTBRIDGE_TERMINAL_BACKEND", "fake").strip().lower()
    if backend == "fake":
        return FakeTerminalBackend()
    if backend == "tmux":
        return TmuxTerminalBackend()
    if backend in {"pty", "local_pty"}:
        host_state_path = os.environ.get("AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH")
        return PtyTerminalBackend(
            max_output_chars=terminal_pty_output_limit_from_env(),
            host_state_path=Path(host_state_path).expanduser() if host_state_path else None,
        )
    if backend in {"pty_host", "hosted_pty"}:
        socket_path = Path(
            os.environ.get(
                "AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET",
                str(Path.home() / ".agentbridge" / "pty-host.sock"),
            )
        ).expanduser()
        host_state_path = os.environ.get("AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH")
        host_state = Path(host_state_path).expanduser() if host_state_path else None
        host_token = os.environ.get("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN", "")
        host_token_file = os.environ.get(
            "AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE",
            "",
        ).strip()
        host_token_file_path = Path(host_token_file).expanduser() if host_token_file else None
        max_output_chars = terminal_pty_output_limit_from_env()
        host_auto_start = env_bool("AGENTBRIDGE_TERMINAL_PTY_HOST_AUTO_START", default=False)
        host_watchdog_enabled = env_bool(
            "AGENTBRIDGE_TERMINAL_PTY_HOST_WATCHDOG_ENABLED",
            default=False,
        )
        supervisor = (
            PtyHostSupervisor(
                PtyHostSupervisorConfig(
                    socket_path=socket_path,
                    auth_token=host_token,
                    auth_token_file=host_token_file_path,
                    max_output_chars=max_output_chars,
                    host_state_path=host_state,
                    startup_timeout_seconds=env_float(
                        "AGENTBRIDGE_TERMINAL_PTY_HOST_STARTUP_TIMEOUT_SECONDS",
                        default=3.0,
                    ),
                    watchdog_enabled=host_watchdog_enabled,
                    watchdog_interval_seconds=env_float(
                        "AGENTBRIDGE_TERMINAL_PTY_HOST_WATCHDOG_INTERVAL_SECONDS",
                        default=5.0,
                    ),
                )
            )
            if host_auto_start or host_watchdog_enabled
            else None
        )
        return PtyHostTerminalBackend(
            socket_path=socket_path,
            auth_token=host_token,
            auth_token_file=host_token_file_path,
            supervisor=supervisor,
        )
    raise RuntimeError("AGENTBRIDGE_TERMINAL_BACKEND must be one of: fake, tmux, pty, pty_host")


def start_terminal_backend_supervision(terminal: TerminalAgentService) -> None:
    start = getattr(terminal.backend, "start_supervision", None)
    if callable(start):
        start()


def stop_terminal_backend_supervision(terminal: TerminalAgentService) -> None:
    stop = getattr(terminal.backend, "stop_supervision", None)
    if callable(stop):
        stop()


def create_terminal_lifecycle_policy_from_env() -> TerminalLifecyclePolicy:
    return TerminalLifecyclePolicy(
        auto_restart_on_lost=env_bool(
            "AGENTBRIDGE_TERMINAL_AUTO_RESTART_ON_LOST",
            default=False,
        ),
        auto_restart_max_attempts=max(
            env_int("AGENTBRIDGE_TERMINAL_AUTO_RESTART_MAX_ATTEMPTS", default=1),
            0,
        ),
        auto_restart_command_allowlist=terminal_auto_restart_command_allowlist_from_env(),
    )


def terminal_auto_restart_command_allowlist_from_env() -> tuple[str, ...]:
    config = os.environ.get("AGENTBRIDGE_TERMINAL_AUTO_RESTART_COMMAND_ALLOWLIST")
    if config is None:
        return ()
    return tuple(item.strip() for item in config.split(",") if item.strip())


def terminal_pty_output_limit_from_env() -> int:
    raw_limit = os.environ.get("AGENTBRIDGE_TERMINAL_PTY_OUTPUT_LIMIT_CHARS")
    if raw_limit is None or not raw_limit.strip():
        return DEFAULT_PTY_OUTPUT_LIMIT_CHARS
    try:
        limit = int(raw_limit)
    except ValueError as exc:
        raise RuntimeError(
            "AGENTBRIDGE_TERMINAL_PTY_OUTPUT_LIMIT_CHARS must be a positive integer"
        ) from exc
    if limit <= 0:
        raise RuntimeError(
            "AGENTBRIDGE_TERMINAL_PTY_OUTPUT_LIMIT_CHARS must be a positive integer"
        )
    return limit


def create_bot_transport_from_env():
    transport = os.environ.get("AGENTBRIDGE_BOT_TRANSPORT", "memory").lower()
    if transport in {"onebot", "onebot.v11"}:
        endpoint = os.environ.get("AGENTBRIDGE_ONEBOT_HTTP_URL")
        if not endpoint:
            raise RuntimeError("AGENTBRIDGE_ONEBOT_HTTP_URL is required for onebot.v11 transport")
        return OneBotV11HTTPTransport(
            endpoint=endpoint,
            access_token=os.environ.get("AGENTBRIDGE_ONEBOT_ACCESS_TOKEN"),
        )
    return InMemoryBotTransport()


def create_bot_rate_limiter_from_env() -> BotDeliveryRateLimiter:
    config = os.environ.get("AGENTBRIDGE_BOT_RATE_LIMITS", "").strip()
    if not config:
        return BotDeliveryRateLimiter()
    policies: list[BotRateLimitPolicy] = []
    for item in config.split(","):
        if not item.strip():
            continue
        try:
            platform_value, quota_value = item.split("=", maxsplit=1)
            capacity_value, window_value = quota_value.split("/", maxsplit=1)
            policy = BotRateLimitPolicy(
                platform=BotPlatform(platform_value.strip()),
                capacity=int(capacity_value.strip()),
                window_seconds=float(window_value.strip()),
            )
        except ValueError as exc:
            raise RuntimeError(
                "AGENTBRIDGE_BOT_RATE_LIMITS must use entries like "
                "onebot.v11=20/60,plain_text=100/60"
            ) from exc
        policies.append(policy)
    return BotDeliveryRateLimiter(policies)


def create_bot_retry_worker_from_env(bot_gateway: BotGatewayService) -> BotDeliveryRetryWorker:
    return BotDeliveryRetryWorker(
        bot_gateway,
        enabled=env_bool("AGENTBRIDGE_BOT_RETRY_WORKER_ENABLED", default=False),
        interval_seconds=env_float("AGENTBRIDGE_BOT_RETRY_INTERVAL_SECONDS", default=30.0),
        batch_size=env_int("AGENTBRIDGE_BOT_RETRY_BATCH_SIZE", default=100),
    )


def create_certificate_scan_worker_from_env(
    control: ControlPlane,
    bot_gateway: BotGatewayService,
) -> DeviceCertificateScanWorker:
    return DeviceCertificateScanWorker(
        control,
        enabled=env_bool(
            "AGENTBRIDGE_DEVICE_CERT_SCAN_WORKER_ENABLED",
            default=False,
        ),
        interval_seconds=env_float(
            "AGENTBRIDGE_DEVICE_CERT_SCAN_INTERVAL_SECONDS",
            default=3600.0,
        ),
        warning_days=device_certificate_expiry_warning_days_from_env(),
        include_revoked=env_bool(
            "AGENTBRIDGE_DEVICE_CERT_SCAN_INCLUDE_REVOKED",
            default=False,
        ),
        actor_id=os.environ.get(
            "AGENTBRIDGE_DEVICE_CERT_SCAN_ACTOR_ID",
            "certificate-scan-worker",
        ),
        bot_gateway=bot_gateway,
        notify_chat_context_ids=certificate_scan_notify_chat_context_ids_from_env(),
        notify_platform=certificate_scan_notify_platform_from_env(),
        notify_only_action_required=env_bool(
            "AGENTBRIDGE_DEVICE_CERT_SCAN_NOTIFY_ONLY_ACTION_REQUIRED",
            default=True,
        ),
    )


def certificate_scan_notify_chat_context_ids_from_env() -> tuple[str, ...]:
    config = os.environ.get("AGENTBRIDGE_DEVICE_CERT_SCAN_NOTIFY_CHAT_CONTEXT_IDS", "")
    return tuple(item.strip() for item in config.split(",") if item.strip())


def certificate_scan_notify_platform_from_env() -> BotPlatform:
    config = os.environ.get("AGENTBRIDGE_DEVICE_CERT_SCAN_NOTIFY_PLATFORM", "")
    value = config.strip() or BotPlatform.ONEBOT_V11.value
    try:
        return BotPlatform(value)
    except ValueError as exc:
        raise RuntimeError(
            "AGENTBRIDGE_DEVICE_CERT_SCAN_NOTIFY_PLATFORM must be one of: "
            "onebot.v11, plain_text"
        ) from exc


def env_bool(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def env_float(name: str, *, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc


def env_int(name: str, *, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def run() -> None:
    uvicorn.run("agentbridge.api:create_app", factory=True, host="127.0.0.1", port=8000)
