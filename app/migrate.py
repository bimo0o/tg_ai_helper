"""Apply database migrations without creating a Telegram or LLM client."""

import argparse
import asyncio

from app.config import ConfigError, Settings
from app.db import create_pool
from app.logging_setup import configure_logging
from app.schema import migrate


async def apply(settings: Settings) -> None:
    pool = await create_pool(settings)
    try:
        await migrate(pool)
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply database migrations")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    try:
        settings = Settings.load(args.env_file)
        configure_logging(settings)
        asyncio.run(apply(settings))
    except ConfigError as exc:
        print(str(exc))
        return 1
    except Exception:
        print("Не удалось применить миграции. Проверьте доступность и настройки PostgreSQL.")
        return 1
    print("Миграции применены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
