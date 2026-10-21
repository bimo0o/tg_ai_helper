import csv
import json

import pytest

from scripts.experiment import ExperimentRow, follows_format, write_results


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1. One\n2. Two\n3. Three", True),
        ("intro\n1. One\n2. Two\n3. Three", False),
        ("1. One\n2. Two", False),
        ("1. \n2. Two\n3. Three", False),
    ],
)
def test_format_checker(text, expected):
    assert follows_format(text) is expected


def test_export_helpers_preserve_answer_without_running_experiment(tmp_path):
    row = ExperimentRow(1, 0.3, 15, "да", "Оценить вручную", 10, 5, 15, "1. a\n2. b\n3. c")
    write_results(tmp_path, [row], {"model": "example-model"})
    with (tmp_path / "results.csv").open(encoding="utf-8-sig", newline="") as source:
        result = list(csv.DictReader(source))
    assert result[0]["answer"] == row.answer
    assert "| 1 | 0.3 | 15 | да |" in (tmp_path / "results.md").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "metadata.json").read_text()) == {"model": "example-model"}
