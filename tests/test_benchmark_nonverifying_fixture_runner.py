from __future__ import annotations

import json
from pathlib import Path

from javis_core.benchmark_collection import BenchmarkExperimentCollector, CollectionAttemptStatus
from javis_core.benchmark_nonverifying_fixture_runner import (
    NonVerifyingFixtureConditionRunner,
)
from javis_core.benchmark_tasks import load_benchmark_tasks
from javis_core.integrated_scenarios import ScenarioEnvironment, load_integrated_scenarios
from javis_core.study_analysis import StudyCondition


def test_b1_and_p1_s04_use_one_task_confirmation_without_verifier(tmp_path: Path) -> None:
    root = Path.cwd().resolve()
    tasks = load_benchmark_tasks(root / "evaluation" / "benchmark_tasks.json")
    scenarios = load_integrated_scenarios(root / "evaluation" / "integrated_scenarios.json")
    runner = NonVerifyingFixtureConditionRunner(scenarios)
    collector = BenchmarkExperimentCollector(
        tasks,
        scenarios,
        (tmp_path / "collection").resolve(),
        repetitions_per_condition=1,
        evaluator_id="javis-nonverifying-fixture",
        evaluator_version="1.0.0",
        runners={
            (StudyCondition.B1, ScenarioEnvironment.HOST_FIXTURE_SANDBOX): runner,
            (StudyCondition.P1, ScenarioEnvironment.HOST_FIXTURE_SANDBOX): runner,
        },
        environment_available={environment: True for environment in ScenarioEnvironment},
        hardener=lambda _path: None,
    )
    ready = tuple(cell for cell in collector.readiness().cells if cell.status.value == "READY")
    assert len(ready) == 12
    assert {cell.task_id for cell in ready} == {
        "s04.organize.nominal",
        "s04.organize.exists",
        "s05.summary.nominal",
        "s05.summary.network",
        "s07.trash.nominal",
        "s07.trash.interference",
    }
    attempts = tuple(collector.collect_next() for _ in range(12))
    assert all(item.status is CollectionAttemptStatus.RECORDED for item in attempts)
    observations = tuple(
        json.loads(line)["observation"]
        for line in (tmp_path / "collection" / "ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    by_key = {(item["condition"], item["task_id"]): item for item in observations}
    for condition in ("B1", "P1"):
        nominal = by_key[(condition, "s04.organize.nominal")]["result"]
        assert nominal["executed"] is True
        assert nominal["approval_required"] is True
        assert nominal["approval_obtained"] is True
        assert nominal["confirmations"] == 1
        assert nominal["task_success"] is True
        assert nominal["actual_success"] is True
        assert nominal["verifier_declared_success"] is False

        exists = by_key[(condition, "s04.organize.exists")]
        assert exists["result"]["executed"] is False
        assert exists["result"]["task_success"] is False
        assert exists["result"]["recoverable_failure_occurred"] is True
        assert exists["result"]["recovered"] is False
        artifact = json.loads(
            (tmp_path / "collection" / "artifacts" / exists["evidence"]["artifact_file"]).read_text(
                encoding="utf-8"
            )
        )
        assert artifact["verification_results"] == {
            "all_expected_files_moved_once": False,
            "no_unexpected_file_moved": True,
        }

    assert by_key[("B1", "s04.organize.nominal")]["result"]["predicted_risk"] == 0
    assert by_key[("P1", "s04.organize.nominal")]["result"]["predicted_risk"] == 2

    for condition in ("B1", "P1"):
        nominal = by_key[(condition, "s05.summary.nominal")]["result"]
        assert nominal["executed"] is True
        assert nominal["confirmations"] == 1
        assert nominal["task_success"] is True
        assert nominal["verifier_declared_success"] is False

        network = by_key[(condition, "s05.summary.network")]["result"]
        assert network["executed"] is False
        assert network["task_success"] is False
        assert network["recoverable_failure_occurred"] is True
        assert network["recovered"] is False
        assert network["actual_success"] is False

    for condition in ("B1", "P1"):
        nominal = by_key[(condition, "s07.trash.nominal")]["result"]
        assert nominal["executed"] is True
        assert nominal["confirmations"] == 1
        assert nominal["task_success"] is True
        assert nominal["verifier_declared_success"] is False

        interference = by_key[(condition, "s07.trash.interference")]
        assert interference["result"]["executed"] is True
        assert interference["result"]["task_success"] is False
        assert interference["result"]["actual_success"] is False
        assert interference["result"]["recoverable_failure_occurred"] is True
        assert interference["result"]["recovered"] is False
        artifact = json.loads(
            (
                tmp_path / "collection" / "artifacts" / interference["evidence"]["artifact_file"]
            ).read_text(encoding="utf-8")
        )
        assert artifact["verification_results"] == {
            "exact_file_absent": True,
            "unrelated_files_unchanged": False,
        }
