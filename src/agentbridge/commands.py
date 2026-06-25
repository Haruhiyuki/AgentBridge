from __future__ import annotations

import re
import shlex
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
    InteractionStatus,
    LeaseOwnerType,
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
    "取消": "cancel",
    "控制": "control",
    "状态": "status",
    "接管": "takeover",
    "释放": "release",
    "角色": "role",
    "授权": "grant",
    "授予": "grant",
    "撤销": "revoke",
    "健康": "health",
    "上下文": "context",
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
        if root == "control":
            return self._parse_control(tokens)
        if root == "role":
            return self._parse_role(tokens)
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

    def _execute_uncached(self, invocation: CommandInvocation) -> CommandResult:
        command = invocation.canonical_command
        args = invocation.args
        if command == "help":
            return self._result(
                invocation,
                "AgentBridge commands",
                "可用命令：project、session、ask/send、control、role、approvals、health。",
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
        if command == "interaction.list":
            interactions = self.control.list_interactions(
                actor=invocation.actor,
                chat_context_id=invocation.chat_context_id,
                status=None,
            )
            if args.get("pending"):
                interactions = [
                    interaction
                    for interaction in interactions
                    if interaction.status
                    in {InteractionStatus.PENDING, InteractionStatus.PARTIALLY_APPROVED}
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
