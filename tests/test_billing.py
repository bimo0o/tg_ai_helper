from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from app.billing import BillingClient, decimal_value


def client(payload, status=200, error=None, management_key=""):
    response = Mock(status=status, json=AsyncMock(return_value=payload))
    context = Mock(
        __aenter__=AsyncMock(return_value=response), __aexit__=AsyncMock(return_value=False)
    )
    if error:
        context.__aenter__.side_effect = error
    session = Mock(get=Mock(return_value=context))
    return BillingClient(
        session, "https://openrouter.ai/api/v1", "example", management_key
    ), session


async def test_key_budget_and_spend_use_regular_key():
    billing, session = client(
        {
            "data": {
                "usage": 2.5,
                "limit": 10,
                "limit_remaining": 7.5,
                "usage_daily": 0.1,
            }
        }
    )
    budget = await billing.get_budget()
    assert budget.key_ok and budget.key_remaining == Decimal("7.5")
    assert budget.daily_usage == Decimal("0.1")
    assert budget.account_balance is None
    session.get.assert_called_once()
    assert session.get.call_args.args[0] == "https://openrouter.ai/api/v1/key"


async def test_account_balance_requires_separate_management_key():
    billing, session = client(
        {"data": {"total_credits": 10, "total_usage": 2.5}}, management_key="example-management"
    )
    budget = await billing.get_budget()
    assert budget.account_balance == Decimal("7.5")
    assert session.get.call_args.args[0].endswith("/credits")
    assert session.get.call_args.kwargs["headers"]["Authorization"] == "Bearer example-management"


@pytest.mark.parametrize("status,error", [(403, None), (500, None), (200, TimeoutError())])
async def test_unavailable_balance_is_not_shown_as_zero(status, error):
    billing, _ = client({}, status=status, error=error)
    budget = await billing.get_budget()
    assert not budget.key_ok and budget.account_balance is None and budget.key_remaining is None


async def test_management_key_is_never_sent_to_other_hosts():
    billing, session = client({}, management_key="example-management")
    billing.base_url = "https://other.example/api/v1"
    assert not (await billing.get_budget()).key_ok
    session.get.assert_not_called()


@pytest.mark.parametrize("value", [None, True, "nan", "infinity", "not-a-number"])
def test_invalid_decimal(value):
    assert decimal_value(value) is None


async def test_malformed_limit_is_not_mistaken_for_unlimited_budget():
    billing, _ = client({"data": {"usage": 1, "limit": "invalid", "limit_remaining": "invalid"}})
    assert not (await billing.get_budget()).key_ok
