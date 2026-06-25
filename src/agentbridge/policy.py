from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from agentbridge.domain import (
    AccessPolicyEffect,
    AccessPolicyRule,
    Actor,
    AgentBridgeError,
    ErrorCode,
    RiskLevel,
)


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
    POLICY_MANAGE = "policy.manage"
    DEVICE_MANAGE = "device.manage"


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
        Permission.POLICY_MANAGE,
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


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    source: str
    reason: str
    permission: str
    roles: list[str]
    matched_rule_id: str | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "allowed": self.allowed,
            "source": self.source,
            "reason": self.reason,
            "permission": self.permission,
            "roles": self.roles,
        }
        if self.matched_rule_id:
            payload["matched_rule_id"] = self.matched_rule_id
        return payload


class PolicyEngine:
    def __init__(
        self,
        rule_provider: Callable[[], list[AccessPolicyRule]] | None = None,
    ) -> None:
        self._rule_provider = rule_provider

    def set_rule_provider(
        self, rule_provider: Callable[[], list[AccessPolicyRule]] | None
    ) -> None:
        self._rule_provider = rule_provider

    def permissions_for(self, actor: Actor) -> set[Permission]:
        permissions: set[Permission] = set()
        for role in actor.roles:
            permissions.update(ROLE_PERMISSIONS.get(role, set()))
        return permissions

    def allows(
        self,
        actor: Actor,
        permission: Permission | str,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> bool:
        return self.evaluate(
            actor,
            permission,
            resource_type=resource_type,
            resource_id=resource_id,
            attributes=attributes,
        ).allowed

    def evaluate(
        self,
        actor: Actor,
        permission: Permission | str,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        attributes: dict[str, object] | None = None,
        rules: list[AccessPolicyRule] | None = None,
    ) -> PolicyDecision:
        permission_value = self._permission_value(permission)
        actor_roles = sorted(actor.roles)
        supplied_attributes = attributes or {}
        candidate_rules = rules
        if candidate_rules is None:
            candidate_rules = self._rule_provider() if self._rule_provider else []

        matched_rules = [
            rule
            for rule in candidate_rules
            if self._rule_matches(
                rule,
                actor=actor,
                permission=permission_value,
                resource_type=resource_type,
                resource_id=resource_id,
                attributes=supplied_attributes,
            )
        ]
        matched_rules.sort(key=lambda rule: (rule.priority, rule.id))
        for rule in matched_rules:
            if rule.effect == AccessPolicyEffect.DENY:
                return PolicyDecision(
                    allowed=False,
                    source="access_policy",
                    reason="matched_deny_rule",
                    permission=permission_value,
                    roles=actor_roles,
                    matched_rule_id=rule.id,
                )
        if matched_rules:
            return PolicyDecision(
                allowed=True,
                source="access_policy",
                reason="matched_allow_rule",
                permission=permission_value,
                roles=actor_roles,
                matched_rule_id=matched_rules[0].id,
            )

        role_permissions = self.permissions_for(actor)
        try:
            known_permission = Permission(permission_value)
        except ValueError:
            known_permission = None
        allowed_by_role = (
            known_permission is not None and known_permission in role_permissions
        )
        return PolicyDecision(
            allowed=allowed_by_role,
            source="role",
            reason="role_permission" if allowed_by_role else "no_matching_rule_or_role",
            permission=permission_value,
            roles=actor_roles,
        )

    def require(
        self,
        actor: Actor,
        permission: Permission | str,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        decision = self.evaluate(
            actor,
            permission,
            resource_type=resource_type,
            resource_id=resource_id,
            attributes=attributes,
        )
        if decision.allowed:
            return
        raise AgentBridgeError(
            ErrorCode.PERMISSION_DENIED,
            f"用户 {actor.id} 缺少权限 {decision.permission}。",
            next_step="请让项目维护者授予对应角色，或使用具备权限的账号执行。",
            status_code=403,
            details={
                "required_permission": decision.permission,
                "roles": decision.roles,
                "policy_source": decision.source,
                "policy_reason": decision.reason,
                **(
                    {"matched_rule_id": decision.matched_rule_id}
                    if decision.matched_rule_id
                    else {}
                ),
            },
        )

    def require_approval_vote(
        self,
        actor: Actor,
        risk_level: RiskLevel,
        *,
        resource_type: str | None = None,
        resource_id: str | None = None,
        attributes: dict[str, object] | None = None,
    ) -> None:
        if risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            self.require(
                actor,
                Permission.APPROVAL_DANGEROUS,
                resource_type=resource_type,
                resource_id=resource_id,
                attributes=attributes,
            )
            return
        self.require(
            actor,
            Permission.APPROVAL_VOTE,
            resource_type=resource_type,
            resource_id=resource_id,
            attributes=attributes,
        )

    def _rule_matches(
        self,
        rule: AccessPolicyRule,
        *,
        actor: Actor,
        permission: str,
        resource_type: str | None,
        resource_id: str | None,
        attributes: dict[str, object],
    ) -> bool:
        if not rule.enabled:
            return False
        if rule.action not in {"*", permission}:
            return False
        effective_resource_type = resource_type or "*"
        if rule.resource_type not in {"*", effective_resource_type}:
            return False
        if rule.resource_id not in {None, "*", resource_id}:
            return False
        if rule.actor_ids and actor.id not in rule.actor_ids:
            return False
        if rule.roles and not set(rule.roles).intersection(actor.roles):
            return False
        return all(attributes.get(key) == value for key, value in rule.attributes.items())

    @staticmethod
    def _permission_value(permission: Permission | str) -> str:
        return permission.value if isinstance(permission, Permission) else str(permission)
