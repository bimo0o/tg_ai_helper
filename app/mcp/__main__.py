"""Run only as a trusted stdio child. Does not read .env or receive a Telegram/LLM key."""

import asyncio
import logging
import os

from aiohttp import ClientSession
from mcp.server.stdio import stdio_server

from app.db import DatabaseSettings, create_pool
from app.mcp.authorization import WriteAuthority
from app.mcp.server import create_server
from app.reminders import Reminders
from app.tool_data import Schedule, Weather

logger = logging.getLogger("app.mcp.startup")


async def run():
    # Schema installation belongs to the host, not to an independently invoked tool server.
    logger.info("MCP startup stage=imports_ready")
    settings = DatabaseSettings(
        postgres_password=os.environ["POSTGRES_PASSWORD"],
        postgres_host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        postgres_port=int(os.environ.get("POSTGRES_PORT", "5432")),
        postgres_db=os.environ.get("POSTGRES_DB", "bot"),
        postgres_user=os.environ.get("POSTGRES_USER", "bot"),
    )
    authority = WriteAuthority(bytes.fromhex(os.environ["MCP_SIGNING_KEY"]))
    schedule = Schedule.load(os.environ["SCHEDULE_PATH"], os.environ["SCHEDULE_TIMEZONE"])
    logger.info("MCP startup stage=config_ready")
    pool = await create_pool(settings)
    logger.info("MCP startup stage=database_ready")
    try:
        async with ClientSession() as session:
            server = create_server(Reminders(pool), Weather(session), schedule, authority)
            async with stdio_server() as (reader, writer):
                logger.info("MCP startup stage=stdio_ready")
                await server.run(reader, writer, server.create_initialization_options())
    finally:
        await pool.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)  # stderr only: stdout is the MCP protocol.
    logger.setLevel(logging.INFO)
    try:
        asyncio.run(run())
    except Exception as error:
        logging.getLogger(__name__).error(
            "MCP server startup failed kind=%s; no configuration values logged",
            type(error).__name__,
        )
        raise SystemExit(1) from None
