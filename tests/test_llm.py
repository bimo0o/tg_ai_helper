import asyncio
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from app.llm import LLMClient, parse_response


def response(text="Ответ", finish="stop"):
    return {
        "choices": [{"finish_reason": finish, "message": {"content": text}}],
        "usage": {"prompt_tokens": 12},
    }


def client(data=None, status=200, error=None):
    http = Mock(status=status, json=AsyncMock(return_value=data))
    context = Mock(__aenter__=AsyncMock(return_value=http), __aexit__=AsyncMock(return_value=False))
    if error:
        context.__aenter__.side_effect = error
    session = Mock(post=Mock(return_value=context))
    return LLMClient(session, "https://api.example/v1", "example-only"), session


async def test_success_parameters_usage_and_no_sensitive_logs(caplog):
    llm, session = client(response())
    with caplog.at_level("INFO", logger="app.llm"):
        result = await llm.generate([{"role": "user", "content": "private-dialogue"}], "model", 0.3)
    assert result.ok and result.text == "Ответ"
    assert result.usage == {"prompt_tokens": 12}
    payload = session.post.call_args.kwargs["json"]
    assert payload["messages"] == [{"role": "user", "content": "private-dialogue"}]
    assert payload["temperature"] == 0.3
    assert "plugins" not in payload
    assert "LLM start" in caplog.text and "LLM end" in caplog.text
    assert "private-dialogue" not in caplog.text and "example-only" not in caplog.text


@pytest.mark.parametrize(
    "error,kind", [(TimeoutError(), "timeout"), (aiohttp.ClientConnectionError(), "service")]
)
async def test_transport_error(error, kind):
    llm, _ = client(error=error)
    result = await llm.generate([], "model", 0.3)
    assert not result.ok and result.error_kind == kind and result.text == ""


@pytest.mark.parametrize("status", [301, 400, 401, 429, 500, 503])
async def test_http_errors(status):
    llm, _ = client(status=status)
    assert (await llm.generate([], "model", 0.3)).error_kind == "http"


@pytest.mark.parametrize(
    "data,kind",
    [
        (response("  "), "empty"),
        (response(None), "invalid"),
        ({}, "invalid"),
        (response(finish="length"), "incomplete"),
        ({"choices": []}, "invalid"),
    ],
)
def test_invalid_empty_and_incomplete_response(data, kind):
    assert parse_response(data).error_kind == kind


async def test_web_and_citations():
    data = response()
    data["choices"][0]["message"]["annotations"] = [
        {"url_citation": {"url": "https://docs.example/plugin"}},
        {"url_citation": {"url": "https://user:password@docs.example"}},
    ]
    llm, session = client(data)
    result = await llm.generate([], "model", 0.7, web=True)
    assert result.citations == ("https://docs.example/plugin",)
    assert session.post.call_args.kwargs["json"]["plugins"][0]["engine"] == "exa"


async def test_invalid_temperature_never_calls_api():
    llm, session = client()
    assert (await llm.generate([], "model", 0.4)).error_kind == "settings"
    session.post.assert_not_called()


async def test_cancellation_propagates():
    llm, _ = client(error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await llm.generate([], "model", 0.3)
