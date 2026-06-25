from __future__ import annotations

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agentbridge.commands import CommandService
from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    Actor,
    AgentBridgeError,
    AgentType,
    LeaseOwnerType,
    SemanticEventSource,
    Visibility,
    WorkspaceType,
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


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str
    actor: ActorPayload = Field(default_factory=ActorPayload)
    chat_context_id: str | None = None
    chat: ChatContextPayload = Field(default_factory=ChatContextPayload)
    idempotency_key: str | None = None
    trace_id: str | None = None


def create_app(control_plane: ControlPlane | None = None) -> FastAPI:
    app = FastAPI(title="AgentBridge Control Plane", version="0.1.0")
    control = control_plane or ControlPlane()
    commands = CommandService(control)
    app.state.control = control
    app.state.commands = commands

    @app.exception_handler(AgentBridgeError)
    async def agentbridge_error_handler(_, exc: AgentBridgeError):
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    def get_control() -> ControlPlane:
        return app.state.control

    def get_commands() -> CommandService:
        return app.state.commands

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
            ]
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


def run() -> None:
    uvicorn.run("agentbridge.api:create_app", factory=True, host="127.0.0.1", port=8000)
