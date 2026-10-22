import asyncio
import copy
import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.llm import Generation
from app.temperature import (
    LAB_PROMPT,
    TemperatureService,
    export_html,
    export_json,
    summary,
)


class Experiments:
    def __init__(self):
        self.rows = {}

    async def create(self, actor, data):
        key = uuid.uuid4()
        self.rows[key] = {
            "id": key,
            "actor": actor,
            "data": copy.deepcopy(data),
            "status": "draft",
            "cancel_requested": False,
        }
        return key

    async def get(self, actor, key=None):
        rows = [
            r for r in self.rows.values() if r["actor"] == actor and (key is None or r["id"] == key)
        ]
        return copy.deepcopy(rows[-1]) if rows else None

    async def claim(self, actor, key, lease):
        row = self.rows.get(key)
        if (
            row is None
            or row["actor"] != actor
            or row["status"] != "draft"
            or any(r["status"] == "running" for r in self.rows.values())
        ):
            return False
        row["status"] = "running"
        return True

    async def renew(self, actor, key, lease):
        return not self.rows[key]["cancel_requested"]

    async def save(self, actor, key, data):
        self.rows[key]["data"] = copy.deepcopy(data)

    async def finish(self, actor, key, status):
        self.rows[key]["status"] = status

    async def cancel(self, actor, key):
        if key in self.rows and self.rows[key]["actor"] == actor:
            self.rows[key]["cancel_requested"] = True
            if self.rows[key]["status"] == "draft":
                self.rows[key]["status"] = "cancelled"


def service():
    operations = AsyncMock()
    operations.is_admin.side_effect = lambda actor: actor == 10
    operations.get_model.return_value = "fixed-model"
    llm = AsyncMock()
    llm.generate.return_value = Generation(True, "same answer", cost_usd=Decimal("0.001"))
    settings = Settings(bot_token="", postgres_password="", llm_model="env-model")
    return TemperatureService(Experiments(), operations, llm, settings)


async def test_twelve_independent_calls_freeze_model_prompt_and_settings_and_account_costs():
    test = service()
    key = await test.prepare(10)
    test.operations.get_model.return_value = "changed-in-admin"
    progress = AsyncMock()
    assert await test.run(10, key, progress)
    calls = test.llm.generate.call_args_list
    assert len(calls) == 12
    assert [c.args[2] for c in calls] == [0.0] * 3 + [0.3] * 3 + [0.7] * 3 + [1.0] * 3
    assert all(c.args[1] == "fixed-model" for c in calls)
    assert all(c.kwargs == {"web": False, "max_output_tokens": 1500} for c in calls)
    assert all(c.args[0] == calls[0].args[0] for c in calls)
    assert len({id(c.args[0]) for c in calls}) == 12
    assert calls[0].args[0][-1] == {"role": "user", "content": LAB_PROMPT}
    assert all(m["content"] != "same answer" for m in calls[0].args[0])
    assert test.operations.start_request.await_count == 12
    assert test.operations.finish_request.await_count == 12
    experiment = await test.get(10, key)
    assert experiment["status"] == "completed"
    assert len(experiment["data"]["rows"]) == 12
    assert "разных текстов 1" in summary(experiment)
    assert "$0.012000" in summary(experiment)
    assert progress.await_args_list[-1].args == (12,)
    assert not await test.run(10, key, progress)
    assert test.llm.generate.await_count == 12


async def test_preview_does_not_call_model_and_all_entry_points_check_owner():
    test = service()
    key = await test.prepare(10)
    test.llm.generate.assert_not_awaited()
    for operation in (
        test.prepare(20),
        test.get(20, key),
        test.cancel(20, key),
        test.run(20, key, AsyncMock()),
    ):
        with pytest.raises(PermissionError):
            await operation
    test.llm.generate.assert_not_awaited()


@pytest.mark.parametrize(
    "prompt,mode", [(" ", "chat"), ("x" * 8001, "chat"), ("question", "web"), ("question", "music")]
)
async def test_invalid_custom_prompt_rejected_before_calls(prompt, mode):
    test = service()
    with pytest.raises(ValueError):
        await test.prepare(10, prompt, mode)
    test.llm.generate.assert_not_awaited()


async def test_cancellation_keeps_inflight_result_and_stops_remaining_calls():
    test = service()
    key = await test.prepare(10)

    async def stop(done):
        await test.cancel(10, key)

    assert await test.run(10, key, stop)
    experiment = await test.get(10, key)
    assert experiment["status"] == "cancelled"
    assert len(experiment["data"]["rows"]) == 1
    test.llm.generate.assert_awaited_once()


async def test_cancelled_draft_and_concurrent_double_click_never_start_extra_calls():
    test = service()
    cancelled = await test.prepare(10)
    await test.cancel(10, cancelled)
    assert not await test.run(10, cancelled, AsyncMock())
    key = await test.prepare(10)
    entered, release = asyncio.Event(), asyncio.Event()

    async def generation(*args, **kwargs):
        entered.set()
        await release.wait()
        return Generation(True, "answer")

    test.llm.generate.side_effect = generation
    task = asyncio.create_task(test.run(10, key, AsyncMock()))
    await asyncio.wait_for(entered.wait(), 1)
    assert not await test.run(10, key, AsyncMock())
    other = await test.prepare(10)
    assert not await test.run(10, other, AsyncMock())
    release.set()
    await task
    assert test.llm.generate.await_count == 12


async def test_error_and_truncated_rows_kept_unknown_cost_not_zero_and_exports_escaped():
    test = service()
    test.llm.generate.side_effect = [
        Generation(False, error_kind="timeout"),
        Generation(True, "<script>alert(1)</script>", truncated=True, finish_reason="length"),
    ] + [Generation(True, "ok", usage={"total_tokens": 9})] * 10
    key = await test.prepare(10, "<img src=x onerror=alert(1)>", "chat")
    assert await test.run(10, key, AsyncMock())
    experiment = await test.get(10, key)
    rows = experiment["data"]["rows"]
    assert rows[0]["error_kind"] == "timeout" and rows[0]["cost_usd"] is None
    assert rows[1]["truncated"] and rows[1]["finish_reason"] == "length"
    assert "известна для 0/12" in summary(experiment)
    report = export_html(experiment).decode()
    assert "<script>" not in report and "<img src" not in report
    assert "&lt;script&gt;" in report and "&lt;img" in report
    exported = json.loads(export_json(experiment))
    assert len(exported["rows"]) == 12
    assert "actor" not in exported and "id" not in exported
    assert exported["rows"][1]["answer"] == "<script>alert(1)</script>"


async def test_process_cancellation_preserves_partial_results_without_auto_resume():
    test = service()
    key = await test.prepare(10)
    entered = asyncio.Event()
    calls = 0

    async def generation(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            await asyncio.Event().wait()
        return Generation(True, "first")

    test.llm.generate.side_effect = generation
    task = asyncio.create_task(test.run(10, key, AsyncMock()))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    experiment = await test.get(10, key)
    assert experiment["status"] == "interrupted"
    assert len(experiment["data"]["rows"]) == 1
    assert not await test.run(10, key, AsyncMock())


async def test_notification_failure_does_not_discard_or_repeat_paid_calls():
    test = service()
    key = await test.prepare(10)
    assert await test.run(10, key, AsyncMock(side_effect=ConnectionError))
    assert len((await test.get(10, key))["data"]["rows"]) == 12


async def test_lab_format_is_checked_without_treating_it_as_content_quality():
    test = service()
    test.llm.generate.return_value = Generation(True, "1. A\n2. B\n3. C")
    key = await test.prepare(10)
    await test.run(10, key, AsyncMock())
    experiment = await test.get(10, key)
    assert all(r["format_ok"] for r in experiment["data"]["rows"])
    assert "<td>да</td>" in export_html(experiment).decode()


async def test_changed_timeout_or_missing_run_rejected_before_paid_calls():
    from dataclasses import replace

    test = service()
    key = await test.prepare(10)
    test.settings = replace(test.settings, llm_timeout_seconds=60)
    assert not await test.run(10, key, AsyncMock())
    assert not await test.run(10, uuid.uuid4(), AsyncMock())
    test.llm.generate.assert_not_awaited()
