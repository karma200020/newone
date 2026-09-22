import pytest

from ops.agent import Investigator
from ops.config import Settings
from ops.domain import Investigation, Principal, Status
from ops.store import Store
from ops.synthetic import generate_scenarios
from ops.tools import SyntheticProvider, ToolGateway, ToolInput, ToolOutput
from ops.worker import run_once


@pytest.mark.parametrize("scenario", generate_scenarios()[:10], ids=lambda s: s.id)
async def test_competing_hypotheses_and_grounded_result(scenario, user: Principal) -> None:  # type: ignore[no-untyped-def]
    state = Investigation(
        tenant_id="acme",
        requested_by=user,
        description=scenario.description,
        scenario_id=scenario.id,
    )
    result = await Investigator(ToolGateway(SyntheticProvider())).run(state)
    assert result.status == Status.DIAGNOSED
    assert result.diagnosis and result.diagnosis.hypothesis_id == scenario.ground_truth
    assert len(result.hypotheses) == 10
    assert result.diagnosis.confidence == "high"
    assert result.actions[0].kind == scenario.expected_remediation
    assert result.actions[0].status == "proposed"
    assert set(result.diagnosis.supporting_evidence) <= {e.id for e in result.evidence}


async def test_partial_failure_and_missing_service(user: Principal) -> None:
    class MissingLogs(SyntheticProvider):
        async def invoke(
            self,
            name: str,
            arguments: ToolInput,
            principal: Principal,
        ) -> ToolOutput:
            if name == "logs.search":
                raise ConnectionError("logs unavailable")
            return await super().invoke(name, arguments, principal)

    state = Investigation(
        tenant_id="acme",
        requested_by=user,
        description="Investigate checkout-service 5xx.",
        scenario_id="INC-003",
    )
    result = await Investigator(ToolGateway(MissingLogs(), retries=0)).run(state)
    assert result.warnings
    assert result.diagnosis and result.diagnosis.confidence != "high"
    state.description = "Investigate an unspecified outage."
    state.affected_services = []
    result = await Investigator(ToolGateway(SyntheticProvider())).run(state)
    assert result.status == Status.NEEDS_CLARIFICATION


async def test_durable_worker(store: Store, settings: Settings, user: Principal) -> None:
    state = Investigation(
        tenant_id="acme",
        requested_by=user,
        description="Investigate checkout-service 5xx.",
        scenario_id="INC-001",
    )
    store.create(state, "worker-test", "hash")
    assert await run_once(store, settings)
    persisted = store.get("acme", state.id)
    assert persisted.status == Status.DIAGNOSED
    assert persisted.trace and persisted.tool_calls
    assert not await run_once(store, settings)
