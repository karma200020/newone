from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import JSON, Float, Integer, String, Text, create_engine, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from ops.config import Settings
from ops.domain import Investigation, new_id, utcnow


class Base(DeclarativeBase):
    pass


class InvestigationRow(Base):
    __tablename__ = "investigations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)


class JobRow(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    investigation_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    target: Mapped[str | None] = mapped_column(String(36), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="ready", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[float] = mapped_column(Float, default=lambda: utcnow().timestamp())
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditRow(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(100), index=True)
    investigation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    timestamp: Mapped[float] = mapped_column(Float)
    actor: Mapped[str] = mapped_column(String(200))
    event: Mapped[str] = mapped_column(String(100))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    signature: Mapped[str] = mapped_column(String(64))


class ExecutionRow(Base):
    __tablename__ = "executions"
    action_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)


class ConflictError(Exception):
    pass


class NotFoundError(Exception):
    pass


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings
        options: dict[str, Any] = {}
        if settings.database_url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in settings.database_url:
                from sqlalchemy.pool import StaticPool

                options["poolclass"] = StaticPool
        self.engine: Engine = create_engine(settings.database_url, pool_pre_ping=True, **options)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)

    def audit(
        self,
        session: Session,
        tenant: str,
        actor: str,
        event: str,
        data: dict[str, Any],
        investigation_id: str | None = None,
    ) -> None:
        timestamp = utcnow().timestamp()
        identity = new_id()
        envelope = {
            "id": identity,
            "tenant_id": tenant,
            "actor": actor,
            "event": event,
            "timestamp": timestamp,
            "data": data,
            "investigation_id": investigation_id,
        }
        signature = hmac.new(
            self.settings.signing_key.get_secret_value().encode(),
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        session.add(AuditRow(**envelope, signature=signature))

    def create(
        self,
        state: Investigation,
        idempotency_key: str,
        request_hash: str,
    ) -> tuple[Investigation, bool]:
        key = hashlib.sha256(
            f"{state.tenant_id}:{state.requested_by.subject}:{idempotency_key}".encode()
        ).hexdigest()
        with self.sessions.begin() as session:
            existing = session.scalar(
                select(InvestigationRow).where(InvestigationRow.idempotency_key == key)
            )
            if existing:
                if existing.request_hash != request_hash:
                    raise ConflictError("Idempotency key already used for a different request")
                return Investigation.model_validate(existing.state), False
            session.add(
                InvestigationRow(
                    id=state.id,
                    tenant_id=state.tenant_id,
                    idempotency_key=key,
                    request_hash=request_hash,
                    state=state.model_dump(mode="json"),
                )
            )
            self.enqueue(session, state, "investigate")
            self.audit(
                session,
                state.tenant_id,
                state.requested_by.subject,
                "investigation.created",
                {"request_id": state.request_id},
                state.id,
            )
        return state, True

    def get(self, tenant: str, investigation_id: str) -> Investigation:
        with self.sessions() as session:
            row = session.scalar(
                select(InvestigationRow).where(
                    InvestigationRow.id == investigation_id,
                    InvestigationRow.tenant_id == tenant,
                )
            )
            if not row:
                raise NotFoundError("Investigation not found")
            return Investigation.model_validate(row.state)

    def list(self, tenant: str, limit: int = 100) -> list[Investigation]:
        with self.sessions() as session:
            rows = session.scalars(
                select(InvestigationRow)
                .where(
                    InvestigationRow.tenant_id == tenant,
                )
                .order_by(InvestigationRow.id.desc())
                .limit(limit)
            )
            return [Investigation.model_validate(row.state) for row in rows]

    def mutate(
        self,
        tenant: str,
        investigation_id: str,
        change: Callable[[Investigation, Session], None],
    ) -> Investigation:
        with self.sessions.begin() as session:
            row = session.scalar(
                select(InvestigationRow)
                .where(
                    InvestigationRow.id == investigation_id,
                    InvestigationRow.tenant_id == tenant,
                )
                .with_for_update()
            )
            if not row:
                raise NotFoundError("Investigation not found")
            state = Investigation.model_validate(row.state)
            version = row.version
            change(state, session)
            state.updated_at = utcnow()
            result = session.execute(
                update(InvestigationRow)
                .where(
                    InvestigationRow.id == investigation_id,
                    InvestigationRow.version == version,
                )
                .values(state=state.model_dump(mode="json"), version=version + 1)
            )
            if result.rowcount != 1:
                raise ConflictError("Concurrent update; retry with fresh state")
            return state

    def enqueue(
        self,
        session: Session,
        state: Investigation,
        kind: str,
        target: str | None = None,
    ) -> None:
        session.add(
            JobRow(
                tenant_id=state.tenant_id,
                investigation_id=state.id,
                kind=kind,
                target=target,
                dedup_key=f"{state.id}:{kind}:{target or ''}",
            )
        )

    def claim(self) -> JobRow | None:
        now = utcnow().timestamp()
        with self.sessions.begin() as session:
            eligible = ((JobRow.status == "ready") & (JobRow.available_at <= now)) | (
                (JobRow.status == "leased") & (JobRow.lease_until < now)
            )
            row = session.scalar(
                select(JobRow)
                .where(eligible)
                .order_by(
                    JobRow.available_at,
                )
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            if row.attempts >= self.settings.max_job_attempts:
                row.status = "failed"
                row.error = "Job lease expired after maximum attempts"
                state_row = session.get(InvestigationRow, row.investigation_id)
                if state_row and row.kind == "investigate":
                    state = Investigation.model_validate(state_row.state)
                    from ops.domain import Status

                    state.status = Status.FAILED
                    state.warnings.append(row.error)
                    state_row.state = state.model_dump(mode="json")
                    state_row.version += 1
                self.audit(
                    session,
                    row.tenant_id,
                    "worker",
                    "job.exhausted",
                    {"job_id": row.id},
                    row.investigation_id,
                )
                return None
            owner = new_id()
            result = session.execute(
                update(JobRow)
                .where(
                    JobRow.id == row.id,
                    eligible,
                )
                .values(
                    status="leased",
                    lease_owner=owner,
                    lease_until=(
                        utcnow()
                        + timedelta(
                            seconds=self.settings.job_lease_seconds,
                        )
                    ).timestamp(),
                    attempts=JobRow.attempts + 1,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                return None
            session.refresh(row)
            return row

    def check_lease(self, session: Session, job: JobRow) -> None:
        row = session.get(JobRow, job.id)
        if (
            row is None
            or row.status != "leased"
            or row.lease_owner != job.lease_owner
            or row.lease_until < utcnow().timestamp()
        ):
            raise ConflictError("Worker lost its lease; stale result discarded")

    def finish(self, job: JobRow, error: str | None = None) -> None:
        with self.sessions.begin() as session:
            self.check_lease(session, job)
            row = session.get(JobRow, job.id)
            assert row is not None
            row.error = error
            row.status = (
                "done"
                if error is None
                else ("failed" if row.attempts >= self.settings.max_job_attempts else "ready")
            )
            row.available_at = utcnow().timestamp() + min(60, 2**row.attempts)
            self.audit(
                session,
                row.tenant_id,
                "worker",
                f"job.{row.status}",
                {"job_id": row.id, "error": error},
                row.investigation_id,
            )
