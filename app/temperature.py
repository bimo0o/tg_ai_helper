"""Owner-only, isolated temperature comparisons. No calls run on import."""

import asyncio
import copy
import html
import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg

from app.llm import TEMPERATURES
from app.prompts import instruction_messages

logger = logging.getLogger(__name__)
LAB_PROMPT = (
    "Объясни разницу между списком и кортежем в Python начинающему студенту. "
    "Ответь ровно тремя непустыми строками, пронумерованными 1., 2., 3.; "
    "каждый пункт — одно или два предложения. Не добавляй вступление и заключение."
)


class TemperatureRepository:
    def __init__(self, pool):
        self.pool = pool

    async def create(self, actor, data):
        run_id = uuid.uuid4()
        await self.pool.execute(
            "INSERT INTO temperature_experiments (id, user_id, data) VALUES ($1, $2, $3::jsonb)",
            run_id,
            actor,
            json.dumps(data, ensure_ascii=False),
        )
        return run_id

    async def get(self, actor, run_id=None):
        await self.pool.execute(
            "UPDATE temperature_experiments SET status='interrupted' "
            "WHERE user_id=$1 AND status='running' AND lease_until < now()",
            actor,
        )
        row = await self.pool.fetchrow(
            "SELECT id, status, cancel_requested, data FROM temperature_experiments "
            "WHERE user_id=$1 AND ($2::uuid IS NULL OR id=$2) AND expires_at > now() "
            "ORDER BY created_at DESC LIMIT 1",
            actor,
            run_id,
        )
        if row is None:
            return None
        return {**dict(row), "data": json.loads(row["data"])}

    async def claim(self, actor, run_id, lease):
        await self.get(actor, run_id)
        try:
            result = await self.pool.execute(
                "UPDATE temperature_experiments SET status='running', "
                "lease_until=now()+$3::integer*interval '1 second' "
                "WHERE user_id=$1 AND id=$2 AND status='draft' "
                "AND created_at > now()-interval '15 minutes' AND expires_at > now()",
                actor,
                run_id,
                lease,
            )
            return result == "UPDATE 1"
        except asyncpg.UniqueViolationError:
            return False

    async def renew(self, actor, run_id, lease):
        return (
            await self.pool.fetchval(
                "UPDATE temperature_experiments SET "
                "lease_until=now()+$3::integer*interval '1 second' "
                "WHERE user_id=$1 AND id=$2 AND status='running' AND NOT cancel_requested "
                "RETURNING TRUE",
                actor,
                run_id,
                lease,
            )
            is True
        )

    async def save(self, actor, run_id, data):
        await self.pool.execute(
            "UPDATE temperature_experiments SET data=$3::jsonb WHERE user_id=$1 AND id=$2",
            actor,
            run_id,
            json.dumps(data, ensure_ascii=False),
        )

    async def finish(self, actor, run_id, status):
        await self.pool.execute(
            "UPDATE temperature_experiments SET status=$3, lease_until=NULL "
            "WHERE user_id=$1 AND id=$2 AND status='running'",
            actor,
            run_id,
            status,
        )

    async def cancel(self, actor, run_id):
        await self.pool.execute(
            "UPDATE temperature_experiments SET cancel_requested=TRUE, "
            "status=CASE WHEN status='draft' THEN 'cancelled' ELSE status END "
            "WHERE user_id=$1 AND id=$2 AND status IN ('draft', 'running')",
            actor,
            run_id,
        )


class TemperatureService:
    def __init__(self, repository, operations, llm, settings):
        self.repository = repository
        self.operations = operations
        self.llm = llm
        self.settings = settings

    async def authorized(self, actor):
        return await self.operations.is_admin(actor)

    async def prepare(self, actor, prompt=LAB_PROMPT, mode="study"):
        if not await self.authorized(actor):
            raise PermissionError
        prompt = prompt.strip()
        if mode not in {"study", "chat"} or not 1 <= len(prompt) <= self.settings.history_max_chars:
            raise ValueError("Invalid experiment prompt.")
        data = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "model": await self.operations.get_model(self.settings.llm_model),
            "mode": mode,
            "prompt": prompt,
            "instructions": instruction_messages(mode, self.settings.max_output_tokens),
            "max_output_tokens": self.settings.max_output_tokens,
            "timeout_seconds": self.settings.llm_timeout_seconds,
            "web_search": False,
            "history": [],
            "music_profile": None,
            "temperatures": list(TEMPERATURES),
            "repeats": 3,
            "rows": [],
            "limitations": "Temperature does not guarantee different answers. "
            "Provider routing and model nondeterminism can also affect results.",
        }
        return await self.repository.create(actor, data)

    async def get(self, actor, run_id=None):
        if not await self.authorized(actor):
            raise PermissionError
        return await self.repository.get(actor, run_id)

    async def cancel(self, actor, run_id):
        if not await self.authorized(actor):
            raise PermissionError
        await self.repository.cancel(actor, run_id)

    async def run(self, actor, run_id, progress):
        if not await self.authorized(actor):
            raise PermissionError
        preview = await self.repository.get(actor, run_id)
        if (
            preview is None
            or preview["data"]["timeout_seconds"] != self.settings.llm_timeout_seconds
        ):
            return False
        lease = int(self.settings.llm_timeout_seconds) + 60
        if not await self.repository.claim(actor, run_id, lease):
            return False
        status = "interrupted"
        try:
            experiment = await self.repository.get(actor, run_id)
            data = experiment["data"]
            # The preview freezes model, prompt and all generation settings.
            messages = data["instructions"] + [{"role": "user", "content": data["prompt"]}]
            for temperature in data["temperatures"]:
                for repeat in range(1, 4):
                    if not await self.repository.renew(actor, run_id, lease):
                        status = "cancelled"
                        return True
                    request_id = await self.operations.start_request((actor, actor), data["model"])
                    started = time.monotonic()
                    started_at = datetime.now(UTC).isoformat()
                    result = await self.llm.generate(
                        copy.deepcopy(messages),
                        data["model"],
                        temperature,
                        web=False,
                        max_output_tokens=data["max_output_tokens"],
                    )
                    duration = round((time.monotonic() - started) * 1000)
                    await self.operations.finish_request(
                        request_id,
                        "temperature",
                        result.ok,
                        duration,
                        result.usage,
                        result.cost_usd,
                        result.error_kind,
                    )
                    data["rows"].append(
                        {
                            "run": len(data["rows"]) + 1,
                            "started_at_utc": started_at,
                            "temperature": temperature,
                            "repeat": repeat,
                            "ok": result.ok,
                            "answer": result.text if result.ok else "",
                            "error_kind": result.error_kind,
                            "length": len(result.text) if result.ok else 0,
                            "duration_ms": duration,
                            "usage": result.usage,
                            "cost_usd": str(result.cost_usd)
                            if result.cost_usd is not None
                            else None,
                            "finish_reason": result.finish_reason,
                            "truncated": result.truncated,
                            "format_ok": (
                                result.ok
                                and len(result.text.strip().splitlines()) == 3
                                and all(
                                    re.fullmatch(rf"{n}\.\s+\S.*", line)
                                    for n, line in enumerate(result.text.strip().splitlines(), 1)
                                )
                            )
                            if data["prompt"] == LAB_PROMPT and data["mode"] == "study"
                            else None,
                        }
                    )
                    await self.repository.save(actor, run_id, data)
                    try:
                        await progress(len(data["rows"]))
                    except Exception:
                        logger.warning("Temperature progress delivery failed; no content logged.")
            status = "completed"
            return True
        finally:
            # Never resume paid calls automatically after cancellation or restart.
            await asyncio.shield(self.repository.finish(actor, run_id, status))


def summary(experiment):
    data = experiment["data"]
    rows = data["rows"]
    labels = {
        "draft": "подготовлен",
        "running": "выполняется",
        "completed": "завершён",
        "cancelled": "отменён",
        "interrupted": "прерван; автоматического повтора нет",
    }
    lines = [
        f"Тест температур: {labels[experiment['status']]}",
        f"Модель: {data['model']}",
        f"Режим: {data['mode']}; сохранено ответов/ошибок: {len(rows)}/12.",
    ]
    for temperature in TEMPERATURES:
        group = [r for r in rows if r["temperature"] == temperature]
        successful = [r for r in group if r["ok"]]
        unique = len({r["answer"] for r in successful})
        lengths = ", ".join(str(r["length"]) for r in successful) or "—"
        lines.append(
            f"T={temperature:.1f}: успешно {len(successful)}/{len(group)}, "
            f"разных текстов {unique}; символы: {lengths}."
        )
    known = [Decimal(r["cost_usd"]) for r in rows if r["cost_usd"] is not None]
    lines.append(
        f"Известная стоимость: ${sum(known, Decimal(0)):.6f} "
        f"(известна для {len(known)}/{len(rows)})."
    )
    lines.append(
        "Различие текстов — точное сравнение, не оценка качества. "
        "Совпадения при любой температуре допустимы. Полные ответы — в отчёте."
    )
    return "\n".join(lines)


def export_json(experiment):
    return json.dumps(
        {"status": experiment["status"], **experiment["data"]}, ensure_ascii=False, indent=2
    ).encode("utf-8")


def export_html(experiment):
    escape = html.escape
    data = experiment["data"]
    parts = [
        "<!doctype html><html lang='ru'><meta charset='utf-8'>",
        "<title>Сравнение температур</title>",
        "<style>body{max-width:1000px;margin:2em auto;padding:1em;font:16px sans-serif}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;border:1px solid #ccc;padding:1em}"
        "section{break-inside:avoid}</style><body><h1>Сравнение температур</h1>",
        f"<pre>{escape(summary(experiment))}</pre>",
        f"<h2>Запрос</h2><pre>{escape(data['prompt'])}</pre>",
        f"<p>Лимит ответа: {data['max_output_tokens']} токенов; "
        f"таймаут: {data['timeout_seconds']} с.</p>",
        f"<p>Подготовлен: {escape(data['created_at_utc'])}. Web выключен, история пуста, "
        "профиль не передаётся. Все ответы сохранены без отбора.</p>",
        "<details><summary>Постоянные инструкции</summary><pre>"
        + escape(json.dumps(data["instructions"], ensure_ascii=False, indent=2))
        + "</pre></details>",
    ]
    parts.append(
        "<h2>Все запуски</h2><table border='1' cellpadding='6'>"
        "<tr><th>№</th><th>T</th><th>Символы</th><th>Формат</th>"
        "<th>Input</th><th>Output</th><th>Время, мс</th><th>Статус</th></tr>"
    )
    for row in data["rows"]:
        format_label = {True: "да", False: "нет", None: "не задан"}[row["format_ok"]]
        usage = row["usage"]
        parts.append(
            f"<tr><td>{row['run']}</td><td>{row['temperature']:.1f}</td>"
            f"<td>{row['length']}</td><td>{format_label}</td>"
            f"<td>{usage.get('prompt_tokens', '—')}</td>"
            f"<td>{usage.get('completion_tokens', '—')}</td><td>{row['duration_ms']}</td>"
            f"<td>{'успех' if row['ok'] else escape(str(row['error_kind']))}</td></tr>"
        )
    parts.append(
        "</table><p>Для лабораторного запроса формат — три нумерованные строки. "
        "Корректность содержания и полезность оцениваются вручную.</p>"
    )
    for row in data["rows"]:
        answer = row["answer"] if row["ok"] else "Ошибка: " + str(row["error_kind"])
        parts.extend(
            [
                f"<section><h2>№ {row['run']} · T={row['temperature']:.1f} · "
                f"повтор {row['repeat']}</h2>",
                f"<p>{row['duration_ms']} мс; {row['length']} символов; "
                f"лимит достигнут: {row['truncated']}; стоимость: "
                f"{escape(row['cost_usd'] or 'неизвестна')}</p>",
                f"<pre>{escape(answer)}</pre></section>",
            ]
        )
    return ("".join(parts) + "</body></html>").encode("utf-8")
