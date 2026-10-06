from __future__ import annotations

import json
from pathlib import Path

from javis_core.benchmark_collection import (
    BenchmarkExperimentCollector,
    CollectionAttemptStatus,
)
from javis_core.benchmark_nonverifying_runner import NonVerifyingMockConditionRunner
from javis_core.benchmark_tasks import load_benchmark_tasks
from javis_core.integrated_scenarios import (
    ScenarioEnvironment,
    load_integrated_scenarios,
)
from javis_core.study_analysis import StudyCondition


def test_b1_and_p1_mock_runner_preserves_condition_semantics(tmp_path: Path) -> None:
    root = Path.cwd().resolve()
    tasks = load_benchmark_tasks(root / "evaluation" / "benchmark_tasks.json")
    scenarios = load_integrated_scenarios(
        root / "evaluation" / "integrated_scenarios.json"
    )
    runner = NonVerifyingMockConditionRunner(scenarios)
    collector = BenchmarkExperimentCollector(
        tasks,
        scenarios,
        (tmp_path / "collection").resolve(),
        repetitions_per_condition=1,
        evaluator_id="javis-nonverifying-mock",
        evaluator_version="1.0.0",
        runners={
            (StudyCondition.B1, ScenarioEnvironment.MOCK_ONLY): runner,
            (StudyCondition.P1, ScenarioEnvironment.MOCK_ONLY): runner,
        },
        environment_available={environment: True for environment in ScenarioEnvironment},
        hardener=lambda _path: None,
    )

    ready = tuple(
        cell for cell in collector.readiness().cells if cell.status.value == "READY"
    )
    assert len(ready) == 8
    assert {cell.condition for cell in ready} == {
        StudyCondition.B1,
        StudyCondition.P1,
    }
    assert {cell.task_id for cell in ready} == {
        "s06.message.nominal",
        "s06.message.target",
        "s13.storage.nominal",
        "s13.storage.popup",
    }
    attempts = tuple(collector.collect_next() for _ in range(8))
    assert all(item.status is CollectionAttemptStatus.RECORDED for item in attempts)

    records = tuple(
        json.loads(line)
        for line in (tmp_path / "collection" / "ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    by_key = {
        (record["observation"]["condition"], record["observation"]["task_id"]): record[
            "observation"
        ]
        for record in records
    }
    for condition in ("B1", "P1"):
        nominal = by_key[(condition, "s06.message.nominal")]
        result = nominal["result"]
        assert result["executed"] is True
        assert result["approval_required"] is True
        assert result["approval_obtained"] is True
        assert result["confirmations"] == 1
        assert result["task_success"] is True
        assert result["actual_success"] is True
        assert result["verifier_declared_success"] is False
        artifact = json.loads(
            (
                tmp_path
                / "collection"
                / "artifacts"
                / nominal["evidence"]["artifact_file"]
            ).read_text(encoding="utf-8")
        )
        assert artifact["verification_results"]["approval_bound_to_payload"] is True

        mismatch = by_key[(condition, "s06.message.target")]["result"]
        assert mismatch["executed"] is False
        assert mismatch["approval_obtained"] is True
        assert mismatch["task_success"] is False
        assert mismatch["actual_success"] is False
        assert mismatch["recovered"] is False

        for task_id in ("s13.storage.nominal", "s13.storage.popup"):
            suggestion = by_key[(condition, task_id)]["result"]
            assert suggestion["executed"] is False
            assert suggestion["approval_required"] is False
            assert suggestion["confirmations"] == 0
            assert suggestion["task_success"] is True
            assert suggestion["verifier_declared_success"] is False

    assert by_key[("B1", "s06.message.nominal")]["result"]["predicted_risk"] == 0
    assert by_key[("P1", "s06.message.nominal")]["result"]["predicted_risk"] == 3
