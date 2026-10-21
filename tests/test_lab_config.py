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
