import logging

from app.config import Settings
from app.logging_setup import configure_logging


def test_configured_filter_hides_llm_credentials_in_exception():
    previous = logging.root.handlers[:]
    previous_level = logging.root.level
    try:
        settings = Settings(
            bot_token="example-telegram",
            postgres_password="example-db",
            llm_api_key="example-llm-key",
            llm_base_url="https://api.example/v1",
        )
        configure_logging(settings)
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            "",
            1,
            "example-llm-key https://api.example/v1 https://user:example-pass@host",
            (),
            None,
        )
        handler = logging.root.handlers[0]
        handler.filter(record)
        rendered = handler.format(record)
        assert "example-llm-key" not in rendered
        assert "api.example" not in rendered
        assert "example-pass" not in rendered
    finally:
        logging.root.handlers = previous
        logging.root.setLevel(previous_level)
