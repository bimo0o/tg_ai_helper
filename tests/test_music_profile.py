from datetime import UTC, datetime, timedelta

import pytest

from app.music_profile import MusicProfiles
from tests.fakes import MemoryPool


async def test_profile_is_per_user_persistent_and_explicitly_removable():
    pool = MemoryPool()
    profiles = MusicProfiles(pool)
    await profiles.set_field(1, "daw", "Ableton Live")
    await profiles.set_field(1, "plugins", "Stock devices")
    await profiles.set_field(2, "daw", "Reaper")
    assert await MusicProfiles(pool).get(1) == {"daw": "Ableton Live", "plugins": "Stock devices"}
    await profiles.clear(1)
    assert await profiles.get(1) == {}
    assert await profiles.get(2) == {"daw": "Reaper"}


async def test_expired_profile_is_not_restored_by_read_or_partial_update():
    pool = MemoryPool()
    profiles = MusicProfiles(pool, 180)
    await profiles.set_field(1, "daw", "Old DAW")
    pool.profiles[1]["last_used_at"] = datetime.now(UTC) - timedelta(days=181)
    assert await profiles.get(1) == {}
    await profiles.set_field(1, "os", "Linux")
    assert await profiles.get(1) == {"os": "Linux"}


@pytest.mark.parametrize("field,value", [("unknown", "x"), ("daw", ""), ("daw", "x" * 241)])
async def test_invalid_profile_input_is_rejected_before_sql(field, value):
    pool = MemoryPool()
    with pytest.raises(ValueError):
        await MusicProfiles(pool).set_field(1, field, value)
    assert pool.calls == []
