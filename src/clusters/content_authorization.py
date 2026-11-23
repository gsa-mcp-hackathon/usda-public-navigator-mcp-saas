"""Authorization for content mutations owned by a bound runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RuntimeRole = Literal["cluster", "tenant"]


@dataclass(frozen=True)
class RuntimePrincipal:
    """Identity derived from deployment configuration, never MCP arguments."""

    role: RuntimeRole
    scope_id: str


@dataclass(frozen=True)
class ContentTarget:
    """Target scope for one self-managed content mutation."""

    scope_id: str
    resource_id: str


@dataclass(frozen=True)
class AuthorizationDecision:
    """Auditable allow or deny result for a content mutation."""

    allowed: bool
    decided_by: str
    reason: str


def authorize_content_action(
    action: str,
    target: ContentTarget,
    principal: RuntimePrincipal | None,
) -> AuthorizationDecision:
    """Allow self-managed content only inside a configured tenant/cluster scope."""
    if action != "criteria.ruleset.persist":
        return AuthorizationDecision(False, "operator", "operator_action_required")
    if principal is None or not principal.scope_id:
        return AuthorizationDecision(False, "operator", "runtime_identity_unavailable")
    if target.scope_id != principal.scope_id:
        return AuthorizationDecision(False, "operator", "target_outside_runtime_scope")
    if principal.role != "cluster":
        return AuthorizationDecision(False, "operator", "cluster_runtime_required")
    return AuthorizationDecision(
        True,
        f"{principal.role}_self",
        "target_within_runtime_scope",
    )
