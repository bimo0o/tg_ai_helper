import json
from unittest.mock import AsyncMock

import pytest

from app.llm import Generation, ToolCall
from app.routing_evaluation import arguments_match, classify, evaluate, load_dataset, main

DATASET = "docs/evaluation/lab2-routing-authored.json"


def test_authored_dataset_is_labeled_fixed_and_has_the_declared_distribution():
    dataset, digest = load_dataset(DATASET)
    assert dataset.source == "authored-supplementary-not-official"
    assert len(dataset.cases) == 12 and len(digest) == 64
    assert dataset.reference_time.isoformat() == "2030-01-10T10:00:00+03:00"
    relative = Generation(
        True,
        tool_calls=(
            ToolCall("a", "prepare_relative_reminder", {"text": "Test", "delay_seconds": 300}),
        ),
    )
    action, args = classify(relative, dataset.reference_time)
    assert action == "add_reminder" and args["remind_at"] == "2030-01-10T10:05:00+03:00"


def test_dry_run_never_loads_secrets_or_creates_llm_client(monkeypatch, capsys):
    from app import routing_evaluation as module

    def forbidden(*args, **kwargs):
        raise AssertionError("dry run touched secrets or API client")

    monkeypatch.setattr(module, "dotenv_values", forbidden)
    monkeypatch.setattr(module, "LLMClient", forbidden)
    monkeypatch.setattr("sys.argv", ["routing_evaluation"])
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["executed"] is False and result["paid_calls_max"] == 12


async def test_metrics_with_mock_model_are_not_a_live_quality_result():
    dataset, _ = load_dataset(DATASET)
    fake = AsyncMock()
    responses = []
    for case in dataset.cases:
        if case.expected_action == "answer":
            responses.append(Generation(True, "Synthetic answer"))
        else:
            name = (
                "request_clarification"
                if case.expected_action == "clarification"
                else case.expected_action
            )
            args = (
                {"question": "Уточни задачу и время."}
                if name == "request_clarification"
                else case.expected_arguments[0]
            )
            responses.append(Generation(True, tool_calls=(ToolCall(case.id, name, args),)))
    fake.generate.side_effect = responses
    snapshots = []
    report = await evaluate(
        dataset,
        fake,
        "synthetic-model",
        0.3,
        1500,
        on_progress=lambda rows: snapshots.append(len(rows)),
    )
    assert report["routing_correct"] == 12 and report["arguments_correct"] == 9
    assert fake.generate.await_count == 12
    assert snapshots == list(range(1, 13))


async def test_access_or_rate_limit_stops_after_one_attempt_and_preserves_partial_results():
    dataset, _ = load_dataset(DATASET)
    fake = AsyncMock()
    fake.generate.return_value = Generation(False, error_kind="http", http_status=402)
    report = await evaluate(dataset, fake, "synthetic-model", 0.3, 1500)
    assert report["completed"] is False and report["cases_executed"] == 1
    assert report["known_cost_usd"] is None and report["unknown_cost_calls"] == 1
    fake.generate.assert_awaited_once()


def test_argument_comparison_accepts_equivalent_instants_but_rejects_owner_and_wrong_date():
    expected = [{"text": "сохранить проект", "remind_at": "2030-01-10T10:30:00+03:00"}]
    assert arguments_match(
        "add_reminder", {"text": "Сохранить проект.", "remind_at": "2030-01-10T07:30:00Z"}, expected
    )
    assert not arguments_match("add_reminder", {**expected[0], "owner": 2}, expected)
    assert not arguments_match("get_schedule", {"date": "2030-01-11"}, [{"date": "2030-01-10"}])


def test_existing_report_is_not_overwritten_and_does_not_start_a_paid_run(tmp_path, monkeypatch):
    from app import routing_evaluation as module

    target = tmp_path / "report.json"
    target.write_text("original", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["routing_evaluation", "--execute", "--output", str(target)])
    monkeypatch.setattr(
        module, "dotenv_values", lambda *args, **kwargs: pytest.fail("read secrets")
    )
    assert main() == 1
    assert target.read_text(encoding="utf-8") == "original"
