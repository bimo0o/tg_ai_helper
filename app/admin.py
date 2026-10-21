from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.billing import BillingClient
from app.config import Settings
from app.operations import OperationsRepository

DENIED = "Доступ запрещён."
PERIODS = {"day": ("24 часа", 1), "week": ("7 дней", 7), "all": ("всё время", None)}


@dataclass(frozen=True)
class AdminView:
    text: str
    period: str
    offset: int
    has_next: bool


def money(value) -> str:
    return "недоступно" if value is None else f"${value:.6f}"


class AdminService:
    def __init__(
        self,
        operations: OperationsRepository,
        billing: BillingClient,
        default_model: str,
        settings: Settings | None = None,
    ):
        self.operations = operations
        self.billing = billing
        self.default_model = default_model
        self.settings = settings or Settings(bot_token="", postgres_password="")

    async def authorized(self, user_id: int) -> bool:
        return await self.operations.is_admin(user_id)

    async def switch_model(self, user_id: int, alias: str) -> bool:
        return await self.operations.set_model(user_id, alias)

    async def overview(self, user_id: int, period="day", offset=0) -> AdminView | None:
        if not await self.authorized(user_id):
            return None
        if period not in PERIODS or not 0 <= offset <= 1000000 or offset % 10:
            raise ValueError("Invalid dashboard page.")
        label, days = PERIODS[period]
        now = datetime.now(UTC)
        since = now - timedelta(days=days) if days else datetime(1970, 1, 1, tzinfo=UTC)
        since = max(since, now - timedelta(days=self.settings.statistics_retention_days))
        summary = await self.operations.summary(since)
        users = await self.operations.user_stats(since, offset)
        model = await self.operations.get_model(self.default_model)
        budget = await self.billing.get_budget()
        lines = [
            f"Админ-панель · {label}",
            f"Модель для новых запросов: {model}",
            f"Запросов к LLM: {summary['requests']}; пользователей: {summary['users']}",
            f"Успешно: {summary['successes']}; ошибок: {summary['errors']}; "
            f"не завершено: {summary['pending']}",
            f"Учтено входных токенов: {summary['prompt_tokens'] or 0}; "
            f"выходных: {summary['completion_tokens'] or 0}",
            f"Учтённая стоимость: {money(summary['cost_usd'])}",
            f"Стоимость известна для {summary['priced']} из {summary['requests']} запросов.",
        ]
        average = summary["average_ms"]
        lines.append(
            f"Среднее время: {average / 1000:.2f} с"
            if average is not None
            else "Среднее время: нет данных"
        )
        lines.append("\nOpenRouter:")
        if budget.key_ok:
            remaining = (
                "лимит не задан"
                if budget.key_limit is None and budget.key_remaining is None
                else money(budget.key_remaining)
            )
            lines.extend(
                [
                    f"Остаток лимита ключа: {remaining}",
                    f"Расход ключа: {money(budget.key_usage)}; "
                    f"за сутки UTC: {money(budget.daily_usage)}",
                ]
            )
        else:
            lines.append("Бюджет ключа: недоступно")
        lines.append(f"Баланс аккаунта: {money(budget.account_balance)}")
        if budget.account_balance is None:
            lines.append("Для общего баланса нужен management key и доступность API.")
        lines.extend(
            [
                "\nВнутренние лимиты бюджета и частоты запросов отключены.",
                "Неизвестная стоимость не включена в учтённые расходы; резервы не создаются.",
                f"Ответ до {self.settings.max_output_tokens} токенов.",
            ]
        )
        lines.append(f"\nПользователи · страница {offset // 10 + 1}:")
        if not users:
            lines.append("Нет запросов за выбранный период.")
        for row in users[:10]:
            lines.append(
                f"ID {row['user_id']}: {row['requests']} запросов, {row['errors']} ошибок, "
                f"{money(row['cost_usd'])} (стоимость известна для {row['priced']})."
            )
        lines.append(
            f"\nСтатистика хранится {self.settings.statistics_retention_days} дней с момента "
            "каждого события. Учёт ведётся с установки админки. Тексты диалогов не показываются."
        )
        return AdminView("\n".join(lines), period, offset, len(users) > 10)
