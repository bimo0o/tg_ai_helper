import argparse
import asyncio
import logging

from aiogram import Dispatcher
from aiohttp import ClientSession

from app.admin import AdminService
from app.assistant import Assistant
from app.billing import BillingClient
from app.config import ConfigError, Settings
from app.db import create_pool
from app.handlers.admin import create_router as admin_router
from app.handlers.commands import create_router as commands_router
from app.handlers.interface import create_router as interface_router
from app.handlers.messages import create_router as messages_router
from app.handlers.music import create_router as music_router
from app.handlers.temperature import create_router as temperature_router
from app.health import HealthState, start_health_server
from app.llm import LLMClient
from app.logging_setup import configure_logging
from app.music_profile import MusicProfiles
from app.operations import OperationsRepository
from app.repository import Repository
from app.retention import maintenance
from app.schema import migrate
from app.telegram import create_bot
from app.temperature import TemperatureRepository, TemperatureService

logger = logging.getLogger("app")


async def run(settings: Settings) -> None:
    state = HealthState()
    bot = create_bot(settings)
    runner = None
    llm_session = None
    maintenance_task = None
    try:
        state.pool = await create_pool(settings)
        logger.info("PostgreSQL подключён: SELECT 1 выполнен.")
        # Начальная проверка токена и маршрута через прокси ограничена по времени.
        async with asyncio.timeout(30):
            me = await bot.get_me()
            webhook = await bot.get_webhook_info()
        if webhook.url:
            raise ConfigError(
                "У бота установлен webhook. Удалите его перед запуском polling "
                "или используйте отдельного учебного бота."
            )
        logger.info("Telegram доступен. Бот @%s запускает polling.", me.username)
        await migrate(state.pool)
        maintenance_task = asyncio.create_task(maintenance(state.pool, settings))
        llm_session = ClientSession()
        repository = Repository(state.pool, retention_days=settings.history_retention_days)
        operations = OperationsRepository(state.pool)
        profiles = MusicProfiles(state.pool, settings.music_profile_retention_days)
        assistant = Assistant(
            repository,
            LLMClient(
                llm_session,
                settings.llm_base_url,
                settings.llm_api_key,
                settings.llm_timeout_seconds,
            ),
            settings,
            operations,
            profiles,
        )
        admin = AdminService(
            operations,
            BillingClient(
                llm_session,
                settings.llm_base_url,
                settings.llm_api_key,
                settings.openrouter_management_key,
            ),
            settings.llm_model,
            settings=settings,
        )
        dispatcher = Dispatcher()
        temperature = TemperatureService(
            TemperatureRepository(state.pool), operations, assistant.llm, settings
        )
        dispatcher.include_router(temperature_router())
        dispatcher.include_router(admin_router())
        dispatcher.include_router(interface_router())
        dispatcher.include_router(music_router())
        dispatcher.include_router(commands_router())
        dispatcher.include_router(messages_router())
        runner = await start_health_server(state, settings.health_port)
        state.polling_task = asyncio.create_task(
            dispatcher.start_polling(
                bot,
                db=state.pool,
                repository=repository,
                assistant=assistant,
                settings=settings,
                operations=operations,
                admin=admin,
                profiles=profiles,
                temperature=temperature,
                allowed_updates=dispatcher.resolve_used_update_types(),
                close_bot_session=False,
            )
        )
        state.initialized = True
        await state.polling_task
    finally:
        state.initialized = False
        if state.polling_task and not state.polling_task.done():
            state.polling_task.cancel()
            await asyncio.gather(state.polling_task, return_exceptions=True)
        if maintenance_task:
            maintenance_task.cancel()
            await asyncio.gather(maintenance_task, return_exceptions=True)
        if runner:
            await runner.cleanup()
        if llm_session:
            await llm_session.close()
        await bot.session.close()
        if state.pool:
            try:
                async with asyncio.timeout(10):
                    await state.pool.close()
            except TimeoutError:
                state.pool.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-ассистент студента")
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args()
    try:
        settings = Settings.load(args.env_file)
        settings.validate_llm()
    except ConfigError as exc:
        print(str(exc))
        return 1
    configure_logging(settings)
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        logger.info("Бот остановлен.")
    except Exception:
        logger.exception("Не удалось запустить бот. Проверьте БД, токен и прокси.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
