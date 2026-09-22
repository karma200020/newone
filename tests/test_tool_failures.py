import asyncio

from ops.domain import Principal
from ops.tools import SyntheticProvider, ToolGateway, ToolInput, ToolOutput


async def test_malformed_response_is_not_an_empty_success(user: Principal) -> None:
    class Malformed(SyntheticProvider):
        async def invoke(
            self,
            name: str,
            arguments: ToolInput,
            principal: Principal,
        ) -> ToolOutput:
            return {"records": [{"unexpected": "payload"}]}  # type: ignore[return-value]

    result = await ToolGateway(Malformed()).call(
        "logs.search",
        ToolInput(service="checkout-service", scenario_id="INC-001"),
        user,
    )
    assert result.error and result.error.code == "invalid_output_or_input"
    assert not result.evidence


async def test_real_timeout_and_circuit_breaker(user: Principal) -> None:
    class Slow(SyntheticProvider):
        async def invoke(
            self,
            name: str,
            arguments: ToolInput,
            principal: Principal,
        ) -> ToolOutput:
            await asyncio.sleep(1)
            return ToolOutput(records=[])

    gateway = ToolGateway(Slow(), timeout=0.01, retries=0)
    arguments = ToolInput(service="checkout-service", scenario_id="INC-001")
    for _ in range(3):
        result = await gateway.call("logs.search", arguments, user)
        assert result.error and result.error.code == "unavailable"
    result = await gateway.call("logs.search", arguments, user)
    assert result.error and result.error.code == "circuit_open"


async def test_database_syntax_error_surfaces_without_crashing(user: Principal) -> None:
    result = await ToolGateway(SyntheticProvider()).call(
        "database.query",
        ToolInput(
            service="checkout-service",
            scenario_id="INC-001",
            query="SELECT unknown_column FROM service_health",
        ),
        user,
    )
    assert result.error and result.error.code == "database_error"
    assert not result.evidence
