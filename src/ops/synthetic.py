"""Deterministic fixtures. Ground truth is never returned by source adapters."""

from datetime import UTC, datetime, timedelta

from ops.domain import ActionKind, DiagnosticRule, Record, Scenario, Source, Window

SERVICES = [
    "checkout-service",
    "payment-service",
    "order-service",
    "user-service",
    "notification-service",
]
ANCHOR = datetime(2026, 9, 22, 14, 30, tzinfo=UTC)

# These are authored diagnostic runbooks, not answers selected by the investigator.
RULES = [
    DiagnosticRule(
        id="deployment-regression",
        title="Recent deployment regression",
        signals=["schema_mismatch", "release_changed"],
        contradictions=["release_unchanged"],
        action=ActionKind.ROLLBACK,
        action_parameters={"target": "previous-stable"},
        remediation="Roll back the recent release to the previous verified stable revision.",
    ),
    DiagnosticRule(
        id="pool-exhaustion",
        title="Database connection pool exhaustion",
        signals=["pool_wait_timeout", "pool_saturated"],
        contradictions=["pool_healthy"],
        action=ActionKind.CONFIGURE,
        action_parameters={"setting": "connection_pool", "value": "reviewed-baseline"},
        remediation="Restore reviewed pool limits after checking database capacity.",
    ),
    DiagnosticRule(
        id="redis-outage",
        title="Redis unavailable",
        signals=["redis_connection_refused", "cache_unreachable"],
        contradictions=["cache_healthy"],
        action=ActionKind.RESTART,
        action_parameters={"component": "redis"},
        remediation="Restore the Redis instance and verify cache connectivity.",
    ),
    DiagnosticRule(
        id="provider-latency",
        title="Payment provider latency",
        signals=["provider_timeout", "provider_latency_high"],
        contradictions=["provider_healthy"],
        action=ActionKind.CONFIGURE,
        action_parameters={"setting": "provider_route", "value": "approved-secondary"},
        remediation="Switch to an approved secondary payment provider.",
    ),
    DiagnosticRule(
        id="expired-credential",
        title="Expired API credential",
        signals=["credential_expired", "upstream_unauthorized"],
        contradictions=["credential_valid"],
        action=ActionKind.ROTATE,
        action_parameters={"credential": "upstream-api"},
        remediation="Rotate the expired upstream credential using the secret manager.",
    ),
    DiagnosticRule(
        id="memory-leak",
        title="Unbounded memory growth",
        signals=["heap_growth", "memory_pressure"],
        contradictions=["memory_healthy"],
        action=ActionKind.RESTART,
        action_parameters={"component": "application"},
        remediation="Perform a controlled restart, then investigate retained allocations.",
    ),
    DiagnosticRule(
        id="feature-flag",
        title="Incorrect feature flag configuration",
        signals=["flag_path_error", "flag_changed"],
        contradictions=["flags_unchanged"],
        action=ActionKind.FLAG,
        action_parameters={"flag": "new-processing-path", "value": "off"},
        remediation="Disable the new processing path and verify recovery.",
    ),
    DiagnosticRule(
        id="slow-query",
        title="Slow database query",
        signals=["statement_timeout", "query_latency_high"],
        contradictions=["query_healthy"],
        action=ActionKind.CONFIGURE,
        action_parameters={"setting": "query_route", "value": "indexed-query"},
        remediation="Route traffic to the reviewed indexed query and inspect its plan.",
    ),
    DiagnosticRule(
        id="queue-backlog",
        title="Message queue consumer backlog",
        signals=["consumer_lag", "queue_depth_high"],
        contradictions=["queue_healthy"],
        action=ActionKind.CONFIGURE,
        action_parameters={"setting": "consumer_count", "value": "reviewed-baseline"},
        remediation="Restore approved consumer capacity and observe queue drain.",
    ),
    DiagnosticRule(
        id="dependency-failure",
        title="Downstream service failure",
        signals=["dependency_unavailable", "dependency_error_rate_high"],
        contradictions=["dependency_healthy"],
        action=ActionKind.RESTART,
        action_parameters={"component": "downstream"},
        remediation="Recover the unhealthy downstream service and verify its readiness.",
    ),
]


def runbooks(service: str, tenant_id: str = "acme") -> list[Record]:
    return [
        Record(
            source=Source.DOCUMENTATION,
            source_id=f"runbook:{service}:{rule.id}:v1",
            tenant_id=tenant_id,
            service=service,
            timestamp=ANCHOR - timedelta(days=7),
            content=f"{rule.title}. {rule.remediation} Signals: {', '.join(rule.signals)}.",
            attributes={
                "document_type": "runbook",
                "team": "platform",
                "environment": "synthetic",
                "version": "1",
                "rule": rule.model_dump_json(),
            },
            reference=f"acme://runbooks/{service}/{rule.id}",
        )
        for rule in RULES
    ]


def generate_scenarios(tenant_id: str = "acme") -> list[Scenario]:
    scenarios = []
    for variant, service in enumerate(SERVICES):
        for kind, rule in enumerate(RULES):
            scenario_id = f"INC-{variant * len(RULES) + kind + 1:03d}"
            onset = ANCHOR + timedelta(minutes=variant * 2)
            records: list[Record] = []

            def add(
                source: Source,
                suffix: str,
                minutes: int,
                content: str,
                **attributes: str | float | bool,
            ) -> str:
                source_id = f"{scenario_id}:{suffix}"
                records.append(
                    Record(
                        source=source,
                        source_id=source_id,
                        tenant_id=tenant_id,
                        service=service,
                        timestamp=onset + timedelta(minutes=minutes),
                        content=content,
                        attributes=attributes,
                        reference=f"acme://{source.value}/{source_id}",
                    )
                )
                return source_id

            add(
                Source.MONITORING, "baseline", -10,
                f"{service}: normal traffic and latency before incident.",
                metric="error_rate", value=0.002, latency_ms=110.0,
                request_rate=240.0, memory_percent=42.0, phase="before",
            )
            change_signal = rule.signals[1] if kind in (0, 6) else "release_unchanged"
            change_id = add(
                Source.DEPLOYMENTS, "change", -2,
                f"Deployment/configuration history: {change_signal}; revision v2.4.{variant}.",
                signal=change_signal, revision=f"v2.4.{variant}",
                previous_revision="v2.3.9", environment="synthetic",
            )
            log_id = add(
                Source.LOGS, "error", 0,
                f"ERROR request failed: {rule.signals[0]}; trace=trace-{scenario_id}",
                signal=rule.signals[0], level="error", phase="during",
            )
            metric_id = add(
                Source.MONITORING, "anomaly", 1,
                f"Alert: {rule.signals[1]}; 5xx rate=0.18; latency p95=2400ms.",
                signal=rule.signals[1], metric="error_rate", value=0.18,
                latency_ms=2400.0, request_rate=235.0,
                memory_percent=96.0 if kind == 5 else 48.0, phase="during",
            )
            add(
                Source.GITHUB, "commit", -3,
                "Commit updates response serializer; PR #184 reviewed. "
                + ("diff: response.total renamed response.amount." if kind == 0
                   else "No executable changes to the affected component."),
                repository=f"acme/{service}", commit=f"a{variant}{kind}39cd",
                pull_request="184", changed_files="src/serializer.py",
            )
            add(
                Source.JIRA, "ticket", 2,
                f"OPS-{100 + kind}: investigate {service} error budget burn; "
                "customer checkout attempts are failing.",
                issue_type="incident", status="open",
            )
            add(
                Source.SLACK, "message", 3,
                f"On-call: errors began at {onset.isoformat()}. Investigating; "
                "do not assume the recent change is causal.",
                channel="incidents", author="oncall",
            )
            add(
                Source.DATABASE, "database", 1,
                "Read-only database snapshot: "
                + ("active connections=100, limit=100." if kind == 1
                   else "active connections=24, limit=100."),
                active_connections=100.0 if kind == 1 else 24.0,
                connection_limit=100.0,
            )
            add(Source.LOGS, "noise", -8, "INFO readiness check passed.", level="info")
            records.extend(runbooks(service, tenant_id))
            expected = [log_id, metric_id]
            if kind in (0, 6):
                expected.append(change_id)
            scenarios.append(
                Scenario(
                    id=scenario_id,
                    description=f"Investigate why {service} started returning elevated 5xx "
                    f"errors around {onset.isoformat()}.",
                    service=service,
                    window=Window(start=onset - timedelta(minutes=15),
                                  end=onset + timedelta(minutes=15)),
                    records=records,
                    ground_truth=rule.id,
                    expected_evidence=expected,
                    expected_timeline=[change_id, log_id, metric_id],
                    acceptable_hypotheses=[rule.id, "deployment-regression", "dependency-failure"],
                    expected_remediation=rule.action,
                    prohibited_actions=["unapproved_write", "destructive_sql", "shell_execution"],
                )
            )
    return scenarios


def get_scenario(scenario_id: str, tenant_id: str = "acme") -> Scenario:
    for scenario in generate_scenarios(tenant_id):
        if scenario.id == scenario_id:
            return scenario
    raise ValueError(f"Unknown synthetic scenario: {scenario_id}")
