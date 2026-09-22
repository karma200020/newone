from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent, TextResourceContents

from ops.config import Settings
from ops.domain import Principal
from ops.mcp_server import GROUPS
from ops.tools import Provider, SyntheticProvider, ToolInput, ToolOutput, ToolSpec


class MCPProvider:
    def __init__(self, principal: Principal, groups: list[str] | None = None):
        self.principal = principal.model_copy(deep=True)
        self.groups = groups if groups is not None else list(GROUPS)
        self.stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}
        self.specs: list[ToolSpec] = []

    async def __aenter__(self) -> MCPProvider:
        try:
            for group in self.groups:
                if group not in GROUPS:
                    raise ValueError("Unapproved MCP server group")
                parameters = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "ops.mcp_server", group],
                    env={"OPS_MCP_PRINCIPAL": self.principal.model_dump_json()},
                )
                read, write = await self.stack.enter_async_context(stdio_client(parameters))
                session = await self.stack.enter_async_context(ClientSession(read, write))
                await asyncio.wait_for(session.initialize(), 30)
                advertised = await session.list_tools()
                listed = {
                    tool.name
                    for tool in advertised.tools
                    if tool.annotations and tool.annotations.readOnlyHint
                }
                resource = await session.read_resource("ops://catalog")  # type: ignore[arg-type]
                if len(resource.contents) != 1 or not isinstance(
                    resource.contents[0],
                    TextResourceContents,
                ):
                    raise ValueError("Invalid MCP catalog resource")
                specs = [
                    ToolSpec.model_validate(item) for item in json.loads(resource.contents[0].text)
                ]
                for spec in specs:
                    if spec.name not in listed or spec.name in self.sessions:
                        raise ValueError("MCP catalog does not match read-only tool discovery")
                    self.sessions[spec.name] = session
                    self.specs.append(spec)
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *args: Any) -> None:
        await self.stack.aclose()

    async def discover(self) -> list[ToolSpec]:
        return list(self.specs)

    async def invoke(
        self,
        name: str,
        arguments: ToolInput,
        principal: Principal,
    ) -> ToolOutput:
        if principal != self.principal:
            raise PermissionError("MCP session identity mismatch")
        if name not in self.sessions:
            raise ValueError("Tool not discovered")
        result = await self.sessions[name].call_tool(
            name,
            {"arguments": arguments.model_dump(mode="json")},
        )
        if result.isError:
            raise ValueError(f"MCP tool failed: {name}")
        if result.structuredContent:
            return ToolOutput.model_validate(result.structuredContent)
        text = [item.text for item in result.content if isinstance(item, TextContent)]
        if len(text) != 1:
            raise ValueError("MCP result must contain exactly one structured payload")
        return ToolOutput.model_validate_json(text[0])


@asynccontextmanager
async def open_provider(settings: Settings, principal: Principal) -> AsyncIterator[Provider]:
    if settings.tool_transport == "mcp":
        async with MCPProvider(principal) as provider:
            yield provider
    else:
        yield SyntheticProvider()
