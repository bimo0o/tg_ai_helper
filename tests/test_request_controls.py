from dataclasses import replace
from decimal import Decimal

from app.assistant import LIMIT_MESSAGES
from app.operations import OperationsRepository
from tests.test_assistant import assistant


async def test_user_budget_rejects_before_second_api_call():
    service, pool, http = assistant()
    service.operations = OperationsRepository(pool)
    service.settings = replace(service.settings, user_daily_budget_usd=Decimal("0.05"))
    assert (await service.respond((1, 1), "one")).ok
    reply = await service.respond((1, 1), "two")
    assert reply.text == LIMIT_MESSAGES["user_budget"]
    assert http.post.call_count == 1 and len(pool.events) == 1


async def test_music_does_not_search_until_explicitly_requested():
    service, _, http = assistant()
    await service.repository.set_mode((1, 1), "music")
    await service.respond((1, 1), "Как настроить компрессор?")
    assert "plugins" not in http.post.call_args.kwargs["json"]
    await service.respond((1, 1), "Найди документацию плагина", use_web=True)
    assert http.post.call_args.kwargs["json"]["plugins"][0]["id"] == "web"


async def test_search_requires_music_and_respects_global_switch():
    service, _, http = assistant()
    assert (await service.respond((1, 1), "query", use_web=True)).text == (
        "Для поиска сначала включите /music."
    )
    await service.repository.set_mode((1, 1), "music")
    service.settings = replace(service.settings, music_web_search=False)
    assert (await service.respond((1, 1), "query", use_web=True)).text == (
        "Web-поиск отключён в настройках бота."
    )
    http.post.assert_not_called()
