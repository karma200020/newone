from __future__ import annotations

import asyncio
import logging
import socket

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from ops.agent import Investigator
from ops.config import Settings, get_settings
from ops.domain import Investigation, Status
from ops.mcp_client import open_provider
from ops.observability import JOBS, TOOL_LATENCY, configure_logging
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
        call = data.get("call")
        if isinstance(call, dict):
            TOOL_LATENCY.labels(str(data["tool"]), call.get("error_code") or "success").observe(
                float(call["duration_ms"]) / 1000
            )
        with store.sessions.begin() as session:
            store.audit(session, job.tenant_id, "worker", "tool.called", data, state.id)

    try:
        if job.kind == "execute":
            from ops.execution import execute_action

            execute_action(store, job)
            store.finish(job)
            JOBS.labels(job.kind, "success").inc()
            return True
        if job.kind != "investigate":
            raise ValueError("Unsupported job kind")
        if state.status == Status.DIAGNOSED:
            store.finish(job)
            JOBS.labels(job.kind, "success").inc()
            return True
        async with open_provider(settings, state.requested_by) as provider:
            gateway = ToolGateway(
                provider,
                timeout=settings.tool_timeout_seconds,
                retries=settings.tool_retries,
                audit=audit,
            )

            async def enrich(current: Investigation) -> None:
                from ops.model import enrich_with_model

                await enrich_with_model(current, settings)

            await Investigator(
                gateway, checkpoint, enrich=enrich if settings.llm_enabled else None
            ).run(state)
        store.finish(job)
    except ConflictError:
        logger.warning("Worker lease or state conflict", extra={"job_id": job.id})
    except Exception as exc:
        # Worker boundary: preserve a visible failure and retry the durable job.
        logger.exception("Investigation job failed", extra={"job_id": job.id})
        message = redact(f"{type(exc).__name__}: {exc}")[:500]
        JOBS.labels(job.kind, "error").inc()
        try:

            def failed(current: Investigation, session: Session) -> None:
                store.check_lease(session, job)
                current.warnings.append(f"Worker attempt {job.attempts} failed: {message}")
                if job.kind == "investigate":
                    current.status = (
                        Status.FAILED
                        if job.attempts >= settings.max_job_attempts
                        else Status.QUEUED
                    )
                else:
                    action = next((a for a in current.actions if a.id == job.target), None)
                    if action:
                        action.verification = f"Execution failed: {message}"
                        if job.attempts >= settings.max_job_attempts:
                            action.status = "failed"
                from ops.provenance import render_report

                current.report = render_report(current)

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
    configure_logging()
    redis = (
        Redis.from_url(settings.redis_url, socket_timeout=3, socket_connect_timeout=1)
        if settings.redis_url
        else None
    )
    try:
        while True:
            store.heartbeat(socket.gethostname())
            worked = await run_once(store, settings)
            if not worked:
                if redis:
                    try:
                        await redis.blpop("ops:wake", timeout=1)
                    except RedisError:
                        logger.warning("Redis unavailable; polling durable queue")
                        await asyncio.sleep(1)
                else:
                    await asyncio.sleep(1)
    finally:
        if redis:
            await redis.aclose()


if __name__ == "__main__":
    asyncio.run(serve())
