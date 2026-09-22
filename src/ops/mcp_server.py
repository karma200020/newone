"""Five MCP stdio servers with server-bound identities and read-only tools."""

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ops.domain import Principal, Source
from ops.security import require_service, require_tool
from ops.tools import SyntheticProvider, ToolInput, ToolOutput, ToolSpec

GROUPS: dict[str, set[Source]] = {
    "github": {Source.GITHUB},
    "jira": {Source.JIRA},
    "slack": {Source.SLACK},
    "monitoring": {Source.MONITORING, Source.LOGS, Source.DEPLOYMENTS, Source.DATABASE},
    "documentation": {Source.DOCUMENTATION},
}


def build_server(group: str, principal: Principal) -> FastMCP:
    if group not in GROUPS:
        raise ValueError("Unknown MCP server group")
    server = FastMCP(f"acme-{group}")
    configured_index = os.environ.get("OPS_DOCUMENT_INDEX_PATH")
    provider = SyntheticProvider(
        document_index=Path(configured_index) if configured_index else None
    )
    specs = [spec for spec in asyncio.run(provider.discover()) if spec.source in GROUPS[group]]

    def bind(spec: ToolSpec) -> Callable[[ToolInput], Awaitable[ToolOutput]]:
        async def invoke(arguments: ToolInput) -> ToolOutput:
            require_tool(principal, spec.name)
            require_service(principal, arguments.service)
            return await provider.invoke(spec.name, arguments, principal)

        return invoke

    for spec in specs:
        server.add_tool(
            bind(spec),
            name=spec.name,
            description=spec.description,
            annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
        )

    @server.resource("ops://catalog")
    def catalog() -> str:
        return "[" + ",".join(spec.model_dump_json() for spec in specs) + "]"

    return server


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: python -m ops.mcp_server <github|jira|slack|monitoring|documentation>"
        )
    # Identity is configured by the trusted parent, never supplied in tool arguments.
    principal = Principal.model_validate_json(os.environ["OPS_MCP_PRINCIPAL"])
    build_server(sys.argv[1], principal).run(transport="stdio")


if __name__ == "__main__":
    main()
