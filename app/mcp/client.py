"""Validated MCP host adapter; writes always need an explicit trusted approval identity."""

import asyncio
import json
from contextlib import asynccontextmanager
from uuid import UUID

from jsonschema import Draft202012Validator
from mcp import Client

from app.mcp.authorization import WriteAuthority
from app.mcp.contracts import INPUTS, OUTPUTS, WeekOutput
from app.mcp.server import WEEK_URI


class ToolUnavailable(ValueError):
    pass


class ToolClient:
    def __init__(self, transport, authority: WriteAuthority):
        self.transport = transport
        self.authority = authority
        self._client = None

    async def __aenter__(self):
        self._client = await Client(self.transport, read_timeout_seconds=18).__aenter__()
        return self

    async def __aexit__(self, *exc):
        client, self._client = self._client, None
        if client is not None:
            await client.__aexit__(*exc)

    @asynccontextmanager
    async def _connection(self):
        # The bot enters ToolClient once for its lifespan, sharing one stdio child/pool.
        if self._client is not None:
            yield self._client
        else:
            # Short-lived mode is for independent diagnostics and protocol tests.
            async with Client(self.transport, read_timeout_seconds=18) as client:
                yield client

    async def discover(self) -> list:
        async with asyncio.timeout(10), self._connection() as client:
            listing = await client.list_tools()
            tools = listing.tools
            if {tool.name for tool in tools} != set(INPUTS) or len(tools) != len(INPUTS):
                raise ToolUnavailable("Unexpected tool discovery")
            for tool in tools:
                Draft202012Validator.check_schema(tool.input_schema)
                Draft202012Validator.check_schema(tool.output_schema)
                # An altered server schema must not broaden our local write contract.
                if tool.input_schema != INPUTS[tool.name].model_json_schema():
                    raise ToolUnavailable("Unexpected input contract")
                if tool.output_schema != OUTPUTS[tool.name].model_json_schema():
                    raise ToolUnavailable("Unexpected output contract")
            self.discovered_tools = tools
            return tools

    async def call(
        self,
        name: str,
        arguments: dict,
        *,
        owner: tuple[int, int] | None = None,
        action_id: UUID | None = None,
    ) -> dict:
        try:
            if name not in INPUTS:
                raise ToolUnavailable("Unknown tool")
            clean = INPUTS[name].model_validate(arguments).model_dump()
            metadata = None
            if name == "add_reminder":
                if owner is None or action_id is None:
                    raise ToolUnavailable("User confirmation required")
                metadata = self.authority.sign(owner, action_id, clean)
            async with (
                asyncio.timeout(20),
                self._connection() as client,
            ):
                result = await client.call_tool(name, clean, meta=metadata)
                if result.is_error:
                    raise ToolUnavailable("Tool execution failed")
                return (
                    OUTPUTS[name].model_validate(result.structured_content).model_dump(mode="json")
                )
        except Exception:
            # Never carry a schema exception containing user values into logs or model context.
            raise ToolUnavailable("MCP unavailable, invalid result or action denied") from None

    async def week(self) -> dict:
        try:
            async with (
                asyncio.timeout(10),
                self._connection() as client,
            ):
                result = await client.read_resource(WEEK_URI)
                if len(result.contents) != 1:
                    raise ValueError
                return WeekOutput.model_validate(json.loads(result.contents[0].text)).model_dump(
                    mode="json"
                )
        except Exception:
            raise ToolUnavailable("MCP resource unavailable or invalid") from None
