import logging
from dataclasses import dataclass, field

from app.config import Settings
from app.llm import LLMClient
from app.prompts import instruction_messages
from app.repository import Owner, Repository

logger = logging.getLogger(__name__)
ERROR_MESSAGE = "Не удалось получить ответ. Попробуйте ещё раз позже."
EMPTY_MESSAGE = "Отправьте непустое текстовое сообщение."


@dataclass(frozen=True)
class Reply:
    text: str
    ok: bool = False
    usage: dict[str, int] = field(default_factory=dict)
    error_kind: str | None = None


class Assistant:
    def __init__(self, repository: Repository, llm: LLMClient, settings: Settings):
        self.repository = repository
        self.llm = llm
        self.settings = settings

    async def respond(self, owner: Owner, text: str) -> Reply:
        if not text.strip():
            return Reply(EMPTY_MESSAGE, error_kind="input")
        if len(text) > self.settings.history_max_chars:
            return Reply(
                "Сообщение слишком длинное. "
                f"Максимум — {self.settings.history_max_chars} символов.",
                error_kind="input",
            )
        try:
            async with self.repository.locked(owner) as repository:
                user = await repository.get_settings(owner)
                history = await repository.get_history(
                    owner,
                    user.mode,
                    self.settings.history_max_messages - 1,
                    self.settings.history_max_chars - len(text),
                )
                messages = [
                    *instruction_messages(user.mode),
                    *history,
                    {"role": "user", "content": text},
                ]
                await repository.add_message(owner, "user", text, user.mode)
                result = await self.llm.generate(
                    messages,
                    self.settings.llm_model,
                    user.temperature,
                    web=user.mode == "music" and self.settings.music_web_search,
                )
                if not result.ok:
                    return Reply(ERROR_MESSAGE, error_kind=result.error_kind)
                answer = result.text
                sources = [url for url in result.citations if url not in answer]
                if sources:
                    answer += "\n\nИсточники:\n" + "\n".join(sources)
                await repository.add_message(owner, "assistant", answer, user.mode)
                return Reply(answer, ok=True, usage=result.usage)
        except Exception:
            logger.warning("Dialogue processing failed; no request content logged.")
            return Reply(ERROR_MESSAGE, error_kind="service")
