from dataclasses import replace

from app.assistant import ERROR_MESSAGE, Assistant
from app.config import Settings
from app.prompts import SYSTEM_PROMPTS
from app.repository import Repository
from tests.fakes import MemoryPool
from tests.test_llm import client, response


def assistant(data=None, **kwargs):
    pool = MemoryPool()
    llm, session = client(response() if data is None else data, **kwargs)
    settings = Settings(bot_token="unused", postgres_password="unused", llm_model="test-model")
    return Assistant(Repository(pool), llm, settings), pool, session


async def test_regular_message_uses_active_prompt_and_exact_request_parameters():
    service, pool, http = assistant()
    await service.repository.set_mode((1, 1), "music")
    await service.repository.set_temperature((1, 1), 0.7)
    result = await service.respond((1, 1), "Как записать бас?")
    assert result.ok and result.text == "Ответ"
    payload = http.post.call_args.kwargs["json"]
    assert payload["messages"] == [
        {"role": "system", "content": SYSTEM_PROMPTS["music"]},
        {"role": "user", "content": "Как записать бас?"},
    ]
    assert payload["temperature"] == 0.7 and payload["model"] == "test-model"
    assert "plugins" in payload
    assert [row["role"] for row in pool.messages] == ["user", "assistant"]


async def test_context_is_restored_for_same_user_but_never_for_another():
    service, pool, http = assistant()
    await service.respond((1, 1), "первый")
    restored = Assistant(Repository(pool), service.llm, service.settings)
    await restored.respond((2, 2), "другой")
    assert len(http.post.call_args.kwargs["json"]["messages"]) == 2
    await restored.respond((1, 1), "второй")
    assert http.post.call_args.kwargs["json"]["messages"][1:] == [
        {"role": "user", "content": "первый"},
        {"role": "assistant", "content": "Ответ"},
        {"role": "user", "content": "второй"},
    ]


async def test_current_request_reserved_and_oldest_pair_removed():
    service, _, http = assistant(response("ok"))
    service.settings = replace(service.settings, history_max_messages=4, history_max_chars=10)
    await service.respond((1, 1), "one")
    await service.respond((1, 1), "two")
    await service.respond((1, 1), "three")
    messages = http.post.call_args.kwargs["json"]["messages"]
    assert messages[0]["content"] == SYSTEM_PROMPTS["study"]
    assert messages[1:] == [
        {"role": "user", "content": "two"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "three"},
    ]
    assert sum(len(m["content"]) for m in messages[1:]) == 10


async def test_request_over_limit_rejected_before_api_and_database():
    service, pool, http = assistant()
    reply = await service.respond((1, 1), "x" * 8001)
    assert reply.text == "Сообщение слишком длинное. Максимум — 8000 символов."
    assert not reply.ok
    assert pool.calls == []
    http.post.assert_not_called()


async def test_llm_error_is_safe_and_only_user_message_is_saved():
    service, pool, _ = assistant(error=TimeoutError("https://user:example@host private prompt"))
    reply = await service.respond((1, 1), "вопрос")
    assert reply.text == ERROR_MESSAGE
    assert not reply.ok and reply.error_kind == "timeout"
    assert [row["role"] for row in pool.messages] == ["user"]


async def test_empty_llm_response_is_never_saved_or_returned_as_empty():
    service, pool, _ = assistant(response(""))
    reply = await service.respond((1, 1), "вопрос")
    assert reply.text == ERROR_MESSAGE
    assert [row["role"] for row in pool.messages] == ["user"]
