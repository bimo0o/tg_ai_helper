"""Trusted server-side owner setup; never grants access through Telegram."""

import argparse
import asyncio
import getpass

from app.config import ConfigError, Settings
from app.db import create_pool
from app.logging_setup import configure_logging
from app.operations import OperationsRepository
from app.schema import migrate


async def assign(settings: Settings, user_id: int) -> None:
    pool = await create_pool(settings)
    try:
        await migrate(pool)
        operations = OperationsRepository(pool)
        await operations.assign_owner(user_id)
        if not await operations.get_model(""):
            await operations.set_model(user_id, "deepseek")
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Assign the single bot owner in PostgreSQL")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    try:
        settings = Settings.load(args.env_file)
        configure_logging(settings)
        user_id = int(getpass.getpass("Ваш числовой Telegram user ID (ввод скрыт): ").strip())
        if not 0 < user_id < 2**63:
            raise ValueError
        asyncio.run(assign(settings, user_id))
    except ConfigError as exc:
        print(str(exc))
        return 1
    except ValueError:
        print("Нужен корректный ID; если владелец уже назначен, смена этой командой запрещена.")
        return 1
    except Exception:
        print("Не удалось назначить владельца. Проверьте PostgreSQL и применённую схему.")
        return 1
    print("Владелец записан в БД. Админ-панель доступна в личном чате: /admin.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
