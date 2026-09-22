from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ops.config import Settings
from ops.domain import Approval, Investigation, Principal, utcnow
from ops.execution import execute_action
from ops.policy import PolicyDenied, action_digest
from ops.store import ExecutionRow, Store
from ops.worker import run_once


async def prepare(store: Store, settings: Settings, user: Principal) -> Investigation:
    state = Investigation(
        tenant_id="acme",
        requested_by=user,
        scenario_id="INC-001",
        description="Investigate checkout-service elevated 5xx errors.",
    )
    store.create(state, state.id, state.id)
    await run_once(store, settings)
    return store.get("acme", state.id)


def approve(store: Store, state: Investigation, *, wrong_action: bool = False) -> None:
    def update(current: Investigation, session: Session) -> None:
        action = current.actions[0]
        if wrong_action:
            from ops.domain import ActionKind

            action.kind = ActionKind.RESTART
            action.parameters = {"component": "redis"}
        action.status = "approved"
        current.approvals.append(
            Approval(
                action_id=action.id,
                action_digest=action_digest(action),
                actor="independent-approver",
                decision="approve",
                expires_at=utcnow() + timedelta(minutes=15),
            )
        )
        store.enqueue(session, current, "execute", action.id)

    store.mutate("acme", state.id, update)


async def test_approved_execution_is_idempotent_and_verified(
    store: Store,
    settings: Settings,
    user: Principal,
) -> None:
    state = await prepare(store, settings, user)
    approve(store, state)
    job = store.claim()
    assert job
    first = execute_action(store, job)
    second = execute_action(store, job)
    assert first.actions[0].status == second.actions[0].status == "succeeded"
    assert first.actions[0].verification and "0.18 -> 0.002" in first.actions[0].verification
    with store.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionRow)) == 1
    assert first.report and "Recovery verified" in first.report


async def test_wrong_remediation_does_not_fake_recovery(
    store: Store,
    settings: Settings,
    user: Principal,
) -> None:
    state = await prepare(store, settings, user)
    approve(store, state, wrong_action=True)
    await run_once(store, settings)
    result = store.get("acme", state.id)
    assert result.actions[0].status == "unverified"
    assert "Recovery NOT verified" in (result.actions[0].verification or "")


async def test_injected_job_without_approval_is_denied(
    store: Store,
    settings: Settings,
    user: Principal,
) -> None:
    state = await prepare(store, settings, user)
    with store.sessions.begin() as session:
        store.enqueue(session, state, "execute", state.actions[0].id)
    job = store.claim()
    assert job
    with pytest.raises(PolicyDenied, match="approval"):
        execute_action(store, job)
    with store.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ExecutionRow)) == 0
