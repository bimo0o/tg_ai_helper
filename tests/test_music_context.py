from contextlib import asynccontextmanager
from decimal import Decimal

from app.music_profile import MusicProfiles
from app.operations import OperationsRepository
from tests.test_assistant import assistant


async def test_search_rejects_mode_changed_after_snapshot_without_api_cost(monkeypatch):
    service, pool, http = assistant()
    await service.repository.set_mode((1, 1), "music")
    service.operations = OperationsRepository(pool)
    original_locked = service.repository.locked

    @asynccontextmanager
    async def changed_mode(owner):
        async with original_locked(owner) as repository:
            await repository.set_mode(owner, "study")
            yield repository

    monkeypatch.setattr(service.repository, "locked", changed_mode)
    reply = await service.respond((1, 1), "find a plugin", use_web=True)
    assert not reply.ok and reply.error_kind == "input"
    assert reply.text == "Для поиска сначала включите /music."
    http.post.assert_not_called()
    assert pool.messages == []
    event = next(iter(pool.events.values()))
    assert event["error_kind"] == "input"
    assert event["cost_usd"] == event["reserved_usd"] == Decimal("0")


async def test_music_profile_survives_reset_and_does_not_leak_to_other_modes_or_users():
    service, pool, http = assistant()
    service.profiles = MusicProfiles(pool)
    await service.profiles.set_field(1, "daw", "Ableton Live")
    await service.respond((1, 1), "study question")
    assert "Ableton Live" not in str(http.post.call_args.kwargs["json"]["messages"])
    await service.repository.set_mode((1, 1), "music")
    await service.respond((1, 1), "music question")
    messages = http.post.call_args.kwargs["json"]["messages"]
    assert messages[1]["role"] == "user" and "Ableton Live" in messages[1]["content"]
    await service.repository.clear_history((1, 1))
    await service.respond((1, 1), "new question")
    assert "Ableton Live" in str(http.post.call_args.kwargs["json"]["messages"])
    await service.repository.set_mode((2, 2), "music")
    await service.respond((2, 2), "other user")
    assert "Ableton Live" not in str(http.post.call_args.kwargs["json"]["messages"])


async def test_profile_never_displaces_current_request_or_exceeds_context_budget():
    service, pool, http = assistant()
    service.profiles = MusicProfiles(pool)
    await service.profiles.set_field(1, "daw", "Ableton Live")
    await service.repository.set_mode((1, 1), "music")
    text = "x" * 8000
    await service.respond((1, 1), text)
    messages = http.post.call_args.kwargs["json"]["messages"]
    assert len(messages) == 2 and messages[-1]["content"] == text
    await service.respond((1, 1), "short")
    messages = http.post.call_args.kwargs["json"]["messages"]
    assert sum(len(message["content"]) for message in messages[1:]) <= 8000
