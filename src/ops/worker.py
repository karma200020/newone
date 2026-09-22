from __future__ import annotations

import asyncio
import logging

from sqlalchemy.orm import Session

from ops.agent import Investigator
from ops.config import Settings, get_settings
from ops.domain import Investigation, Status
from ops.mcp_client import open_provider
from ops.store import ConflictError, Store
from ops.tools import ToolGateway, redact

logger = logging.getLogger(__name__)


async def run_once(store: Store, settings: Settings) -> bool:
    job = store.claim()
    if job is None:
        return False
    state = store.get(job.tenant_id, job.investigation_id)

    def checkpoint(updated: Investigation) -> None:
        def save(current: Investigation, session: Session) -> None:
            store.check_lease(session, job)
            for field in Investigation.model_fields:
                setattr(current, field, getattr(updated, field))
            store.audit(
                session,
                current.tenant_id,
                "worker",
                "investigation.checkpoint",
                {"stage": current.status.value, "run_id": current.run_id},
                current.id,
            )

        store.mutate(job.tenant_id, job.investigation_id, save)

    def audit(data: dict[str, object]) -> None:
        with store.sessions.begin() as session:
            store.audit(session, job.tenant_id, "worker", "tool.called", data, state.id)

    try:
        if job.kind == "execute":
            from ops.execution import execute_action

            execute_action(store, job)
            store.finish(job)
            return True
        if job.kind != "investigate":
            raise ValueError("Unsupported job kind")
        if state.status == Status.DIAGNOSED:
            store.finish(job)
            return True
        async with open_provider(settings, state.requested_by) as provider:
            gateway = ToolGateway(
                provider,
                timeout=settings.tool_timeout_seconds,
                retries=settings.tool_retries,
                audit=audit,
            )
            await Investigator(gateway, checkpoint).run(state)
        store.finish(job)
    except ConflictError:
        logger.warning("Worker lease or state conflict", extra={"job_id": job.id})
    except Exception as exc:
        # Worker boundary: preserve a visible failure and retry the durable job.
        logger.exception("Investigation job failed", extra={"job_id": job.id})
        message = redact(f"{type(exc).__name__}: {exc}")[:500]
        try:

            def failed(current: Investigation, session: Session) -> None:
                store.check_lease(session, job)
                current.warnings.append(f"Worker attempt {job.attempts} failed: {message}")
                if job.kind == "investigate":
                    current.status = (
                        Status.FAILED if job.attempts >= settings.max_job_attempts else Status.QUEUED
                    )
                else:
                    action = next((a for a in current.actions if a.id == job.target), None)
                    if action:
                        action.verification = f"Execution failed: {message}"
                        if job.attempts >= settings.max_job_attempts:
                            action.status = "failed"

            store.mutate(job.tenant_id, job.investigation_id, failed)
            store.finish(job, message)
        except ConflictError:
            logger.warning("Discarded error from expired worker lease", extra={"job_id": job.id})
    return True


async def serve() -> None:
    settings = get_settings()
    store = Store(settings)
    if settings.environment != "production":
        store.initialize()
    logging.basicConfig(level=logging.INFO)
    while True:
        worked = await run_once(store, settings)
        if not worked:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(serve())
