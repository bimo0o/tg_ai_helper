import asyncio
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import aiohttp


def decimal_value(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


@dataclass(frozen=True)
class Budget:
    key_ok: bool = False
    key_limit: Decimal | None = None
    key_remaining: Decimal | None = None
    key_usage: Decimal | None = None
    daily_usage: Decimal | None = None
    account_balance: Decimal | None = None


class BillingClient:
    def __init__(self, session, base_url: str, api_key: str, management_key: str = ""):
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.management_key = management_key

    async def _get(self, path: str, key: str) -> dict | None:
        parsed = urlsplit(self.base_url)
        if parsed.scheme != "https" or parsed.hostname != "openrouter.ai":
            return None
        try:
            async with asyncio.timeout(5):
                async with self.session.get(
                    self.base_url + path,
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=aiohttp.ClientTimeout(total=5),
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        return None
                    payload = await response.json()
                    data = payload.get("data") if isinstance(payload, dict) else None
                    return data if isinstance(data, dict) else None
        except (TimeoutError, aiohttp.ClientError, OSError, ValueError):
            return None

    async def get_budget(self) -> Budget:
        key = await self._get("/key", self.api_key)
        account = await self._get("/credits", self.management_key) if self.management_key else None
        key_ok = bool(key is not None and {"usage", "limit", "limit_remaining"} <= key.keys())
        key = key or {}
        key_ok = key_ok and all(
            key.get(name) is None or decimal_value(key[name]) is not None
            for name in ("limit", "limit_remaining")
        )
        total = decimal_value(account.get("total_credits")) if account else None
        spent = decimal_value(account.get("total_usage")) if account else None
        return Budget(
            key_ok=key_ok and decimal_value(key.get("usage")) is not None,
            key_limit=decimal_value(key.get("limit")),
            key_remaining=decimal_value(key.get("limit_remaining")),
            key_usage=decimal_value(key.get("usage")),
            daily_usage=decimal_value(key.get("usage_daily")),
            account_balance=total - spent if total is not None and spent is not None else None,
        )
