import copy
import json
import subprocess
import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from app.config import Settings
from app.mcp.authorization import META_KEY, AuthorizationError, WriteAuthority
from app.mcp.client import ToolClient, ToolUnavailable
from app.mcp.contracts import INPUTS, WeatherOutput
from app.mcp.server import create_server
from app.mcp.transport import stdio_transport
from app.tool_data import Schedule


def fixture_server():
    reminders = AsyncMock()
    reminders.execute.return_value = (
        {
            "id": UUID(int=1),
            "remind_at": datetime(2030, 1, 1, tzinfo=UTC),
            "timezone": "UTC",
        },
        True,
    )
    weather = AsyncMock()
    weather.current.return_value = WeatherOutput(status="not_found", places=[])
    authority = WriteAuthority(b"test-only-key-" * 3)
    schedule = Schedule({}, "UTC", clock=lambda: datetime(2027, 1, 1, tzinfo=UTC))
    server = create_server(reminders, weather, schedule, authority)
    return server, reminders, weather, authority


async def test_discovery_and_read_tools_travel_through_real_mcp_protocol():
    server, reminders, weather, authority = fixture_server()
    host = ToolClient(server, authority)
    tools = await host.discover()
    assert {tool.name for tool in tools} == set(INPUTS)
    assert all(tool.input_schema["additionalProperties"] is False for tool in tools)
    assert all(tool.output_schema for tool in tools)
    result = await host.call("get_weather", {"city": " Test City "})
    assert result["status"] == "not_found"
    weather.current.assert_awaited_once_with("Test City")
    assert (await host.call("get_schedule", {"date": "2027-01-01"}))["lessons"] == []
    week = await host.week()
    assert week["days"][0]["date"] == "2026-12-28"
    assert week["days"][-1]["date"] == "2027-01-03"
    reminders.execute.assert_not_awaited()


async def test_shared_client_keeps_one_connection_for_multiple_operations():
    server, _, _, authority = fixture_server()
    async with ToolClient(server, authority) as host:
        connection = host._client
        assert len(await host.discover()) == 3
        assert (await host.call("get_schedule", {"date": "2027-01-01"}))["lessons"] == []
        assert len((await host.week())["days"]) == 7
        assert host._client is connection
    assert host._client is None


async def test_write_needs_signed_context_outside_model_arguments():
    server, reminders, _, authority = fixture_server()
    arguments = {"text": "Synthetic", "remind_at": "2030-01-01T00:00:00+00:00"}
    host = ToolClient(server, authority)
    with pytest.raises(ToolUnavailable):
        await host.call("add_reminder", arguments)
    async with Client(server) as independent:
        result = await independent.call_tool("add_reminder", arguments)
        assert result.is_error
    reminders.execute.assert_not_awaited()
    action = uuid4()
    result = await host.call("add_reminder", arguments, owner=(1, 1), action_id=action)
    assert result["created"] is True and result["id"] == str(UUID(int=1))
    reminders.execute.assert_awaited_once_with((1, 1), action, **arguments)
    with pytest.raises(ToolUnavailable):
        await host.call("add_reminder", {**arguments, "user_id": 2}, owner=(1, 1), action_id=action)
    assert reminders.execute.await_count == 1


def test_signed_context_rejects_tampering_replay_after_expiry_and_other_secret():
    authority = WriteAuthority(b"a" * 32, clock=lambda: 1000)
    arguments = {"text": "Synthetic", "remind_at": "2030-01-01T00:00:00Z"}
    grant = authority.sign((1, 1), uuid4(), arguments)
    assert authority.verify(grant, arguments).user_id == 1
    for field, value in (("user_id", 2), ("expires_at", 999999), ("action_id", str(uuid4()))):
        changed = copy.deepcopy(grant)
        changed[META_KEY][field] = value
        with pytest.raises(AuthorizationError):
            authority.verify(changed, arguments)
    with pytest.raises(AuthorizationError):
        authority.verify(grant, {**arguments, "text": "Changed"})
    with pytest.raises(AuthorizationError):
        WriteAuthority(b"b" * 32, clock=lambda: 1000).verify(grant, arguments)
    with pytest.raises(AuthorizationError):
        WriteAuthority(b"a" * 32, clock=lambda: 1060).verify(grant, arguments)


@pytest.mark.parametrize(
    "tool,args",
    [
        ("get_weather", {"city": " "}),
        ("get_weather", {"city": "x" * 101}),
        ("get_weather", {"city": "Test", "url": "http://private.invalid"}),
        ("get_schedule", {"date": "2027-02-30"}),
        ("get_schedule", {"date": "20270101"}),
        ("add_reminder", {"text": "x", "remind_at": "2030-01-01T10:00:00"}),
    ],
)
async def test_invalid_input_is_rejected_by_independent_server(tool, args):
    server, reminders, weather, _ = fixture_server()
    async with Client(server) as client:
        # SDK can reject against discovered schemas before the request reaches the handler.
        try:
            result = await client.call_tool(tool, args)
        except Exception:
            pass
        else:
            assert result.is_error
    reminders.execute.assert_not_awaited()
    weather.current.assert_not_awaited()


async def test_server_failures_do_not_expose_user_values(caplog):
    server, reminders, weather, authority = fixture_server()
    weather.current.side_effect = RuntimeError("PRIVATE_CONTENT")
    with pytest.raises(ToolUnavailable):
        await ToolClient(server, authority).call("get_weather", {"city": "PRIVATE_CONTENT"})
    assert "PRIVATE_CONTENT" not in caplog.text


async def test_invalid_structured_output_is_rejected():
    server, _, weather, authority = fixture_server()
    weather.current.return_value = {"status": "ok", "places": [], "observation": None}
    with pytest.raises(ToolUnavailable):
        await ToolClient(server, authority).call("get_weather", {"city": "Test"})


async def test_stdio_subprocess_discovery_and_resource_without_external_apis():
    transport = StdioServerParameters(
        command=sys.executable, args=["-m", "tests.mcp_stdio_fixture"]
    )
    host = ToolClient(transport, WriteAuthority(b"test-only-key-" * 3))
    assert len(await host.discover()) == 3
    assert len((await host.week())["days"]) == 7
    assert (await host.call("get_schedule", {"date": "2027-01-01"}))["lessons"] == []
    async with Client(transport) as client:
        resources = await client.list_resources()
        assert str(resources.resources[0].uri) == "schedule://current-week"
        result = await client.read_resource("schedule://current-week")
        assert len(json.loads(result.contents[0].text)["days"]) == 7


def test_stdio_child_environment_does_not_receive_telegram_or_model_keys():
    settings = Settings(
        bot_token="private-token",
        postgres_password="test-database-only",
        llm_api_key="private-model-key",
        openrouter_management_key="private-management",
    )
    transport = stdio_transport(settings, b"x" * 32)
    assert set(transport.env) == {
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "MCP_SIGNING_KEY",
        "SCHEDULE_PATH",
        "SCHEDULE_TIMEZONE",
    }
    assert all("private-" not in value for value in transport.env.values())


async def test_startup_mcp_failure_is_optional_and_safely_logged(monkeypatch, caplog):
    from app.mcp import bootstrap

    host = AsyncMock()
    host.__aenter__.side_effect = RuntimeError("PRIVATE_CONFIGURATION")
    monkeypatch.setattr(bootstrap, "ToolClient", lambda *args: host)
    settings = Settings(bot_token="unused", postgres_password="test-only")
    assert await bootstrap.connect_tools(settings) is None
    host.__aexit__.assert_awaited_once()
    assert "PRIVATE_CONFIGURATION" not in caplog.text


def test_child_entrypoint_does_not_import_telegram_framework():
    code = (
        "import sys; import app.mcp.__main__; "
        'assert not any(name == "aiogram" or name.startswith("aiogram.") for name in sys.modules)'
    )
    subprocess.run([sys.executable, "-c", code], capture_output=True, check=True, timeout=30)
