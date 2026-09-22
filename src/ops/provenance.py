from ops.domain import Evidence, Investigation, Source


class ProvenanceError(ValueError):
    pass


def validate_provenance(state: Investigation) -> None:
    evidence = {item.id: item for item in state.evidence}
    if len(evidence) != len(state.evidence):
        raise ProvenanceError("Duplicate evidence identifiers")
    for item in state.evidence:
        if Evidence.digest(item.content) != item.content_hash:
            raise ProvenanceError(f"Evidence content changed: {item.id}")
        if item.timestamp.tzinfo is None:
            raise ProvenanceError(f"Evidence timestamp lacks timezone: {item.id}")

    def check(ids: list[str], *, conclusion: bool = False) -> None:
        if not set(ids).issubset(evidence):
            raise ProvenanceError("Conclusion or trace references nonexistent evidence")
        if conclusion and any(evidence[key].suspicious for key in ids):
            raise ProvenanceError("A conclusion cites quarantined evidence")

    for hypothesis in state.hypotheses:
        check(hypothesis.supporting_evidence, conclusion=True)
        check(hypothesis.contradictory_evidence, conclusion=True)
    for entry in state.timeline:
        check(entry.evidence_ids)
    for call in state.tool_calls:
        check(call.evidence_ids)
    for event in state.trace:
        check(event.evidence_ids)
    for action in state.actions:
        check(action.evidence_ids, conclusion=True)
        if not action.evidence_ids:
            raise ProvenanceError("Recommended action has no supporting evidence")
    if state.diagnosis:
        diagnosis = state.diagnosis
        check(diagnosis.supporting_evidence, conclusion=True)
        check(diagnosis.contradictory_evidence, conclusion=True)
        if diagnosis.hypothesis_id:
            matching = [h for h in state.hypotheses if h.id == diagnosis.hypothesis_id]
            if len(matching) != 1 or matching[0].title != diagnosis.root_cause:
                raise ProvenanceError("Diagnosis does not match a tested hypothesis")
            hypothesis = matching[0]
            if set(diagnosis.supporting_evidence) != set(hypothesis.supporting_evidence):
                raise ProvenanceError("Diagnosis evidence differs from tested evidence")
            if not diagnosis.supporting_evidence:
                raise ProvenanceError("Diagnosis is unsupported")
            sources = {evidence[key].source_type for key in diagnosis.supporting_evidence}
            sources.discard(Source.DOCUMENTATION)
            if diagnosis.confidence == "high" and len(sources) < 2:
                raise ProvenanceError("High confidence requires independent source types")


def render_report(state: Investigation) -> str:
    validate_provenance(state)
    lines = [
        f"# Incident report {state.id}",
        "",
        "**Environment: synthetic. No live production systems were modified.**",
        "",
        f"Request: {state.description}",
        f"Status: {state.status.value}",
        f"Services: {', '.join(state.affected_services) or 'unknown'}",
        f"Run: {state.run_id}; request: {state.request_id}",
        "",
        "## Diagnosis",
    ]
    if state.diagnosis:
        diagnosis = state.diagnosis
        lines.extend([
            diagnosis.root_cause,
            f"Confidence: {diagnosis.confidence} (not a calibrated probability).",
            diagnosis.confidence_explanation,
            "Supporting evidence: " + ", ".join(diagnosis.supporting_evidence),
            "Contradictory evidence: " + (", ".join(diagnosis.contradictory_evidence) or "none found"),
            "",
            "## Uncertainty and missing evidence",
            *[f"- {item}" for item in diagnosis.uncertainty],
        ])
    else:
        lines.append("No diagnosis available.")
    lines.extend(["", "## Timeline"])
    lines.extend(
        f"- {item.timestamp.isoformat()}: {item.summary} [{', '.join(item.evidence_ids)}]"
        for item in state.timeline
    )
    lines.extend(["", "## Tested hypotheses"])
    lines.extend(
        f"- {item.title}: {item.test_summary} "
        f"Supporting: {', '.join(item.supporting_evidence) or 'none'}. "
        f"Contradicting: {', '.join(item.contradictory_evidence) or 'none'}."
        for item in state.hypotheses
    )
    lines.extend(["", "## Recommended actions"])
    lines.extend(
        f"- {item.kind.value} on {item.service}: {item.reason} "
        f"Risk: {item.risk}; status: {item.status}. "
        f"Evidence: {', '.join(item.evidence_ids)}. "
        f"Verification: {item.verification or 'not executed'}."
        for item in state.actions
    )
    lines.extend(["", "## Approvals"])
    lines.extend(f"- {a.actor}: {a.decision} for {a.action_id} at {a.timestamp.isoformat()}"
                 for a in state.approvals)
    lines.extend(["", "## Tool activity"])
    lines.extend(
        f"- {call.tool}: {call.duration_ms:.1f} ms, {call.attempts} attempt(s), "
        f"cache={call.cache_hit}, error={call.error or 'none'}."
        for call in state.tool_calls
    )
    lines.extend(["", "## Evidence manifest"])
    for evidence in state.evidence:
        lines.extend([
            f"### {evidence.id}",
            f"Source: {evidence.source_type.value} / {evidence.source_id}",
            f"Timestamp: {evidence.timestamp.isoformat()}; tool: {evidence.tool}",
            f"Reference: {evidence.reference or 'not supplied'}",
            f"SHA-256 (redacted content): {evidence.content_hash}",
            f"Quarantined: {evidence.suspicious}",
            evidence.content,
            "",
        ])
    lines.extend([
        "## Usage",
        f"Model: {state.usage.model}; version: {state.usage.model_version}; "
        f"prompt: {state.usage.prompt_version}",
        f"Tokens: {state.usage.input_tokens} input / {state.usage.output_tokens} output; "
        f"estimated provider cost: ${state.usage.cost_usd:.6f}.",
    ])
    return "\n".join(lines) + "\n"
