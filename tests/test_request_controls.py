from dataclasses import replace
from decimal import Decimal

from app.operations import OperationsRepository
from tests.test_assistant import assistant


async def test_old_unknown_reservations_do_not_block_new_api_calls():
    service, pool, http = assistant()
    service.operations = OperationsRepository(pool)
    assert (await service.respond((1, 1), "one")).ok
    next(iter(pool.events.values()))["reserved_usd"] = Decimal("2")
    reply = await service.respond((1, 1), "two")
    assert reply.ok
    assert http.post.call_count == 2 and len(pool.events) == 2


async def test_music_does_not_search_until_explicitly_requested():
    service, _, http = assistant()
    await service.repository.set_mode((1, 1), "music")
    await service.respond((1, 1), "Как настроить компрессор?")
    assert "plugins" not in http.post.call_args.kwargs["json"]
    await service.respond((1, 1), "Найди документацию плагина", use_web=True)
    assert http.post.call_args.kwargs["json"]["plugins"][0]["id"] == "web"


async def test_search_works_outside_music_and_respects_global_switch():
    service, _, http = assistant()
    assert (await service.respond((1, 1), "query", use_web=True)).ok
    assert http.post.call_args.kwargs["json"]["plugins"][0]["id"] == "web"
    http.post.reset_mock()
    await service.repository.set_mode((1, 1), "music")
    service.settings = replace(service.settings, music_web_search=False)
    assert (await service.respond((1, 1), "query", use_web=True)).text == (
        "Web-поиск отключён в настройках бота."
    )
    http.post.assert_not_called()
