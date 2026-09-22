import pytest

from ops.agent import Investigator
from ops.domain import Investigation, Principal
from ops.provenance import ProvenanceError, validate_provenance
from ops.tools import SyntheticProvider, ToolGateway


async def test_report_grounding_and_tamper_rejection(user: Principal) -> None:
    state = Investigation(
        tenant_id="acme", requested_by=user, scenario_id="INC-001",
        description="Investigate checkout-service returning elevated 5xx errors.",
    )
    await Investigator(ToolGateway(SyntheticProvider())).run(state)
    validate_provenance(state)
    assert state.report and "Evidence manifest" in state.report and "SHA-256" in state.report
    assert state.diagnosis
    state.diagnosis.supporting_evidence.append("invented-source")
    with pytest.raises(ProvenanceError, match="nonexistent"):
        validate_provenance(state)
    state.diagnosis.supporting_evidence.pop()
    state.evidence[0].content = "tampered"
    with pytest.raises(ProvenanceError, match="changed"):
        validate_provenance(state)
