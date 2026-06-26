from __future__ import annotations

import re
import shlex
from copy import deepcopy
from uuid import uuid4

from agentbridge.control_plane import ControlPlane
from agentbridge.domain import (
    Actor,
    AgentBridgeError,
    AgentSession,
    AgentType,
    AuditOutcome,
    CommandInvocation,
    CommandResult,
    ErrorCode,
    Interaction,
    InteractionStatus,
    InteractionType,
    LeaseOwnerType,
    PolicyScope,
    RiskLevel,
    Visibility,
    WorkspaceType,
)

COMMAND_ALIASES = {
    "项目": "project",
    "会话": "session",
    "使用": "use",
    "列表": "list",
    "信息": "info",
    "新建": "new",
    "创建": "create",
    "关闭": "close",
    "发送": "send",
    "继续": "continue",
    "回答": "answer",
    "批准": "approve",
    "拒绝": "deny",
    "审批": "approval",
    "问题": "question",
    "计划": "plan",
    "取消": "cancel",
    "控制": "control",
    "状态": "status",
    "接管": "takeover",
    "释放": "release",
    "角色": "role",
    "授权": "grant",
    "授予": "grant",
    "撤销": "revoke",
    "策略": "policy",
    "设置": "set",
    "健康": "health",
    "上下文": "context",
    "选择": "select",
}

KNOWN_ROOTS = {
    "help",
    "context",
    "project",
    "session",
    "ask",
    "send",
    "continue",
    "answer",
    "approve",
    "deny",
    "approval",
    "approvals",
    "question",
    "plan",
    "queue",
    "diff",
    "files",
    "history",
    "logs",
    "export",
    "control",
    "terminal",
    "role",
    "policy",
    "settings",
    "verbose",
    "model",
    "mode",
    "notifications",
    "health",
    "machine",
    "bot",
    "doctor",
    "audit",
    "select",
}

ASCII_COMMAND_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]*$")

COMMAND_REGISTRY_SCHEMA_VERSION = "agentbridge.command_registry.v1"

STRING_SCHEMA = {"type": "string", "minLength": 1}
OPTIONAL_STRING_SCHEMA = {"type": ["string", "null"], "minLength": 1}
INTEGER_SCHEMA = {"type": "integer"}
OPTIONAL_INTEGER_SCHEMA = {"type": ["integer", "null"]}
BOOLEAN_SCHEMA = {"type": "boolean"}
STRING_ARRAY_SCHEMA = {"type": "array", "items": STRING_SCHEMA}


def command_arguments_schema(
    properties: dict[str, object] | None = None,
    *,
    required: list[str] | None = None,
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def command_spec(
    name: str,
    *,
    aliases: list[str] | None = None,
    summary: str,
    usage: str,
    argument_schema: dict[str, object] | None = None,
    required_permission: str | None,
    target_mode: str,
    risk: str = RiskLevel.LOW.value,
    supports_dry_run: bool = False,
    requires_confirmation: bool = False,
    private_result_allowed: bool = False,
    renderer: str = "text",
) -> dict[str, object]:
    return {
        "name": name,
        "aliases": aliases or [],
        "summary": summary,
        "usage": usage,
        "argument_schema": argument_schema or command_arguments_schema(),
        "required_permission": required_permission,
        "target_mode": target_mode,
        "risk": risk,
        "supports_dry_run": supports_dry_run,
        "requires_confirmation": requires_confirmation,
        "private_result_allowed": private_result_allowed,
        "renderer": renderer,
    }


COMMAND_SPECS: tuple[dict[str, object], ...] = (
    command_spec(
        "help",
        aliases=["/agent", "/agent help"],
        summary="Show AgentBridge command help.",
        usage="/agent help",
        required_permission=None,
        target_mode="none",
    ),
    command_spec(
        "health",
        aliases=["健康"],
        summary="Report Control Plane health.",
        usage="/agent health",
        required_permission=None,
        target_mode="none",
    ),
    command_spec(
        "context.show",
        aliases=["context", "上下文"],
        summary="Show the active Bot chat context pointers.",
        usage="/agent context",
        required_permission="project.view",
        target_mode="none",
        private_result_allowed=True,
    ),
    command_spec(
        "project.list",
        aliases=["project", "project list", "项目 列表"],
        summary="List projects visible to the current chat context.",
        usage="/agent project list [--all]",
        argument_schema=command_arguments_schema({"all": BOOLEAN_SCHEMA}),
        required_permission="project.view",
        target_mode="project",
    ),
    command_spec(
        "project.info",
        aliases=["project info"],
        summary="Show project metadata and quotas.",
        usage="/agent project info [project]",
        argument_schema=command_arguments_schema({"project": OPTIONAL_STRING_SCHEMA}),
        required_permission="project.view",
        target_mode="project",
        private_result_allowed=True,
    ),
    command_spec(
        "project.use",
        aliases=["project use", "项目 使用"],
        summary="Select the active project for this chat context.",
        usage="/agent project use <project> [--version <pointer-version>]",
        argument_schema=command_arguments_schema(
            {"project": STRING_SCHEMA, "expected_version": OPTIONAL_INTEGER_SCHEMA},
            required=["project"],
        ),
        required_permission="project.view",
        target_mode="project",
    ),
    command_spec(
        "project.select",
        aliases=["select project", "选择 项目"],
        summary="Select a project by the current project-list number.",
        usage="/agent select project <number> [--version <pointer-version>]",
        argument_schema=command_arguments_schema(
            {"index": INTEGER_SCHEMA, "expected_version": OPTIONAL_INTEGER_SCHEMA},
            required=["index"],
        ),
        required_permission="project.view",
        target_mode="project",
    ),
    command_spec(
        "project.create",
        aliases=["project create", "项目 创建"],
        summary="Register a managed project and default workspace.",
        usage="/agent project create --name <name> --path <path> [--root <root>]",
        argument_schema=command_arguments_schema(
            {
                "name": STRING_SCHEMA,
                "slug": OPTIONAL_STRING_SCHEMA,
                "path": OPTIONAL_STRING_SCHEMA,
                "allowed_root": OPTIONAL_STRING_SCHEMA,
                "aliases": STRING_ARRAY_SCHEMA,
                "machine_id": OPTIONAL_STRING_SCHEMA,
                "max_active_sessions": OPTIONAL_INTEGER_SCHEMA,
                "max_running_turns": OPTIONAL_INTEGER_SCHEMA,
                "max_queued_turns": OPTIONAL_INTEGER_SCHEMA,
                "daily_turns_per_user": OPTIONAL_INTEGER_SCHEMA,
            },
            required=["name"],
        ),
        required_permission="project.manage",
        target_mode="project",
        risk=RiskLevel.MEDIUM.value,
        supports_dry_run=True,
        requires_confirmation=True,
    ),
    command_spec(
        "session.list",
        aliases=["session", "session list", "会话 列表"],
        summary="List sessions for the active or specified project.",
        usage="/agent session list [--project <project>]",
        argument_schema=command_arguments_schema({"project": OPTIONAL_STRING_SCHEMA}),
        required_permission="session.view",
        target_mode="project",
    ),
    command_spec(
        "session.create",
        aliases=["session new", "session create", "会话 新建"],
        summary="Create a new Agent session in the active project.",
        usage="/agent session new <name> [--agent claude|codex|generic_tui]",
        argument_schema=command_arguments_schema(
            {
                "name": STRING_SCHEMA,
                "project": OPTIONAL_STRING_SCHEMA,
                "workspace_id": OPTIONAL_STRING_SCHEMA,
                "agent": OPTIONAL_STRING_SCHEMA,
                "visibility": OPTIONAL_STRING_SCHEMA,
            }
        ),
        required_permission="session.create",
        target_mode="project",
        risk=RiskLevel.MEDIUM.value,
    ),
    command_spec(
        "session.use",
        aliases=["session use", "会话 使用"],
        summary="Select the active session for this chat context.",
        usage="/agent session use <session> [--version <pointer-version>]",
        argument_schema=command_arguments_schema(
            {"session": STRING_SCHEMA, "expected_version": OPTIONAL_INTEGER_SCHEMA},
            required=["session"],
        ),
        required_permission="session.view",
        target_mode="session",
    ),
    command_spec(
        "session.select",
        aliases=["select session", "选择 会话"],
        summary="Select a session by the current session-list number.",
        usage="/agent select session <number> [--project <project>] [--version <pointer-version>]",
        argument_schema=command_arguments_schema(
            {
                "index": INTEGER_SCHEMA,
                "project": OPTIONAL_STRING_SCHEMA,
                "expected_version": OPTIONAL_INTEGER_SCHEMA,
            },
            required=["index"],
        ),
        required_permission="session.view",
        target_mode="session",
    ),
    command_spec(
        "session.info",
        aliases=["session info"],
        summary="Show session status and routing metadata.",
        usage="/agent session info [session]",
        argument_schema=command_arguments_schema({"session": OPTIONAL_STRING_SCHEMA}),
        required_permission="session.view",
        target_mode="session",
        private_result_allowed=True,
    ),
    command_spec(
        "session.close",
        aliases=["session close"],
        summary="Close a managed session.",
        usage="/agent session close [session]",
        argument_schema=command_arguments_schema({"session": OPTIONAL_STRING_SCHEMA}),
        required_permission="session.manage",
        target_mode="session",
        risk=RiskLevel.MEDIUM.value,
        requires_confirmation=True,
    ),
    command_spec(
        "turn.enqueue",
        aliases=["ask", "send", "continue", "发送", "继续"],
        summary="Queue work for the target Agent session.",
        usage="/agent ask <prompt> [--session <session>]",
        argument_schema=command_arguments_schema(
            {
                "prompt": STRING_SCHEMA,
                "session": OPTIONAL_STRING_SCHEMA,
                "mode": {"type": "string", "enum": ["ask", "send", "continue"]},
            },
            required=["prompt"],
        ),
        required_permission="session.send",
        target_mode="session",
        risk=RiskLevel.MEDIUM.value,
        private_result_allowed=True,
    ),
    command_spec(
        "queue.list",
        aliases=["queue", "queue list"],
        summary="List queued turns for a session.",
        usage="/agent queue list [--session <session>]",
        argument_schema=command_arguments_schema({"session": OPTIONAL_STRING_SCHEMA}),
        required_permission="session.view",
        target_mode="session",
    ),
    command_spec(
        "queue.remove",
        aliases=["queue remove"],
        summary="Remove one queued turn.",
        usage="/agent queue remove <turn> [--session <session>] [--version <queue-version>]",
        argument_schema=command_arguments_schema(
            {
                "turn": STRING_SCHEMA,
                "session": OPTIONAL_STRING_SCHEMA,
                "expected_queue_version": OPTIONAL_STRING_SCHEMA,
            },
            required=["turn"],
        ),
        required_permission="session.send",
        target_mode="session",
        risk=RiskLevel.MEDIUM.value,
    ),
    command_spec(
        "queue.clear",
        aliases=["queue clear"],
        summary="Clear queued turns after confirming the affected count.",
        usage="/agent queue clear --confirm <count> [--session <session>]",
        argument_schema=command_arguments_schema(
            {
                "session": OPTIONAL_STRING_SCHEMA,
                "expected_queue_version": OPTIONAL_STRING_SCHEMA,
                "confirmed_count": OPTIONAL_INTEGER_SCHEMA,
            }
        ),
        required_permission="session.manage",
        target_mode="session",
        risk=RiskLevel.HIGH.value,
        requires_confirmation=True,
    ),
    command_spec(
        "queue.move",
        aliases=["queue move"],
        summary="Move one queued turn before another queued turn.",
        usage="/agent queue move <turn> --before <turn> --version <queue-version>",
        argument_schema=command_arguments_schema(
            {
                "turn": STRING_SCHEMA,
                "before": STRING_SCHEMA,
                "session": OPTIONAL_STRING_SCHEMA,
                "expected_queue_version": STRING_SCHEMA,
            },
            required=["turn", "before", "expected_queue_version"],
        ),
        required_permission="session.manage",
        target_mode="session",
        risk=RiskLevel.MEDIUM.value,
    ),
    command_spec(
        "queue.pause",
        aliases=["queue pause"],
        summary="Pause turn dispatch for a session queue.",
        usage="/agent queue pause --version <queue-version> [--session <session>]",
        argument_schema=command_arguments_schema(
            {"session": OPTIONAL_STRING_SCHEMA, "expected_queue_version": STRING_SCHEMA},
            required=["expected_queue_version"],
        ),
        required_permission="session.manage",
        target_mode="session",
        risk=RiskLevel.MEDIUM.value,
    ),
    command_spec(
        "queue.resume",
        aliases=["queue resume"],
        summary="Resume turn dispatch for a session queue.",
        usage="/agent queue resume --version <queue-version> [--session <session>]",
        argument_schema=command_arguments_schema(
            {"session": OPTIONAL_STRING_SCHEMA, "expected_queue_version": STRING_SCHEMA},
            required=["expected_queue_version"],
        ),
        required_permission="session.manage",
        target_mode="session",
        risk=RiskLevel.MEDIUM.value,
    ),
    command_spec(
        "control.status",
        aliases=["control", "control status", "控制 状态"],
        summary="Show the current writer lease for a session.",
        usage="/agent control status [session]",
        argument_schema=command_arguments_schema({"session": OPTIONAL_STRING_SCHEMA}),
        required_permission="session.view",
        target_mode="session",
    ),
    command_spec(
        "control.takeover",
        aliases=["control takeover", "控制 接管"],
        summary="Acquire the remote writer lease.",
        usage="/agent control takeover [session] [--ttl <seconds>]",
        argument_schema=command_arguments_schema(
            {"session": OPTIONAL_STRING_SCHEMA, "ttl_seconds": OPTIONAL_INTEGER_SCHEMA}
        ),
        required_permission="terminal.control",
        target_mode="session",
        risk=RiskLevel.HIGH.value,
        requires_confirmation=True,
    ),
    command_spec(
        "control.release",
        aliases=["control release", "控制 释放"],
        summary="Release the current writer lease by epoch.",
        usage="/agent control release [session] --epoch <epoch>",
        argument_schema=command_arguments_schema(
            {"session": OPTIONAL_STRING_SCHEMA, "epoch": INTEGER_SCHEMA},
            required=["epoch"],
        ),
        required_permission="terminal.control",
        target_mode="session",
        risk=RiskLevel.MEDIUM.value,
    ),
    command_spec(
        "role.list",
        aliases=["role", "role list"],
        summary="List group role bindings for this chat context.",
        usage="/agent role list",
        required_permission="group.role.manage",
        target_mode="none",
        private_result_allowed=True,
    ),
    command_spec(
        "role.grant",
        aliases=["role grant", "角色 授予"],
        summary="Grant chat-context roles to an actor.",
        usage="/agent role grant <actor-id> <role>[,<role>...]",
        argument_schema=command_arguments_schema(
            {"target_actor_id": STRING_SCHEMA, "roles": STRING_ARRAY_SCHEMA},
            required=["target_actor_id", "roles"],
        ),
        required_permission="group.role.manage",
        target_mode="none",
        risk=RiskLevel.HIGH.value,
        requires_confirmation=True,
    ),
    command_spec(
        "role.revoke",
        aliases=["role revoke"],
        summary="Revoke chat-context roles from an actor.",
        usage="/agent role revoke <actor-id> <role>[,<role>...]",
        argument_schema=command_arguments_schema(
            {"target_actor_id": STRING_SCHEMA, "roles": STRING_ARRAY_SCHEMA},
            required=["target_actor_id", "roles"],
        ),
        required_permission="group.role.manage",
        target_mode="none",
        risk=RiskLevel.HIGH.value,
        requires_confirmation=True,
    ),
    command_spec(
        "policy.show",
        aliases=["policy", "policy show"],
        summary="Show approval quorum policy for project or chat scope.",
        usage="/agent policy show [--project <project>]",
        argument_schema=command_arguments_schema({"project": OPTIONAL_STRING_SCHEMA}),
        required_permission="policy.manage",
        target_mode="project",
        private_result_allowed=True,
    ),
    command_spec(
        "policy.set",
        aliases=["policy set"],
        summary="Set approval quorum for a risk level.",
        usage="/agent policy set <low|medium|high|critical> <quorum> [--project <project>]",
        argument_schema=command_arguments_schema(
            {
                "project": OPTIONAL_STRING_SCHEMA,
                "risk_level": {
                    "type": "string",
                    "enum": [level.value for level in RiskLevel],
                },
                "quorum": INTEGER_SCHEMA,
            },
            required=["risk_level", "quorum"],
        ),
        required_permission="policy.manage",
        target_mode="project",
        risk=RiskLevel.HIGH.value,
        supports_dry_run=True,
        requires_confirmation=True,
    ),
    command_spec(
        "interaction.list",
        aliases=["approvals", "approval list", "question list"],
        summary="List interactions, approvals, questions, or plans.",
        usage="/agent approvals [--pending]",
        argument_schema=command_arguments_schema(
            {
                "pending": BOOLEAN_SCHEMA,
                "interaction_type": OPTIONAL_STRING_SCHEMA,
                "session": OPTIONAL_STRING_SCHEMA,
            }
        ),
        required_permission="session.view",
        target_mode="interaction",
    ),
    command_spec(
        "interaction.show",
        aliases=["approval show", "question show", "plan show"],
        summary="Show one interaction by ID.",
        usage="/agent approval show <interaction-id>",
        argument_schema=command_arguments_schema(
            {"interaction": STRING_SCHEMA, "interaction_type": OPTIONAL_STRING_SCHEMA},
            required=["interaction"],
        ),
        required_permission="session.view",
        target_mode="interaction",
        private_result_allowed=True,
    ),
    command_spec(
        "interaction.answer",
        aliases=["answer", "回答"],
        summary="Answer a free-text question interaction.",
        usage="/agent answer <interaction-id> <answer>",
        argument_schema=command_arguments_schema(
            {"interaction": STRING_SCHEMA, "answer": STRING_SCHEMA},
            required=["interaction", "answer"],
        ),
        required_permission="session.send",
        target_mode="interaction",
        risk=RiskLevel.MEDIUM.value,
        private_result_allowed=True,
    ),
    command_spec(
        "interaction.cancel",
        aliases=["approval cancel"],
        summary="Cancel a pending interaction.",
        usage="/agent approval cancel <interaction-id> [reason]",
        argument_schema=command_arguments_schema(
            {"interaction": STRING_SCHEMA, "reason": OPTIONAL_STRING_SCHEMA},
            required=["interaction"],
        ),
        required_permission="session.manage",
        target_mode="interaction",
        risk=RiskLevel.MEDIUM.value,
        requires_confirmation=True,
    ),
    command_spec(
        "approval.vote",
        aliases=["approve", "deny", "批准", "拒绝"],
        summary="Approve or deny an approval interaction.",
        usage="/agent approve <interaction-id> [once]",
        argument_schema=command_arguments_schema(
            {
                "interaction": STRING_SCHEMA,
                "approve": BOOLEAN_SCHEMA,
                "scope": OPTIONAL_STRING_SCHEMA,
                "reason": OPTIONAL_STRING_SCHEMA,
            },
            required=["interaction", "approve"],
        ),
        required_permission="approval.vote",
        target_mode="interaction",
        risk=RiskLevel.HIGH.value,
        private_result_allowed=True,
    ),
    command_spec(
        "plan.list",
        aliases=["plan", "plan list"],
        summary="List pending plan interactions.",
        usage="/agent plan list [--session <session>]",
        argument_schema=command_arguments_schema(
            {"pending": BOOLEAN_SCHEMA, "session": OPTIONAL_STRING_SCHEMA}
        ),
        required_permission="session.view",
        target_mode="interaction",
    ),
    command_spec(
        "plan.approve",
        aliases=["plan approve"],
        summary="Approve a proposed plan.",
        usage="/agent plan approve <interaction-id>",
        argument_schema=command_arguments_schema(
            {"interaction": STRING_SCHEMA},
            required=["interaction"],
        ),
        required_permission="session.send",
        target_mode="interaction",
        risk=RiskLevel.MEDIUM.value,
        private_result_allowed=True,
    ),
    command_spec(
        "plan.revise",
        aliases=["plan revise"],
        summary="Request changes to a proposed plan.",
        usage="/agent plan revise <interaction-id> <feedback>",
        argument_schema=command_arguments_schema(
            {"interaction": STRING_SCHEMA, "feedback": STRING_SCHEMA},
            required=["interaction", "feedback"],
        ),
        required_permission="session.send",
        target_mode="interaction",
        risk=RiskLevel.MEDIUM.value,
        private_result_allowed=True,
    ),
    command_spec(
        "plan.cancel",
        aliases=["plan cancel", "plan deny"],
        summary="Cancel a proposed plan interaction.",
        usage="/agent plan cancel <interaction-id> [reason]",
        argument_schema=command_arguments_schema(
            {"interaction": STRING_SCHEMA, "reason": OPTIONAL_STRING_SCHEMA},
            required=["interaction"],
        ),
        required_permission="session.manage",
        target_mode="interaction",
        risk=RiskLevel.MEDIUM.value,
        requires_confirmation=True,
        private_result_allowed=True,
    ),
)


def command_registry_payload() -> dict[str, object]:
    specs = [deepcopy(spec) for spec in COMMAND_SPECS]
    return {
        "schema_version": COMMAND_REGISTRY_SCHEMA_VERSION,
        "root_command": "agent",
        "aliases": ["ab"],
        "text_prefixes": ["/agent", "/ab"],
        "commands": [str(spec["name"]) for spec in specs],
        "specs": specs,
    }


class CommandService:
    def __init__(self, control_plane: ControlPlane) -> None:
        self.control = control_plane

    def parse(
        self,
        *,
        raw_text: str,
        actor: Actor,
        chat_context_id: str,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> CommandInvocation:
        body = self._strip_prefix(raw_text)
        tokens = shlex.split(body)
        if not tokens:
            canonical = "help"
            args: dict[str, object] = {}
        else:
            root = self._canonical_token(tokens[0])
            if root not in KNOWN_ROOTS:
                if ASCII_COMMAND_RE.match(root):
                    raise AgentBridgeError(
                        ErrorCode.COMMAND_UNKNOWN,
                        f"未知 /agent 子命令：{tokens[0]}",
                        next_step=(
                            "请执行 /agent help 查看可用命令，或使用 /agent ask 明确发送任务。"
                        ),
                    )
                canonical = "ask"
                args = {"prompt": body.strip()}
            else:
                canonical, args = self._parse_known(root, tokens[1:], body)

        trace = trace_id or f"trace_{uuid4().hex}"
        return CommandInvocation(
            id=f"cmd_{uuid4().hex[:12]}",
            trace_id=trace,
            idempotency_key=idempotency_key or f"{chat_context_id}:{actor.id}:{raw_text}",
            raw_text=raw_text,
            canonical_command=canonical,
            args=args,
            actor=actor,
            chat_context_id=chat_context_id,
        )

    def execute(self, invocation: CommandInvocation) -> CommandResult:
        existing = self.control.repository.get_command_result(invocation.idempotency_key)
        if existing:
            return existing

        result = self._execute_uncached(invocation)
        project_id_value = result.data.get("project_id")
        session_id_value = result.data.get("session_id")
        command_audit = self.control.audit(
            action="command.executed",
            actor=invocation.actor,
            outcome=AuditOutcome.ALLOWED,
            trace_id=invocation.trace_id,
            chat_context_id=invocation.chat_context_id,
            project_id=project_id_value if isinstance(project_id_value, str) else None,
            session_id=session_id_value if isinstance(session_id_value, str) else None,
            details={
                "canonical_command": invocation.canonical_command,
                "invocation_id": invocation.id,
            },
        )
        result = result.model_copy(update={"audit_id": command_audit.id})
        self.control.repository.store_command_result(invocation.idempotency_key, result)
        return result

    def _strip_prefix(self, raw_text: str) -> str:
        stripped = raw_text.strip()
        for prefix in ("/agent", "/ab"):
            if stripped == prefix:
                return ""
            if stripped.startswith(prefix + " "):
                return stripped[len(prefix) :].strip()
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            "命令必须以 /agent 或 /ab 开头。",
            next_step="请使用 /agent help 查看命令帮助。",
        )

    def _canonical_token(self, token: str) -> str:
        return COMMAND_ALIASES.get(token, token).lower()

    def _parse_known(
        self, root: str, tokens: list[str], original_body: str
    ) -> tuple[str, dict[str, object]]:
        if root == "help":
            return "help", {}
        if root == "health":
            return "health", {}
        if root == "context":
            return "context.show", {}
        if root in {"ask", "send", "continue"}:
            positional, options = parse_options(tokens)
            prompt = " ".join(positional).strip()
            return (
                "turn.enqueue",
                {
                    "prompt": prompt,
                    "session": options.get("session") or options.get("s"),
                    "mode": root,
                },
            )
        if root == "project":
            return self._parse_project(tokens)
        if root == "session":
            return self._parse_session(tokens)
        if root == "select":
            return self._parse_select(tokens)
        if root == "queue":
            return self._parse_queue(tokens)
        if root == "control":
            return self._parse_control(tokens)
        if root == "role":
            return self._parse_role(tokens)
        if root == "policy":
            return self._parse_policy(tokens)
        if root == "answer":
            return self._parse_answer(tokens)
        if root == "approve":
            return self._parse_approve(tokens)
        if root == "deny":
            return self._parse_deny(tokens)
        if root == "approval":
            return self._parse_approval(tokens)
        if root == "approvals":
            return self._parse_approvals(tokens)
        if root == "question":
            return self._parse_question(tokens)
        if root == "plan":
            return self._parse_plan(tokens)
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"子命令暂未实现：{root}",
            next_step="请执行 /agent help 查看当前可用命令。",
        )

    def _parse_project(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        if not tokens:
            return "project.list", {}
        action = self._canonical_token(tokens[0])
        positional, options = parse_options(tokens[1:])
        if action == "list":
            return "project.list", {"all": bool(options.get("all"))}
        if action == "info":
            return "project.info", {"project": positional[0] if positional else None}
        if action == "use":
            if not positional:
                raise missing_argument("project use", "<project>")
            return "project.use", {
                "project": positional[0],
                "expected_version": parse_optional_int(options.get("version")),
            }
        if action == "create":
            name = str(options.get("name") or " ".join(positional)).strip()
            if not name:
                raise missing_argument("project create", "--name <name>")
            aliases = parse_csv(str(options.get("alias") or options.get("aliases") or ""))
            return "project.create", {
                "name": name,
                "slug": options.get("slug"),
                "path": options.get("path"),
                "allowed_root": (
                    options.get("root") or options.get("allowed-root") or options.get("path")
                ),
                "aliases": aliases,
                "machine_id": options.get("machine") or "local",
                "max_active_sessions": parse_optional_int(
                    options.get("max-active-sessions")
                    or options.get("max-active")
                    or options.get("max-sessions")
                ),
                "max_running_turns": parse_optional_int(
                    options.get("max-running-turns") or options.get("max-running")
                ),
                "max_queued_turns": parse_optional_int(
                    options.get("max-queued-turns")
                    or options.get("max-queued")
                    or options.get("max-queue")
                ),
                "daily_turns_per_user": parse_optional_int(
                    options.get("daily-turns-per-user")
                    or options.get("daily-turns")
                    or options.get("max-daily-turns")
                ),
            }
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"未知 project 子命令：{action}",
            next_step="可用子命令：list、info、use、create。",
        )

    def _parse_session(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        if not tokens:
            return "session.list", {}
        action = self._canonical_token(tokens[0])
        positional, options = parse_options(tokens[1:])
        if action == "list":
            return "session.list", {"project": options.get("project") or options.get("p")}
        if action == "new":
            return "session.create", {
                "name": " ".join(positional).strip() or "AgentBridge Session",
                "project": options.get("project") or options.get("p"),
                "workspace_id": options.get("workspace-id"),
                "agent": options.get("agent"),
                "visibility": options.get("visibility") or "group",
            }
        if action == "use":
            if not positional:
                raise missing_argument("session use", "<session>")
            return "session.use", {
                "session": positional[0],
                "expected_version": parse_optional_int(options.get("version")),
            }
        if action == "info":
            return "session.info", {"session": positional[0] if positional else None}
        if action == "close":
            return "session.close", {"session": positional[0] if positional else None}
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"未知 session 子命令：{action}",
            next_step="可用子命令：list、new、use、info、close。",
        )

    def _parse_select(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        if not tokens:
            raise missing_argument("select", "<project|session> <number>")
        target = self._canonical_token(tokens[0])
        positional, options = parse_options(tokens[1:])
        if not positional:
            raise missing_argument(f"select {target}", "<number>")
        index = parse_selection_index(f"select {target}", positional[0])
        if target == "project":
            return "project.select", {
                "index": index,
                "expected_version": parse_optional_int(options.get("version")),
            }
        if target == "session":
            return "session.select", {
                "index": index,
                "project": options.get("project") or options.get("p"),
                "expected_version": parse_optional_int(options.get("version")),
            }
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"未知 select 目标：{target}",
            next_step="可用目标：project、session。",
        )

    def _parse_queue(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        if not tokens:
            return "queue.list", {}
        action = self._canonical_token(tokens[0])
        positional, options = parse_options(tokens[1:])
        session = options.get("session") or options.get("s")
        queue_version = options.get("queue-version") or options.get("version")
        if queue_version is True:
            raise missing_argument(f"queue {action}", "--version <queue_version>")
        if action == "list":
            return "queue.list", {"session": session}
        if action == "remove":
            if not positional:
                raise missing_argument("queue remove", "<turn>")
            return "queue.remove", {
                "turn": positional[0],
                "session": session,
                "expected_queue_version": queue_version,
            }
        if action == "clear":
            return "queue.clear", {
                "session": session,
                "expected_queue_version": queue_version,
                "confirmed_count": parse_optional_int(options.get("confirm")),
            }
        if action == "move":
            if not positional:
                raise missing_argument("queue move", "<turn>")
            before = options.get("before")
            if before is None or before is True:
                raise missing_argument("queue move", "--before <turn>")
            if queue_version is None:
                raise missing_argument("queue move", "--version <queue_version>")
            return "queue.move", {
                "turn": positional[0],
                "before": before,
                "session": session,
                "expected_queue_version": queue_version,
            }
        if action in {"pause", "resume"}:
            if queue_version is None:
                raise missing_argument(f"queue {action}", "--version <queue_version>")
            return f"queue.{action}", {
                "session": session,
                "expected_queue_version": queue_version,
            }
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"未知 queue 子命令：{action}",
            next_step="当前可用子命令：list、remove、clear、move、pause、resume。",
        )

    def _parse_control(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        if not tokens:
            return "control.status", {}
        action = self._canonical_token(tokens[0])
        positional, options = parse_options(tokens[1:])
        if action == "status":
            return "control.status", {"session": positional[0] if positional else None}
        if action == "takeover":
            return "control.takeover", {
                "session": positional[0] if positional else None,
                "ttl_seconds": parse_optional_int(options.get("ttl")) or 300,
            }
        if action == "release":
            return "control.release", {
                "session": positional[0] if positional else None,
                "epoch": parse_optional_int(options.get("epoch")),
            }
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"未知 control 子命令：{action}",
            next_step="可用子命令：status、takeover、release。",
        )

    def _parse_role(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        if not tokens:
            return "role.list", {}
        action = self._canonical_token(tokens[0])
        positional, options = parse_options(tokens[1:])
        if action == "list":
            return "role.list", {}
        if action == "grant":
            if not positional:
                raise missing_argument("role grant", "<actor-id>")
            roles = parse_role_values(
                positional[1:],
                options.get("role"),
                options.get("roles"),
            )
            if not roles:
                raise missing_argument("role grant", "<role>")
            return "role.grant", {"target_actor_id": positional[0], "roles": roles}
        if action in {"revoke", "remove"}:
            if not positional:
                raise missing_argument("role revoke", "<actor-id>")
            roles = parse_role_values(
                positional[1:],
                options.get("role"),
                options.get("roles"),
            )
            if not roles:
                raise missing_argument("role revoke", "<role>")
            return "role.revoke", {"target_actor_id": positional[0], "roles": roles}
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"未知 role 子命令：{action}",
            next_step="可用子命令：list、grant、revoke。",
        )

    def _parse_policy(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        if not tokens:
            return "policy.show", {}
        action = self._canonical_token(tokens[0])
        positional, options = parse_options(tokens[1:])
        project_token = options.get("project") or options.get("p")
        if action == "show":
            return "policy.show", {"project": project_token}
        if action == "set":
            if len(positional) < 2:
                raise missing_argument("policy set", "<risk-level> <quorum>")
            risk_level = risk_level_from_policy_key(positional[0])
            return "policy.set", {
                "project": project_token,
                "risk_level": risk_level.value,
                "quorum": parse_required_int("policy set", positional[1]),
            }
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"未知 policy 子命令：{action}",
            next_step="可用子命令：show、set。",
        )

    def _parse_answer(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        positional, options = parse_options(tokens)
        if not positional:
            raise missing_argument("answer", "<interaction-id>")
        answer = str(options.get("text") or " ".join(positional[1:])).strip()
        if not answer:
            raise missing_argument("answer", "<answer>")
        return "interaction.answer", {"interaction": positional[0], "answer": answer}

    def _parse_approve(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        positional, _ = parse_options(tokens)
        if not positional:
            raise missing_argument("approve", "<interaction-id>")
        scope = positional[1] if len(positional) > 1 else "once"
        return "approval.vote", {
            "interaction": positional[0],
            "approve": True,
            "scope": scope,
            "reason": None,
        }

    def _parse_deny(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        positional, _ = parse_options(tokens)
        if not positional:
            raise missing_argument("deny", "<interaction-id>")
        return "approval.vote", {
            "interaction": positional[0],
            "approve": False,
            "scope": "once",
            "reason": " ".join(positional[1:]).strip() or None,
        }

    def _parse_approval(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        if not tokens:
            return "interaction.list", {"pending": True}
        action = self._canonical_token(tokens[0])
        positional, options = parse_options(tokens[1:])
        if action == "show":
            if not positional:
                raise missing_argument("approval show", "<interaction-id>")
            return "interaction.show", {"interaction": positional[0]}
        if action == "list":
            return "interaction.list", {"pending": bool(options.get("pending"))}
        if action == "cancel":
            if not positional:
                raise missing_argument("approval cancel", "<interaction-id>")
            return "interaction.cancel", {
                "interaction": positional[0],
                "reason": " ".join(positional[1:]).strip() or None,
            }
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"未知 approval 子命令：{action}",
            next_step="可用子命令：show、list、cancel。",
        )

    def _parse_approvals(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        _, options = parse_options(tokens)
        return "interaction.list", {"pending": bool(options.get("pending", True))}

    def _parse_question(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        if not tokens:
            return "interaction.list", {
                "pending": True,
                "interaction_type": InteractionType.QUESTION.value,
            }
        action = self._canonical_token(tokens[0])
        positional, options = parse_options(tokens[1:])
        if action == "show":
            if not positional:
                raise missing_argument("question show", "<interaction-id>")
            return "interaction.show", {
                "interaction": positional[0],
                "interaction_type": InteractionType.QUESTION.value,
            }
        if action == "list":
            return "interaction.list", {
                "pending": bool(options.get("pending", True)),
                "interaction_type": InteractionType.QUESTION.value,
            }
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"未知 question 子命令：{action}",
            next_step="可用子命令：show、list。",
        )

    def _parse_plan(self, tokens: list[str]) -> tuple[str, dict[str, object]]:
        if not tokens:
            return "plan.list", {"pending": True, "session": None}
        action = self._canonical_token(tokens[0])
        positional, options = parse_options(tokens[1:])
        if action == "show":
            if not positional:
                raise missing_argument("plan show", "<interaction-id>")
            return "interaction.show", {
                "interaction": positional[0],
                "interaction_type": InteractionType.PLAN.value,
            }
        if action == "list":
            return "plan.list", {
                "pending": bool(options.get("pending", True)),
                "session": options.get("session") or options.get("s"),
            }
        if action == "approve":
            if not positional:
                raise missing_argument("plan approve", "<interaction-id>")
            return "plan.approve", {"interaction": positional[0]}
        if action in {"revise", "revision"}:
            if not positional:
                raise missing_argument("plan revise", "<interaction-id>")
            feedback = " ".join(positional[1:]).strip()
            if not feedback:
                raise missing_argument("plan revise", "<feedback>")
            return "plan.revise", {
                "interaction": positional[0],
                "feedback": feedback,
            }
        if action in {"cancel", "deny"}:
            if not positional:
                raise missing_argument("plan cancel", "<interaction-id>")
            return "plan.cancel", {
                "interaction": positional[0],
                "reason": " ".join(positional[1:]).strip() or None,
            }
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"未知 plan 子命令：{action}",
            next_step="可用子命令：show、list、approve、revise、cancel。",
        )

    def _execute_uncached(self, invocation: CommandInvocation) -> CommandResult:
        command = invocation.canonical_command
        args = invocation.args
        if command == "help":
            return self._result(
                invocation,
                "AgentBridge commands",
                "可用命令：project、session、ask/send、control、role、policy、approvals、health。",
            )
        if command == "health":
            return self._result(invocation, "Health", "Control Plane 正常。", self.control.health())
        if command == "context.show":
            context = self.control.repository.get_chat_context(invocation.chat_context_id)
            return self._result(
                invocation,
                "Context",
                f"pointer_version={context.pointer_version}",
                {"context": context.model_dump(mode="json")},
            )
        if command == "project.create":
            return self._execute_project_create(invocation)
        if command == "project.list":
            projects = self.control.list_projects_for_context(
                invocation.actor, invocation.chat_context_id
            )
            return self._result(
                invocation,
                "Projects",
                f"共 {len(projects)} 个项目。",
                {"projects": [project.model_dump(mode="json") for project in projects]},
            )
        if command == "project.info":
            project = self._resolve_project_arg(invocation)
            return self._result(
                invocation,
                "Project Info",
                f"{project.name} ({project.slug})",
                {"project_id": project.id, "project": project.model_dump(mode="json")},
            )
        if command == "project.use":
            context = self.control.use_project(
                actor=invocation.actor,
                chat_context_id=invocation.chat_context_id,
                project_token=str(args["project"]),
                expected_version=args.get("expected_version")
                if isinstance(args.get("expected_version"), int)
                else None,
                trace_id=invocation.trace_id,
            )
            return self._result(
                invocation,
                "Project Selected",
                f"已切换活动项目，pointer_version={context.pointer_version}。",
                {
                    "project_id": context.active_project_id,
                    "context": context.model_dump(mode="json"),
                },
            )
        if command == "project.select":
            return self._execute_project_select(invocation)
        if command == "session.create":
            return self._execute_session_create(invocation)
        if command == "session.list":
            project_id = self._optional_project_id(invocation)
            sessions = self.control.list_sessions_for_context(
                invocation.actor,
                project_id=project_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Sessions",
                f"共 {len(sessions)} 个会话。",
                {"sessions": [session.model_dump(mode="json") for session in sessions]},
            )
        if command == "session.select":
            return self._execute_session_select(invocation)
        if command == "session.use":
            context = self.control.use_session(
                actor=invocation.actor,
                chat_context_id=invocation.chat_context_id,
                session_token=str(args["session"]),
                expected_version=args.get("expected_version")
                if isinstance(args.get("expected_version"), int)
                else None,
                trace_id=invocation.trace_id,
            )
            return self._result(
                invocation,
                "Session Selected",
                f"已切换活动会话，pointer_version={context.pointer_version}。",
                {
                    "project_id": context.active_project_id,
                    "session_id": context.active_session_id,
                    "context": context.model_dump(mode="json"),
                },
            )
        if command == "session.info":
            session = self._resolve_session_arg(invocation)
            return self._result(
                invocation,
                "Session Info",
                f"[{session.short_code}] {session.name} · {session.status.value}",
                {
                    "project_id": session.project_id,
                    "session_id": session.id,
                    "session": session.model_dump(mode="json"),
                },
            )
        if command == "session.close":
            session = self._resolve_session_arg(invocation)
            closed = self.control.close_session(
                actor=invocation.actor,
                session_id=session.id,
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Session Closed",
                f"已关闭 [{closed.short_code}] {closed.name}。",
                {
                    "project_id": closed.project_id,
                    "session_id": closed.id,
                    "session": closed.model_dump(mode="json"),
                },
            )
        if command == "turn.enqueue":
            session = self._resolve_session_arg(invocation)
            turn = self.control.enqueue_turn(
                actor=invocation.actor,
                session_id=session.id,
                prompt=str(args.get("prompt") or ""),
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Turn Queued",
                f"任务已进入 [{session.short_code}] 队列。",
                {
                    "project_id": session.project_id,
                    "session_id": session.id,
                    "turn_id": turn.id,
                    "turn": turn.model_dump(mode="json"),
                },
            )
        if command == "queue.list":
            session = self._resolve_session_arg(invocation)
            turns, queue_version, queue_paused = self.control.list_turn_queue(
                actor=invocation.actor,
                session_id=session.id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Queue",
                (
                    f"[{session.short_code}] queued Turns：{len(turns)}，"
                    f"queue_version={queue_version}。"
                ),
                {
                    "project_id": session.project_id,
                    "session_id": session.id,
                    "queue_version": queue_version,
                    "queue_paused": queue_paused,
                    "turns": [turn.model_dump(mode="json") for turn in turns],
                },
            )
        if command == "queue.remove":
            turn = self.control.repository.get_turn(str(args["turn"]))
            session = self.control.repository.get_session(turn.session_id)
            if args.get("session"):
                requested_session = self._resolve_session_arg(invocation)
                if requested_session.id != session.id:
                    raise AgentBridgeError(
                        ErrorCode.RESOURCE_CONFLICT,
                        "Turn 不属于指定 Session。",
                        next_step="请执行 /agent queue list --session <session> 后重试。",
                        status_code=409,
                        details={
                            "turn_id": turn.id,
                            "turn_session_id": turn.session_id,
                            "session_id": requested_session.id,
                        },
                    )
            cancelled, queue_version = self.control.remove_queued_turn(
                actor=invocation.actor,
                session_id=session.id,
                turn_id=turn.id,
                trace_id=invocation.trace_id,
                expected_queue_version=str(args["expected_queue_version"])
                if args.get("expected_queue_version")
                else None,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Turn Removed",
                (
                    f"已从 [{session.short_code}] 队列移除 Turn {cancelled.id}，"
                    f"queue_version={queue_version}。"
                ),
                {
                    "project_id": session.project_id,
                    "session_id": session.id,
                    "queue_version": queue_version,
                    "turn_id": cancelled.id,
                    "turn": cancelled.model_dump(mode="json"),
                },
            )
        if command == "queue.move":
            turn = self.control.repository.get_turn(str(args["turn"]))
            before_turn = self.control.repository.get_turn(str(args["before"]))
            session = self.control.repository.get_session(turn.session_id)
            if args.get("session"):
                requested_session = self._resolve_session_arg(invocation)
                if requested_session.id != session.id:
                    raise AgentBridgeError(
                        ErrorCode.RESOURCE_CONFLICT,
                        "Turn 不属于指定 Session。",
                        next_step="请执行 /agent queue list --session <session> 后重试。",
                        status_code=409,
                        details={
                            "turn_id": turn.id,
                            "turn_session_id": turn.session_id,
                            "session_id": requested_session.id,
                        },
                    )
            reordered, queue_version = self.control.reorder_turn_queue(
                actor=invocation.actor,
                session_id=session.id,
                turn_id=turn.id,
                before_turn_id=before_turn.id,
                expected_queue_version=str(args["expected_queue_version"]),
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Queue Reordered",
                (
                    f"已将 Turn {turn.id} 移到 {before_turn.id} 前，"
                    f"queue_version={queue_version}。"
                ),
                {
                    "project_id": session.project_id,
                    "session_id": session.id,
                    "queue_version": queue_version,
                    "turn_id": turn.id,
                    "before_turn_id": before_turn.id,
                    "turns": [turn.model_dump(mode="json") for turn in reordered],
                },
            )
        if command in {"queue.pause", "queue.resume"}:
            session = self._resolve_session_arg(invocation)
            queue_paused = command == "queue.pause"
            updated_session, queue_version = self.control.set_turn_queue_paused(
                actor=invocation.actor,
                session_id=session.id,
                paused=queue_paused,
                expected_queue_version=str(args["expected_queue_version"]),
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Queue Paused" if queue_paused else "Queue Resumed",
                (
                    f"[{session.short_code}] 队列已"
                    f"{'暂停' if queue_paused else '恢复'}，"
                    f"queue_version={queue_version}。"
                ),
                {
                    "project_id": session.project_id,
                    "session_id": session.id,
                    "queue_version": queue_version,
                    "queue_paused": updated_session.queue_paused,
                    "session": updated_session.model_dump(mode="json"),
                },
            )
        if command == "queue.clear":
            session = self._resolve_session_arg(invocation)
            cancelled, queue_version = self.control.clear_turn_queue(
                actor=invocation.actor,
                session_id=session.id,
                trace_id=invocation.trace_id,
                expected_queue_version=str(args["expected_queue_version"])
                if args.get("expected_queue_version")
                else None,
                confirmed_count=int(args["confirmed_count"])
                if args.get("confirmed_count") is not None
                else None,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Queue Cleared",
                (
                    f"已清空 [{session.short_code}] 队列，移除 {len(cancelled)} 个 Turn，"
                    f"queue_version={queue_version}。"
                ),
                {
                    "project_id": session.project_id,
                    "session_id": session.id,
                    "queue_version": queue_version,
                    "turns": [turn.model_dump(mode="json") for turn in cancelled],
                    "count": len(cancelled),
                },
            )
        if command == "control.status":
            session = self._resolve_session_arg(invocation)
            lease = self.control.repository.current_lease(session.id)
            owner = "none" if lease is None else lease.owner_type.value
            return self._result(
                invocation,
                "Control Status",
                f"[{session.short_code}] 当前写入者：{owner}。",
                {
                    "project_id": session.project_id,
                    "session_id": session.id,
                    "lease": lease.model_dump(mode="json") if lease else None,
                },
            )
        if command == "control.takeover":
            session = self._resolve_session_arg(invocation)
            lease = self.control.acquire_lease(
                actor=invocation.actor,
                session_id=session.id,
                owner_type=LeaseOwnerType.WEB_ADMIN,
                owner_id=invocation.actor.id,
                ttl_seconds=int(args.get("ttl_seconds") or 300),
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Control Acquired",
                f"已取得远程写入租约，epoch={lease.epoch}。",
                {
                    "project_id": session.project_id,
                    "session_id": session.id,
                    "lease": lease.model_dump(mode="json"),
                },
            )
        if command == "control.release":
            session = self._resolve_session_arg(invocation)
            epoch = args.get("epoch")
            if not isinstance(epoch, int):
                raise missing_argument("control release", "--epoch <epoch>")
            next_epoch = self.control.release_lease(
                actor=invocation.actor,
                session_id=session.id,
                epoch=epoch,
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Control Released",
                f"已释放写入租约，next_epoch={next_epoch}。",
                {
                    "project_id": session.project_id,
                    "session_id": session.id,
                    "next_epoch": next_epoch,
                },
            )
        if command == "role.list":
            bindings = self.control.list_group_role_bindings(
                actor=invocation.actor,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Role Bindings",
                f"共 {len(bindings)} 条角色绑定。",
                {
                    "chat_context_id": invocation.chat_context_id,
                    "bindings": [binding.model_dump(mode="json") for binding in bindings],
                },
            )
        if command == "role.grant":
            roles = set(str(role) for role in args.get("roles", []))
            binding = self.control.grant_group_roles(
                actor=invocation.actor,
                chat_context_id=invocation.chat_context_id,
                target_actor_id=str(args["target_actor_id"]),
                roles=roles,
                trace_id=invocation.trace_id,
            )
            return self._result(
                invocation,
                "Role Granted",
                f"已授予 {binding.actor_id}；当前角色：{', '.join(sorted(binding.roles))}。",
                {
                    "chat_context_id": invocation.chat_context_id,
                    "target_actor_id": binding.actor_id,
                    "binding": binding.model_dump(mode="json"),
                },
            )
        if command == "role.revoke":
            roles = set(str(role) for role in args.get("roles", []))
            binding = self.control.revoke_group_roles(
                actor=invocation.actor,
                chat_context_id=invocation.chat_context_id,
                target_actor_id=str(args["target_actor_id"]),
                roles=roles,
                trace_id=invocation.trace_id,
            )
            current_roles = sorted(binding.roles) if binding else []
            return self._result(
                invocation,
                "Role Revoked",
                f"已撤销 {args['target_actor_id']}；当前角色：{', '.join(current_roles) or '无'}。",
                {
                    "chat_context_id": invocation.chat_context_id,
                    "target_actor_id": str(args["target_actor_id"]),
                    "binding": binding.model_dump(mode="json") if binding else None,
                },
            )
        if command == "policy.show":
            scope_type, scope_id = self._policy_scope(invocation)
            state = self.control.get_approval_policy_state(
                actor=invocation.actor,
                scope_type=scope_type,
                scope_id=scope_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Approval Policy",
                f"{scope_type.value}:{scope_id}",
                {
                    "scope_type": scope_type.value,
                    "scope_id": scope_id,
                    "policy": state,
                },
            )
        if command == "policy.set":
            scope_type, scope_id = self._policy_scope(invocation)
            override = self.control.update_approval_policy_quorum(
                actor=invocation.actor,
                scope_type=scope_type,
                scope_id=scope_id,
                risk_level=RiskLevel(str(args["risk_level"])),
                quorum=int(args["quorum"]),
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Approval Policy Updated",
                f"{scope_type.value}:{scope_id} quorum 已更新。",
                {
                    "scope_type": scope_type.value,
                    "scope_id": scope_id,
                    "override": override.model_dump(mode="json"),
                },
            )
        if command == "interaction.list":
            session_id: str | None = None
            if args.get("session"):
                session_id = self._resolve_session_arg(invocation).id
            interactions = self.control.list_interactions(
                actor=invocation.actor,
                chat_context_id=invocation.chat_context_id,
                session_id=session_id,
                status=None,
            )
            if args.get("pending"):
                interactions = [
                    interaction
                    for interaction in interactions
                    if interaction.status
                    in {InteractionStatus.PENDING, InteractionStatus.PARTIALLY_APPROVED}
                ]
            if args.get("interaction_type"):
                interactions = [
                    interaction
                    for interaction in interactions
                    if interaction.type.value == args["interaction_type"]
                ]
            return self._result(
                invocation,
                "Interactions",
                f"共 {len(interactions)} 个交互。",
                {
                    "interactions": [
                        interaction.model_dump(mode="json") for interaction in interactions
                    ]
                },
            )
        if command == "interaction.show":
            interaction = self.control.get_interaction(
                actor=invocation.actor,
                interaction_id=str(args["interaction"]),
                chat_context_id=invocation.chat_context_id,
            )
            if args.get("interaction_type"):
                self._require_interaction_type(
                    interaction,
                    InteractionType(str(args["interaction_type"])),
                )
            return self._result(
                invocation,
                "Interaction",
                f"{interaction.id} · {interaction.type.value} · {interaction.status.value}",
                {
                    "session_id": interaction.session_id,
                    "interaction_id": interaction.id,
                    "interaction": interaction.model_dump(mode="json"),
                },
            )
        if command == "plan.list":
            session_id = self._resolve_session_arg(invocation).id if args.get("session") else None
            interactions = self.control.list_interactions(
                actor=invocation.actor,
                chat_context_id=invocation.chat_context_id,
                session_id=session_id,
                status=None,
            )
            plans = [
                interaction
                for interaction in interactions
                if interaction.type == InteractionType.PLAN
            ]
            if args.get("pending"):
                plans = [
                    interaction
                    for interaction in plans
                    if interaction.status
                    in {InteractionStatus.PENDING, InteractionStatus.PARTIALLY_APPROVED}
                ]
            return self._result(
                invocation,
                "Plans",
                f"共 {len(plans)} 个计划交互。",
                {
                    "interactions": [
                        interaction.model_dump(mode="json") for interaction in plans
                    ]
                },
            )
        if command == "plan.approve":
            current = self.control.get_interaction(
                actor=invocation.actor,
                interaction_id=str(args["interaction"]),
                chat_context_id=invocation.chat_context_id,
            )
            self._require_interaction_type(current, InteractionType.PLAN)
            interaction = self.control.answer_interaction(
                actor=invocation.actor,
                interaction_id=current.id,
                answer="approved",
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Plan Approved",
                f"已批准计划 {interaction.id}。",
                {
                    "session_id": interaction.session_id,
                    "interaction_id": interaction.id,
                    "interaction": interaction.model_dump(mode="json"),
                    "plan_decision": "approved",
                },
            )
        if command == "plan.revise":
            current = self.control.get_interaction(
                actor=invocation.actor,
                interaction_id=str(args["interaction"]),
                chat_context_id=invocation.chat_context_id,
            )
            self._require_interaction_type(current, InteractionType.PLAN)
            interaction = self.control.answer_interaction(
                actor=invocation.actor,
                interaction_id=current.id,
                answer=str(args["feedback"]),
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Plan Revision Requested",
                f"已提交计划修改意见 {interaction.id}。",
                {
                    "session_id": interaction.session_id,
                    "interaction_id": interaction.id,
                    "interaction": interaction.model_dump(mode="json"),
                    "plan_decision": "revise",
                    "feedback": str(args["feedback"]),
                },
            )
        if command == "plan.cancel":
            current = self.control.get_interaction(
                actor=invocation.actor,
                interaction_id=str(args["interaction"]),
                chat_context_id=invocation.chat_context_id,
            )
            self._require_interaction_type(current, InteractionType.PLAN)
            interaction = self.control.cancel_interaction(
                actor=invocation.actor,
                interaction_id=current.id,
                reason=str(args["reason"]) if args.get("reason") else None,
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Plan Cancelled",
                f"已取消计划 {interaction.id}。",
                {
                    "session_id": interaction.session_id,
                    "interaction_id": interaction.id,
                    "interaction": interaction.model_dump(mode="json"),
                    "reason": args.get("reason"),
                },
            )
        if command == "interaction.answer":
            interaction = self.control.answer_interaction(
                actor=invocation.actor,
                interaction_id=str(args["interaction"]),
                answer=str(args["answer"]),
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Interaction Answered",
                f"已回答 {interaction.id}。",
                {
                    "session_id": interaction.session_id,
                    "interaction_id": interaction.id,
                    "interaction": interaction.model_dump(mode="json"),
                },
            )
        if command == "interaction.cancel":
            interaction = self.control.cancel_interaction(
                actor=invocation.actor,
                interaction_id=str(args["interaction"]),
                reason=str(args["reason"]) if args.get("reason") else None,
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            return self._result(
                invocation,
                "Interaction Cancelled",
                f"已取消 {interaction.id}。",
                {
                    "session_id": interaction.session_id,
                    "interaction_id": interaction.id,
                    "interaction": interaction.model_dump(mode="json"),
                },
            )
        if command == "approval.vote":
            interaction = self.control.vote_interaction(
                actor=invocation.actor,
                interaction_id=str(args["interaction"]),
                approve=bool(args["approve"]),
                reason=str(args["reason"]) if args.get("reason") else None,
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            action = "批准" if args["approve"] else "拒绝"
            return self._result(
                invocation,
                "Approval Voted",
                f"已{action} {interaction.id}，状态：{interaction.status.value}。",
                {
                    "session_id": interaction.session_id,
                    "interaction_id": interaction.id,
                    "interaction": interaction.model_dump(mode="json"),
                    "scope": args.get("scope") or "once",
                },
            )
        raise AgentBridgeError(
            ErrorCode.COMMAND_UNKNOWN,
            f"命令暂未实现：{command}",
            next_step="请执行 /agent help 查看当前可用命令。",
        )

    def _execute_project_create(self, invocation: CommandInvocation) -> CommandResult:
        args = invocation.args
        project = self.control.create_project(
            actor=invocation.actor,
            name=str(args["name"]),
            slug=str(args["slug"]) if args.get("slug") else None,
            aliases=list(args.get("aliases") or []),
            max_active_sessions=int(args["max_active_sessions"])
            if args.get("max_active_sessions") is not None
            else 10,
            max_running_turns=int(args["max_running_turns"])
            if args.get("max_running_turns") is not None
            else 4,
            max_queued_turns=int(args["max_queued_turns"])
            if args.get("max_queued_turns") is not None
            else 100,
            daily_turns_per_user=int(args["daily_turns_per_user"])
            if args.get("daily_turns_per_user") is not None
            else 50,
            trace_id=invocation.trace_id,
            chat_context_id=invocation.chat_context_id,
        )
        workspace_data = None
        if args.get("path"):
            workspace = self.control.add_workspace(
                actor=invocation.actor,
                project_id=project.id,
                machine_id=str(args.get("machine_id") or "local"),
                path=str(args["path"]),
                allowed_root=str(args.get("allowed_root") or args["path"]),
                workspace_type=WorkspaceType.SHARED,
                trace_id=invocation.trace_id,
                chat_context_id=invocation.chat_context_id,
            )
            workspace_data = workspace.model_dump(mode="json")
        self.control.bind_project(
            actor=invocation.actor,
            chat_context_id=invocation.chat_context_id,
            project_id=project.id,
            alias_in_chat=project.aliases[0] if project.aliases else project.slug,
            is_default=True,
            trace_id=invocation.trace_id,
        )
        return self._result(
            invocation,
            "Project Created",
            f"已创建项目 {project.name}。",
            {
                "project_id": project.id,
                "project": project.model_dump(mode="json"),
                "workspace": workspace_data,
            },
        )

    def _execute_session_create(self, invocation: CommandInvocation) -> CommandResult:
        args = invocation.args
        project_id = self._required_project_id(invocation)
        project = self.control.repository.get_project(project_id)
        agent = AgentType(args["agent"]) if args.get("agent") else project.default_agent
        visibility = Visibility(str(args.get("visibility") or "group"))
        session = self.control.create_session(
            actor=invocation.actor,
            project_id=project_id,
            workspace_id=str(args["workspace_id"]) if args.get("workspace_id") else None,
            name=str(args.get("name") or "AgentBridge Session"),
            agent_type=agent,
            visibility=visibility,
            trace_id=invocation.trace_id,
            chat_context_id=invocation.chat_context_id,
        )
        context = self.control.repository.get_chat_context(invocation.chat_context_id)
        self.control.repository.update_active_session(
            invocation.chat_context_id,
            session.id,
            expected_version=context.pointer_version,
        )
        return self._result(
            invocation,
            "Session Created",
            f"已创建 [{session.short_code}] {session.name}。",
            {
                "project_id": session.project_id,
                "session_id": session.id,
                "session": session.model_dump(mode="json"),
            },
        )

    def _execute_project_select(self, invocation: CommandInvocation) -> CommandResult:
        args = invocation.args
        projects = self.control.list_projects_for_context(
            invocation.actor,
            invocation.chat_context_id,
        )
        index = int(args["index"])
        project = select_numbered_item(
            projects,
            index,
            item_label="项目",
            list_command="/agent project list",
        )
        context = self.control.use_project(
            actor=invocation.actor,
            chat_context_id=invocation.chat_context_id,
            project_token=project.id,
            expected_version=args.get("expected_version")
            if isinstance(args.get("expected_version"), int)
            else None,
            trace_id=invocation.trace_id,
        )
        return self._result(
            invocation,
            "Project Selected",
            f"已选择第 {index} 个项目：{project.name}，pointer_version={context.pointer_version}。",
            {
                "project_id": project.id,
                "selected_index": index,
                "project": project.model_dump(mode="json"),
                "context": context.model_dump(mode="json"),
            },
        )

    def _execute_session_select(self, invocation: CommandInvocation) -> CommandResult:
        args = invocation.args
        project_id = self._optional_project_id(invocation)
        sessions = self.control.list_sessions_for_context(
            invocation.actor,
            project_id=project_id,
            chat_context_id=invocation.chat_context_id,
        )
        index = int(args["index"])
        session = select_numbered_item(
            sessions,
            index,
            item_label="会话",
            list_command="/agent session list",
        )
        expected_version = (
            args.get("expected_version")
            if isinstance(args.get("expected_version"), int)
            else None
        )
        context = self.control.repository.get_chat_context(invocation.chat_context_id)
        if context.active_project_id != session.project_id:
            context = self.control.use_project(
                actor=invocation.actor,
                chat_context_id=invocation.chat_context_id,
                project_token=session.project_id,
                expected_version=expected_version,
                trace_id=invocation.trace_id,
            )
            expected_version = context.pointer_version
        context = self.control.use_session(
            actor=invocation.actor,
            chat_context_id=invocation.chat_context_id,
            session_token=session.id,
            expected_version=expected_version,
            trace_id=invocation.trace_id,
        )
        return self._result(
            invocation,
            "Session Selected",
            (
                f"已选择第 {index} 个会话：[{session.short_code}] {session.name}，"
                f"pointer_version={context.pointer_version}。"
            ),
            {
                "project_id": session.project_id,
                "session_id": session.id,
                "selected_index": index,
                "session": session.model_dump(mode="json"),
                "context": context.model_dump(mode="json"),
            },
        )

    def _resolve_project_arg(self, invocation: CommandInvocation):
        token = invocation.args.get("project")
        context = self.control.repository.get_chat_context(invocation.chat_context_id)
        if token:
            return self.control.repository.resolve_project(str(token), invocation.chat_context_id)
        if context.active_project_id:
            return self.control.repository.get_project(context.active_project_id)
        raise AgentBridgeError(
            ErrorCode.TARGET_PROJECT_REQUIRED,
            "当前聊天上下文没有活动项目。",
            next_step="请执行 /agent project use <project> 或 /agent project list。",
        )

    def _optional_project_id(self, invocation: CommandInvocation) -> str | None:
        token = invocation.args.get("project")
        if token:
            project = self.control.repository.resolve_project(
                str(token), invocation.chat_context_id
            )
            return project.id
        context = self.control.repository.get_chat_context(invocation.chat_context_id)
        return context.active_project_id

    def _required_project_id(self, invocation: CommandInvocation) -> str:
        project_id = self._optional_project_id(invocation)
        if project_id:
            return project_id
        raise AgentBridgeError(
            ErrorCode.TARGET_PROJECT_REQUIRED,
            "创建会话需要明确项目。",
            next_step="请先执行 /agent project use <project>，或添加 --project <project>。",
        )

    def _policy_scope(self, invocation: CommandInvocation) -> tuple[PolicyScope, str]:
        project_token = invocation.args.get("project")
        if project_token:
            project = self.control.repository.resolve_project(
                str(project_token),
                invocation.chat_context_id,
            )
            return PolicyScope.PROJECT, project.id
        return PolicyScope.CHAT_CONTEXT, invocation.chat_context_id

    def _resolve_session_arg(self, invocation: CommandInvocation) -> AgentSession:
        token = invocation.args.get("session")
        context = self.control.repository.get_chat_context(invocation.chat_context_id)
        if token:
            return self.control.repository.resolve_session(str(token), context.active_project_id)
        if context.active_session_id:
            return self.control.repository.get_session(context.active_session_id)
        if context.active_project_id:
            candidates = [
                session
                for session in self.control.repository.list_sessions(context.active_project_id)
                if session.status.value not in {"closed", "archived"}
            ]
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise AgentBridgeError(
                    ErrorCode.TARGET_SESSION_AMBIGUOUS,
                    "当前项目存在多个活动会话，不能自动选择。",
                    next_step="请执行 /agent session use <session> 或添加 --session <session>。",
                    details={"candidates": [session.short_code for session in candidates]},
                )
        raise AgentBridgeError(
            ErrorCode.TARGET_SESSION_REQUIRED,
            "当前聊天上下文没有活动会话。",
            next_step="请执行 /agent session new 或 /agent session use <session>。",
        )

    def _require_interaction_type(
        self,
        interaction: Interaction,
        expected_type: InteractionType,
    ) -> None:
        if interaction.type == expected_type:
            return
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            (
                "Interaction 类型不匹配："
                f"需要 {expected_type.value}，实际是 {interaction.type.value}。"
            ),
            next_step="请确认 Interaction ID 来自对应的问题、计划或审批消息。",
            details={
                "interaction_id": interaction.id,
                "expected_type": expected_type.value,
                "actual_type": interaction.type.value,
            },
        )

    def _result(
        self,
        invocation: CommandInvocation,
        title: str,
        message: str,
        data: dict[str, object] | None = None,
    ) -> CommandResult:
        return CommandResult(
            invocation_id=invocation.id,
            trace_id=invocation.trace_id,
            canonical_command=invocation.canonical_command,
            title=title,
            message=message,
            data=data or {},
        )


def parse_options(tokens: list[str]) -> tuple[list[str], dict[str, object]]:
    positional: list[str] = []
    options: dict[str, object] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            positional.extend(tokens[index + 1 :])
            break
        if token.startswith("--"):
            key = token[2:]
            if not key:
                raise AgentBridgeError(
                    ErrorCode.COMMAND_ARGUMENT_INVALID,
                    "参数名不能为空。",
                    next_step="请检查 -- 参数格式。",
                )
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
                options[key] = tokens[index + 1]
                index += 2
            else:
                options[key] = True
                index += 1
            continue
        if token.startswith("-") and len(token) > 1:
            key = token[1:]
            if index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
                options[key] = tokens[index + 1]
                index += 2
            else:
                options[key] = True
                index += 1
            continue
        positional.append(token)
        index += 1
    return positional, options


def parse_optional_int(value: object) -> int | None:
    if value is None or value is True:
        return None
    try:
        return int(str(value))
    except ValueError as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            f"需要整数参数，收到：{value}",
            next_step="请提供数字参数。",
        ) from exc


def parse_required_int(command: str, value: object) -> int:
    parsed = parse_optional_int(value)
    if parsed is None:
        raise missing_argument(command, "<integer>")
    return parsed


def parse_selection_index(command: str, value: object) -> int:
    index = parse_required_int(command, value)
    if index < 1:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            f"{command} 编号必须从 1 开始。",
            next_step="请执行对应的 list 命令查看当前编号。",
            details={"index": index},
        )
    return index


def select_numbered_item[T](
    items: list[T],
    index: int,
    *,
    item_label: str,
    list_command: str,
) -> T:
    if index > len(items):
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            f"{item_label} 编号超出范围：{index}。",
            next_step=f"请执行 {list_command} 查看当前编号后重试。",
            details={"index": index, "count": len(items)},
        )
    return items[index - 1]


def risk_level_from_policy_key(value: str) -> RiskLevel:
    token = value.strip().lower()
    for prefix in ("approval.", "approvals."):
        if token.startswith(prefix):
            token = token[len(prefix) :]
    for suffix in (".quorum", "_quorum", "-quorum"):
        if token.endswith(suffix):
            token = token[: -len(suffix)]
    try:
        return RiskLevel(token)
    except ValueError as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            f"未知审批风险等级：{value}",
            next_step="请使用 low、medium、high 或 critical。",
        ) from exc


def parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_role_values(positional: list[str], *option_values: object) -> list[str]:
    values: list[str] = []
    for item in positional:
        values.extend(parse_csv(item))
    for option_value in option_values:
        if isinstance(option_value, str):
            values.extend(parse_csv(option_value))
    return values


def missing_argument(command: str, argument: str) -> AgentBridgeError:
    return AgentBridgeError(
        ErrorCode.COMMAND_ARGUMENT_INVALID,
        f"{command} 缺少参数 {argument}。",
        next_step=f"请补充 {argument} 后重试。",
    )
