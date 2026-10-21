from unittest.mock import AsyncMock

from app import admin_setup
from app.config import Settings
from app.operations import MODEL_OPTIONS
from tests.fakes import MemoryPool


async def test_setup_stores_owner_and_initial_qwen_without_external_clients(monkeypatch):
    pool = MemoryPool()
    pool.close = AsyncMock()
    monkeypatch.setattr(admin_setup, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(admin_setup, "migrate", AsyncMock())
    await admin_setup.assign(Settings(bot_token="unused", postgres_password="unused"), 10)
    assert pool.admin_id == 10 and pool.model == MODEL_OPTIONS["qwen"]
    pool.close.assert_awaited_once()


async def test_repeated_setup_keeps_selected_model(monkeypatch):
    pool = MemoryPool()
    pool.admin_id = 10
    pool.model = MODEL_OPTIONS["deepseek"]
    pool.close = AsyncMock()
    monkeypatch.setattr(admin_setup, "create_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(admin_setup, "migrate", AsyncMock())
    await admin_setup.assign(Settings(bot_token="unused", postgres_password="unused"), 10)
    assert pool.model == MODEL_OPTIONS["deepseek"]
