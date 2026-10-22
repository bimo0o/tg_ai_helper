"""Read-only deployment check: no Telegram, model or weather requests, no reminder writes."""

import argparse
import asyncio
import json
import os
import time
from urllib.request import ProxyHandler, build_opener


async def run():
    from app.config import Settings
    from app.mcp.bootstrap import connect_tools

    # Process environment only; do not read an env file or touch bot/model credentials.
    settings = Settings(
        bot_token="unused",
        postgres_password=os.environ["POSTGRES_PASSWORD"],
        postgres_host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        postgres_port=int(os.environ.get("POSTGRES_PORT", "5432")),
        postgres_db=os.environ.get("POSTGRES_DB", "bot"),
        postgres_user=os.environ.get("POSTGRES_USER", "bot"),
        schedule_path=os.environ.get("SCHEDULE_PATH", "app/data/schedule.example.json"),
        schedule_timezone=os.environ.get("SCHEDULE_TIMEZONE", "UTC"),
    )
    started = time.monotonic()
    tools = await connect_tools(settings)
    if tools is None:
        print(json.dumps({"mcp_ready": False}))
        return 1
    try:
        print(
            json.dumps(
                {
                    "mcp_ready": True,
                    "tools": len(tools.discovered_tools),
                    "resources": 1,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            )
        )
        return 0
    finally:
        await tools.__aexit__(None, None, None)


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description="Read-only MCP readiness check")
        parser.add_argument(
            "--running", action="store_true", help="Probe the running bot's MCP session"
        )
        args = parser.parse_args()
        if args.running:
            port = int(os.environ.get("HEALTH_PORT", "8080"))
            with build_opener(ProxyHandler({})).open(
                f"http://127.0.0.1:{port}/mcp-ready", timeout=15
            ) as response:
                body = json.load(response)
            print(json.dumps(body))
            raise SystemExit(0 if body.get("mcp_ready") is True else 1)
        raise SystemExit(asyncio.run(run()))
    except Exception:
        print(json.dumps({"mcp_ready": False, "error": "probe_failed"}))
        raise SystemExit(1) from None
