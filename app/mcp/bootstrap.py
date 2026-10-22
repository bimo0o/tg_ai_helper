"""Optional MCP startup. Failure disables tools without disabling ordinary conversation."""

import asyncio
import logging
import secrets
import time

from app.mcp.authorization import WriteAuthority
from app.mcp.client import ToolClient
from app.mcp.transport import stdio_transport

logger = logging.getLogger(__name__)


async def connect_tools(settings):
    secret = secrets.token_bytes(32)
    host = ToolClient(stdio_transport(settings, secret), WriteAuthority(secret))
    started = time.monotonic()
    try:
        async with asyncio.timeout(30):
            await host.__aenter__()
            logger.info("MCP connected duration_ms=%d", int((time.monotonic() - started) * 1000))
            listing = await host.discover()
            logger.info(
                "MCP discovery completed tools=%d duration_ms=%d",
                len(listing),
                int((time.monotonic() - started) * 1000),
            )
        return host
    except Exception as error:
        logger.warning("MCP unavailable at startup kind=%s", type(error).__name__)
        try:
            await host.__aexit__(None, None, None)
        except Exception:
            pass
        return None
