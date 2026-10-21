import asyncio
from decimal import Decimal
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
    assert payload["max_tokens"] == 1500
    assert "plugins" not in payload
    assert "LLM start" in caplog.text and "LLM end" in caplog.text
    assert "private-dialogue" not in caplog.text and "example-only" not in caplog.text


@pytest.mark.parametrize("model", ["deepseek/deepseek-v3.2", "deepseek/deepseek-v4.1-flash"])
async def test_openrouter_deepseek_disables_reasoning_and_prioritizes_throughput(model):
    _, session = client(response())
    llm = LLMClient(session, "https://openrouter.ai/api/v1", "example-only")
    assert (await llm.generate([], model, 0.3)).ok
    payload = session.post.call_args.kwargs["json"]
    assert payload["reasoning"] == {"enabled": False}
    assert payload["provider"] == {"require_parameters": True, "sort": "throughput"}
    assert payload["max_tokens"] == 1500


async def test_reasoning_control_is_not_sent_to_unrelated_model_or_endpoint():
    llm, session = client(response())
    await llm.generate([], "deepseek/deepseek-v3.2", 0.3)
    assert "reasoning" not in session.post.call_args.kwargs["json"]
    assert "sort" not in session.post.call_args.kwargs["json"]["provider"]
    llm = LLMClient(session, "https://openrouter.ai/api/v1", "example-only")
    await llm.generate([], "qwen/qwen3-235b-a22b-2507", 0.3)
    assert "reasoning" not in session.post.call_args.kwargs["json"]


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
        (response("", finish="length"), "incomplete"),
        ({"choices": []}, "invalid"),
    ],
)
def test_invalid_empty_and_incomplete_response(data, kind):
    assert parse_response(data).error_kind == kind


def test_length_limit_preserves_nonempty_answer_and_marks_it_as_partial():
    result = parse_response(response("Начало полезного ответа", finish="length"))
    assert result.ok and result.truncated and result.finish_reason == "length"
    assert result.text == "Начало полезного ответа"


async def test_diagnostic_logs_include_status_and_finish_but_not_answer(caplog):
    llm, _ = client(response("private answer", finish="length"))
    with caplog.at_level("INFO", logger="app.llm"):
        await llm.generate([], "model", 0.3)
    assert "result=truncated" in caplog.text
    assert "http_status=200 finish_reason=length" in caplog.text
    assert "private answer" not in caplog.text


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


@pytest.mark.parametrize("finish", ["stop", "length"])
def test_actual_provider_cost_is_preserved_even_for_incomplete_answer(finish):
    data = response(finish=finish)
    data["usage"]["cost"] = 0.001234
    result = parse_response(data)
    assert result.cost_usd == Decimal("0.001234")
    assert result.usage == {"prompt_tokens": 12}


@pytest.mark.parametrize("cost", [None, -1, True, "nan", "invalid"])
def test_missing_or_invalid_cost_remains_unknown(cost):
    data = response()
    data["usage"]["cost"] = cost
    assert parse_response(data).cost_usd is None


async def test_output_limit_is_applied_and_invalid_limit_never_calls_api():
    llm, session = client(response())
    await llm.generate([], "model", 0.3, max_output_tokens=256)
    assert session.post.call_args.kwargs["json"]["max_tokens"] == 256
    session.post.reset_mock()
    assert not (await llm.generate([], "model", 0.3, max_output_tokens=0)).ok
    session.post.assert_not_called()
