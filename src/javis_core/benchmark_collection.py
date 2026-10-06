from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import Field, StrictBool, model_validator

from .benchmark_results import (
    BenchmarkEvidenceProvenance,
    BenchmarkObservation,
    BenchmarkObservationSet,
)
from .benchmark_tasks import BenchmarkTask, BenchmarkTaskManifest, validate_benchmark_contract
from .evaluation import TrialResult
from .file_lock import InterprocessFileLock
from .integrated_scenarios import (
    BaselineIsolation,
    IntegratedScenario,
    IntegratedScenarioManifest,
    ScenarioEnvironment,
)
from .models import FrozenModel
from .registry import default_registry
from .study_analysis import StudyCondition
from .windows_acl import WindowsUserOnlyAcl


class BenchmarkCollectionError(RuntimeError):
    pass


class CollectionReadinessStatus(StrEnum):
    COMPLETED = "COMPLETED"
    READY = "READY"
    ENVIRONMENT_UNAVAILABLE = "ENVIRONMENT_UNAVAILABLE"
    RUNNER_UNAVAILABLE = "RUNNER_UNAVAILABLE"


class CollectionAttemptStatus(StrEnum):
    RECORDED = "RECORDED"
    COMPLETE = "COMPLETE"
    ENVIRONMENT_UNAVAILABLE = "ENVIRONMENT_UNAVAILABLE"
    RUNNER_UNAVAILABLE = "RUNNER_UNAVAILABLE"
    RUNNER_FAILED = "RUNNER_FAILED"
    ASSERTION_FAILED = "ASSERTION_FAILED"
    RESULT_DRIFTED = "RESULT_DRIFTED"


class BenchmarkCollectionCell(FrozenModel):
    cell_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run-[0-9a-f]{64}$")
    task_id: str = Field(min_length=3, max_length=128)
    condition: StudyCondition
    repetition: int = Field(ge=1, le=20)
    declared_environment: ScenarioEnvironment
    execution_environment: ScenarioEnvironment


class BenchmarkRunOutcome(FrozenModel):
    result: TrialResult
    assertion_results: dict[str, StrictBool] = Field(min_length=1, max_length=12)


class BenchmarkEvidenceArtifact(FrozenModel):
    schema_version: str = Field(default="1.1", pattern=r"^1\.1$")
    cell_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(min_length=3, max_length=128)
    condition: StudyCondition
    repetition: int = Field(ge=1, le=20)
    declared_environment: ScenarioEnvironment
    execution_environment: ScenarioEnvironment
    run_id: str = Field(pattern=r"^run-[0-9a-f]{64}$")
    result: TrialResult
    verification_assertions: tuple[str, ...] = Field(min_length=1, max_length=12)
    verification_results: dict[str, StrictBool] = Field(min_length=1, max_length=12)


class BenchmarkLedgerRecord(FrozenModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    cell_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation: BenchmarkObservation


class BenchmarkReadinessCell(FrozenModel):
    cell_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(min_length=3, max_length=128)
    condition: StudyCondition
    repetition: int = Field(ge=1, le=20)
    execution_environment: ScenarioEnvironment
    status: CollectionReadinessStatus


class BenchmarkReadinessReport(FrozenModel):
    schema_version: str = "1.0"
    total_cells: int = Field(ge=120, le=4000)
    completed: int = Field(ge=0, le=4000)
    ready: int = Field(ge=0, le=4000)
    environment_unavailable: int = Field(ge=0, le=4000)
    runner_unavailable: int = Field(ge=0, le=4000)
    complete: bool
    cells: tuple[BenchmarkReadinessCell, ...] = Field(min_length=120, max_length=4000)

    @model_validator(mode="after")
    def validate_counts(self) -> BenchmarkReadinessReport:
        counts = {
            status: sum(cell.status is status for cell in self.cells)
            for status in CollectionReadinessStatus
        }
        if (
            self.total_cells != len(self.cells)
            or self.completed != counts[CollectionReadinessStatus.COMPLETED]
            or self.ready != counts[CollectionReadinessStatus.READY]
            or self.environment_unavailable
            != counts[CollectionReadinessStatus.ENVIRONMENT_UNAVAILABLE]
            or self.runner_unavailable != counts[CollectionReadinessStatus.RUNNER_UNAVAILABLE]
            or self.complete != (self.completed == self.total_cells)
        ):
            raise ValueError("benchmark readiness counts are inconsistent")
        return self


class BenchmarkCollectionAttempt(FrozenModel):
    status: CollectionAttemptStatus
    cell_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class BenchmarkConditionRunner(Protocol):
    def supports(self, cell: BenchmarkCollectionCell, task: BenchmarkTask) -> bool: ...

    def run(self, cell: BenchmarkCollectionCell, task: BenchmarkTask) -> BenchmarkRunOutcome: ...


Clock = Callable[[], datetime]
FileHardener = Callable[[Path], None]


class BenchmarkExperimentCollector:
    """Collect only assertion-backed real observations; never synthesize NOT_RUN rows."""

    _MAX_LEDGER_BYTES = 32 * 1024 * 1024
    _MAX_RECORD_BYTES = 128 * 1024
    _MAX_ARTIFACT_BYTES = 128 * 1024

    def __init__(
        self,
        tasks: BenchmarkTaskManifest,
        scenarios: IntegratedScenarioManifest,
        collection_root: Path,
        *,
        repetitions_per_condition: int,
        evaluator_id: str,
        evaluator_version: str,
        runners: Mapping[tuple[StudyCondition, ScenarioEnvironment], BenchmarkConditionRunner],
        environment_available: Mapping[ScenarioEnvironment, bool],
        clock: Clock = lambda: datetime.now(UTC),
        hardener: FileHardener | None = None,
    ) -> None:
        if not 1 <= repetitions_per_condition <= 20:
            raise ValueError("repetitions must be within 1..20")
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,127}", evaluator_id) is None:
            raise ValueError("invalid evaluator identifier")
        version_pattern = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
        if re.fullmatch(version_pattern, evaluator_version) is None:
            raise ValueError("invalid evaluator version")
        validate_benchmark_contract(tasks, scenarios, default_registry())
        self._tasks = tasks
        self._scenarios = scenarios
        self._root = _prepare_collection_root(collection_root)
        self._artifacts = self._root / "artifacts"
        self._artifacts.mkdir(exist_ok=True)
        if self._artifacts.is_symlink():
            raise BenchmarkCollectionError("artifact directory must not be a symlink")
        self._ledger = self._root / "ledger.jsonl"
        self._lock_path = self._root / "ledger.lock"
        self._repetitions = repetitions_per_condition
        self._evaluator_id = evaluator_id
        self._evaluator_version = evaluator_version
        self._runners = dict(runners)
        self._available = dict(environment_available)
        self._clock = clock
        self._hardener = hardener or (
            WindowsUserOnlyAcl().harden if os.name == "nt" else _portable_hardener
        )
        self._thread_lock = threading.Lock()
        self._schedule = build_benchmark_schedule(tasks, scenarios, repetitions_per_condition)

    @property
    def schedule(self) -> tuple[BenchmarkCollectionCell, ...]:
        return self._schedule

    def readiness(self) -> BenchmarkReadinessReport:
        completed = self._load_records()
        cells: list[BenchmarkReadinessCell] = []
        for cell in self._schedule:
            key = (cell.condition, cell.execution_environment)
            if cell.cell_id in completed:
                status = CollectionReadinessStatus.COMPLETED
            elif not self._available.get(cell.execution_environment, False):
                status = CollectionReadinessStatus.ENVIRONMENT_UNAVAILABLE
            elif not self._runner_supports(self._runners.get(key), cell):
                status = CollectionReadinessStatus.RUNNER_UNAVAILABLE
            else:
                status = CollectionReadinessStatus.READY
            cells.append(
                BenchmarkReadinessCell(
                    cell_id=cell.cell_id,
                    task_id=cell.task_id,
                    condition=cell.condition,
                    repetition=cell.repetition,
                    execution_environment=cell.execution_environment,
                    status=status,
                )
            )
        return BenchmarkReadinessReport(
            total_cells=len(cells),
            completed=sum(cell.status is CollectionReadinessStatus.COMPLETED for cell in cells),
            ready=sum(cell.status is CollectionReadinessStatus.READY for cell in cells),
            environment_unavailable=sum(
                cell.status is CollectionReadinessStatus.ENVIRONMENT_UNAVAILABLE for cell in cells
            ),
            runner_unavailable=sum(
                cell.status is CollectionReadinessStatus.RUNNER_UNAVAILABLE for cell in cells
            ),
            complete=all(cell.status is CollectionReadinessStatus.COMPLETED for cell in cells),
            cells=tuple(cells),
        )

    def collect_next(self) -> BenchmarkCollectionAttempt:
        with self._thread_lock, InterprocessFileLock(self._lock_path, hardener=self._hardener):
            records = self._load_records_unlocked()
            first_blocker: BenchmarkCollectionAttempt | None = None
            for cell in self._schedule:
                if cell.cell_id in records:
                    continue
                if not self._available.get(cell.execution_environment, False):
                    if first_blocker is None:
                        first_blocker = BenchmarkCollectionAttempt(
                            status=CollectionAttemptStatus.ENVIRONMENT_UNAVAILABLE,
                            cell_id=cell.cell_id,
                        )
                    continue
                runner = self._runners.get((cell.condition, cell.execution_environment))
                if not self._runner_supports(runner, cell):
                    if first_blocker is None:
                        first_blocker = BenchmarkCollectionAttempt(
                            status=CollectionAttemptStatus.RUNNER_UNAVAILABLE,
                            cell_id=cell.cell_id,
                        )
                    continue
                assert runner is not None
                task = self._task(cell.task_id)
                started = _utc_now(self._clock)
                monotonic_started = time.perf_counter_ns()
                try:
                    outcome = runner.run(cell, task)
                except Exception:
                    return BenchmarkCollectionAttempt(
                        status=CollectionAttemptStatus.RUNNER_FAILED,
                        cell_id=cell.cell_id,
                    )
                completed = _utc_now(self._clock)
                if completed < started:
                    raise BenchmarkCollectionError("collection clock moved backwards")
                if completed == started:
                    elapsed_microseconds = max(
                        1,
                        (time.perf_counter_ns() - monotonic_started + 999) // 1000,
                    )
                    completed = started + timedelta(microseconds=elapsed_microseconds)
                if not self._result_matches(cell, task, outcome.result):
                    return BenchmarkCollectionAttempt(
                        status=CollectionAttemptStatus.RESULT_DRIFTED,
                        cell_id=cell.cell_id,
                    )
                if set(outcome.assertion_results) != set(task.expected_assertions):
                    return BenchmarkCollectionAttempt(
                        status=CollectionAttemptStatus.ASSERTION_FAILED,
                        cell_id=cell.cell_id,
                    )
                artifact = BenchmarkEvidenceArtifact(
                    cell_id=cell.cell_id,
                    task_id=cell.task_id,
                    condition=cell.condition,
                    repetition=cell.repetition,
                    declared_environment=cell.declared_environment,
                    execution_environment=cell.execution_environment,
                    run_id=cell.run_id,
                    result=outcome.result,
                    verification_assertions=task.expected_assertions,
                    verification_results=outcome.assertion_results,
                )
                artifact_name, digest = self._write_artifact(artifact)
                observation = BenchmarkObservation(
                    task_id=cell.task_id,
                    condition=cell.condition,
                    repetition=cell.repetition,
                    result=outcome.result,
                    evidence=BenchmarkEvidenceProvenance(
                        run_id=cell.run_id,
                        evaluator_id=self._evaluator_id,
                        evaluator_version=self._evaluator_version,
                        declared_environment=cell.declared_environment,
                        execution_environment=cell.execution_environment,
                        started_at=started,
                        completed_at=completed,
                        artifact_file=artifact_name,
                        artifact_sha256=digest,
                        verification_assertions=task.expected_assertions,
                    ),
                )
                self._append_record(
                    BenchmarkLedgerRecord(
                        cell_id=cell.cell_id,
                        observation=observation,
                    )
                )
                return BenchmarkCollectionAttempt(
                    status=CollectionAttemptStatus.RECORDED,
                    cell_id=cell.cell_id,
                )
            if first_blocker is not None:
                return first_blocker
            return BenchmarkCollectionAttempt(status=CollectionAttemptStatus.COMPLETE)

    def export(self, destination: Path) -> BenchmarkObservationSet:
        records = self._load_records()
        if set(records) != {cell.cell_id for cell in self._schedule}:
            raise BenchmarkCollectionError("benchmark collection is incomplete")
        observations = tuple(records[cell.cell_id].observation for cell in self._schedule)
        result = BenchmarkObservationSet(
            repetitions_per_condition=self._repetitions,
            observations=observations,
        )
        _write_new_atomic(destination, result.model_dump_json().encode("utf-8"), self._hardener)
        return result

    def _load_records(self) -> dict[str, BenchmarkLedgerRecord]:
        with self._thread_lock, InterprocessFileLock(self._lock_path, hardener=self._hardener):
            return self._load_records_unlocked()

    def _load_records_unlocked(self) -> dict[str, BenchmarkLedgerRecord]:
        if not self._ledger.exists():
            self._verify_artifact_inventory({})
            return {}
        if self._ledger.is_symlink() or not self._ledger.is_file():
            raise BenchmarkCollectionError("benchmark ledger is invalid")
        try:
            if self._ledger.stat().st_size > self._MAX_LEDGER_BYTES:
                raise BenchmarkCollectionError("benchmark ledger exceeded size limit")
            raw = self._ledger.read_bytes()
        except OSError as error:
            raise BenchmarkCollectionError("benchmark ledger is unreadable") from error
        if raw and not raw.endswith(b"\n"):
            raise BenchmarkCollectionError("benchmark ledger is truncated")
        scheduled = {cell.cell_id: cell for cell in self._schedule}
        records: dict[str, BenchmarkLedgerRecord] = {}
        for line in raw.splitlines():
            if not line or len(line) > self._MAX_RECORD_BYTES:
                raise BenchmarkCollectionError("benchmark ledger record is invalid")
            record = _parse_record(line)
            if record.cell_id in records:
                raise BenchmarkCollectionError("benchmark ledger contains duplicate cells")
            cell = scheduled.get(record.cell_id)
            if cell is None or not self._observation_matches_cell(record.observation, cell):
                raise BenchmarkCollectionError("benchmark ledger cell drifted")
            self._verify_artifact(record)
            records[record.cell_id] = record
        self._verify_artifact_inventory(records)
        return records

    def _verify_artifact_inventory(self, records: Mapping[str, BenchmarkLedgerRecord]) -> None:
        expected = {record.observation.evidence.artifact_file for record in records.values()}
        try:
            entries = tuple(self._artifacts.iterdir())
        except OSError as error:
            raise BenchmarkCollectionError("benchmark artifact directory is unreadable") from error
        actual: set[str] = set()
        for entry in entries:
            if entry.is_symlink() or not entry.is_file() or entry.name in actual:
                raise BenchmarkCollectionError("benchmark artifact inventory is invalid")
            actual.add(entry.name)
        if actual != expected:
            raise BenchmarkCollectionError("orphan benchmark artifact detected")

    def _verify_artifact(self, record: BenchmarkLedgerRecord) -> None:
        path = self._artifacts / record.observation.evidence.artifact_file
        if path.parent != self._artifacts or path.is_symlink() or not path.is_file():
            raise BenchmarkCollectionError("benchmark artifact is unavailable")
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise BenchmarkCollectionError("benchmark artifact is unreadable") from error
        if len(raw) > self._MAX_ARTIFACT_BYTES:
            raise BenchmarkCollectionError("benchmark artifact exceeded size limit")
        if hashlib.sha256(raw).hexdigest() != record.observation.evidence.artifact_sha256:
            raise BenchmarkCollectionError("benchmark artifact identity mismatch")
        try:
            artifact = BenchmarkEvidenceArtifact.model_validate_json(raw)
        except ValueError as error:
            raise BenchmarkCollectionError("benchmark artifact is malformed") from error
        observation = record.observation
        if (
            artifact.cell_id != record.cell_id
            or artifact.task_id != observation.task_id
            or artifact.condition is not observation.condition
            or artifact.repetition != observation.repetition
            or artifact.run_id != observation.evidence.run_id
            or artifact.result != observation.result
            or artifact.declared_environment is not observation.evidence.declared_environment
            or artifact.execution_environment is not observation.evidence.execution_environment
            or artifact.verification_assertions != observation.evidence.verification_assertions
            or set(artifact.verification_results)
            != set(observation.evidence.verification_assertions)
        ):
            raise BenchmarkCollectionError("benchmark artifact binding mismatch")

    def _write_artifact(self, artifact: BenchmarkEvidenceArtifact) -> tuple[str, str]:
        name = f"{artifact.cell_id}.json"
        destination = self._artifacts / name
        if destination.exists() or destination.is_symlink():
            raise BenchmarkCollectionError("orphan or duplicate benchmark artifact")
        payload = _canonical_json(artifact.model_dump(mode="json"))
        if len(payload) > self._MAX_ARTIFACT_BYTES:
            raise BenchmarkCollectionError("benchmark artifact exceeded size limit")
        _write_new_atomic(destination, payload, self._hardener)
        return name, hashlib.sha256(payload).hexdigest()

    def _append_record(self, record: BenchmarkLedgerRecord) -> None:
        payload = _canonical_json(record.model_dump(mode="json")) + b"\n"
        if len(payload) > self._MAX_RECORD_BYTES:
            raise BenchmarkCollectionError("benchmark ledger record exceeded size limit")
        if self._ledger.exists() and self._ledger.is_symlink():
            raise BenchmarkCollectionError("benchmark ledger is invalid")
        try:
            if not self._ledger.exists():
                _write_new_atomic(self._ledger, b"", self._hardener)
            with self._ledger.open("ab", buffering=0) as stream:
                stream.write(payload)
                os.fsync(stream.fileno())
            self._hardener(self._ledger)
        except OSError as error:
            raise BenchmarkCollectionError("benchmark ledger append failed") from error

    def _task(self, task_id: str) -> BenchmarkTask:
        return next(task for task in self._tasks.tasks if task.task_id == task_id)

    def _runner_supports(
        self,
        runner: BenchmarkConditionRunner | None,
        cell: BenchmarkCollectionCell,
    ) -> bool:
        if runner is None:
            return False
        try:
            return runner.supports(cell, self._task(cell.task_id)) is True
        except Exception:
            return False

    @staticmethod
    def _result_matches(
        cell: BenchmarkCollectionCell, task: BenchmarkTask, result: TrialResult
    ) -> bool:
        return (
            result.trial_id == cell.run_id
            and result.intended_command == task.intended_command
            and result.ground_truth_risk is task.ground_truth_risk
        )

    def _observation_matches_cell(
        self, observation: BenchmarkObservation, cell: BenchmarkCollectionCell
    ) -> bool:
        evidence = observation.evidence
        task = self._task(cell.task_id)
        return (
            observation.task_id == cell.task_id
            and observation.condition is cell.condition
            and observation.repetition == cell.repetition
            and self._result_matches(cell, task, observation.result)
            and evidence.run_id == cell.run_id
            and evidence.evaluator_id == self._evaluator_id
            and evidence.evaluator_version == self._evaluator_version
            and evidence.declared_environment is cell.declared_environment
            and evidence.execution_environment is cell.execution_environment
            and evidence.artifact_file == f"{cell.cell_id}.json"
            and evidence.verification_assertions == task.expected_assertions
        )


def build_benchmark_schedule(
    tasks: BenchmarkTaskManifest,
    scenarios: IntegratedScenarioManifest,
    repetitions: int,
) -> tuple[BenchmarkCollectionCell, ...]:
    if not 1 <= repetitions <= 20:
        raise ValueError("repetitions must be within 1..20")
    by_id = {scenario.scenario_id: scenario for scenario in scenarios.scenarios}
    cells: list[BenchmarkCollectionCell] = []
    for task in tasks.tasks:
        scenario = by_id[task.scenario_id]
        for condition in StudyCondition:
            execution = _execution_environment(condition, task.environment, scenario)
            for repetition in range(1, repetitions + 1):
                identity = "|".join(
                    (
                        task.task_id,
                        condition.value,
                        str(repetition),
                        task.environment.value,
                        execution.value,
                    )
                )
                cell_id = hashlib.sha256(identity.encode("ascii")).hexdigest()
                cells.append(
                    BenchmarkCollectionCell(
                        cell_id=cell_id,
                        run_id=f"run-{cell_id}",
                        task_id=task.task_id,
                        condition=condition,
                        repetition=repetition,
                        declared_environment=task.environment,
                        execution_environment=execution,
                    )
                )
    return tuple(cells)


def _execution_environment(
    condition: StudyCondition,
    declared: ScenarioEnvironment,
    scenario: IntegratedScenario,
) -> ScenarioEnvironment:
    if condition is not StudyCondition.B0:
        return declared
    if scenario.b0_isolation is BaselineIsolation.MOCK:
        return ScenarioEnvironment.MOCK_ONLY
    return ScenarioEnvironment.SNAPSHOT_VM_ONLY


def _prepare_collection_root(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise BenchmarkCollectionError("collection root must be absolute and non-symlink")
    try:
        path.mkdir(parents=False, exist_ok=True)
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise BenchmarkCollectionError("collection root is unavailable") from error
    if not resolved.is_dir():
        raise BenchmarkCollectionError("collection root must be a directory")
    return resolved


def _parse_record(raw: bytes) -> BenchmarkLedgerRecord:
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
        return BenchmarkLedgerRecord.model_validate(document)
    except BenchmarkCollectionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise BenchmarkCollectionError("benchmark ledger record is malformed") from error


def _write_new_atomic(destination: Path, payload: bytes, hardener: FileHardener) -> None:
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise BenchmarkCollectionError("benchmark output destination is invalid")
    parent = destination.parent.resolve(strict=True)
    temporary = parent / f".{destination.name}.{os.getpid()}.tmp"
    if temporary.exists() or temporary.is_symlink():
        raise BenchmarkCollectionError("benchmark temporary output is unavailable")
    try:
        with temporary.open("xb", buffering=0) as stream:
            stream.write(payload)
            os.fsync(stream.fileno())
        hardener(temporary)
        temporary.replace(destination)
        hardener(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _utc_now(clock: Clock) -> datetime:
    value = clock()
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None or offset.total_seconds() != 0:
        raise BenchmarkCollectionError("collection clock must return UTC")
    return value


def _portable_hardener(path: Path) -> None:
    path.chmod(0o600)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkCollectionError("duplicate benchmark ledger JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise BenchmarkCollectionError(f"non-finite benchmark ledger number: {value}")
