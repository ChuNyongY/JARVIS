from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .benchmark_tasks import BenchmarkTaskManifest
from .evaluation import EvaluationReport, TrialResult, evaluate_trials
from .integrated_scenarios import (
    BaselineIsolation,
    IntegratedScenarioManifest,
    ScenarioEnvironment,
)
from .models import FrozenModel
from .study_analysis import StudyCondition


class BenchmarkResultError(ValueError):
    pass


class TargetStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class BenchmarkEvidenceProvenance(FrozenModel):
    run_id: str = Field(min_length=3, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    evaluator_id: str = Field(
        min_length=3,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9._-]+$",
    )
    evaluator_version: str = Field(
        pattern=r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
    )
    declared_environment: ScenarioEnvironment
    execution_environment: ScenarioEnvironment
    started_at: AwareDatetime
    completed_at: AwareDatetime
    artifact_file: str = Field(
        min_length=3,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9._-]+$",
    )
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_assertions: tuple[str, ...] = Field(min_length=1, max_length=12)

    @field_validator("verification_assertions")
    @classmethod
    def unique_assertion_tokens(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        token = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
        if len(values) != len(set(values)) or any(
            token.fullmatch(value) is None for value in values
        ):
            raise ValueError("verification assertion IDs must be unique")
        return values

    @model_validator(mode="after")
    def validate_timestamps(self) -> BenchmarkEvidenceProvenance:
        if (
            self.started_at.utcoffset() != timedelta(0)
            or self.completed_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("benchmark evidence timestamps must use UTC")
        if self.completed_at <= self.started_at:
            raise ValueError("benchmark evidence completion must follow start")
        return self


class BenchmarkObservation(FrozenModel):
    task_id: str = Field(min_length=3, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    condition: StudyCondition
    repetition: int = Field(ge=1, le=20)
    result: TrialResult
    evidence: BenchmarkEvidenceProvenance

    @model_validator(mode="after")
    def bind_trial_to_evidence(self) -> BenchmarkObservation:
        if self.result.trial_id != self.evidence.run_id:
            raise ValueError("trial_id must match evidence run_id")
        return self


class BenchmarkObservationSet(FrozenModel):
    schema_version: str = Field(default="1.2", pattern=r"^1\.2$")
    repetitions_per_condition: int = Field(ge=1, le=20)
    observations: tuple[BenchmarkObservation, ...] = Field(min_length=120, max_length=4000)

    @model_validator(mode="after")
    def unique_cells(self) -> BenchmarkObservationSet:
        cells = [
            (item.task_id, item.condition, item.repetition)
            for item in self.observations
        ]
        if len(cells) != len(set(cells)):
            raise ValueError("benchmark observation cells must be unique")
        run_ids = [item.evidence.run_id for item in self.observations]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("benchmark evidence run IDs must be unique")
        artifact_files = [item.evidence.artifact_file for item in self.observations]
        if len(artifact_files) != len(set(artifact_files)):
            raise ValueError("benchmark evidence artifact files must be unique")
        evaluator_versions = {
            (item.evidence.evaluator_id, item.evidence.evaluator_version)
            for item in self.observations
        }
        if len(evaluator_versions) != 1:
            raise ValueError("benchmark grid must use one evaluator build")
        return self


class BenchmarkTargetResult(FrozenModel):
    metric: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    operator: str = Field(pattern=r"^(?:>=|<=|==)$")
    threshold: float = Field(ge=0, le=1)
    observed: float | None = Field(default=None, ge=0)
    status: TargetStatus

    @model_validator(mode="after")
    def status_matches_observation(self) -> BenchmarkTargetResult:
        if (self.observed is None) != (self.status is TargetStatus.NOT_EVALUABLE):
            raise ValueError("target status and observed value are inconsistent")
        return self


class BenchmarkEvaluationReport(FrozenModel):
    schema_version: str = "1.0"
    task_count: int = Field(ge=30, le=50)
    repetitions_per_condition: int = Field(ge=1, le=20)
    observation_count: int = Field(ge=120, le=4000)
    conditions: dict[StudyCondition, EvaluationReport]
    p2_targets: tuple[BenchmarkTargetResult, ...] = Field(min_length=4, max_length=4)
    all_p2_targets_evaluable: bool
    all_p2_targets_met: bool

    @model_validator(mode="after")
    def validate_summary(self) -> BenchmarkEvaluationReport:
        if set(self.conditions) != set(StudyCondition):
            raise ValueError("all B0/B1/P1/P2 condition reports are required")
        evaluable = all(
            item.status is not TargetStatus.NOT_EVALUABLE for item in self.p2_targets
        )
        passed = all(item.status is TargetStatus.PASS for item in self.p2_targets)
        if self.all_p2_targets_evaluable != evaluable or self.all_p2_targets_met != passed:
            raise ValueError("benchmark target summary is inconsistent")
        return self


def load_benchmark_observations(
    path: Path,
    *,
    max_bytes: int = 8 * 1024 * 1024,
) -> BenchmarkObservationSet:
    if max_bytes < 2 or path.is_symlink() or not path.is_file():
        raise BenchmarkResultError("invalid benchmark observation file")
    try:
        if path.stat().st_size > max_bytes:
            raise BenchmarkResultError("benchmark observations exceeded size limit")
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raise BenchmarkResultError("benchmark observations exceeded size limit")
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
        return BenchmarkObservationSet.model_validate(document)
    except BenchmarkResultError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise BenchmarkResultError("invalid benchmark observations") from error


def evaluate_benchmark_observations(
    tasks: BenchmarkTaskManifest,
    scenarios: IntegratedScenarioManifest,
    observations: BenchmarkObservationSet,
    evidence_directory: Path,
) -> BenchmarkEvaluationReport:
    task_by_id = {task.task_id: task for task in tasks.tasks}
    scenario_by_id = {scenario.scenario_id: scenario for scenario in scenarios.scenarios}
    expected_cells = {
        (task.task_id, condition, repetition)
        for task in tasks.tasks
        for condition in StudyCondition
        for repetition in range(1, observations.repetitions_per_condition + 1)
    }
    actual_cells = {
        (item.task_id, item.condition, item.repetition)
        for item in observations.observations
    }
    if actual_cells != expected_cells:
        raise BenchmarkResultError(
            "benchmark observations must contain the exact task-condition-repetition grid"
        )
    for item in observations.observations:
        task = task_by_id.get(item.task_id)
        if task is None:
            raise BenchmarkResultError("benchmark observation references an unknown task")
        if (
            item.result.intended_command != task.intended_command
            or item.result.ground_truth_risk is not task.ground_truth_risk
        ):
            raise BenchmarkResultError("benchmark ground-truth labels drifted")
        scenario = scenario_by_id.get(task.scenario_id)
        if scenario is None:
            raise BenchmarkResultError("benchmark scenario is unavailable")
        expected_execution = _expected_execution_environment(
            item.condition, task.environment, scenario.b0_isolation
        )
        if (
            item.evidence.declared_environment is not task.environment
            or item.evidence.execution_environment is not expected_execution
        ):
            raise BenchmarkResultError("benchmark evidence environment drifted")
        if set(item.evidence.verification_assertions) != set(task.expected_assertions):
            raise BenchmarkResultError("benchmark verification assertions drifted")

    verify_benchmark_evidence_artifacts(observations, evidence_directory)

    condition_reports = {
        condition: evaluate_trials(
            item.result
            for item in observations.observations
            if item.condition is condition
        )
        for condition in StudyCondition
    }
    p2 = condition_reports[StudyCondition.P2]
    targets = (
        _target("task_success_rate", ">=", 0.80, p2.task_success_rate),
        _target("unintended_execution_rate", "<=", 0.01, p2.unintended_execution_rate),
        _target("unsafe_execution_rate", "==", 0.0, p2.unsafe_execution_rate),
        _target("recovery_rate", ">=", 0.60, p2.recovery_rate),
    )
    return BenchmarkEvaluationReport(
        task_count=len(tasks.tasks),
        repetitions_per_condition=observations.repetitions_per_condition,
        observation_count=len(observations.observations),
        conditions=condition_reports,
        p2_targets=targets,
        all_p2_targets_evaluable=all(
            item.status is not TargetStatus.NOT_EVALUABLE for item in targets
        ),
        all_p2_targets_met=all(item.status is TargetStatus.PASS for item in targets),
    )


def verify_benchmark_evidence_artifacts(
    observations: BenchmarkObservationSet,
    evidence_directory: Path,
    *,
    max_artifact_bytes: int = 16 * 1024 * 1024,
) -> None:
    if (
        max_artifact_bytes < 1
        or not evidence_directory.is_absolute()
        or evidence_directory.is_symlink()
        or not evidence_directory.is_dir()
    ):
        raise BenchmarkResultError("invalid benchmark evidence directory")
    for observation in observations.observations:
        artifact = evidence_directory / observation.evidence.artifact_file
        if artifact.is_symlink() or not artifact.is_file():
            raise BenchmarkResultError("benchmark evidence artifact is unavailable")
        try:
            before = artifact.stat()
            if before.st_size > max_artifact_bytes:
                raise BenchmarkResultError("benchmark evidence artifact exceeded size limit")
            digest = hashlib.sha256()
            total = 0
            with artifact.open("rb") as stream:
                while chunk := stream.read(64 * 1024):
                    total += len(chunk)
                    if total > max_artifact_bytes:
                        raise BenchmarkResultError(
                            "benchmark evidence artifact exceeded size limit"
                        )
                    digest.update(chunk)
            after = artifact.stat()
        except BenchmarkResultError:
            raise
        except OSError as error:
            raise BenchmarkResultError("benchmark evidence artifact is unreadable") from error
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or digest.hexdigest() != observation.evidence.artifact_sha256
        ):
            raise BenchmarkResultError("benchmark evidence artifact identity mismatch")
def _target(
    metric: str,
    operator: str,
    threshold: float,
    observed: float | None,
) -> BenchmarkTargetResult:
    if observed is None:
        status = TargetStatus.NOT_EVALUABLE
    elif operator == ">=":
        status = TargetStatus.PASS if observed >= threshold else TargetStatus.FAIL
    elif operator == "<=":
        status = TargetStatus.PASS if observed <= threshold else TargetStatus.FAIL
    else:
        status = TargetStatus.PASS if observed == threshold else TargetStatus.FAIL
    return BenchmarkTargetResult(
        metric=metric,
        operator=operator,
        threshold=threshold,
        observed=observed,
        status=status,
    )


def _expected_execution_environment(
    condition: StudyCondition,
    declared: ScenarioEnvironment,
    b0_isolation: BaselineIsolation,
) -> ScenarioEnvironment:
    if condition is not StudyCondition.B0:
        return declared
    if b0_isolation is BaselineIsolation.MOCK:
        return ScenarioEnvironment.MOCK_ONLY
    return ScenarioEnvironment.SNAPSHOT_VM_ONLY


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkResultError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise BenchmarkResultError(f"non-finite JSON number: {value}")
