import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal

from app.config import Settings
from app.llm import Generation, LLMClient
from app.music_profile import MusicProfiles
from app.operations import LimitExceeded, OperationsRepository
from app.prompts import instruction_messages, profile_messages
from app.repository import BUSY_MESSAGE, DialogueBusy, Owner, Repository

logger = logging.getLogger(__name__)
ERROR_MESSAGE = "Не удалось получить ответ. Попробуйте ещё раз позже."
EMPTY_MESSAGE = "Отправьте непустое текстовое сообщение."
TIMEOUT_MESSAGE = "Модель не успела ответить за отведённое время. Попробуйте позже."
TRUNCATED_NOTICE = (
    "Ответ достиг лимита генерации и может быть незавершён. "
    "Можно попросить продолжить с последнего пункта."
)


def generation_error(result: Generation) -> str:
    if result.error_kind == "timeout":
        return TIMEOUT_MESSAGE
    if result.http_status == 429:
        return "Сервис модели ограничил частоту запросов. Попробуйте позже."
    if result.http_status in {401, 402, 403}:
        return "Сервис модели отклонил доступ к запросу. Требуется проверка настроек владельцем."
    if result.http_status is not None and result.http_status >= 500:
        return "Сервис модели временно недоступен. Попробуйте позже."
    if result.error_kind == "incomplete":
        return "Модель не вернула завершённый текстовый ответ. Попробуйте переформулировать вопрос."
    return ERROR_MESSAGE


LIMIT_MESSAGES = {
    "rate": "Слишком много запросов. Подождите минуту и попробуйте снова.",
    "daily_requests": "Суточный лимит запросов исчерпан. Попробуйте после 00:00 UTC.",
    "user_budget": "Ваш дневной бюджет исчерпан или зарезервирован. Попробуйте позже.",
    "global_budget": "Дневной бюджет бота исчерпан или зарезервирован. Попробуйте позже.",
}


@dataclass(frozen=True)
class Reply:
    text: str
    ok: bool = False
    usage: dict[str, int] = field(default_factory=dict)
    error_kind: str | None = None


class Assistant:
    def __init__(
        self,
        repository: Repository,
        llm: LLMClient,
        settings: Settings,
        operations: OperationsRepository | None = None,
        profiles: MusicProfiles | None = None,
    ):
        self.repository = repository
        self.llm = llm
        self.settings = settings
        self.operations = operations
        self.profiles = profiles

    async def respond(self, owner: Owner, text: str, *, use_web: bool = False) -> Reply:
        if not text.strip():
            return Reply(EMPTY_MESSAGE, error_kind="input")
        if len(text) > self.settings.history_max_chars:
            return Reply(
                "Сообщение слишком длинное. "
                f"Максимум — {self.settings.history_max_chars} символов.",
                error_kind="input",
            )
        started = time.monotonic()
        event = None
        mode = "unknown"
        result = Generation(False, error_kind="service")
        reply = Reply(ERROR_MESSAGE, error_kind="service")
        api_started = False
        try:
            profile = {}
            if self.profiles is not None or use_web:
                snapshot = await self.repository.get_settings(owner)
                if snapshot.mode == "music" and self.profiles is not None:
                    profile = await self.profiles.get(owner[1])
            if use_web:
                if snapshot.mode != "music":
                    return Reply("Для поиска сначала включите /music.", error_kind="input")
                if not self.settings.music_web_search:
                    return Reply("Web-поиск отключён в настройках бота.", error_kind="input")
            model = self.settings.llm_model
            if self.operations is not None:
                model = await self.operations.get_model(model)
                event = await self.operations.start_request(owner, model, self.settings)
            async with self.repository.locked(owner) as repository:
                user = await repository.get_settings(owner)
                mode = user.mode
                if use_web and mode != "music":
                    reply = Reply("Для поиска сначала включите /music.", error_kind="input")
                    return reply
                context = profile_messages(profile) if mode == "music" else []
                profile_chars = sum(len(message["content"]) for message in context)
                if profile_chars + len(text) > self.settings.history_max_chars:
                    context, profile_chars = [], 0
                history = await repository.get_history(
                    owner,
                    user.mode,
                    self.settings.history_max_messages - 1 - len(context),
                    self.settings.history_max_chars - len(text) - profile_chars,
                )
                messages = [
                    *instruction_messages(user.mode),
                    *context,
                    *history,
                    {"role": "user", "content": text},
                ]
                await repository.add_message(owner, "user", text, user.mode)
                api_started = True
                result = await self.llm.generate(
                    messages,
                    model,
                    user.temperature,
                    web=use_web and user.mode == "music" and self.settings.music_web_search,
                    max_output_tokens=self.settings.max_output_tokens,
                )
                if not result.ok:
                    reply = Reply(generation_error(result), error_kind=result.error_kind)
                    return reply
                answer = result.text
                if result.truncated:
                    answer += "\n\n" + TRUNCATED_NOTICE
                sources = [url for url in result.citations if url not in answer]
                if sources:
                    answer += "\n\nИсточники:\n" + "\n".join(sources)
                await repository.add_message(owner, "assistant", answer, user.mode)
                reply = Reply(answer, ok=True, usage=result.usage)
        except DialogueBusy:
            reply = Reply(BUSY_MESSAGE, error_kind="busy")
        except LimitExceeded as exc:
            reply = Reply(LIMIT_MESSAGES[exc.kind], error_kind=exc.kind)
        except Exception:
            reply = Reply(ERROR_MESSAGE, error_kind="service")
            logger.warning("Dialogue processing failed; no request content logged.")
        finally:
            if event is not None:
                try:
                    await self.operations.finish_request(
                        event,
                        mode,
                        reply.ok,
                        int((time.monotonic() - started) * 1000),
                        result.usage,
                        result.cost_usd if api_started else Decimal("0"),
                        None if reply.ok else reply.error_kind or result.error_kind or "service",
                    )
                except Exception:
                    logger.warning("Request accounting incomplete; no request content logged.")
        return reply
