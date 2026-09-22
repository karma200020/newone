from ops.domain import Principal
from ops.mcp_client import MCPProvider
from ops.tools import ToolGateway, ToolInput


async def test_real_mcp_discovery_and_invocation(user: Principal) -> None:
    async with MCPProvider(user, groups=["github"]) as provider:
        catalog = await provider.discover()
        assert {spec.name for spec in catalog} >= {"github.search", "github.commit"}
        result = await ToolGateway(provider).call(
            "github.search", ToolInput(service="checkout-service", scenario_id="INC-001"), user,
        )
        assert result.error is None
        assert result.evidence and result.evidence[0].source_id == "INC-001:commit"
