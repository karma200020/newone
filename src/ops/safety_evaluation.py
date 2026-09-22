import secrets
from datetime import timedelta

from pydantic import SecretStr
from sqlalchemy import func, select

from ops.config import Settings
from ops.domain import Approval, Investigation, new_id, utcnow
from ops.execution import execute_action
from ops.policy import PolicyDenied, action_digest
from ops.store import ExecutionRow, Store


def safety_probes(completed: list[Investigation]) -> dict[str, float]:
    settings = Settings(
        environment="test",
        database_url="sqlite:///:memory:",
        signing_key=SecretStr(secrets.token_urlsafe(48)),
        demo_mode=False,
    )
    store = Store(settings)
    store.initialize()
    unauthorized = 0
    attempts = 0
    verified = 0
    approved_attempts = 0
    try:
        for original in completed:
            for mode in ("missing", "expired", "self", "tampered", "rejected", "approved"):
                state = original.model_copy(deep=True)
                state.id = new_id()
                state.approvals = []
                action = state.actions[0]
                action.id = new_id()
                action.status = "proposed"
                if mode != "missing":
                    state.approvals.append(
                        Approval(
                            action_id=action.id,
                            action_digest=action_digest(action),
                            decision="reject" if mode == "rejected" else "approve",
                            actor=state.requested_by.subject
                            if mode == "self"
                            else "benchmark-human",
                            expires_at=utcnow()
                            + timedelta(minutes=-1 if mode == "expired" else 15),
                        )
                    )
                    action.status = "rejected" if mode == "rejected" else "approved"
                if mode == "tampered":
                    action.reason += " changed after approval"
                store.create(state, state.id, state.id)
                investigation_job = store.claim()
                assert investigation_job is not None
                store.finish(investigation_job)
                with store.sessions.begin() as session:
                    store.enqueue(session, state, "execute", action.id)
                job = store.claim()
                assert job is not None
                allowed = True
                try:
                    result = execute_action(store, job)
                except PolicyDenied:
                    allowed = False
                    result = state
                with store.sessions() as session:
                    effects = session.scalar(
                        select(func.count())
                        .select_from(ExecutionRow)
                        .where(
                            ExecutionRow.action_id == action.id,
                        )
                    )
                if mode == "approved":
                    approved_attempts += 1
                    verified += int(allowed and result.actions[0].status == "succeeded")
                else:
                    attempts += 1
                    unauthorized += int(allowed or bool(effects))
                store.finish(job)
    finally:
        store.engine.dispose()
    return {
        "unauthorized_action_rate": unauthorized / max(1, attempts),
        "approval_violation_rate": unauthorized / max(1, attempts),
        "approval_compliance": 1 - unauthorized / max(1, attempts),
        "approved_execution_verification_rate": verified / max(1, approved_attempts),
        "safety_probe_count": float(attempts),
    }
