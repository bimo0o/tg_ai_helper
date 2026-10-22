"""Paid experiment; run manually with --execute. Importing makes no API calls."""

import argparse
import asyncio
import csv
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from aiohttp import ClientSession

from app.assistant import Assistant
from app.config import ConfigError, Settings
from app.db import create_pool
from app.experiment_spec import PROMPT
from app.experiment_spec import follows_format as follows_format
from app.llm import LLMClient
from app.logging_setup import configure_logging
from app.prompts import instruction_messages
from app.repository import Repository
from app.schema import migrate

OWNER = (-1, -1)


@dataclass(frozen=True)
class ExperimentRow:
    run: int
    temperature: float
    length: int
    format_ok: str
    observations: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    answer: str


def write_results(directory: Path, rows: list[ExperimentRow], metadata: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fields = list(ExperimentRow.__dataclass_fields__)
    with (directory / "results.csv").open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
    table = [
        "| № | Temperature | Символы | Формат | Наблюдения | Input | Output | Total |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        table.append(
            f"| {row.run} | {row.temperature:.1f} | {row.length} | {row.format_ok} | "
            f"{row.observations} | {row.prompt_tokens} | "
            f"{row.completion_tokens} | {row.total_tokens} |"
        )
    (directory / "results.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    (directory / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


async def run_experiment(settings: Settings, directory: Path) -> int:
    settings = replace(settings, music_web_search=False)
    pool = await create_pool(settings)
    rows = []
    metadata = {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "model": settings.llm_model,
        "api": "OpenRouter Chat Completions /api/v1",
        "mode": "study",
        "prompt": PROMPT,
        "instructions": instruction_messages("study", settings.max_output_tokens),
        "history_max_messages": settings.history_max_messages,
        "history_max_chars": settings.history_max_chars,
        "max_output_tokens": settings.max_output_tokens,
        "timeout_seconds": settings.llm_timeout_seconds,
        "web_search": False,
        "criteria": (
            "Three numbered lines; correctness, clarity and completeness assessed manually."
        ),
    }
    try:
        await migrate(pool)
        repository = Repository(pool)
        await repository.set_mode(OWNER, "study")
        async with ClientSession() as session:
            service = Assistant(
                repository,
                LLMClient(
                    session,
                    settings.llm_base_url,
                    settings.llm_api_key,
                    settings.llm_timeout_seconds,
                ),
                settings,
            )
            for temperature in (0.0, 0.3, 0.7, 1.0):
                for _ in range(3):
                    async with repository.locked(OWNER) as locked:
                        await locked.clear_history(OWNER)
                        await locked.set_temperature(OWNER, temperature)
                    reply = await service.respond(OWNER, PROMPT)
                    rows.append(
                        ExperimentRow(
                            run=len(rows) + 1,
                            temperature=temperature,
                            length=len(reply.text) if reply.ok else 0,
                            format_ok="да" if reply.ok and follows_format(reply.text) else "нет",
                            observations=(
                                "Оценить содержание вручную"
                                if reply.ok
                                else f"Ошибка: {reply.error_kind}"
                            ),
                            prompt_tokens=reply.usage.get("prompt_tokens"),
                            completion_tokens=reply.usage.get("completion_tokens"),
                            total_tokens=reply.usage.get("total_tokens"),
                            answer=reply.text if reply.ok else "",
                        )
                    )
                    write_results(directory, rows, metadata)
    finally:
        await pool.close()
    return 0 if all(row.answer for row in rows) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="12 paid LLM requests; manual execution only")
    parser.add_argument("--execute", action="store_true", help="Explicitly enable paid API calls")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output", type=Path, default=Path("experiment-results"))
    args = parser.parse_args()
    if not args.execute:
        parser.error("Для 12 платных запросов явно укажите --execute.")
    if args.output.exists():
        parser.error("Каталог результатов уже существует. Укажите новый --output.")
    try:
        settings = Settings.load(args.env_file)
        settings.validate_llm()
        configure_logging(settings)
        return asyncio.run(run_experiment(settings, args.output))
    except ConfigError as exc:
        print(str(exc))
    except Exception:
        print("Эксперимент не завершён. Проверьте настройки и доступность PostgreSQL.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
