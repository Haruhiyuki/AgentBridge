from __future__ import annotations

import os

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentbridge.bot_gateway import BotGatewayService, BotPlatform, InMemoryBotTransport
from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    Actor,
    AgentBridgeError,
    AgentType,
    BotDeliveryStatus,
    LeaseOwnerType,
    SemanticEventSource,
    Visibility,
    WorkspaceType,
)
from agentbridge.onebot import OneBotInboundAdapter, OneBotV11HTTPTransport
from agentbridge.persistence import SQLAlchemyRepository
from agentbridge.policy import Permission
from agentbridge.renderer import OneBotV11TextRenderer, document_from_event
from agentbridge.storage import InMemoryRepository
from agentbridge.terminal_agent import (
    FakeTerminalBackend,
    TerminalAgentService,
    TerminalInputKind,
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
    trace_id: str = "api"


class CreateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    machine_id: str = "local"
    path: str
    allowed_root: str
    workspace_type: WorkspaceType = WorkspaceType.SHARED
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


class StartTerminalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    command: str = "sh"
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


class RetryBotDeliveriesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_context_id: str | None = None
    limit: int = 100


class OneBotEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: dict[str, object]
    bot_instance_id: str = "onebot-http"
    default_roles: set[str] = Field(default_factory=lambda: {"member"})


class GroupRoleChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: ActorPayload = Field(default_factory=ActorPayload)
    target_actor_id: str
    roles: set[str]
    trace_id: str = "api"


def create_app(control_plane: ControlPlane | None = None) -> FastAPI:
    app = FastAPI(title="AgentBridge Control Plane", version="0.1.0")
    control = control_plane or ControlPlane(repository=create_repository_from_env())
    commands = CommandService(control)
    terminal = TerminalAgentService(control, backend=create_terminal_backend_from_env())
    bot_gateway = BotGatewayService(control, transport=create_bot_transport_from_env())
    app.state.control = control
    app.state.commands = commands
    app.state.terminal = terminal
    app.state.bot_gateway = bot_gateway

    @app.exception_handler(AgentBridgeError)
    async def agentbridge_error_handler(_, exc: AgentBridgeError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    def get_control() -> ControlPlane:
        return app.state.control

    def get_commands() -> CommandService:
        return app.state.commands

    def get_terminal() -> TerminalAgentService:
        return app.state.terminal

    def get_bot_gateway() -> BotGatewayService:
        return app.state.bot_gateway

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
            document = document_from_event(event)
            rendered.append(
                {
                    "event_id": event.id,
                    "seq": event.seq,
                    "document": document.model_dump(mode="json"),
                    "text_messages": renderer.render(document),
                }
            )
        return rendered

    @app.post("/api/v1/sessions/{session_id}/events")
    def ingest_session_event(
        session_id: str,
        payload: IngestSessionEventRequest,
        control: ControlPlane = Depends(get_control),
    ):
        session = control.repository.get_session(session_id)
        event = control.emit_event(
            event_type=payload.type,
            source=payload.source,
            trace_id=payload.trace_id,
            project_id=session.project_id,
            session_id=session_id,
            turn_id=payload.turn_id,
            interaction_id=payload.interaction_id,
            payload=payload.payload,
            idempotency_key=payload.idempotency_key,
        )
        return event.model_dump(mode="json")

    @app.post("/api/v1/sessions/{session_id}/terminal/start")
    def start_terminal(
        session_id: str,
        payload: StartTerminalRequest,
        control: ControlPlane = Depends(get_control),
        terminal_service: TerminalAgentService = Depends(get_terminal),
    ):
        actor = payload.actor.to_actor()
        control.policy.require(actor, Permission.TERMINAL_CONTROL)
        terminal_service.start_session(
            session_id=session_id,
            command=payload.command,
            trace_id=payload.trace_id,
        )
        return {"status": "started"}

    @app.post("/api/v1/sessions/{session_id}/terminal/input")
    def submit_terminal_input(
        session_id: str,
        payload: TerminalInputRequest,
        control: ControlPlane = Depends(get_control),
        terminal_service: TerminalAgentService = Depends(get_terminal),
    ):
        actor = payload.actor.to_actor()
        control.policy.require(actor, Permission.TERMINAL_CONTROL)
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
        control.policy.require(actor, Permission.SESSION_VIEW)
        return {"snapshot": terminal_service.snapshot(session_id=session_id)}

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
        context = control.get_or_create_chat_context(
            bot_instance_id=inbound.bot_instance_id,
            platform=inbound.platform.value,
            chat_space_id=inbound.chat_space_id,
            thread_id=inbound.thread_id,
            user_id=inbound.user_id,
        )
        invocation = command_service.parse(
            raw_text=inbound.raw_text,
            actor=inbound.actor,
            chat_context_id=context.id,
            idempotency_key=inbound.idempotency_key,
            trace_id=inbound.trace_id,
        )
        result = command_service.execute(invocation)
        return {
            "handled": True,
            "chat_context_id": context.id,
            "result": result.model_dump(mode="json"),
        }

    @app.get("/api/v1/audit")
    def list_audit(control: ControlPlane = Depends(get_control)):
        return [event.model_dump(mode="json") for event in control.repository.audit_events]

    return app


def ensure_chat_context_id(payload: CommandRequest, control: ControlPlane) -> str:
    if payload.chat_context_id:
        control.repository.get_chat_context(payload.chat_context_id)
        return payload.chat_context_id
    context = control.get_or_create_chat_context(**payload.chat.model_dump())
    return context.id


def create_repository_from_env() -> InMemoryRepository:
    database_url = os.environ.get("AGENTBRIDGE_DATABASE_URL")
    if not database_url:
        return InMemoryRepository()
    auto_create_schema = os.environ.get("AGENTBRIDGE_AUTO_CREATE_SCHEMA", "false").lower()
    return SQLAlchemyRepository(
        database_url,
        create_schema=auto_create_schema in {"1", "true", "yes", "on"},
    )


def create_terminal_backend_from_env():
    backend = os.environ.get("AGENTBRIDGE_TERMINAL_BACKEND", "fake").lower()
    if backend == "tmux":
        return TmuxTerminalBackend()
    return FakeTerminalBackend()


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


def run() -> None:
    uvicorn.run("agentbridge.api:create_app", factory=True, host="127.0.0.1", port=8000)
