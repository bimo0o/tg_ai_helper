from dataclasses import replace

import pytest

from app.config import ConfigError, Settings


def configured():
    return Settings(
        bot_token="unused",
        postgres_password="unused",
        llm_base_url="https://api.example/v1",
        llm_api_key="example-only",
        llm_model="example-model",
    )


def test_llm_config_defaults_and_hidden_credentials():
    settings = configured()
    settings.validate_llm()
    assert (settings.history_max_messages, settings.history_max_chars) == (40, 8000)
    assert "example-only" not in repr(settings)


def test_management_key_is_optional_and_hidden_in_repr():
    settings = replace(configured(), openrouter_management_key="example-management-only")
    settings.validate_llm()
    assert "example-management-only" not in repr(settings)
    assert configured().openrouter_management_key == ""


@pytest.mark.parametrize("name", ["llm_base_url", "llm_api_key", "llm_model"])
def test_required_llm_setting(name):
    with pytest.raises(ConfigError, match=name.upper()):
        replace(configured(), **{name: ""}).validate_llm()


@pytest.mark.parametrize(
    "url", ["http://api.example", "https://user:example@api.example", "https://api.example:bad"]
)
def test_bad_url_never_leaks_value(url):
    with pytest.raises(ConfigError) as exc:
        replace(configured(), llm_base_url=url).validate_llm()
    assert str(exc.value) == "LLM_BASE_URL: нужен HTTPS URL без учётных данных."


@pytest.mark.parametrize(
    "name,value",
    [("history_max_messages", 1), ("history_max_chars", 0), ("llm_timeout_seconds", 0)],
)
def test_context_limits_are_validated(name, value):
    with pytest.raises(ConfigError, match=name.upper()):
        replace(configured(), **{name: value}).validate_llm()


def test_retention_defaults():
    settings = configured()
    assert (
        settings.history_retention_days,
        settings.statistics_retention_days,
        settings.music_profile_retention_days,
    ) == (30, 90, 180)


@pytest.mark.parametrize(
    "changes",
    [
        {"history_retention_days": 0},
        {"max_output_tokens": 0},
    ],
)
def test_invalid_output_and_retention_policy_is_rejected(changes):
    with pytest.raises(ConfigError):
        replace(configured(), **changes).validate_llm()


def test_legacy_budget_and_rate_env_settings_are_ignored(tmp_path):
    env = tmp_path / "legacy.env"
    env.write_text(
        "BOT_TOKEN=123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk\n"
        "POSTGRES_PASSWORD=example-only\n"
        "DAILY_BUDGET_USD=invalid\n"
        "USER_DAILY_BUDGET_USD=invalid\n"
        "REQUEST_RESERVE_USD=invalid\n"
        "USER_REQUESTS_PER_MINUTE=invalid\n"
        "USER_REQUESTS_PER_DAY=invalid\n",
        encoding="utf-8",
    )
    settings = Settings.load(env, environ={})
    assert settings.max_output_tokens == 1500
