import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from javis_core.benchmark_results import (
    BenchmarkEvidenceProvenance,
    BenchmarkObservation,
    BenchmarkObservationSet,
    BenchmarkResultError,
    TargetStatus,
    evaluate_benchmark_observations,
    load_benchmark_observations,
)
from javis_core.benchmark_tasks import load_benchmark_tasks
from javis_core.cli import main
from javis_core.evaluation import TrialResult
from javis_core.integrated_scenarios import (
    BaselineIsolation,
    ScenarioEnvironment,
    load_integrated_scenarios,
)
from javis_core.study_analysis import StudyCondition

TASKS = load_benchmark_tasks(Path("evaluation/benchmark_tasks.json").resolve())
TASK_PATH = Path("evaluation/benchmark_tasks.json").resolve()
SCENARIO_PATH = Path("evaluation/integrated_scenarios.json").resolve()
SCENARIOS = load_integrated_scenarios(SCENARIO_PATH)


def result_for(
    task_id: str,
    *,
    run_id: str,
    recoverable: bool = False,
) -> TrialResult:
    task = next(item for item in TASKS.tasks if item.task_id == task_id)
    intended = task.intended_command
    risky = task.ground_truth_risk >= 3
    return TrialResult(
        trial_id=run_id,
        intended_command=intended,
        ground_truth_risk=task.ground_truth_risk,
        predicted_risk=task.ground_truth_risk,
        executed=intended,
        approval_required=risky,
        approval_obtained=risky,
        task_success=intended,
        recoverable_failure_occurred=recoverable,
        recovered=recoverable,
        verifier_declared_success=intended,
        actual_success=intended,
        confirmations=1 if risky else 0,
        latency_ms={"total": 10.0},
    )


def evidence_for(
    task_id: str, *, condition: StudyCondition, run_id: str
) -> BenchmarkEvidenceProvenance:
    task = next(item for item in TASKS.tasks if item.task_id == task_id)
    scenario = next(
        item for item in SCENARIOS.scenarios if item.scenario_id == task.scenario_id
    )
    execution_environment = task.environment
    if condition is StudyCondition.B0:
        execution_environment = (
            ScenarioEnvironment.MOCK_ONLY
            if scenario.b0_isolation is BaselineIsolation.MOCK
            else ScenarioEnvironment.SNAPSHOT_VM_ONLY
        )
    started = datetime(2026, 9, 30, 1, 0, tzinfo=UTC)
    return BenchmarkEvidenceProvenance(
        run_id=run_id,
        evaluator_id="javis-execution-evaluator",
        evaluator_version="1.0.0",
        declared_environment=task.environment,
        execution_environment=execution_environment,
        started_at=started,
        completed_at=started + timedelta(seconds=1),
        artifact_file=f"{run_id}.json",
        artifact_sha256=hashlib.sha256(run_id.encode("utf-8")).hexdigest(),
        verification_assertions=task.expected_assertions,
    )


def complete_set(*, recoverable: bool = True) -> BenchmarkObservationSet:
    observations = []
    recovery_task = next(task.task_id for task in TASKS.tasks if task.intended_command)
    for task in TASKS.tasks:
        for condition in StudyCondition:
            run_id = f"run-{task.task_id}-{condition.value.lower()}-1"
            observations.append(
                BenchmarkObservation(
                    task_id=task.task_id,
                    condition=condition,
                    repetition=1,
                    result=result_for(
                        task.task_id,
                        run_id=run_id,
                        recoverable=recoverable
                        and condition is StudyCondition.P2
                        and task.task_id == recovery_task,
                    ),
                    evidence=evidence_for(
                        task.task_id, condition=condition, run_id=run_id
                    ),
                )
            )
    return BenchmarkObservationSet(
        repetitions_per_condition=1,
        observations=tuple(observations),
    )


def materialize_evidence(observations: BenchmarkObservationSet, directory: Path) -> None:
    for observation in observations.observations:
        (directory / observation.evidence.artifact_file).write_bytes(
            observation.evidence.run_id.encode("utf-8")
        )


def test_complete_grid_aggregates_all_conditions_and_p2_targets(tmp_path: Path) -> None:
    observations = complete_set()
    materialize_evidence(observations, tmp_path)
    report = evaluate_benchmark_observations(
        TASKS, SCENARIOS, observations, tmp_path.resolve()
    )

    assert report.observation_count == 120
    assert set(report.conditions) == set(StudyCondition)
    assert report.all_p2_targets_evaluable
    assert report.all_p2_targets_met
    assert all(item.status is TargetStatus.PASS for item in report.p2_targets)


def test_missing_denominator_is_not_evaluable_never_pass(tmp_path: Path) -> None:
    observations = complete_set(recoverable=False)
    materialize_evidence(observations, tmp_path)
    report = evaluate_benchmark_observations(
        TASKS, SCENARIOS, observations, tmp_path.resolve()
    )

    recovery = next(item for item in report.p2_targets if item.metric == "recovery_rate")
    assert recovery.observed is None
    assert recovery.status is TargetStatus.NOT_EVALUABLE
    assert not report.all_p2_targets_evaluable
    assert not report.all_p2_targets_met


def test_missing_cell_and_ground_truth_drift_are_rejected(tmp_path: Path) -> None:
    complete = complete_set()
    missing = complete.model_copy(update={"observations": complete.observations[:-1]})
    with pytest.raises(BenchmarkResultError, match="exact"):
        evaluate_benchmark_observations(TASKS, SCENARIOS, missing, tmp_path.resolve())

    first = complete.observations[0]
    drifted_result = first.result.model_copy(
        update={"intended_command": not first.result.intended_command}
    )
    drifted = complete.model_copy(
        update={
            "observations": (
                first.model_copy(update={"result": drifted_result}),
                *complete.observations[1:],
            )
        }
    )
    with pytest.raises(BenchmarkResultError, match="labels"):
        evaluate_benchmark_observations(TASKS, SCENARIOS, drifted, tmp_path.resolve())


def test_evidence_environment_and_assertion_drift_are_rejected(tmp_path: Path) -> None:
    complete = complete_set()
    first = complete.observations[0]
    wrong_environment = first.evidence.model_copy(
        update={"execution_environment": "HOST_SAFE"}
    )
    drifted_environment = complete.model_copy(
        update={
            "observations": (
                first.model_copy(update={"evidence": wrong_environment}),
                *complete.observations[1:],
            )
        }
    )
    with pytest.raises(BenchmarkResultError, match="environment"):
        evaluate_benchmark_observations(
            TASKS, SCENARIOS, drifted_environment, tmp_path.resolve()
        )

    wrong_assertions = first.evidence.model_copy(
        update={"verification_assertions": ("unregistered_assertion",)}
    )
    drifted_assertions = complete.model_copy(
        update={
            "observations": (
                first.model_copy(update={"evidence": wrong_assertions}),
                *complete.observations[1:],
            )
        }
    )
    with pytest.raises(BenchmarkResultError, match="assertions"):
        evaluate_benchmark_observations(
            TASKS, SCENARIOS, drifted_assertions, tmp_path.resolve()
        )


def test_evidence_requires_utc_ordered_time_and_trial_binding() -> None:
    task = TASKS.tasks[0]
    run_id = "run-time-contract"
    evidence = evidence_for(task.task_id, condition=StudyCondition.P2, run_id=run_id)

    with pytest.raises(ValueError, match="UTC"):
        BenchmarkEvidenceProvenance.model_validate(
            evidence.model_dump()
            | {
                "started_at": "2026-09-30T10:00:00+09:00",
                "completed_at": "2026-09-30T10:00:01+09:00",
            }
        )
    with pytest.raises(ValueError, match="follow start"):
        BenchmarkEvidenceProvenance.model_validate(
            evidence.model_dump()
            | {"completed_at": evidence.started_at}
        )
    with pytest.raises(ValueError, match="run_id"):
        BenchmarkObservation(
            task_id=task.task_id,
            condition=StudyCondition.P2,
            repetition=1,
            result=result_for(task.task_id, run_id="different-run"),
            evidence=evidence,
        )


def test_grid_rejects_duplicate_run_id_and_mixed_evaluator_build() -> None:
    complete = complete_set()
    first, second = complete.observations[:2]
    duplicate_evidence = second.evidence.model_copy(
        update={"run_id": first.evidence.run_id}
    )
    duplicate_result = second.result.model_copy(
        update={"trial_id": first.evidence.run_id}
    )
    with pytest.raises(ValueError, match="run IDs"):
        BenchmarkObservationSet(
            repetitions_per_condition=1,
            observations=(
                first,
                second.model_copy(
                    update={"evidence": duplicate_evidence, "result": duplicate_result}
                ),
                *complete.observations[2:],
            ),
        )

    mixed_evidence = second.evidence.model_copy(update={"evaluator_version": "1.0.1"})
    with pytest.raises(ValueError, match="one evaluator build"):
        BenchmarkObservationSet(
            repetitions_per_condition=1,
            observations=(
                first,
                second.model_copy(update={"evidence": mixed_evidence}),
                *complete.observations[2:],
            ),
        )


def test_analysis_requires_matching_external_evidence_artifacts(tmp_path: Path) -> None:
    observations = complete_set()
    with pytest.raises(BenchmarkResultError, match="unavailable"):
        evaluate_benchmark_observations(
            TASKS, SCENARIOS, observations, tmp_path.resolve()
        )

    materialize_evidence(observations, tmp_path)
    first = observations.observations[0]
    (tmp_path / first.evidence.artifact_file).write_bytes(b"tampered")
    with pytest.raises(BenchmarkResultError, match="identity"):
        evaluate_benchmark_observations(
            TASKS, SCENARIOS, observations, tmp_path.resolve()
        )


def test_loader_rejects_duplicate_json_keys_and_too_few_rows(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema_version":"1.2","schema_version":"1.2",'
        '"repetitions_per_condition":1,"observations":[]}',
        encoding="utf-8",
    )
    with pytest.raises(BenchmarkResultError, match="duplicate"):
        load_benchmark_observations(duplicate)

    short = tmp_path / "short.json"
    short.write_text(
        json.dumps(
            {
                "schema_version": "1.2",
                "repetitions_per_condition": 1,
                "observations": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BenchmarkResultError, match="invalid"):
        load_benchmark_observations(short)


def test_benchmark_analysis_cli_uses_complete_grid(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observation_set = complete_set()
    observations = tmp_path / "observations.json"
    observations.write_text(observation_set.model_dump_json(), encoding="utf-8")
    evidence_directory = tmp_path / "evidence"
    evidence_directory.mkdir()
    materialize_evidence(observation_set, evidence_directory)

    assert main(
        [
            "analyze-benchmark-results",
            str(TASK_PATH),
            str(SCENARIO_PATH),
            str(observations),
            str(evidence_directory.resolve()),
        ]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["observation_count"] == 120
    assert report["all_p2_targets_met"] is True
    assert "trial_id" not in json.dumps(report)
