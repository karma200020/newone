from datetime import timedelta

import pytest

from ops.agent import Investigator
from ops.config import Settings
from ops.domain import Approval, Investigation, Principal, utcnow
from ops.policy import (
    PolicyDenied,
    action_digest,
    authorize_approver,
    authorize_execution,
    evaluate_policy,
)
from ops.tools import SyntheticProvider, ToolGateway


async def proposal(user: Principal) -> Investigation:
    state = Investigation(
        tenant_id=user.tenant_id,
        requested_by=user,
        scenario_id="INC-001",
        description="Investigate checkout-service elevated 5xx errors.",
    )
    return await Investigator(ToolGateway(SyntheticProvider())).run(state)


async def test_high_risk_cannot_downgrade_or_bypass_approval(
    user: Principal,
    settings: Settings,
) -> None:
    state = await proposal(user)
    action = state.actions[0]
    settings.low_risk_approval = False
    assert evaluate_policy(state, action, settings).approval_required
    with pytest.raises(PolicyDenied, match="approval"):
        authorize_execution(state, action, settings)
    action.risk = "low"
    with pytest.raises(PolicyDenied, match="risk"):
        evaluate_policy(state, action, settings)


async def test_approval_is_identity_and_content_bound(user: Principal, settings: Settings) -> None:
    state = await proposal(user)
    action = state.actions[0]
    with pytest.raises(PolicyDenied, match="own"):
        authorize_approver(user, state, action)
    approver = user.model_copy(update={"subject": "approver", "role": "approver"})
    authorize_approver(approver, state, action)
    state.approvals.append(
        Approval(
            action_id=action.id,
            action_digest=action_digest(action),
            decision="approve",
            actor=approver.subject,
            expires_at=utcnow() + timedelta(minutes=15),
        )
    )
    authorize_execution(state, action, settings)
    action.reason += " modified"
    with pytest.raises(PolicyDenied, match="modified"):
        authorize_execution(state, action, settings)


async def test_untrusted_action_parameters_are_denied(user: Principal, settings: Settings) -> None:
    state = await proposal(user)
    state.actions[0].parameters = {"target": "https://attacker.invalid/execute"}
    with pytest.raises(PolicyDenied, match="allowlist"):
        evaluate_policy(state, state.actions[0], settings)
