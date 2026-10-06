from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from javis_core.benchmark_collection import (
    BenchmarkCollectionError,
    BenchmarkConditionRunner,
    BenchmarkExperimentCollector,
    BenchmarkRunOutcome,
    CollectionAttemptStatus,
    CollectionReadinessStatus,
)
from javis_core.benchmark_results import evaluate_benchmark_observations
from javis_core.benchmark_tasks import BenchmarkTask, FaultProfile, load_benchmark_tasks
from javis_core.evaluation import TrialResult
from javis_core.integrated_scenarios import (
    ScenarioEnvironment,
    load_integrated_scenarios,
)
from javis_core.study_analysis import StudyCondition

TASKS = load_benchmark_tasks(Path("evaluation/benchmark_tasks.json").resolve())
SCENARIOS = load_integrated_scenarios(
    Path("evaluation/integrated_scenarios.json").resolve()
)


class AdvancingClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 4, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=1)
        return current


class FixedClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 4, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


class PassingRunner:
    def supports(self, cell, task: BenchmarkTask) -> bool:  # type: ignore[no-untyped-def]
        return True

    def run(self, cell, task: BenchmarkTask) -> BenchmarkRunOutcome:  # type: ignore[no-untyped-def]
        intended = task.intended_command
        risky = task.ground_truth_risk >= 3
        recoverable = intended and task.fault_profile is not FaultProfile.NONE
        return BenchmarkRunOutcome(
            result=TrialResult(
                trial_id=cell.run_id,
                intended_command=intended,
                ground_truth_risk=task.ground_truth_risk,
                predicted_risk=task.ground_truth_risk,
                executed=intended,
                approval_required=risky,
                approval_obtained=risky,
                task_success=intended,
                recoverable_failure_occurred=recoverable,
                recovered=recoverable,
                verifier_declared_success=True,
                actual_success=True,
                confirmations=1 if risky else 0,
                latency_ms={"total": 1.0},
            ),
            assertion_results={name: True for name in task.expected_assertions},
        )


class AssertionFailureRunner(PassingRunner):
    def run(self, cell, task: BenchmarkTask) -> BenchmarkRunOutcome:  # type: ignore[no-untyped-def]
        outcome = super().run(cell, task)
        first = task.expected_assertions[0]
        return outcome.model_copy(
            update={"assertion_results": {**outcome.assertion_results, first: False}}
        )


class MissingAssertionRunner(PassingRunner):
    def run(self, cell, task: BenchmarkTask) -> BenchmarkRunOutcome:  # type: ignore[no-untyped-def]
        outcome = super().run(cell, task)
        remaining = dict(outcome.assertion_results)
        remaining.pop(task.expected_assertions[0])
        return outcome.model_copy(update={"assertion_results": remaining})


class NegativeOutcomeRunner(PassingRunner):
    def run(self, cell, task: BenchmarkTask) -> BenchmarkRunOutcome:  # type: ignore[no-untyped-def]
        outcome = super().run(cell, task)
        first = task.expected_assertions[0]
        return BenchmarkRunOutcome(
            result=outcome.result.model_copy(
                update={
                    "executed": False,
                    "task_success": False,
                    "verifier_declared_success": False,
                    "actual_success": False,
                }
            ),
            assertion_results={**outcome.assertion_results, first: False},
        )


class SelectiveRunner(PassingRunner):
    def __init__(self, supported_task_id: str) -> None:
        self._supported_task_id = supported_task_id

    def supports(self, cell, task: BenchmarkTask) -> bool:  # type: ignore[no-untyped-def]
        return task.task_id == self._supported_task_id


def runner_mapping(
    runner: BenchmarkConditionRunner,
) -> dict[tuple[StudyCondition, ScenarioEnvironment], BenchmarkConditionRunner]:
    return {
        (condition, environment): runner
        for condition in StudyCondition
        for environment in ScenarioEnvironment
    }


def availability(value: bool = True) -> dict[ScenarioEnvironment, bool]:
    return {environment: value for environment in ScenarioEnvironment}


def collector(
    tmp_path: Path,
    *,
    runners: dict[
        tuple[StudyCondition, ScenarioEnvironment], BenchmarkConditionRunner
    ]
    | None = None,
    environments: dict[ScenarioEnvironment, bool] | None = None,
) -> BenchmarkExperimentCollector:
    root = tmp_path / "collection"
    return BenchmarkExperimentCollector(
        TASKS,
        SCENARIOS,
        root.resolve(),
        repetitions_per_condition=1,
        evaluator_id="javis-evaluator",
        evaluator_version="1.0.0",
        runners=runners or {},
        environment_available=environments or availability(),
        clock=AdvancingClock(),
        hardener=lambda _path: None,
    )


def test_schedule_is_exact_and_b0_uses_declared_isolation(tmp_path: Path) -> None:
    subject = collector(tmp_path)

    assert len(subject.schedule) == 120
    assert len({cell.cell_id for cell in subject.schedule}) == 120
    assert all(
        cell.execution_environment
        in {ScenarioEnvironment.MOCK_ONLY, ScenarioEnvironment.SNAPSHOT_VM_ONLY}
        for cell in subject.schedule
        if cell.condition is StudyCondition.B0
    )
    assert all(
        cell.execution_environment is cell.declared_environment
        for cell in subject.schedule
        if cell.condition is not StudyCondition.B0
    )


def test_readiness_keeps_unavailable_cells_out_of_observations(tmp_path: Path) -> None:
    environments = availability()
    environments[ScenarioEnvironment.SNAPSHOT_VM_ONLY] = False
    subject = collector(tmp_path, environments=environments)

    report = subject.readiness()

    assert report.completed == 0
    assert report.environment_unavailable > 0
    assert report.runner_unavailable > 0
    assert not report.complete
    assert all(
        cell.status
        in {
            CollectionReadinessStatus.ENVIRONMENT_UNAVAILABLE,
            CollectionReadinessStatus.RUNNER_UNAVAILABLE,
        }
        for cell in report.cells
    )
    assert not (tmp_path / "collection" / "ledger.jsonl").exists()


def test_collect_resume_and_privacy_minimized_ledger(tmp_path: Path) -> None:
    subject = collector(tmp_path, runners=runner_mapping(PassingRunner()))
    first = subject.collect_next()
    resumed = collector(tmp_path, runners=runner_mapping(PassingRunner()))
    second = resumed.collect_next()

    assert first.status is CollectionAttemptStatus.RECORDED
    assert second.status is CollectionAttemptStatus.RECORDED
    assert first.cell_id != second.cell_id
    report = resumed.readiness()
    assert report.completed == 2
    raw = (tmp_path / "collection" / "ledger.jsonl").read_text(encoding="utf-8")
    assert TASKS.tasks[0].utterance not in raw
    assert str(tmp_path) not in raw
    assert "exception" not in raw.casefold()


def test_equal_resolution_wall_clock_uses_monotonic_elapsed_time(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "collection").resolve()
    subject = BenchmarkExperimentCollector(
        TASKS,
        SCENARIOS,
        root,
        repetitions_per_condition=1,
        evaluator_id="javis-evaluator",
        evaluator_version="1.0.0",
        runners=runner_mapping(PassingRunner()),
        environment_available=availability(),
        clock=FixedClock(),
        hardener=lambda _path: None,
    )

    assert subject.collect_next().status is CollectionAttemptStatus.RECORDED
    record = json.loads((root / "ledger.jsonl").read_text(encoding="utf-8"))
    evidence = record["observation"]["evidence"]
    started = datetime.fromisoformat(evidence["started_at"].replace("Z", "+00:00"))
    completed = datetime.fromisoformat(evidence["completed_at"].replace("Z", "+00:00"))
    assert completed > started


def test_missing_assertion_never_creates_observation(tmp_path: Path) -> None:
    subject = collector(tmp_path, runners=runner_mapping(MissingAssertionRunner()))

    attempt = subject.collect_next()

    assert attempt.status is CollectionAttemptStatus.ASSERTION_FAILED
    assert subject.readiness().completed == 0
    assert not (tmp_path / "collection" / "ledger.jsonl").exists()
    assert not tuple((tmp_path / "collection" / "artifacts").iterdir())


def test_failed_external_assertion_is_recorded_independently_of_system_verifier(
    tmp_path: Path,
) -> None:
    subject = collector(tmp_path, runners=runner_mapping(AssertionFailureRunner()))

    attempt = subject.collect_next()

    assert attempt.status is CollectionAttemptStatus.RECORDED
    assert subject.readiness().completed == 1


def test_truthful_negative_verification_is_recorded_as_an_observation(
    tmp_path: Path,
) -> None:
    subject = collector(tmp_path, runners=runner_mapping(NegativeOutcomeRunner()))

    attempt = subject.collect_next()

    assert attempt.status is CollectionAttemptStatus.RECORDED
    assert subject.readiness().completed == 1
    assert attempt.cell_id is not None
    artifact = json.loads(
        (
            tmp_path
            / "collection"
            / "artifacts"
            / f"{attempt.cell_id}.json"
        ).read_text(encoding="utf-8")
    )
    assert artifact["schema_version"] == "1.1"
    assert not all(artifact["verification_results"].values())
    assert artifact["result"]["verifier_declared_success"] is False


def test_unavailable_cell_does_not_block_later_ready_cell(tmp_path: Path) -> None:
    initial = collector(tmp_path)
    first = initial.schedule[0]
    later = next(
        cell
        for cell in initial.schedule
        if cell.execution_environment is not first.execution_environment
    )
    environments = availability()
    environments[first.execution_environment] = False
    subject = collector(
        tmp_path,
        runners={
            (later.condition, later.execution_environment): PassingRunner(),
        },
        environments=environments,
    )

    attempt = subject.collect_next()

    assert attempt.status is CollectionAttemptStatus.RECORDED
    assert attempt.cell_id == later.cell_id


def test_partial_runner_marks_only_supported_cells_ready_and_skips_others(
    tmp_path: Path,
) -> None:
    baseline = collector(tmp_path)
    supported = next(
        cell
        for cell in baseline.schedule
        if cell.condition is StudyCondition.P2
        and cell.execution_environment is ScenarioEnvironment.HOST_SAFE
    )
    runner = SelectiveRunner(supported.task_id)
    subject = collector(
        tmp_path,
        runners={(supported.condition, supported.execution_environment): runner},
    )

    report = subject.readiness()
    ready_ids = {
        cell.cell_id
        for cell in report.cells
        if cell.status is CollectionReadinessStatus.READY
    }
    attempt = subject.collect_next()

    assert ready_ids == {supported.cell_id}
    assert attempt.status is CollectionAttemptStatus.RECORDED
    assert attempt.cell_id == supported.cell_id


def test_tampered_artifact_blocks_resume_and_export(tmp_path: Path) -> None:
    subject = collector(tmp_path, runners=runner_mapping(PassingRunner()))
    attempt = subject.collect_next()
    assert attempt.cell_id is not None
    artifact = tmp_path / "collection" / "artifacts" / f"{attempt.cell_id}.json"
    artifact.write_text("{}", encoding="utf-8")

    with pytest.raises(BenchmarkCollectionError, match="identity"):
        subject.readiness()
    with pytest.raises(BenchmarkCollectionError, match="identity"):
        subject.export((tmp_path / "observations.json").resolve())


def test_orphan_artifact_blocks_collection_before_any_run(tmp_path: Path) -> None:
    subject = collector(tmp_path, runners=runner_mapping(PassingRunner()))
    orphan = tmp_path / "collection" / "artifacts" / "orphan.json"
    orphan.write_text("{}", encoding="utf-8")

    with pytest.raises(BenchmarkCollectionError, match="orphan"):
        subject.readiness()
    with pytest.raises(BenchmarkCollectionError, match="orphan"):
        subject.collect_next()


def test_evaluator_identity_drift_blocks_resume(tmp_path: Path) -> None:
    subject = collector(tmp_path, runners=runner_mapping(PassingRunner()))
    assert subject.collect_next().status is CollectionAttemptStatus.RECORDED
    ledger = tmp_path / "collection" / "ledger.jsonl"
    record = json.loads(ledger.read_text(encoding="utf-8"))
    record["observation"]["evidence"]["evaluator_version"] = "2.0.0"
    ledger.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkCollectionError, match="drifted"):
        subject.readiness()


def test_incomplete_collection_cannot_export(tmp_path: Path) -> None:
    subject = collector(tmp_path, runners=runner_mapping(PassingRunner()))
    subject.collect_next()

    with pytest.raises(BenchmarkCollectionError, match="incomplete"):
        subject.export((tmp_path / "observations.json").resolve())


def test_complete_real_runner_grid_exports_and_analyzes(tmp_path: Path) -> None:
    subject = collector(tmp_path, runners=runner_mapping(PassingRunner()))
    for _ in range(120):
        assert subject.collect_next().status is CollectionAttemptStatus.RECORDED
    assert subject.collect_next().status is CollectionAttemptStatus.COMPLETE

    destination = (tmp_path / "observations.json").resolve()
    observations = subject.export(destination)
    report = evaluate_benchmark_observations(
        TASKS,
        SCENARIOS,
        observations,
        (tmp_path / "collection" / "artifacts").resolve(),
    )

    assert observations.schema_version == "1.2"
    assert len(observations.observations) == 120
    assert report.all_p2_targets_met
    assert subject.readiness().complete
    raw = json.loads(destination.read_text(encoding="utf-8"))
    assert raw["schema_version"] == "1.2"
