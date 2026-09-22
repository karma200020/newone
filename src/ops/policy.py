import hashlib
import json
from typing import Literal

from ops.config import Settings
from ops.domain import Action, ActionKind, Approval, Investigation, Principal, StrictModel, utcnow
from ops.provenance import validate_provenance

HIGH_RISK = {
    ActionKind.ROLLBACK,
    ActionKind.RESTART,
    ActionKind.CONFIGURE,
    ActionKind.FLAG,
    ActionKind.ROTATE,
}
PARAMETERS: dict[ActionKind, dict[str, set[str]]] = {
    ActionKind.ROLLBACK: {"target": {"previous-stable"}},
    ActionKind.RESTART: {"component": {"redis", "application", "downstream"}},
    ActionKind.CONFIGURE: {
        "setting": {"connection_pool", "provider_route", "query_route", "consumer_count"},
        "value": {"reviewed-baseline", "approved-secondary", "indexed-query"},
    },
    ActionKind.FLAG: {"flag": {"new-processing-path"}, "value": {"off"}},
    ActionKind.ROTATE: {"credential": {"upstream-api"}},
    ActionKind.REPORT: {},
    ActionKind.JIRA: {},
    ActionKind.SLACK: {},
}
CONFIG_PAIRS = {
    ("connection_pool", "reviewed-baseline"),
    ("provider_route", "approved-secondary"),
    ("query_route", "indexed-query"),
    ("consumer_count", "reviewed-baseline"),
}


class PolicyDenied(PermissionError):
    pass


class PolicyDecision(StrictModel):
    permission: Literal["LOW_RISK_WRITE", "HIGH_RISK"]
    approval_required: bool
    reason: str


def action_digest(action: Action) -> str:
    immutable = action.model_dump(mode="json", exclude={"status", "verification"})
    return hashlib.sha256(
        json.dumps(
            immutable,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def check_scope(principal: Principal, state: Investigation, action: Action) -> None:
    if principal.tenant_id != state.tenant_id:
        raise PolicyDenied("Tenant mismatch")
    if "*" not in principal.services and action.service not in principal.services:
        raise PolicyDenied("Action resource is outside the principal's service scope")
    if "*" not in principal.tools and f"actions.{action.kind.value}" not in principal.tools:
        raise PolicyDenied("Action tool is outside the principal's tool scope")
    if action.service not in state.affected_services:
        raise PolicyDenied("Action resource is outside the investigation")


def evaluate_policy(state: Investigation, action: Action, settings: Settings) -> PolicyDecision:
    validate_provenance(state)
    if action.environment != "synthetic":
        raise PolicyDenied("Only the isolated synthetic executor is implemented")
    if not any(
        a.id == action.id and action_digest(a) == action_digest(action) for a in state.actions
    ):
        raise PolicyDenied("Action is not an immutable proposal in this investigation")
    expected = PARAMETERS[action.kind]
    if set(action.parameters) != set(expected):
        raise PolicyDenied("Action parameters do not match the trusted executor schema")
    if any(value not in expected[key] for key, value in action.parameters.items()):
        raise PolicyDenied("Action parameters exceed the trusted executor allowlist")
    if (
        action.kind == ActionKind.CONFIGURE
        and (
            action.parameters["setting"],
            action.parameters["value"],
        )
        not in CONFIG_PAIRS
    ):
        raise PolicyDenied("Invalid configuration setting/value pair")
    high = action.kind in HIGH_RISK
    if action.risk != ("high" if high else "low"):
        raise PolicyDenied("Caller-supplied risk conflicts with policy classification")
    if state.requested_by.role not in {"operator", "admin"}:
        raise PolicyDenied("Investigation creator cannot propose writes")
    check_scope(state.requested_by, state, action)
    return PolicyDecision(
        permission="HIGH_RISK" if high else "LOW_RISK_WRITE",
        approval_required=high or settings.low_risk_approval,
        reason="High-risk actions always require explicit, unexpired, action-bound human approval."
        if high
        else "Low-risk write policy is configurable.",
    )


def authorize_approver(principal: Principal, state: Investigation, action: Action) -> None:
    check_scope(principal, state, action)
    if principal.role not in {"approver", "admin"}:
        raise PolicyDenied("Approver role is required")
    if principal.subject == state.requested_by.subject:
        raise PolicyDenied("The requester cannot approve their own action")


def valid_approval(state: Investigation, action: Action) -> Approval | None:
    for approval in reversed(state.approvals):
        if approval.action_id != action.id:
            continue
        if (
            approval.decision == "approve"
            and approval.action_digest == action_digest(action)
            and approval.expires_at > utcnow()
            and approval.actor != state.requested_by.subject
        ):
            return approval
        return None
    return None


def authorize_execution(
    state: Investigation,
    action: Action,
    settings: Settings,
) -> PolicyDecision:
    decision = evaluate_policy(state, action, settings)
    if action.status in {"rejected", "failed", "unverified"}:
        raise PolicyDenied("Rejected or failed actions require a fresh proposal")
    if decision.approval_required and valid_approval(state, action) is None:
        raise PolicyDenied("Missing, expired or modified-action approval")
    return decision
