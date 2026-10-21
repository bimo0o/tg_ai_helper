from dataclasses import replace
from decimal import Decimal

import pytest

from app.music_profile import MusicProfiles
from app.operations import OperationsRepository
from app.prompts import SYSTEM_PROMPTS
from tests.test_assistant import assistant
from tests.test_commands import dispatch
from tests.test_llm import response


async def test_chat_does_not_search_or_include_music_profile():
    service, pool, http = assistant()
    service.profiles = MusicProfiles(pool)
    await service.profiles.set_field(1, "plugins", "private plugin collection")
    await service.repository.set_mode((1, 1), "chat")
    assert (await service.respond((1, 1), "Explain photosynthesis")).ok
    payload = http.post.call_args.kwargs["json"]
    assert "plugins" not in payload
    assert payload["messages"][0]["content"] == SYSTEM_PROMPTS["chat"]
    assert "private plugin collection" not in str(payload)


async def test_web_searches_each_turn_preserves_sources_and_isolates_profile():
    data = response("Answer")
    data["choices"][0]["message"]["annotations"] = [
        {"url_citation": {"url": "https://example.org/source"}}
    ]
    service, pool, http = assistant(data)
    service.operations = OperationsRepository(pool)
    service.profiles = MusicProfiles(pool)
    await service.profiles.set_field(1, "daw", "private preference")
    await service.repository.set_mode((1, 1), "web")
    first = await service.respond((1, 1), "Find current science news")
    second = await service.respond((1, 1), "Explain the first discovery")
    assert first.ok and second.ok
    assert "https://example.org/source" in second.text
    for call in http.post.call_args_list:
        payload = call.kwargs["json"]
        assert payload["plugins"][0]["id"] == "web"
        assert payload["messages"][0]["content"] == SYSTEM_PROMPTS["web"]
        assert "private preference" not in str(payload)
    assert len(http.post.call_args.kwargs["json"]["messages"]) == 4
    assert all(row["mode"] == "web" for row in pool.events.values())


@pytest.mark.parametrize("mode", ["study", "translate", "chat"])
async def test_one_off_search_has_general_context_and_preserves_selected_mode(mode):
    service, pool, http = assistant()
    await service.repository.set_mode((1, 1), mode)
    await service.repository.add_message((1, 1), "user", "private mode history", mode)
    await service.repository.add_message((1, 1), "assistant", "old answer", mode)
    assert (await service.respond((1, 1), "Find travel information", use_web=True)).ok
    payload = http.post.call_args.kwargs["json"]
    assert payload["messages"][0]["content"] == SYSTEM_PROMPTS["web"]
    assert "private mode history" not in str(payload)
    assert payload["plugins"][0]["id"] == "web"
    assert (await service.repository.get_settings((1, 1))).mode == mode
    assert pool.messages[-1]["mode"] == "web"


async def test_disabled_web_mode_never_calls_api_and_releases_reservation():
    service, pool, http = assistant()
    service.operations = OperationsRepository(pool)
    service.settings = replace(service.settings, music_web_search=False)
    await service.repository.set_mode((1, 1), "web")
    reply = await service.respond((1, 1), "Search question")
    assert not reply.ok and "отключён" in reply.text
    http.post.assert_not_called()
    event = next(iter(pool.events.values()))
    assert event["reserved_usd"] == event["cost_usd"] == Decimal("0")


async def test_web_mode_has_no_local_budget_or_rate_rejections():
    service, pool, http = assistant()
    service.operations = OperationsRepository(pool)
    await service.repository.set_mode((1, 1), "web")
    for _ in range(4):
        assert (await service.respond((1, 1), "search question")).ok
    assert http.post.call_count == 4
    assert all(row["reserved_usd"] == 0 for row in pool.events.values())


@pytest.mark.parametrize("mode", ["chat", "web"])
async def test_general_mode_command_persists_and_reset_preserves_mode(mode):
    bot, repo = await dispatch("/" + mode)
    assert (await repo.get_settings((1, 1))).mode == mode
    assert "История очищена" in bot.session.call_args.args[1].text
    await dispatch("/reset", repo.pool)
    assert (await repo.get_settings((1, 1))).mode == mode
