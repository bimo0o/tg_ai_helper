"""Concurrency assertions need real PostgreSQL, never an in-memory SQL imitation."""

import asyncio
import os
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from app.agent import Agent
from app.assistant import Assistant
from app.db import create_pool
from app.llm import Generation, ToolCall
from app.mcp.authorization import WriteAuthority
from app.mcp.client import ToolClient
from app.mcp.contracts import INPUTS
from app.operations import OperationsRepository
from app.reminders import Reminders, ReminderUnavailable
from app.repository import Repository
from app.retention import purge
from app.schema import migrate
from tests.test_integration import database as database

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="Requires Docker opt-in"),
]


async def test_confirmation_is_durable_isolated_and_idempotent(database):
    settings, *_ = database
    pool = await create_pool(settings)
    now = datetime.now(UTC)
    when = (now + timedelta(hours=1)).isoformat()
    service = Reminders(pool, clock=lambda: now)
    try:
        await migrate(pool)
        await service.set_timezone(1, "UTC")
        assert await service.get_timezone(1) == "UTC"
        assert await service.get_timezone(2) is None
        proposals = await asyncio.gather(
            *(
                service.propose((1, 1), "update:1", "Synthetic reminder", when, "UTC")
                for _ in range(8)
            )
        )
        action = proposals[0]
        assert len({row["id"] for row in proposals}) == 1
        assert await pool.fetchval("SELECT count(*) FROM reminders") == 0
        for owner in ((2, 2), (1, 2)):
            with pytest.raises(ReminderUnavailable):
                await service.confirm(owner, action["id"])
            assert not await service.dismiss(owner, action["id"])
        with pytest.raises(ReminderUnavailable):
            await service.execute((1, 1), action["id"], action["text"], when)
        await service.confirm((1, 1), action["id"])
        for text, date in (
            ("Modified", when),
            (action["text"], (now + timedelta(hours=2)).isoformat()),
        ):
            with pytest.raises(ReminderUnavailable):
                await service.execute((1, 1), action["id"], text, date)
        # New repository instance models a restarted host after persisted confirmation.
        restored = Reminders(pool, clock=lambda: now)
        results = await asyncio.gather(
            *(restored.execute((1, 1), action["id"], action["text"], when) for _ in range(8))
        )
        assert sum(created for _, created in results) == 1
        assert len({row["id"] for row, _ in results}) == 1
        assert len(await restored.list((1, 1))) == 1
        assert await restored.list((2, 2)) == []
        assert not await restored.cancel((2, 2), results[0][0]["id"])
        assert await restored.cancel((1, 1), results[0][0]["id"])
        assert not await restored.cancel((1, 1), results[0][0]["id"])
    finally:
        await pool.close()


async def test_expiry_and_delivery_claims_do_not_create_or_resend_duplicates(database):
    settings, *_ = database
    pool = await create_pool(settings)
    now = datetime.now(UTC)
    service = Reminders(pool, clock=lambda: now)

    async def create(key):
        when = (now + timedelta(hours=1)).isoformat()
        action = await service.propose((1, 1), key, "Synthetic", when, "UTC")
        await service.confirm((1, 1), action["id"])
        row, _ = await service.execute((1, 1), action["id"], "Synthetic", when)
        return row

    try:
        await migrate(pool)
        expired = await service.propose(
            (1, 1), "expired", "Synthetic", (now + timedelta(hours=1)).isoformat(), "UTC"
        )
        service.clock = lambda: now + timedelta(minutes=6)
        with pytest.raises(ReminderUnavailable):
            await service.confirm((1, 1), expired["id"])
        service.clock = lambda: now
        a = await create("a")
        b = await create("b")
        old = await create("old")
        await pool.execute(
            "UPDATE reminders SET remind_at=$1 WHERE id=ANY($2::uuid[])",
            now - timedelta(seconds=2),
            [a["id"], b["id"]],
        )
        await pool.execute(
            "UPDATE reminders SET remind_at=$1 WHERE id=$2", now - timedelta(days=2), old["id"]
        )
        claims = [row for row in await asyncio.gather(*(service.claim() for _ in range(8))) if row]
        assert {row["id"] for row in claims} == {a["id"], b["id"]}
        assert len(claims) == 2
        assert not await service.cancel((1, 1), a["id"])
        first, second = claims
        assert not await service.finish(first["id"], uuid4(), "sent")
        assert await service.finish(first["id"], first["claim_token"], "sent")
        assert not await service.finish(first["id"], first["claim_token"], "sent")
        service.clock = lambda: now + timedelta(minutes=3)
        assert await service.claim() is None
        assert not await service.finish(second["id"], second["claim_token"], "sent")
        assert await pool.fetchval("SELECT status FROM reminders WHERE id=$1", second["id"]) == (
            "unconfirmed"
        )
        assert (
            await pool.fetchval("SELECT status FROM reminders WHERE id=$1", old["id"]) == "missed"
        )
    finally:
        await pool.close()


async def test_retention_preserves_active_reminder_and_removes_finished_text(database):
    settings, *_ = database
    pool = await create_pool(settings)
    now = datetime.now(UTC)
    service = Reminders(pool, clock=lambda: now)
    try:
        await migrate(pool)
        ids = []
        for key in ("active", "finished"):
            when = (now + timedelta(days=100)).isoformat()
            action = await service.propose((1, 1), key, "Synthetic text", when, "UTC")
            await service.confirm((1, 1), action["id"])
            row, _ = await service.execute((1, 1), action["id"], "Synthetic text", when)
            ids.append(row["id"])
        await pool.execute("UPDATE reminder_actions SET created_at=now()-interval '31 days'")
        assert await service.cancel((1, 1), ids[1])
        await pool.execute(
            "UPDATE reminders SET finished_at=now()-interval '31 days' WHERE id=$1", ids[1]
        )
        await pool.execute(
            "INSERT INTO tool_audit (chat_id,user_id,tool,outcome,duration_ms,created_at) "
            "VALUES (1,1,'add_reminder','success',5,now()-interval '91 days')"
        )
        await purge(pool, settings)
        assert await pool.fetchval("SELECT count(*) FROM reminders") == 1
        assert await pool.fetchval("SELECT id FROM reminders") == ids[0]
        assert await pool.fetchval("SELECT count(*) FROM reminder_actions") == 1
        assert await pool.fetchval("SELECT count(*) FROM tool_audit") == 0
    finally:
        await pool.close()


async def test_actual_stdio_server_writes_only_confirmed_action_to_postgres(database, tmp_path):
    settings, *_ = database
    pool = await create_pool(settings)
    source = tmp_path / "synthetic.json"
    source.write_text("{}", encoding="utf-8")
    secret = b"integration-test-only-key-" * 2
    transport = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp"],
        env={
            "POSTGRES_HOST": settings.postgres_host,
            "POSTGRES_PORT": str(settings.postgres_port),
            "POSTGRES_DB": settings.postgres_db,
            "POSTGRES_USER": settings.postgres_user,
            "POSTGRES_PASSWORD": settings.postgres_password,
            "MCP_SIGNING_KEY": secret.hex(),
            "SCHEDULE_PATH": str(source),
            "SCHEDULE_TIMEZONE": "UTC",
        },
    )
    try:
        await migrate(pool)
        service = Reminders(pool)
        when = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        action = await service.propose((1, 1), "stdio", "Synthetic", when, "UTC")
        arguments = {"text": action["text"], "remind_at": when}
        async with Client(transport) as independent:
            assert (await independent.call_tool("add_reminder", arguments)).is_error
        assert await pool.fetchval("SELECT count(*) FROM reminders") == 0
        async with ToolClient(transport, WriteAuthority(secret)) as host:
            assert len(await host.discover()) == 3
            assert len((await host.week())["days"]) == 7
            from app.mcp.client import ToolUnavailable

            with pytest.raises(ToolUnavailable):
                await host.call("add_reminder", arguments, owner=(1, 1), action_id=action["id"])
            await service.confirm((1, 1), action["id"])
            results = await asyncio.gather(
                *(
                    host.call("add_reminder", arguments, owner=(1, 1), action_id=action["id"])
                    for _ in range(4)
                )
            )
            assert sum(result["created"] for result in results) == 1
            assert len({result["id"] for result in results}) == 1
        assert await pool.fetchval("SELECT count(*) FROM reminders") == 1
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.mcp_probe",
            env={**os.environ, **transport.env},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        async with asyncio.timeout(45):
            stdout, _ = await process.communicate()
        import json

        assert process.returncode == 0
        assert json.loads(stdout)["mcp_ready"] is True
        assert await pool.fetchval("SELECT count(*) FROM reminders") == 1
    finally:
        await pool.close()


async def test_agent_uses_bound_connection_and_accounts_each_round_even_with_unknown_cost(database):
    settings, *_ = database
    pool = await asyncpg.create_pool(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        min_size=1,
        max_size=1,
        command_timeout=5,
    )
    try:
        await migrate(pool)
        reminders = Reminders(pool)
        await reminders.set_timezone(1, "UTC")
        tools = AsyncMock()
        tools.discovered_tools = [
            SimpleNamespace(name=name, description=name, input_schema=model.model_json_schema())
            for name, model in INPUTS.items()
        ]
        tools.call.return_value = {"date": "2030-01-01", "lessons": []}
        llm = AsyncMock()
        llm.generate.side_effect = [
            Generation(
                True,
                tool_calls=(ToolCall("a", "get_schedule", {"date": "2030-01-01"}),),
                cost_usd=Decimal("0.001"),
                usage={"prompt_tokens": 10},
            ),
            Generation(True, "No lessons", cost_usd=None, usage={"prompt_tokens": 20}),
        ]
        operations = OperationsRepository(pool)
        assistant = Assistant(
            Repository(pool),
            llm,
            replace(settings, llm_model="fixture-model"),
            operations,
            agent=Agent(llm, tools, reminders),
        )
        async with asyncio.timeout(10):
            reply = await assistant.respond(
                (1, 1), "Synthetic schedule question", source_key="telegram:1"
            )
        assert reply.ok and reply.text == "No lessons"
        summary = await operations.summary(datetime(1970, 1, 1, tzinfo=UTC))
        assert summary["requests"] == 2 and summary["priced"] == 1
        assert summary["cost_usd"] == Decimal("0.001")
        assert summary["prompt_tokens"] == 30
        decision = await reminders.last_decision((1, 1))
        assert decision["tool"] == "get_schedule" and decision["status"] == "success"
        assert await reminders.last_decision((2, 2)) is None
    finally:
        await pool.close()
