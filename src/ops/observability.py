import contextvars
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Histogram
from redis import Redis
from redis.exceptions import RedisError

from ops.config import Settings
from ops.domain import new_id, utcnow
from ops.tools import redact

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
HTTP_REQUESTS = Counter("ops_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram("ops_http_seconds", "HTTP request duration", ["method", "route"])
TOOL_LATENCY = Histogram("ops_tool_seconds", "Tool duration", ["tool", "outcome"])
JOBS = Counter("ops_jobs_total", "Completed worker attempts", ["kind", "outcome"])
logger = logging.getLogger(__name__)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "request_id": request_id_context.get(),
        }
        for name in ("investigation_id", "run_id", "job_id", "event", "duration_ms"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def install_request_tracing(app: FastAPI) -> None:
    @app.middleware("http")
    async def tracing(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = new_id()
        request.state.request_id = request_id
        token = request_id_context.set(request_id)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'"
            )
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            duration = time.perf_counter() - start
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_LATENCY.labels(request.method, route).observe(duration)
            logger.info(
                "HTTP %s %s %s",
                request.method,
                route,
                status,
                extra={"duration_ms": duration * 1000},
            )
            request_id_context.reset(token)


def notify_worker(settings: Settings) -> None:
    if not settings.redis_url:
        return
    try:
        with Redis.from_url(
            settings.redis_url, socket_timeout=1, socket_connect_timeout=1
        ) as redis:
            pipeline = redis.pipeline()
            pipeline.lpush("ops:wake", "job")
            pipeline.ltrim("ops:wake", 0, 99)
            pipeline.expire("ops:wake", 60)
            pipeline.execute()
    except RedisError:
        logger.warning("Redis wakeup unavailable; durable database polling remains active")


def redis_ready(settings: Settings) -> bool:
    if not settings.redis_url:
        return settings.environment != "production"
    try:
        with Redis.from_url(
            settings.redis_url, socket_timeout=1, socket_connect_timeout=1
        ) as redis:
            return bool(redis.ping())
    except RedisError:
        logger.warning("Redis readiness check failed")
        return False
