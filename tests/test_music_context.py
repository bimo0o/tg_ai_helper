from contextlib import asynccontextmanager

from app.music_profile import MusicProfiles
from app.operations import OperationsRepository
from tests.test_assistant import assistant


async def test_search_uses_general_prompt_when_music_mode_changes_before_lock(monkeypatch):
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
    assert reply.ok
    payload = http.post.call_args.kwargs["json"]
    from app.prompts import instruction_messages

    assert payload["messages"][0]["content"] == instruction_messages("web", 1500)[0]["content"]
    assert payload["plugins"][0]["id"] == "web"
    assert all(row["mode"] == "web" for row in pool.messages)
    assert (await service.repository.get_settings((1, 1))).mode == "study"
    event = next(iter(pool.events.values()))
    assert event["error_kind"] is None and event["mode"] == "web"


async def test_music_profile_survives_reset_and_does_not_leak_to_other_modes_or_users():
    service, pool, http = assistant()
    service.profiles = MusicProfiles(pool)
    await service.profiles.set_field(1, "daw", "Ableton Live")
    await service.respond((1, 1), "study question")
    assert "Ableton Live" not in str(http.post.call_args.kwargs["json"]["messages"][1:])
    await service.repository.set_mode((1, 1), "music")
    await service.respond((1, 1), "music question")
    messages = http.post.call_args.kwargs["json"]["messages"]
    assert messages[1]["role"] == "user" and "Ableton Live" in messages[1]["content"]
    await service.repository.clear_history((1, 1))
    await service.respond((1, 1), "new question")
    assert "Ableton Live" in str(http.post.call_args.kwargs["json"]["messages"][1:])
    await service.repository.set_mode((2, 2), "music")
    await service.respond((2, 2), "other user")
    assert "Ableton Live" not in str(http.post.call_args.kwargs["json"]["messages"][1:])


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
