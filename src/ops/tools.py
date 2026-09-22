from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import HTTPException
from pydantic import Field, ValidationError

from ops.domain import (
    Evidence, Principal, Record, Source, StrictModel, ToolCall, Window,
)
from ops.security import require_service, require_tool
from ops.sql_safety import UnsafeQuery, query_snapshot
from ops.synthetic import get_scenario


class ToolInput(StrictModel):
    service: str = Field(pattern=r"^[a-z][a-z0-9-]{1,80}$")
    scenario_id: str = Field(pattern=r"^INC-\d{3}$")
    window: Window | None = None
    query: str = Field(default="", max_length=8000)
    source_id: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=50, ge=1, le=200)
    metadata: dict[str, str] = Field(default_factory=dict)


class ToolSpec(StrictModel):
    name: str
    source: Source
    description: str
    input_schema: dict[str, Any] = Field(default_factory=lambda: ToolInput.model_json_schema())
    output_schema: dict[str, Any] = Field(default_factory=lambda: ToolOutput.model_json_schema())


class ToolOutput(StrictModel):
    records: list[Record] = Field(max_length=200)
    truncated: bool = False


class ToolError(StrictModel):
    code: str
    message: str
    retryable: bool = False


class ToolResult(StrictModel):
    call: ToolCall
    evidence: list[Evidence] = Field(default_factory=list)
    error: ToolError | None = None


TOOL_SOURCES: dict[str, Source] = {
    "monitoring.metrics": Source.MONITORING,
    "monitoring.alerts": Source.MONITORING,
    "logs.search": Source.LOGS,
    "deployments.list": Source.DEPLOYMENTS,
    "deployments.details": Source.DEPLOYMENTS,
    "github.search": Source.GITHUB,
    "github.commit": Source.GITHUB,
    "github.pull_request": Source.GITHUB,
    "github.changed_files": Source.GITHUB,
    "github.history": Source.GITHUB,
    "jira.search": Source.JIRA,
    "jira.incident": Source.JIRA,
    "jira.recent_tasks": Source.JIRA,
    "slack.search": Source.SLACK,
    "slack.messages": Source.SLACK,
    "documentation.search": Source.DOCUMENTATION,
    "documentation.runbooks": Source.DOCUMENTATION,
    "database.query": Source.DATABASE,
    "database.schema": Source.DATABASE,
}


class Provider(Protocol):
    async def discover(self) -> list[ToolSpec]: ...

    async def invoke(
        self, name: str, arguments: ToolInput, principal: Principal,
    ) -> ToolOutput: ...


class SyntheticProvider:
    async def discover(self) -> list[ToolSpec]:
        return [ToolSpec(name=name, source=source,
                         description=f"Read synthetic {source.value}: {name.split('.')[1]}")
                for name, source in TOOL_SOURCES.items()]

    async def invoke(
        self, name: str, arguments: ToolInput, principal: Principal,
    ) -> ToolOutput:
        if name not in TOOL_SOURCES:
            raise ValueError("Unknown tool")
        records = [
            record for record in get_scenario(arguments.scenario_id, principal.tenant_id).records
            if record.source == TOOL_SOURCES[name]
            and record.service == arguments.service
            and record.tenant_id == principal.tenant_id
        ]
        if arguments.window and TOOL_SOURCES[name] != Source.DOCUMENTATION:
            records = [r for r in records if arguments.window.start <= r.timestamp
                       <= arguments.window.end]
        if arguments.source_id:
            records = [r for r in records if r.source_id == arguments.source_id]
        if arguments.metadata:
            records = [r for r in records if all(
                str(r.attributes.get(k, "")) == v for k, v in arguments.metadata.items()
            )]
        if name == "database.query":
            active = float(records[0].attributes.get("active_connections", 0)) if records else 0
            rows = query_snapshot(arguments.query or "SELECT * FROM service_health",
                                  arguments.service, active, arguments.limit)
            records = [r.model_copy(update={"content": json.dumps(rows)}) for r in records]
        elif name == "database.schema":
            records = [r.model_copy(update={
                "content": "service_health(service TEXT, active_connections REAL, "
                           "connection_limit REAL)",
            }) for r in records]
        elif name == "documentation.search":
            from ops.retrieval import HybridRetriever

            hits = HybridRetriever(records).search(
                arguments.query, service=arguments.service, tenant_id=principal.tenant_id,
                metadata=arguments.metadata, limit=arguments.limit,
            )
            records = [hit.record for hit in hits]
        elif arguments.query:
            words = arguments.query.lower().split()
            records = [r for r in records if any(word in r.content.lower() for word in words)]
        return ToolOutput(records=records[:arguments.limit],
                          truncated=len(records) > arguments.limit)


INJECTION = re.compile(
    r"ignore\s+(?:all\s+)?(?:previous|system)|override\s+(?:policy|instructions)"
    r"|execute\s+(?:this\s+)?command|reveal\s+(?:secrets|credentials)"
    r"|system\s*:\s*|bypass\s+approval", re.I,
)
EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
SECRET = re.compile(r"\b(?:Bearer\s+\S+|(?:api[_-]?key|password|secret)\s*[=:]\s*\S+)", re.I)


def redact(text: str) -> str:
    return SECRET.sub("[REDACTED_SECRET]", EMAIL.sub("[REDACTED_EMAIL]", text))


def evidence_from(record: Record, tool: str) -> Evidence:
    content = redact(record.content)
    attributes = {k: redact(v) if isinstance(v, str) else v
                  for k, v in record.attributes.items()}
    digest = Evidence.digest(content)
    identity = Evidence.digest(
        f"{record.tenant_id}:{record.source}:{record.source_id}:{digest}"
    )[:24]
    return Evidence(
        id=f"ev-{identity}", source_type=record.source, source_id=record.source_id,
        timestamp=record.timestamp, content=content, relevance=1, tool=tool,
        reference=record.reference, content_hash=digest,
        suspicious=bool(INJECTION.search(record.content) or INJECTION.search(json.dumps(attributes))),
        attributes=attributes,
    )


class ToolGateway:
    def __init__(
        self, provider: Provider, *, timeout: float = 10, retries: int = 2,
        audit: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.provider = provider
        self.timeout = timeout
        self.retries = retries
        self.audit = audit
        self.cache: dict[str, tuple[float, ToolOutput]] = {}
        self.failures: dict[str, tuple[int, float]] = {}

    async def discover(self) -> list[ToolSpec]:
        return await asyncio.wait_for(self.provider.discover(), timeout=self.timeout)

    async def call(
        self, name: str, arguments: ToolInput, principal: Principal,
    ) -> ToolResult:
        started = time.monotonic()
        call = ToolCall(tool=name)
        result = ToolResult(call=call)
        key = Evidence.digest(
            f"{principal.tenant_id}:{principal.subject}:{name}:{arguments.model_dump_json()}"
        )
        circuit_key = f"{principal.tenant_id}:{name}"
        try:
            require_tool(principal, name)
            require_service(principal, arguments.service)
            failures, opened = self.failures.get(circuit_key, (0, 0))
            if failures >= 3 and time.monotonic() - opened < 30:
                result.error = ToolError(code="circuit_open", message="Tool temporarily unavailable")
                return result
            output: ToolOutput | None = None
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 30:
                output = cached[1]
                call.cache_hit = True
            else:
                for attempt in range(self.retries + 1):
                    call.attempts = attempt + 1
                    try:
                        raw = await asyncio.wait_for(
                            self.provider.invoke(name, arguments, principal), self.timeout,
                        )
                        output = ToolOutput.model_validate(raw)
                        if len(output.model_dump_json()) > 250_000:
                            raise ValueError("Tool output exceeds size policy")
                        for record in output.records:
                            if (record.tenant_id != principal.tenant_id
                                    or record.service != arguments.service):
                                raise PermissionError("Tool returned out-of-scope evidence")
                        self.cache[key] = (time.monotonic(), output)
                        self.failures.pop(circuit_key, None)
                        break
                    except (TimeoutError, ConnectionError, OSError) as exc:
                        self.failures[circuit_key] = (failures + attempt + 1, time.monotonic())
                        if attempt == self.retries:
                            result.error = ToolError(
                                code="unavailable", message=type(exc).__name__, retryable=True,
                            )
                        else:
                            await asyncio.sleep(min(1, 0.05 * 2 ** attempt))
            if output is not None:
                result.evidence = [evidence_from(record, name) for record in output.records]
                call.evidence_ids = [e.id for e in result.evidence]
                if output.truncated:
                    result.error = ToolError(
                        code="truncated", message="Results capped; narrow the query",
                    )
        except (PermissionError, HTTPException) as exc:
            result.error = ToolError(code="forbidden", message=redact(str(exc)))
        except (ValidationError, ValueError, UnsafeQuery) as exc:
            result.error = ToolError(code="invalid_output_or_input", message=redact(str(exc))[:500])
        finally:
            call.duration_ms = (time.monotonic() - started) * 1000
            if result.error:
                call.error_code, call.error = result.error.code, result.error.message
            if self.audit:
                self.audit({"tool": name, "service": arguments.service, "tenant": principal.tenant_id,
                            "call": call.model_dump(mode="json")})
        return result
