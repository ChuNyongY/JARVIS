import json
from pathlib import Path

import pytest

from javis_core.benchmark_tasks import (
    BenchmarkTaskError,
    load_benchmark_tasks,
    validate_benchmark_contract,
)
from javis_core.cli import main
from javis_core.integrated_scenarios import load_integrated_scenarios
from javis_core.registry import default_registry

TASKS = Path("evaluation/benchmark_tasks.json").resolve()
SCENARIOS = Path("evaluation/integrated_scenarios.json").resolve()


def test_authoritative_30_task_manifest_covers_and_matches_all_scenarios() -> None:
    benchmark = load_benchmark_tasks(TASKS)
    scenarios = load_integrated_scenarios(SCENARIOS)

    validate_benchmark_contract(benchmark, scenarios, default_registry())

    assert len(benchmark.tasks) == 30
    assert {task.scenario_id for task in benchmark.tasks} == {
        f"S{number:02d}" for number in range(1, 16)
    }
    assert sum(task.fault_profile.value != "NONE" for task in benchmark.tasks) >= 10


def test_loader_rejects_duplicate_keys_and_short_task_set(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":"1.0","schema_version":"1.0","tasks":[]}',
        encoding="utf-8",
    )
    with pytest.raises(BenchmarkTaskError, match="duplicate"):
        load_benchmark_tasks(duplicate)

    short = tmp_path / "short.json"
    short.write_text(json.dumps({"schema_version": "1.0", "tasks": []}), encoding="utf-8")
    with pytest.raises(BenchmarkTaskError, match="invalid"):
        load_benchmark_tasks(short)

    original = json.loads(TASKS.read_text(encoding="utf-8"))
    for index, (field, value) in enumerate(
        (("intended_command", "false"), ("ground_truth_risk", "4"))
    ):
        document = json.loads(json.dumps(original))
        document["tasks"][0][field] = value
        path = tmp_path / f"coercion-{index}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        with pytest.raises(BenchmarkTaskError, match="invalid"):
            load_benchmark_tasks(path)


def test_contract_rejects_environment_capability_assertion_and_risk_drift(
    tmp_path: Path,
) -> None:
    original = json.loads(TASKS.read_text(encoding="utf-8"))
    mutations = (
        ("environment", "MOCK_ONLY"),
        ("required_capabilities", ["system.info.read"]),
        ("expected_assertions", ["invented_assertion"]),
        ("ground_truth_risk", 0),
    )
    for index, (field, value) in enumerate(mutations):
        document = json.loads(json.dumps(original))
        target = document["tasks"][16]
        target[field] = value
        path = tmp_path / f"drift-{index}.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        benchmark = load_benchmark_tasks(path)
        with pytest.raises(BenchmarkTaskError, match="drift|risk"):
            validate_benchmark_contract(
                benchmark,
                load_integrated_scenarios(SCENARIOS),
                default_registry(),
            )


def test_benchmark_validation_cli_reports_structure_without_utterances(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["validate-benchmark-tasks", str(TASKS), str(SCENARIOS)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "schema_version": "1.0",
        "task_count": 30,
        "scenario_count": 15,
        "fault_task_count": 15,
        "valid": True,
    }
