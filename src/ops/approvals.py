from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from sqlalchemy.orm import Session

from ops.api import User
from ops.config import Settings
from ops.domain import Action, Approval, Investigation, StrictModel, TraceEvent, utcnow
from ops.policy import (
    PolicyDenied,
    action_digest,
    authorize_approver,
    evaluate_policy,
)
from ops.provenance import render_report
from ops.security import require_service
from ops.store import ConflictError, Store

router = APIRouter(prefix="/api/v1")


class ApprovalRequest(StrictModel):
    decision: Literal["approve", "reject"]
    expected_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def find_action(state: Investigation, action_id: str) -> Action:
    for action in state.actions:
        if action.id == action_id:
            return action
    raise HTTPException(404, "Action not found")


@router.get("/approvals")
def queue(request: Request, user: User) -> list[dict[str, Any]]:
    store: Store = request.app.state.store
    results = []
    for state in store.list(user.tenant_id):
        for action in state.actions:
            if action.status != "proposed":
                continue
            if "*" not in user.services and action.service not in user.services:
                continue
            results.append({
                "investigation_id": state.id, "requested_by": state.requested_by.subject,
                "action": action.model_dump(mode="json"), "digest": action_digest(action),
            })
    return results


@router.post("/investigations/{investigation_id}/actions/{action_id}/decision")
def decide(
    investigation_id: str, action_id: str, body: ApprovalRequest,
    request: Request, user: User,
) -> Investigation:
    store: Store = request.app.state.store
    settings: Settings = request.app.state.settings

    def update(state: Investigation, session: Session) -> None:
        action = find_action(state, action_id)
        require_service(user, action.service)
        authorize_approver(user, state, action)
        evaluate_policy(state, action, settings)
        digest = action_digest(action)
        if digest != body.expected_digest:
            raise ConflictError("Proposal changed; review it again before deciding")
        expected_status = "approved" if body.decision == "approve" else "rejected"
        if action.status == expected_status and any(
            a.action_id == action.id and a.actor == user.subject
            and a.decision == body.decision and a.action_digest == digest
            for a in state.approvals
        ):
            return
        if action.status != "proposed":
            raise ConflictError("This action already has a decision")
        state.approvals.append(Approval(
            action_id=action.id, action_digest=digest, decision=body.decision,
            actor=user.subject, expires_at=utcnow() + timedelta(minutes=15),
        ))
        action.status = expected_status
        state.trace.append(TraceEvent(
            stage="approval", summary=f"{user.subject} chose {body.decision} for {action.kind.value}",
            evidence_ids=action.evidence_ids,
        ))
        if body.decision == "approve":
            store.enqueue(session, state, "execute", action.id)
        store.audit(session, user.tenant_id, user.subject, f"action.{expected_status}",
                    {"action_id": action.id, "digest": digest}, state.id)
        state.report = render_report(state)

    try:
        return store.mutate(user.tenant_id, investigation_id, update)
    except PolicyDenied as exc:
        with store.sessions.begin() as session:
            store.audit(session, user.tenant_id, user.subject, "approval.denied",
                        {"action_id": action_id, "reason": str(exc)}, investigation_id)
        raise HTTPException(403, str(exc)) from exc
