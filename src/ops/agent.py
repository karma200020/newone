from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import ValidationError

from ops.domain import (
    Action, Diagnosis, DiagnosticRule, Hypothesis, Investigation, Source,
    Status, TimelineEntry, TraceEvent, Window, utcnow,
)
from ops.tools import ToolGateway, ToolInput

Checkpoint = Callable[[Investigation], None]


def trace(state: Investigation, stage: str, summary: str, evidence: list[str] | None = None) -> None:
    state.trace.append(TraceEvent(stage=stage, summary=summary, evidence_ids=evidence or []))
    state.updated_at = utcnow()


class Investigator:
    def __init__(self, gateway: ToolGateway, checkpoint: Checkpoint | None = None):
        self.gateway = gateway
        self.checkpoint = checkpoint

    def save(self, state: Investigation) -> None:
        if self.checkpoint:
            self.checkpoint(state)

    async def run(self, state: Investigation) -> Investigation:
        state.status = Status.UNDERSTANDING
        services = sorted(set(re.findall(r"\b[a-z][a-z0-9-]*-service\b",
                                         state.description.lower())))
        if len(services) != 1:
            state.status = Status.NEEDS_CLARIFICATION
            state.warnings.append("Specify exactly one affected service to investigate.")
            trace(state, "clarification", state.warnings[-1])
            self.save(state)
            return state
        state.affected_services = services
        state.severity = "high" if "5xx" in state.description.lower() else "unknown"
        if state.window is None:
            timestamp = re.search(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})",
                state.description,
            )
            if timestamp:
                start = datetime.fromisoformat(timestamp.group().replace("Z", "+00:00"))
                state.window = Window(start=start - timedelta(minutes=15),
                                      end=start + timedelta(minutes=15))
        state.plan = [
            "Establish the incident window from supplied context or observed telemetry.",
            "Collect independent telemetry, change history, collaboration and runbook evidence.",
            "Generate competing hypotheses from versioned diagnostic runbooks.",
            "Test each hypothesis against supporting, contradictory and missing evidence.",
            "Recommend only an evidence-backed action; require policy evaluation before execution.",
        ]
        trace(state, "plan", "Read-only investigation planned; external content is untrusted data.")
        self.save(state)
        try:
            catalog = await self.gateway.discover()
        except (TimeoutError, ConnectionError, ValueError, OSError) as exc:
            state.status = Status.FAILED
            state.warnings.append(f"Tool discovery failed: {type(exc).__name__}")
            trace(state, "failure", state.warnings[-1])
            self.save(state)
            return state
        selected = [tool for tool in catalog if tool.investigation_default]
        state.status = Status.COLLECTING
        self.save(state)
        results = await asyncio.gather(*[
            self.gateway.call(tool.name, ToolInput(
                service=services[0], scenario_id=state.scenario_id, window=state.window,
            ), state.requested_by) for tool in selected
        ])
        known = {e.id for e in state.evidence}
        for result in results:
            state.tool_calls.append(result.call)
            for evidence in result.evidence:
                if evidence.id not in known:
                    state.evidence.append(evidence)
                    known.add(evidence.id)
            trace(state, "tool", f"{result.call.tool}: "
                  f"{len(result.evidence)} records"
                  + (f"; {result.error.code}" if result.error else ""),
                  result.call.evidence_ids)
            if result.error:
                state.warnings.append(f"{result.call.tool}: {result.error.message}")
        if not selected:
            state.warnings.append("No investigation tools were discovered.")
        telemetry = [e for e in state.evidence if e.source_type in
                     (Source.LOGS, Source.MONITORING, Source.DEPLOYMENTS)]
        if state.window is None and telemetry:
            latest = max(e.timestamp for e in telemetry)
            clock = re.search(r"\b(\d{1,2}):(\d{2})\b", state.description)
            if clock and int(clock[1]) < 24 and int(clock[2]) < 60:
                center = latest.replace(hour=int(clock[1]), minute=int(clock[2]), second=0)
                state.window = Window(start=center - timedelta(minutes=15),
                                      end=center + timedelta(minutes=15))
                state.warnings.append("Date and timezone inferred from synthetic telemetry; "
                                      "confirm these match the requested clock time.")
            else:
                state.window = Window(
                    start=min(e.timestamp for e in telemetry) - timedelta(minutes=1),
                    end=latest + timedelta(minutes=1),
                )
                state.warnings.append("Incident window inferred from observed synthetic telemetry.")
        if state.window:
            state.evidence = [e for e in state.evidence if e.source_type == Source.DOCUMENTATION
                              or state.window.start <= e.timestamp <= state.window.end]
            telemetry = [e for e in telemetry if state.window.start <= e.timestamp <= state.window.end]
        state.timeline = [
            TimelineEntry(timestamp=e.timestamp, summary=e.content[:220], evidence_ids=[e.id])
            for e in sorted(telemetry, key=lambda e: (e.timestamp, e.source_id))
        ]
        state.observations = [f"{e.source_type.value}: {e.content[:200]}"
                              for e in telemetry if not e.suspicious]
        state.status = Status.TESTING
        trace(state, "hypothesis", "Compare all retrieved diagnostic rules; do not assume "
              "temporal correlation proves causation.")
        self.save(state)
        rules: list[DiagnosticRule] = []
        for evidence in state.evidence:
            if evidence.source_type != Source.DOCUMENTATION or evidence.suspicious:
                continue
            encoded = evidence.attributes.get("rule")
            if isinstance(encoded, str):
                try:
                    rule = DiagnosticRule.model_validate_json(encoded)
                    if rule.id not in {r.id for r in rules}:
                        rules.append(rule)
                except ValidationError:
                    state.warnings.append(f"Invalid diagnostic rule in {evidence.source_id}")
        observations = [e for e in state.evidence
                        if e.source_type != Source.DOCUMENTATION and not e.suspicious]
        state.hypotheses = []
        for rule in rules:
            supporting = [e for e in observations if e.attributes.get("signal") in rule.signals]
            contradicting = [e for e in observations
                             if e.attributes.get("signal") in rule.contradictions]
            matched = {e.attributes["signal"] for e in supporting}
            missing = [signal for signal in rule.signals if signal not in matched]
            source_count = len({e.source_type for e in supporting})
            coverage = len(matched) / max(1, len(rule.signals))
            score = max(0, coverage * min(1, source_count / max(1, rule.minimum_sources))
                        - 0.35 * len(contradicting))
            state.hypotheses.append(Hypothesis(
                id=rule.id, title=rule.title, required_signals=rule.signals,
                supporting_evidence=[e.id for e in supporting],
                contradictory_evidence=[e.id for e in contradicting],
                missing_signals=missing, score=score,
                test_summary=f"{len(matched)}/{len(rule.signals)} signals across "
                f"{source_count} independent source types; {len(contradicting)} contradictions.",
            ))
        state.hypotheses.sort(key=lambda h: (-h.score, h.id))
        self.diagnose(state, rules)
        state.status = Status.DIAGNOSED
        trace(state, "diagnosis", state.diagnosis.root_cause if state.diagnosis else "Inconclusive",
              state.diagnosis.supporting_evidence if state.diagnosis else [])
        self.save(state)
        return state

    @staticmethod
    def diagnose(state: Investigation, rules: list[DiagnosticRule]) -> None:
        candidate = state.hypotheses[0] if state.hypotheses else None
        tied = bool(candidate and len(state.hypotheses) > 1
                    and state.hypotheses[1].score >= candidate.score - 0.1)
        missing_sources = any(c.error_code for c in state.tool_calls)
        uncertainty = list(state.warnings)
        if any(e.suspicious for e in state.evidence):
            uncertainty.append("Suspicious source content was quarantined from hypothesis testing.")
        if candidate is None or candidate.score < 0.5 or tied:
            state.diagnosis = Diagnosis(
                root_cause="Insufficient evidence to distinguish the competing hypotheses.",
                hypothesis_id=None, confidence="low",
                confidence_explanation="No uniquely supported hypothesis meets the evidence threshold.",
                supporting_evidence=[], contradictory_evidence=[],
                uncertainty=uncertainty + ["Collect additional independent telemetry."],
            )
            return
        high = candidate.score >= 0.95 and not missing_sources
        uncertainty.extend(f"Missing diagnostic signal: {s}" for s in candidate.missing_signals)
        uncertainty.append("Correlation in synthetic telemetry is not proof of real-world causation.")
        state.diagnosis = Diagnosis(
            root_cause=candidate.title, hypothesis_id=candidate.id,
            confidence="high" if high else "medium",
            confidence_explanation=candidate.test_summary
            + " This is an ordinal evidence score, not a calibrated probability.",
            supporting_evidence=candidate.supporting_evidence,
            contradictory_evidence=candidate.contradictory_evidence, uncertainty=uncertainty,
        )
        # Runbook recommendations remain inert data until a separate policy gate authorizes them.
        if not candidate.missing_signals and not candidate.contradictory_evidence:
            rule = next(rule for rule in rules if rule.id == candidate.id)
            state.actions = [Action(
                kind=rule.action, service=state.affected_services[0], reason=rule.remediation,
                evidence_ids=candidate.supporting_evidence, parameters=rule.action_parameters,
                risk="high",
            )]
