"""User-run routing experiment. Dry by default; no Telegram, DB writes or live weather calls."""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from uuid import uuid4

from aiohttp import ClientSession
from dotenv import dotenv_values
from pydantic import Field, model_validator

from app.agent import Agent, Clarification
from app.config import Settings
from app.llm import TEMPERATURES, Generation, LLMClient
from app.mcp.authorization import WriteAuthority
from app.mcp.client import ToolClient
from app.mcp.contracts import INPUTS, Contract, WeatherOutput
from app.mcp.server import create_server
from app.prompts import instruction_messages
from app.reminder_time import reminder_instant, timezone
from app.tool_data import Schedule


class Case(Contract):
    id: str = Field(min_length=1, max_length=50)
    prompt: str = Field(min_length=1, max_length=4000)
    expected_action: Literal[
        "answer", "clarification", "get_weather", "get_schedule", "add_reminder"
    ]
    expected_arguments: list[dict] = Field(max_length=5)

    @model_validator(mode="after")
    def valid_expectations(self):
        if self.expected_action in INPUTS:
            if not self.expected_arguments:
                raise ValueError("Expected tool arguments are required")
            for variant in self.expected_arguments:
                INPUTS[self.expected_action].model_validate(variant)
        elif self.expected_arguments:
            raise ValueError("Non-tool actions must not have arguments")
        return self


class Dataset(Contract):
    source: Literal["authored-supplementary-not-official"]
    reference_time: datetime
    timezone: str
    cases: list[Case] = Field(min_length=12, max_length=12)

    @model_validator(mode="after")
    def fixed_conditions(self):
        timezone(self.timezone)
        if self.reference_time.tzinfo is None or len({case.id for case in self.cases}) != 12:
            raise ValueError("An explicit reference instant and unique case IDs are required")
        counts = {
            action: sum(c.expected_action == action for c in self.cases)
            for action in ("get_weather", "get_schedule", "add_reminder", "answer", "clarification")
        }
        if counts != {
            "get_weather": 3,
            "get_schedule": 3,
            "add_reminder": 3,
            "answer": 2,
            "clarification": 1,
        }:
            raise ValueError("Expected distribution is 3/3/3/2/1")
        for case in self.cases:
            if case.expected_action == "add_reminder":
                for args in case.expected_arguments:
                    reminder_instant(args["remind_at"], self.timezone, self.reference_time)
        return self


def load_dataset(path):
    with Path(path).open("rb") as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        raise ValueError("Dataset too large")
    return Dataset.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


def arguments_match(name, actual, alternatives):
    try:
        actual = INPUTS[name].model_validate(actual).model_dump()
    except (ValueError, KeyError):
        return False
    for expected in alternatives:
        matched = True
        for key, value in expected.items():
            if key == "remind_at":
                matched &= datetime.fromisoformat(actual[key]) == datetime.fromisoformat(value)
            else:

                def normalize(text):
                    return " ".join(text.strip().rstrip(".!?").casefold().split())

                matched &= normalize(actual[key]) == normalize(value)
        if matched:
            return True
    return False


def classify(result):
    if not result.ok:
        return "error", {}
    if not result.tool_calls:
        return "answer", {}
    if len(result.tool_calls) != 1:
        return "multiple", {}
    call = result.tool_calls[0]
    if call.name == "request_clarification":
        try:
            question = Clarification.model_validate(call.arguments).question
        except ValueError:
            return "error", {}
        return "clarification", {"question": question}
    return call.name, call.arguments


class FirstDecision:
    """Exactly one paid call per case; follow-up rendering is outside this routing experiment."""

    def __init__(self, delegate):
        self.delegate, self.first = delegate, None

    async def generate(self, *args, **kwargs):
        if self.first is None:
            self.first = await self.delegate.generate(*args, **kwargs)
            return self.first
        return Generation(True, "Evaluation fixture: rendering is not measured.")


class EvaluationReminders:
    def __init__(self, dataset):
        self.dataset = dataset
        self.clock = lambda: dataset.reference_time

    async def get_timezone(self, user):
        return self.dataset.timezone

    async def propose(self, owner, source, text, remind_at, zone):
        instant = reminder_instant(remind_at, zone, self.clock())
        return {"id": uuid4(), "text": text, "remind_at": instant, "timezone": zone}

    async def execute(self, *args, **kwargs):
        raise ValueError("Evaluation does not authorize writes")

    async def decision(self, *args, **kwargs):
        pass

    async def audit(self, *args, **kwargs):
        pass


class EvaluationWeather:
    async def current(self, city):
        # No fabricated observation and no external service call; only tool choice is measured.
        return WeatherOutput(status="not_found", places=[])


async def evaluate(dataset, llm, model, temperature, budget, on_progress=None):
    reminders = EvaluationReminders(dataset)
    authority = WriteAuthority(secrets.token_bytes(32))
    schedule = Schedule({}, dataset.timezone, clock=reminders.clock)
    server = create_server(reminders, EvaluationWeather(), schedule, authority)
    records = []
    async with ToolClient(server, authority) as tools:
        await tools.discover()
        for case in dataset.cases:
            probe = FirstDecision(llm)
            agent = Agent(probe, tools, reminders)
            started = time.monotonic()
            outcome = await agent.run(
                [*instruction_messages("chat", budget), {"role": "user", "content": case.prompt}],
                model,
                temperature,
                budget,
                (1, 1),
                "evaluation:" + case.id,
            )
            result = probe.first or outcome.generation
            action, arguments = classify(result)
            routing_ok = action == case.expected_action
            args_ok = (
                routing_ok and arguments_match(action, arguments, case.expected_arguments)
                if case.expected_action in INPUTS
                else None
            )
            records.append(
                {
                    "id": case.id,
                    "prompt": case.prompt,
                    "expected_action": case.expected_action,
                    "expected_arguments": case.expected_arguments,
                    "actual_action": action,
                    "actual_arguments": arguments,
                    "answer_excerpt": result.text[:1500],
                    "answer_excerpt_truncated": len(result.text) > 1500,
                    "routing_correct": routing_ok,
                    "arguments_correct": args_ok,
                    "validation_and_workflow_ok": outcome.generation.ok,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    "usage": result.usage,
                    "cost_usd": str(result.cost_usd) if result.cost_usd is not None else None,
                    "error_kind": result.error_kind,
                }
            )
            if on_progress is not None:
                on_progress(records)
            if result.http_status in {401, 402, 403, 429}:
                break  # Do not repeat a denied or rate-limited API request eleven more times.
    priced = [Decimal(record["cost_usd"]) for record in records if record["cost_usd"] is not None]
    return {
        "completed": len(records) == 12,
        "cases_executed": len(records),
        "routing_correct": sum(r["routing_correct"] for r in records),
        "routing_total": 12,
        "arguments_correct": sum(r["arguments_correct"] is True for r in records),
        "arguments_total": 9,
        "known_cost_usd": str(sum(priced)) if priced else None,
        "unknown_cost_calls": sum(record["cost_usd"] is None for record in records),
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Авторский эксперимент маршрутизации; по умолчанию без API"
    )
    parser.add_argument("--dataset", default="docs/evaluation/lab2-routing-authored.json")
    parser.add_argument(
        "--execute", action="store_true", help="Выполнить до 12 платных LLM-запросов"
    )
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output")
    parser.add_argument(
        "--model", help="Зафиксировать модель вместо LLM_MODEL; БД админки не читается"
    )
    parser.add_argument("--temperature", type=float, choices=TEMPERATURES, default=0.3)
    args = parser.parse_args()
    try:
        dataset, digest = load_dataset(args.dataset)
        if not args.execute:
            print(
                json.dumps(
                    {
                        "executed": False,
                        "source": dataset.source,
                        "cases": 12,
                        "paid_calls_max": 12,
                        "dataset_sha256": digest,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        output = Path(
            args.output
            or (
                ".evaluation/lab2-authored-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + ".json"
            )
        )
        if output.exists():
            raise ValueError("Refusing to overwrite a previous experiment")
        values = {**dotenv_values(args.env_file, interpolate=False), **os.environ}
        settings = Settings(
            bot_token="unused",
            postgres_password="unused",
            llm_base_url=values.get("LLM_BASE_URL") or "",
            llm_api_key=values.get("LLM_API_KEY") or "",
            llm_model=args.model or values.get("LLM_MODEL") or "",
            llm_timeout_seconds=int(values.get("LLM_TIMEOUT_SECONDS") or 30),
            max_output_tokens=int(values.get("MAX_OUTPUT_TOKENS") or 1500),
        )
        settings.validate_llm()

        metadata = {
            "source": dataset.source,
            "dataset_sha256": digest,
            "model": settings.llm_model,
            "temperature": args.temperature,
            "max_output_tokens": settings.max_output_tokens,
            "reference_time": dataset.reference_time.isoformat(),
            "timezone": dataset.timezone,
            "executed_at": datetime.now(UTC).isoformat(),
            "scope": "first_native_decision_with_fixture_data",
        }

        async def execute(on_progress):
            async with ClientSession() as session:
                llm = LLMClient(
                    session,
                    settings.llm_base_url,
                    settings.llm_api_key,
                    settings.llm_timeout_seconds,
                )
                return await evaluate(
                    dataset,
                    llm,
                    settings.llm_model,
                    args.temperature,
                    settings.max_output_tokens,
                    on_progress=on_progress,
                )

        output.parent.mkdir(parents=True, exist_ok=True)
        # Reserve and test the output BEFORE spending money. Preserve completed cases progressively.
        with output.open("x", encoding="utf-8") as target:

            def save(records, status="running", summary=None):
                target.seek(0)
                json.dump(
                    {**metadata, "status": status, "records": records, **(summary or {})},
                    target,
                    ensure_ascii=False,
                    indent=2,
                )
                target.truncate()
                target.flush()

            save([])
            report = asyncio.run(execute(save))
            save(report["records"], "completed" if report["completed"] else "interrupted", report)
        print(
            f"Авторский набор: действия {report['routing_correct']}/12; "
            f"аргументы {report['arguments_correct']}/9."
        )
        print(f"Отчёт: {output}. Это не результат на отсутствующем официальном наборе.")
        if not report["completed"]:
            print("Прогон остановлен из-за отказа API; метрики неполные.")
        return 0 if report["completed"] else 1
    except Exception:
        print(
            "Эксперимент не выполнен или не сохранён. Проверьте настройки, путь и формат набора. "
            "Секреты не выводятся."
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
