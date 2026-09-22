from ops.domain import Principal
from ops.mcp_client import MCPProvider
from ops.tools import ToolGateway, ToolInput


async def test_real_mcp_discovery_and_invocation(user: Principal) -> None:
    async with MCPProvider(user, groups=["github"]) as provider:
        catalog = await provider.discover()
        assert {spec.name for spec in catalog} >= {"github.search", "github.commit"}
        result = await ToolGateway(provider).call(
            "github.search",
            ToolInput(service="checkout-service", scenario_id="INC-001"),
            user,
        )
        assert result.error is None
        assert result.evidence and result.evidence[0].source_id == "INC-001:commit"


async def test_complete_investigation_through_five_mcp_servers(user: Principal) -> None:
    from ops.agent import Investigator
    from ops.domain import Investigation

    async with MCPProvider(user) as provider:
        state = Investigation(
            tenant_id=user.tenant_id,
            requested_by=user,
            scenario_id="INC-001",
            description="Investigate checkout-service elevated 5xx errors.",
        )
        result = await Investigator(ToolGateway(provider)).run(state)
        assert result.diagnosis and result.diagnosis.hypothesis_id == "deployment-regression"
        assert result.diagnosis.confidence == "high"
        assert len({e.source_type for e in result.evidence}) == 8
        assert result.usage.retrieval_calls == 1
        assert not any(call.error for call in result.tool_calls)
