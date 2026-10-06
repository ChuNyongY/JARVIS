from __future__ import annotations

import json
from pathlib import Path

from javis_core.benchmark_collection import (
    BenchmarkExperimentCollector,
    CollectionAttemptStatus,
)
from javis_core.benchmark_nonverifying_host_runner import (
    NonVerifyingHostConditionRunner,
)
from javis_core.benchmark_tasks import load_benchmark_tasks
from javis_core.integrated_scenarios import (
    ScenarioEnvironment,
    load_integrated_scenarios,
)
from javis_core.study_analysis import StudyCondition


def test_b1_and_p1_host_actions_are_single_pass_without_recovery(tmp_path: Path) -> None:
    class FakeVolume:
        def __init__(self) -> None:
            self.value = 55.0
            instances.append(self)

        def read_percent(self) -> float:
            return self.value

        def set_percent(self, percent: float) -> None:
            self.value = percent

    instances: list[FakeVolume] = []

    root = Path.cwd().resolve()
    tasks = load_benchmark_tasks(root / "evaluation" / "benchmark_tasks.json")
    scenarios = load_integrated_scenarios(root / "evaluation" / "integrated_scenarios.json")
    runner = NonVerifyingHostConditionRunner(
        scenarios,
        project_root=root,
        volume_backend_factory=FakeVolume,
    )
    collector = BenchmarkExperimentCollector(
        tasks,
        scenarios,
        (tmp_path / "collection").resolve(),
        repetitions_per_condition=1,
        evaluator_id="javis-nonverifying-host",
        evaluator_version="1.0.0",
        runners={
            (StudyCondition.B1, ScenarioEnvironment.HOST_SAFE): runner,
            (StudyCondition.P1, ScenarioEnvironment.HOST_SAFE): runner,
        },
        environment_available={environment: True for environment in ScenarioEnvironment},
        hardener=lambda _path: None,
    )

    ready = tuple(cell for cell in collector.readiness().cells if cell.status.value == "READY")
    assert len(ready) == 8
    assert {cell.task_id for cell in ready} == {
        "s02.volume.nominal",
        "s02.volume.delay",
        "s11.system.nominal",
        "s11.system.timeout",
    }
    attempts = tuple(collector.collect_next() for _ in range(8))
    assert all(item.status is CollectionAttemptStatus.RECORDED for item in attempts)
    records = tuple(
        json.loads(line)["observation"]
        for line in (tmp_path / "collection" / "ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    by_key = {(item["condition"], item["task_id"]): item for item in records}
    for condition in ("B1", "P1"):
        nominal = by_key[(condition, "s11.system.nominal")]["result"]
        assert nominal["executed"] is True
        assert nominal["task_success"] is True
        assert nominal["actual_success"] is True
        assert nominal["verifier_declared_success"] is False
        assert nominal["confirmations"] == 0

        timeout = by_key[(condition, "s11.system.timeout")]["result"]
        assert timeout["executed"] is False
        assert timeout["task_success"] is False
        assert timeout["actual_success"] is False
        assert timeout["recoverable_failure_occurred"] is True
        assert timeout["recovered"] is False
        assert timeout["verifier_declared_success"] is False

        volume = by_key[(condition, "s02.volume.nominal")]["result"]
        assert volume["executed"] is True
        assert volume["task_success"] is True
        assert volume["actual_success"] is True
        assert volume["verifier_declared_success"] is False
        assert volume["confirmations"] == (1 if condition == "B1" else 0)

        delay = by_key[(condition, "s02.volume.delay")]["result"]
        assert delay["executed"] is False
        assert delay["task_success"] is False
        assert delay["recoverable_failure_occurred"] is True
        assert delay["recovered"] is False

    assert len(instances) == 4
    assert all(item.value == 55.0 for item in instances)
