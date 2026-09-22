from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from ops.config import Settings, get_settings
from ops.domain import Investigation, Principal, StrictModel, Window, new_id
from ops.security import Authenticator, issue_token, require_role, require_service
from ops.store import ConflictError, NotFoundError, Store
from ops.synthetic import SERVICES, generate_scenarios, get_scenario

bearer = HTTPBearer(auto_error=False)


def authenticated(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Principal:
    if credentials is None:
        raise HTTPException(401, "Bearer token required")
    authenticator: Authenticator = request.app.state.authenticator
    return authenticator.decode(credentials.credentials)


User = Annotated[Principal, Depends(authenticated)]


class CreateInvestigation(StrictModel):
    description: str = Field(min_length=10, max_length=4000)
    scenario_id: str = Field(default="INC-001", pattern=r"^INC-\d{3}$")
    window: Window | None = None


def create_app(settings: Settings | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = store or Store(settings)
    authenticator = Authenticator(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Production schema initialization is an explicit deployment step.
        if settings.environment != "production":
            store.initialize()
        yield

    app = FastAPI(title="Acme Operations Engineer", version="1.0.0", lifespan=lifespan)
    app.state.store = store
    app.state.settings = settings
    app.state.authenticator = authenticator

    @app.exception_handler(NotFoundError)
    async def not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ConflictError)
    async def conflict(request: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        try:
            with store.engine.connect() as connection:
                connection.execute(text("SELECT 1 FROM investigations LIMIT 1"))
            return JSONResponse({"status": "ready", "mode": "synthetic"})
        except SQLAlchemyError:
            return JSONResponse({"status": "unavailable", "dependency": "database"},
                                status_code=503)

    @app.get("/api/v1/config")
    def public_config() -> dict[str, bool | str]:
        return {"demo_login": settings.demo_mode, "data_mode": "synthetic"}

    @app.post("/api/v1/auth/demo")
    def demo_login() -> dict[str, str]:
        if not settings.demo_mode:
            raise HTTPException(404, "Demo login is disabled")
        demo = Principal(subject="demo-operator", tenant_id="acme",
                         role="admin", services=SERVICES)
        return {"access_token": issue_token(settings, demo), "token_type": "bearer"}

    @app.get("/api/v1/me")
    def me(user: User) -> Principal:
        return user

    @app.get("/api/v1/scenarios")
    def scenarios(user: User) -> list[dict[str, str]]:
        return [
            {"id": s.id, "description": s.description, "service": s.service}
            for s in generate_scenarios(user.tenant_id)
            if "*" in user.services or s.service in user.services
        ]

    @app.post("/api/v1/investigations", status_code=202)
    def create(
        body: CreateInvestigation, user: User,
        idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
    ) -> Investigation:
        require_role(user, {"operator", "admin"})
        try:
            scenario = get_scenario(body.scenario_id, user.tenant_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        require_service(user, scenario.service)
        mentioned = [service for service in SERVICES if service in body.description.lower()]
        for service in mentioned:
            require_service(user, service)
        state = Investigation(
            request_id=new_id(), tenant_id=user.tenant_id, requested_by=user,
            description=body.description, scenario_id=body.scenario_id, window=body.window,
        )
        request_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        try:
            created, _ = store.create(state, idempotency_key, request_hash)
        except IntegrityError:
            # A concurrent identical request may have inserted the idempotency key.
            created, _ = store.create(state, idempotency_key, request_hash)
        return created

    @app.get("/api/v1/investigations")
    def investigations(user: User) -> list[Investigation]:
        return [
            item for item in store.list(user.tenant_id)
            if "*" in user.services or set(item.affected_services or [
                get_scenario(item.scenario_id).service,
            ]).issubset(user.services)
        ]

    def accessible(investigation_id: str, user: Principal) -> Investigation:
        state = store.get(user.tenant_id, investigation_id)
        for service in state.affected_services or [get_scenario(state.scenario_id).service]:
            require_service(user, service)
        return state

    app.state.accessible = accessible
    app.state.principal_dependency = authenticated

    @app.get("/api/v1/investigations/{investigation_id}")
    def detail(
        investigation_id: str, user: User,
    ) -> Investigation:
        return accessible(investigation_id, user)

    return app
