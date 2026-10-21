"""Единственное место чтения и проверки настроек приложения."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from aiogram.utils.token import TokenValidationError, validate_token
from dotenv import dotenv_values


class ConfigError(ValueError):
    """Ошибка настройки без секретных значений в сообщении."""


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(repr=False)
    postgres_password: str = field(repr=False)
    telegram_proxy_url: str = field(default="", repr=False)
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "bot"
    postgres_user: str = "bot"
    log_level: str = "INFO"
    health_port: int = 8080
    llm_base_url: str = field(default="", repr=False)
    llm_api_key: str = field(default="", repr=False)
    openrouter_management_key: str = field(default="", repr=False)
    llm_model: str = ""
    llm_timeout_seconds: int = 30
    history_max_messages: int = 40
    history_max_chars: int = 8000
    music_web_search: bool = True

    def validate_llm(self) -> None:
        for name in ("llm_base_url", "llm_api_key", "llm_model"):
            if not getattr(self, name).strip():
                raise ConfigError(f"{name.upper()}: обязательная настройка не задана.")
        try:
            url = urlsplit(self.llm_base_url)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError
            _ = url.port
        except ValueError:
            raise ConfigError("LLM_BASE_URL: нужен HTTPS URL без учётных данных.") from None
        if not 2 <= self.history_max_messages <= 10000:
            raise ConfigError("HISTORY_MAX_MESSAGES: нужно число от 2 до 10000.")
        if not 1 <= self.history_max_chars <= 1000000:
            raise ConfigError("HISTORY_MAX_CHARS: нужно число от 1 до 1000000.")
        if not 1 <= self.llm_timeout_seconds <= 120:
            raise ConfigError("LLM_TIMEOUT_SECONDS: нужно число от 1 до 120.")

    @classmethod
    def load(
        cls, env_file: Path | str = ".env", *, environ: Mapping[str, str] | None = None
    ) -> "Settings":
        values = {
            **dotenv_values(env_file, interpolate=False),
            **(os.environ if environ is None else environ),
        }

        def value(key: str, default: str = "") -> str:
            return values.get(key) or default

        def port(key: str, default: str) -> int:
            try:
                result = int(value(key, default))
                if not 1 <= result <= 65535:
                    raise ValueError
                return result
            except ValueError:
                raise ConfigError(f"{key}: нужен номер порта от 1 до 65535.") from None

        def integer(key: str, default: str) -> int:
            try:
                return int(value(key, default))
            except ValueError:
                raise ConfigError(f"{key}: нужно целое число.") from None

        web_search = value("MUSIC_WEB_SEARCH", "true").lower()
        if web_search not in {"true", "false"}:
            raise ConfigError("MUSIC_WEB_SEARCH: используйте true или false.")

        token = value("BOT_TOKEN")
        try:
            validate_token(token)
        except TokenValidationError:
            raise ConfigError("BOT_TOKEN: укажите токен, полученный у BotFather.") from None
        password = value("POSTGRES_PASSWORD")
        if not password:
            raise ConfigError("POSTGRES_PASSWORD: пароль базы данных не задан.")
        proxy = value("TELEGRAM_PROXY_URL")
        if proxy:
            try:
                parsed = urlsplit(proxy)
                if (
                    parsed.scheme not in {"http", "socks5"}
                    or not parsed.hostname
                    or not parsed.port
                    or parsed.path not in {"", "/"}
                    or parsed.query
                    or parsed.fragment
                ):
                    raise ValueError
            except ValueError:
                raise ConfigError(
                    "TELEGRAM_PROXY_URL: нужен http://host:port или socks5://host:port; "
                    "при необходимости добавьте user:password@."
                ) from None
        level = value("LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError("LOG_LEVEL: используйте DEBUG, INFO, WARNING, ERROR или CRITICAL.")
        return cls(
            bot_token=token,
            postgres_password=password,
            telegram_proxy_url=proxy,
            postgres_host=value("POSTGRES_HOST", "127.0.0.1"),
            postgres_port=port("POSTGRES_PORT", "5432"),
            postgres_db=value("POSTGRES_DB", "bot"),
            postgres_user=value("POSTGRES_USER", "bot"),
            log_level=level,
            health_port=port("HEALTH_PORT", "8080"),
            llm_base_url=value("LLM_BASE_URL"),
            llm_api_key=value("LLM_API_KEY"),
            openrouter_management_key=value("OPENROUTER_MANAGEMENT_KEY"),
            llm_model=value("LLM_MODEL"),
            llm_timeout_seconds=integer("LLM_TIMEOUT_SECONDS", "30"),
            history_max_messages=integer("HISTORY_MAX_MESSAGES", "40"),
            history_max_chars=integer("HISTORY_MAX_CHARS", "8000"),
            music_web_search=web_search == "true",
        )
