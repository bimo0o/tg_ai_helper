"""Optional MCP startup. Failure disables tools without disabling ordinary conversation."""

import asyncio
import logging
import secrets

from app.mcp.authorization import WriteAuthority
from app.mcp.client import ToolClient
from app.mcp.transport import stdio_transport

logger = logging.getLogger(__name__)


async def connect_tools(settings):
    secret = secrets.token_bytes(32)
    host = ToolClient(stdio_transport(settings, secret), WriteAuthority(secret))
    try:
        async with asyncio.timeout(15):
            await host.__aenter__()
            await host.discover()
        return host
    except Exception as error:
        logger.warning("MCP unavailable at startup kind=%s", type(error).__name__)
        try:
            await host.__aexit__(None, None, None)
        except Exception:
            pass
        return None
