import json

FIELDS = {
    "daw": "DAW",
    "os": "Операционная система",
    "genres": "Жанры",
    "plugins": "Плагины",
    "equipment": "Оборудование",
    "budget": "Бюджет",
    "level": "Опыт",
}
MAX_FIELD_CHARS = 240


class MusicProfiles:
    def __init__(self, pool, retention_days=180):
        self.pool = pool
        self.retention_days = retention_days

    async def get(self, user_id: int) -> dict[str, str]:
        value = await self.pool.fetchval(
            "UPDATE music_profiles SET last_used_at=now() WHERE user_id=$1 "
            "AND last_used_at >= now() - $2::integer * interval '1 day' RETURNING data",
            user_id,
            self.retention_days,
        )
        if value is None:
            return {}
        data = json.loads(value) if isinstance(value, str) else value
        if not isinstance(data, dict):
            return {}
        return {
            key: value
            for key, value in data.items()
            if key in FIELDS and isinstance(value, str) and 0 < len(value) <= MAX_FIELD_CHARS
        }

    async def set_field(self, user_id: int, field: str, value: str) -> None:
        value = value.strip()
        if field not in FIELDS or not 0 < len(value) <= MAX_FIELD_CHARS:
            raise ValueError("Invalid profile field or value.")
        await self.pool.execute(
            "INSERT INTO music_profiles (user_id, data) "
            "VALUES ($1, jsonb_build_object($2::text, $3::text)) "
            "ON CONFLICT (user_id) DO UPDATE SET data="
            "(CASE WHEN music_profiles.last_used_at < now() - $4::integer * interval '1 day' "
            "THEN '{}'::jsonb ELSE music_profiles.data END) || EXCLUDED.data, "
            "updated_at=now(), last_used_at=now()",
            user_id,
            field,
            value,
            self.retention_days,
        )

    async def clear(self, user_id: int) -> None:
        await self.pool.execute("DELETE FROM music_profiles WHERE user_id=$1", user_id)
