from __future__ import annotations

import json
from pathlib import Path

from javis_core.benchmark_b0_runner import DirectB0MockScenarioRunner
from javis_core.benchmark_collection import (
    BenchmarkExperimentCollector,
    CollectionAttemptStatus,
)
from javis_core.benchmark_tasks import load_benchmark_tasks
from javis_core.integrated_scenarios import (
    ScenarioEnvironment,
    load_integrated_scenarios,
)
from javis_core.study_analysis import StudyCondition


def test_direct_b0_runner_collects_only_mock_isolated_cells(tmp_path: Path) -> None:
    root = Path.cwd().resolve()
    tasks = load_benchmark_tasks(root / "evaluation" / "benchmark_tasks.json")
    scenarios = load_integrated_scenarios(
        root / "evaluation" / "integrated_scenarios.json"
    )
    runner = DirectB0MockScenarioRunner(scenarios)
    collector = BenchmarkExperimentCollector(
        tasks,
        scenarios,
        (tmp_path / "collection").resolve(),
        repetitions_per_condition=1,
        evaluator_id="javis-b0-mock",
        evaluator_version="1.0.0",
        runners={(StudyCondition.B0, ScenarioEnvironment.MOCK_ONLY): runner},
        environment_available={
            ScenarioEnvironment.HOST_SAFE: True,
            ScenarioEnvironment.HOST_FIXTURE_SANDBOX: True,
            ScenarioEnvironment.MOCK_ONLY: True,
            ScenarioEnvironment.SNAPSHOT_VM_ONLY: False,
        },
        hardener=lambda _path: None,
    )

    ready = tuple(
        cell for cell in collector.readiness().cells if cell.status.value == "READY"
    )
    assert {cell.task_id for cell in ready} == {
        "s06.message.nominal",
        "s06.message.target",
        "s13.storage.nominal",
        "s13.storage.popup",
    }
    attempts = tuple(collector.collect_next() for _ in range(4))
    assert all(item.status is CollectionAttemptStatus.RECORDED for item in attempts)

    records = tuple(
        json.loads(line)
        for line in (tmp_path / "collection" / "ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    by_task = {
        record["observation"]["task_id"]: record["observation"]
        for record in records
    }
    nominal = by_task["s06.message.nominal"]
    assert nominal["condition"] == "B0"
    assert nominal["result"]["executed"] is True
    assert nominal["result"]["approval_required"] is True
    assert nominal["result"]["approval_obtained"] is False
    assert nominal["result"]["task_success"] is True
    assert nominal["result"]["verifier_declared_success"] is False
    assert nominal["result"]["actual_success"] is True
    nominal_artifact = json.loads(
        (
            tmp_path
            / "collection"
            / "artifacts"
            / nominal["evidence"]["artifact_file"]
        ).read_text(encoding="utf-8")
    )
    assert nominal_artifact["verification_results"] == {
        "approval_bound_to_payload": False,
        "message_content_matches": True,
        "recipient_matches": True,
    }

    mismatch = by_task["s06.message.target"]["result"]
    assert mismatch["executed"] is False
    assert mismatch["task_success"] is False
    assert mismatch["actual_success"] is False
    for task_id in ("s13.storage.nominal", "s13.storage.popup"):
        result = by_task[task_id]["result"]
        assert result["executed"] is False
        assert result["task_success"] is True
        assert result["verifier_declared_success"] is False
        assert result["actual_success"] is True


def test_direct_b0_runner_rejects_non_b0_and_non_mock_cells(tmp_path: Path) -> None:
    root = Path.cwd().resolve()
    tasks = load_benchmark_tasks(root / "evaluation" / "benchmark_tasks.json")
    scenarios = load_integrated_scenarios(
        root / "evaluation" / "integrated_scenarios.json"
    )
    runner = DirectB0MockScenarioRunner(scenarios)
    collector = BenchmarkExperimentCollector(
        tasks,
        scenarios,
        (tmp_path / "collection").resolve(),
        repetitions_per_condition=1,
        evaluator_id="javis-b0-mock",
        evaluator_version="1.0.0",
        runners={(StudyCondition.B0, ScenarioEnvironment.MOCK_ONLY): runner},
        environment_available={environment: True for environment in ScenarioEnvironment},
        hardener=lambda _path: None,
    )

    supported = tuple(
        cell
        for cell in collector.schedule
        if runner.supports(
            cell,
            next(task for task in tasks.tasks if task.task_id == cell.task_id),
        )
    )
    assert len(supported) == 4
    assert all(cell.condition is StudyCondition.B0 for cell in supported)
    assert all(
        cell.execution_environment is ScenarioEnvironment.MOCK_ONLY
        for cell in supported
    )
