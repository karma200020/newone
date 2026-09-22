from typing import Any

from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import select

from ops.api import User
from ops.security import require_role, require_service
from ops.store import AuditRow, EvaluationRow, Store
from ops.synthetic import get_scenario

router = APIRouter(prefix="/api/v1")


@router.get("/investigations/{investigation_id}/audit")
def audit(investigation_id: str, request: Request, user: User) -> list[dict[str, Any]]:
    store: Store = request.app.state.store
    state = store.get(user.tenant_id, investigation_id)
    for service in state.affected_services or [get_scenario(state.scenario_id).service]:
        require_service(user, service)
    with store.sessions() as session:
        rows = session.scalars(
            select(AuditRow)
            .where(
                AuditRow.tenant_id == user.tenant_id,
                AuditRow.investigation_id == investigation_id,
            )
            .order_by(AuditRow.timestamp)
            .limit(1000)
        )
        return [
            {
                "id": row.id,
                "timestamp": row.timestamp,
                "actor": row.actor,
                "event": row.event,
                "data": row.data,
                "signature": row.signature,
            }
            for row in rows
        ]


@router.get("/evaluations")
def evaluations(request: Request, user: User) -> list[dict[str, Any]]:
    store: Store = request.app.state.store
    with store.sessions() as session:
        rows = session.scalars(
            select(EvaluationRow)
            .where(
                EvaluationRow.tenant_id == user.tenant_id,
            )
            .order_by(EvaluationRow.timestamp.desc())
            .limit(20)
        )
        return [
            {
                "id": row.id,
                **{key: value for key, value in row.report.items() if key != "scenarios"},
            }
            for row in rows
        ]


@router.get("/metrics")
def metrics(user: User) -> Response:
    require_role(user, {"admin"})
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
