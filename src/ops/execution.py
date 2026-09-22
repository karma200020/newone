"""Transactional, isolated synthetic executor. No production side effects."""

import time

from sqlalchemy.orm import Session

from ops.domain import Investigation, Record, Source, ToolCall, TraceEvent, utcnow
from ops.policy import PolicyDenied, action_digest, authorize_execution
from ops.provenance import render_report
from ops.store import ExecutionRow, JobRow, SimulationRow, Store
from ops.synthetic import RULES, get_scenario
from ops.tools import evidence_from


def execute_action(store: Store, job: JobRow) -> Investigation:
    started = time.perf_counter()

    def execute(state: Investigation, session: Session) -> None:
        store.check_lease(session, job)
        action = next((a for a in state.actions if a.id == job.target), None)
        if action is None:
            raise PolicyDenied("Execution job does not reference a proposed action")
        prior = session.get(ExecutionRow, action.id)
        if prior:
            if prior.tenant_id != state.tenant_id or prior.result["digest"] != action_digest(action):
                raise PolicyDenied("Execution identity or proposal digest changed")
            return
        authorize_execution(state, action, store.settings)
        action.status = "executing"
        simulation = session.get(SimulationRow, state.id)
        if simulation is None:
            simulation = SimulationRow(
                investigation_id=state.id, tenant_id=state.tenant_id, fault_active=True, revision=0,
            )
            session.add(simulation)
        if simulation.tenant_id != state.tenant_id:
            raise PolicyDenied("Simulator tenant mismatch")
        # The simulator's fault model is private; none of these labels enter agent evidence.
        scenario = get_scenario(state.scenario_id, state.tenant_id)
        fault = next(rule for rule in RULES if rule.id == scenario.ground_truth)
        if action.kind == fault.action and action.parameters == fault.action_parameters:
            simulation.fault_active = False
        simulation.revision += 1
        session.flush()
        # Verify by reading persisted simulator state, not by trusting the action return value.
        session.refresh(simulation)
        measured_rate = 0.18 if simulation.fault_active else 0.002
        measured_latency = 2400.0 if simulation.fault_active else 110.0
        post = Record(
            source=Source.MONITORING,
            source_id=f"simulation:{state.id}:post-action:{simulation.revision}",
            tenant_id=state.tenant_id, service=action.service, timestamp=utcnow(),
            content=f"Post-action synthetic probe: error_rate={measured_rate}; "
                    f"p95_latency_ms={measured_latency}.",
            attributes={"metric": "error_rate", "value": measured_rate,
                        "latency_ms": measured_latency, "phase": "verification"},
            reference=f"acme://simulator/{state.id}/{simulation.revision}",
        )
        evidence = evidence_from(post, "monitoring.verify")
        state.evidence.append(evidence)
        state.tool_calls.append(ToolCall(
            tool="monitoring.verify", evidence_ids=[evidence.id],
            duration_ms=(time.perf_counter() - started) * 1000,
        ))
        before_rates = [float(e.attributes["value"]) for e in state.evidence
                        if e.attributes.get("metric") == "error_rate"
                        and e.attributes.get("phase") == "during"]
        before = max(before_rates) if before_rates else None
        verified = before is not None and measured_rate < before and measured_rate < 0.01
        action.status = "succeeded" if verified else "unverified"
        action.verification = (
            f"Synthetic error rate {before} -> {measured_rate}; p95 {measured_latency}ms. "
            f"{'Recovery verified' if verified else 'Recovery NOT verified'} [{evidence.id}]. "
            "This is a simulator observation, not live production telemetry."
        )
        state.trace.append(TraceEvent(
            stage="verification", summary=action.verification, evidence_ids=[evidence.id],
        ))
        store.check_lease(session, job)
        session.add(ExecutionRow(
            action_id=action.id, tenant_id=state.tenant_id,
            result={"digest": action_digest(action), "status": action.status,
                    "evidence_id": evidence.id, "verified": verified},
        ))
        store.audit(session, state.tenant_id, "worker", "action.executed",
                    {"action_id": action.id, "digest": action_digest(action),
                     "status": action.status, "evidence_id": evidence.id}, state.id)
        state.report = render_report(state)

    return store.mutate(job.tenant_id, job.investigation_id, execute)
