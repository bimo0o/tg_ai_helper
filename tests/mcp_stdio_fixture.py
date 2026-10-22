"""Protocol-only test child: no database, environment secrets or outbound APIs."""

import asyncio

from mcp.server.stdio import stdio_server

from tests.test_mcp import fixture_server


async def run():
    server, *_ = fixture_server()
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(run())
