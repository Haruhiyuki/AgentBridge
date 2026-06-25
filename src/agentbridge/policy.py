from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentbridge.domain import Actor, AgentBridgeError, ErrorCode, RiskLevel


class Permission(StrEnum):
    PROJECT_VIEW = "project.view"
    PROJECT_MANAGE = "project.manage"
    SESSION_VIEW = "session.view"
    SESSION_CREATE = "session.create"
    SESSION_SEND = "session.send"
    SESSION_MANAGE = "session.manage"
    APPROVAL_VOTE = "approval.vote"
    APPROVAL_DANGEROUS = "approval.dangerous"
    TERMINAL_CONTROL = "terminal.control"
    AUDIT_VIEW = "audit.view"
    GROUP_ROLE_MANAGE = "group.role.manage"


ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "member": {
        Permission.PROJECT_VIEW,
        Permission.SESSION_VIEW,
    },
    "operator": {
        Permission.PROJECT_VIEW,
        Permission.SESSION_VIEW,
        Permission.SESSION_CREATE,
        Permission.SESSION_SEND,
    },
    "approver": {
        Permission.PROJECT_VIEW,
        Permission.SESSION_VIEW,
        Permission.APPROVAL_VOTE,
    },
    "dangerous_approver": {
        Permission.PROJECT_VIEW,
        Permission.SESSION_VIEW,
        Permission.APPROVAL_VOTE,
        Permission.APPROVAL_DANGEROUS,
    },
    "maintainer": {
        Permission.PROJECT_VIEW,
        Permission.PROJECT_MANAGE,
        Permission.SESSION_VIEW,
        Permission.SESSION_CREATE,
        Permission.SESSION_SEND,
        Permission.SESSION_MANAGE,
        Permission.APPROVAL_VOTE,
        Permission.TERMINAL_CONTROL,
        Permission.AUDIT_VIEW,
        Permission.GROUP_ROLE_MANAGE,
    },
    "admin": set(Permission),
}


@dataclass(frozen=True)
class ApprovalPolicy:
    quorum_by_risk: dict[RiskLevel, int]

    @classmethod
    def default(cls) -> ApprovalPolicy:
        return cls(
            quorum_by_risk={
                RiskLevel.LOW: 1,
                RiskLevel.MEDIUM: 1,
                RiskLevel.HIGH: 1,
                RiskLevel.CRITICAL: 2,
            }
        )

    def quorum_for(self, risk_level: RiskLevel) -> int:
        return max(self.quorum_by_risk.get(risk_level, 1), 1)

    def snapshot_for(self, risk_level: RiskLevel) -> dict[str, object]:
        return {
            "risk_level": risk_level.value,
            "required_votes": self.quorum_for(risk_level),
            "dangerous_permission_required": risk_level
            in {RiskLevel.HIGH, RiskLevel.CRITICAL},
        }


class PolicyEngine:
    def permissions_for(self, actor: Actor) -> set[Permission]:
        permissions: set[Permission] = set()
        for role in actor.roles:
            permissions.update(ROLE_PERMISSIONS.get(role, set()))
        return permissions

    def allows(self, actor: Actor, permission: Permission) -> bool:
        return permission in self.permissions_for(actor)

    def require(self, actor: Actor, permission: Permission) -> None:
        if self.allows(actor, permission):
            return
        raise AgentBridgeError(
            ErrorCode.PERMISSION_DENIED,
            f"用户 {actor.id} 缺少权限 {permission.value}。",
            next_step="请让项目维护者授予对应角色，或使用具备权限的账号执行。",
            status_code=403,
            details={"required_permission": permission.value, "roles": sorted(actor.roles)},
        )

    def require_approval_vote(self, actor: Actor, risk_level: RiskLevel) -> None:
        if risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            self.require(actor, Permission.APPROVAL_DANGEROUS)
            return
        self.require(actor, Permission.APPROVAL_VOTE)
