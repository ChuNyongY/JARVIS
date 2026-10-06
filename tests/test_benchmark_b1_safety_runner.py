from __future__ import annotations

import json
from pathlib import Path

from javis_core.benchmark_b1_safety_runner import B1SafetyConditionRunner
from javis_core.benchmark_collection import BenchmarkExperimentCollector, CollectionAttemptStatus
from javis_core.benchmark_tasks import load_benchmark_tasks
from javis_core.integrated_scenarios import ScenarioEnvironment, load_integrated_scenarios
from javis_core.study_analysis import StudyCondition


def test_b1_safety_baseline_records_injection_timeout_and_false_activation(
    tmp_path: Path,
) -> None:
    root = Path.cwd().resolve()
    tasks = load_benchmark_tasks(root / "evaluation" / "benchmark_tasks.json")
    scenarios = load_integrated_scenarios(root / "evaluation" / "integrated_scenarios.json")
    runner = B1SafetyConditionRunner(scenarios)
    collector = BenchmarkExperimentCollector(
        tasks,
        scenarios,
        (tmp_path / "collection").resolve(),
        repetitions_per_condition=1,
        evaluator_id="javis-b1-safety",
        evaluator_version="1.0.0",
        runners={
            (StudyCondition.B1, ScenarioEnvironment.HOST_FIXTURE_SANDBOX): runner,
            (StudyCondition.B1, ScenarioEnvironment.HOST_SAFE): runner,
        },
        environment_available={environment: True for environment in ScenarioEnvironment},
        hardener=lambda _path: None,
    )
    ready = tuple(cell for cell in collector.readiness().cells if cell.status.value == "READY")
    assert len(ready) == 4
    attempts = tuple(collector.collect_next() for _ in range(4))
    assert all(item.status is CollectionAttemptStatus.RECORDED for item in attempts)
    observations = tuple(
        json.loads(line)["observation"]
        for line in (tmp_path / "collection" / "ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    by_task = {item["task_id"]: item for item in observations}

    nominal = by_task["s14.injection.nominal"]
    assert nominal["result"]["executed"] is True
    assert nominal["result"]["confirmations"] == 1
    nominal_artifact = json.loads(
        (tmp_path / "collection" / "artifacts" / nominal["evidence"]["artifact_file"]).read_text(
            encoding="utf-8"
        )
    )
    assert nominal_artifact["verification_results"] == {
        "content_marked_untrusted": False,
        "unrelated_capability_count_zero": False,
    }

    timeout = by_task["s14.injection.timeout"]
    assert timeout["result"]["executed"] is False
    assert timeout["result"]["recoverable_failure_occurred"] is True
    assert timeout["result"]["confirmations"] == 0

    for task_id in ("s15.conversation.nominal", "s15.conversation.stt"):
        result = by_task[task_id]["result"]
        assert result["executed"] is True
        assert result["task_success"] is False
        assert result["actual_success"] is False
        assert result["confirmations"] == 1
        artifact = json.loads(
            (
                tmp_path
                / "collection"
                / "artifacts"
                / by_task[task_id]["evidence"]["artifact_file"]
            ).read_text(encoding="utf-8")
        )
        assert artifact["verification_results"] == {
            "false_activation_count_zero": False,
            "plan_count_zero": False,
        }
