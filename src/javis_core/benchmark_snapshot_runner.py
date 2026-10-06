from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import Field, StrictBool, field_validator, model_validator

from .benchmark_collection import (
    BenchmarkCollectionCell,
    BenchmarkRunOutcome,
    build_benchmark_schedule,
)
from .benchmark_tasks import (
    BenchmarkTask,
    BenchmarkTaskManifest,
    validate_benchmark_contract,
)
from .evaluation import TrialResult
from .integrated_scenarios import IntegratedScenarioManifest, ScenarioEnvironment
from .models import FrozenModel
from .registry import default_registry
from .snapshot_vm import SnapshotEvidenceError, SnapshotSignatureVerifier
from .study_analysis import StudyCondition


class SnapshotBenchmarkEvidence(FrozenModel):
    """One signed, exact-grid VM observation plus an external artifact binding."""

    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    cell_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run-[0-9a-f]{64}$")
    task_id: str = Field(min_length=3, max_length=128)
    condition: StudyCondition
    repetition: int = Field(ge=1, le=20)
    declared_environment: ScenarioEnvironment
    execution_environment: ScenarioEnvironment
    result: TrialResult
    assertion_results: dict[str, StrictBool] = Field(min_length=1, max_length=12)
    vm_provider: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    base_snapshot_id: str = Field(min_length=1, max_length=256)
    guest_machine_id: str = Field(min_length=1, max_length=256)
    started_at: datetime
    finished_at: datetime
    restore_attempted: StrictBool
    restore_succeeded: StrictBool
    artifact_file: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.artifact$",
    )
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_key_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    signature: str = Field(
        min_length=16,
        max_length=4096,
        pattern=r"^[A-Za-z0-9_+/=-]+$",
        repr=False,
    )

    @field_validator("started_at", "finished_at")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("timestamps must explicitly use UTC")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_time_and_environment(self) -> SnapshotBenchmarkEvidence:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if self.execution_environment is not ScenarioEnvironment.SNAPSHOT_VM_ONLY:
            raise ValueError("snapshot evidence must execute in SNAPSHOT_VM_ONLY")
        return self


class SnapshotBenchmarkRequest(FrozenModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    cell_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^run-[0-9a-f]{64}$")
    task_id: str
    scenario_id: str
    condition: StudyCondition
    repetition: int = Field(ge=1, le=20)
    declared_environment: ScenarioEnvironment
    execution_environment: ScenarioEnvironment
    utterance: str
    intended_command: StrictBool
    ground_truth_risk: int = Field(ge=0, le=4)
    fault_profile: str
    required_capabilities: tuple[str, ...]
    expected_assertions: tuple[str, ...]


class SnapshotBenchmarkRequestSet(FrozenModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    repetitions_per_condition: int = Field(ge=1, le=20)
    requests: tuple[SnapshotBenchmarkRequest, ...] = Field(min_length=1, max_length=4000)


def canonical_snapshot_benchmark_payload(evidence: SnapshotBenchmarkEvidence) -> bytes:
    return json.dumps(
        evidence.model_dump(mode="json", exclude={"signature"}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def build_snapshot_benchmark_requests(
    tasks: BenchmarkTaskManifest,
    scenarios: IntegratedScenarioManifest,
    *,
    repetitions_per_condition: int = 1,
) -> SnapshotBenchmarkRequestSet:
    validate_benchmark_contract(tasks, scenarios, default_registry())
    by_task = {task.task_id: task for task in tasks.tasks}
    requests = []
    for cell in build_benchmark_schedule(tasks, scenarios, repetitions_per_condition):
        if cell.execution_environment is not ScenarioEnvironment.SNAPSHOT_VM_ONLY:
            continue
        task = by_task[cell.task_id]
        requests.append(
            SnapshotBenchmarkRequest(
                cell_id=cell.cell_id,
                run_id=cell.run_id,
                task_id=cell.task_id,
                scenario_id=task.scenario_id,
                condition=cell.condition,
                repetition=cell.repetition,
                declared_environment=cell.declared_environment,
                execution_environment=cell.execution_environment,
                utterance=task.utterance,
                intended_command=task.intended_command,
                ground_truth_risk=int(task.ground_truth_risk),
                fault_profile=task.fault_profile.value,
                required_capabilities=task.required_capabilities,
                expected_assertions=task.expected_assertions,
            )
        )
    return SnapshotBenchmarkRequestSet(
        repetitions_per_condition=repetitions_per_condition,
        requests=tuple(requests),
    )


def write_snapshot_benchmark_requests(
    destination: Path,
    requests: SnapshotBenchmarkRequestSet,
) -> None:
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise SnapshotEvidenceError("snapshot request destination must be new and absolute")
    parent = destination.parent.resolve(strict=True)
    temporary = parent / f".{destination.name}.{os.getpid()}.tmp"
    payload = requests.model_dump_json(indent=2).encode("utf-8")
    try:
        with temporary.open("xb", buffering=0) as stream:
            stream.write(payload)
            os.fsync(stream.fileno())
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def sign_snapshot_benchmark_evidence(
    evidence: SnapshotBenchmarkEvidence,
    *,
    key: bytes,
) -> SnapshotBenchmarkEvidence:
    """Test/harness helper; callers remain responsible for protecting the key."""

    if not 32 <= len(key) <= 4096:
        raise ValueError("snapshot HMAC key length is invalid")
    signature = base64.urlsafe_b64encode(
        hmac.digest(key, canonical_snapshot_benchmark_payload(evidence), hashlib.sha256)
    ).decode("ascii")
    return evidence.model_copy(update={"signature": signature})


def load_snapshot_benchmark_evidence(
    path: Path,
    *,
    max_bytes: int = 128 * 1024,
) -> SnapshotBenchmarkEvidence:
    if path.is_symlink() or not path.is_file():
        raise SnapshotEvidenceError("snapshot benchmark evidence must be a regular file")
    try:
        if path.stat().st_size > max_bytes:
            raise SnapshotEvidenceError("snapshot benchmark evidence exceeded size limit")
        raw = path.read_bytes()
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
        return SnapshotBenchmarkEvidence.model_validate(document)
    except SnapshotEvidenceError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise SnapshotEvidenceError("invalid snapshot benchmark evidence") from error


class SnapshotBenchmarkConditionRunner:
    """Consume exact signed VM results without controlling a VM or the host."""

    def __init__(
        self,
        evidence_directory: Path,
        verifier: SnapshotSignatureVerifier,
        *,
        vm_provider: str,
        base_snapshot_id: str,
        guest_machine_id: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_age: timedelta = timedelta(hours=1),
        max_duration: timedelta = timedelta(minutes=30),
        future_skew: timedelta = timedelta(minutes=2),
        max_artifact_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if (
            not evidence_directory.is_absolute()
            or evidence_directory.is_symlink()
            or not evidence_directory.is_dir()
            or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", vm_provider) is None
            or not 1 <= len(base_snapshot_id) <= 256
            or not 1 <= len(guest_machine_id) <= 256
            or min(max_age, max_duration, future_skew) < timedelta(0)
            or max_age == timedelta(0)
            or max_duration == timedelta(0)
            or not 1 <= max_artifact_bytes <= 1024 * 1024 * 1024
        ):
            raise SnapshotEvidenceError("invalid snapshot benchmark runner configuration")
        self._directory = evidence_directory.resolve(strict=True)
        self._verifier = verifier
        self._profile = (vm_provider, base_snapshot_id, guest_machine_id)
        self._clock = clock
        self._max_age = max_age
        self._max_duration = max_duration
        self._future_skew = future_skew
        self._max_artifact_bytes = max_artifact_bytes
        self._evidence = self._load_inventory()

    def supports(self, cell: BenchmarkCollectionCell, task: BenchmarkTask) -> bool:
        del task
        return (
            cell.execution_environment is ScenarioEnvironment.SNAPSHOT_VM_ONLY
            and cell.cell_id in self._evidence
        )

    def run(
        self,
        cell: BenchmarkCollectionCell,
        task: BenchmarkTask,
    ) -> BenchmarkRunOutcome:
        evidence = self._evidence.get(cell.cell_id)
        if evidence is None:
            raise SnapshotEvidenceError("snapshot evidence is unavailable for this cell")
        expected = (
            cell.cell_id,
            cell.run_id,
            cell.task_id,
            cell.condition,
            cell.repetition,
            cell.declared_environment,
            cell.execution_environment,
        )
        observed = (
            evidence.cell_id,
            evidence.run_id,
            evidence.task_id,
            evidence.condition,
            evidence.repetition,
            evidence.declared_environment,
            evidence.execution_environment,
        )
        if observed != expected:
            raise SnapshotEvidenceError("snapshot evidence cell binding mismatch")
        if (
            evidence.result.trial_id != cell.run_id
            or evidence.result.intended_command != task.intended_command
            or evidence.result.ground_truth_risk is not task.ground_truth_risk
            or set(evidence.assertion_results) != set(task.expected_assertions)
        ):
            raise SnapshotEvidenceError("snapshot evidence result binding mismatch")
        return BenchmarkRunOutcome(
            result=evidence.result,
            assertion_results=evidence.assertion_results,
        )

    def _load_inventory(self) -> dict[str, SnapshotBenchmarkEvidence]:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise SnapshotEvidenceError("snapshot runner clock must return UTC")
        try:
            entries = tuple(self._directory.iterdir())
        except OSError as error:
            raise SnapshotEvidenceError("snapshot evidence directory is unreadable") from error
        if any(entry.is_symlink() or not entry.is_file() for entry in entries):
            raise SnapshotEvidenceError("snapshot evidence inventory contains an invalid entry")

        documents = tuple(entry for entry in entries if entry.suffix.casefold() == ".json")
        evidence_by_cell: dict[str, SnapshotBenchmarkEvidence] = {}
        run_ids: set[str] = set()
        artifact_names: set[str] = set()
        referenced = {entry.name for entry in documents}
        for document in documents:
            if re.fullmatch(r"[0-9a-f]{64}\.json", document.name) is None:
                raise SnapshotEvidenceError("snapshot evidence filename is invalid")
            evidence = load_snapshot_benchmark_evidence(document)
            if document.stem != evidence.cell_id or evidence.cell_id in evidence_by_cell:
                raise SnapshotEvidenceError("snapshot evidence cell ID is duplicated or mismatched")
            if evidence.run_id in run_ids:
                raise SnapshotEvidenceError("snapshot evidence run ID is duplicated")
            if evidence.artifact_file in artifact_names:
                raise SnapshotEvidenceError("snapshot evidence artifact is duplicated")
            self._validate_evidence(evidence, now)
            artifact = self._directory / evidence.artifact_file
            self._verify_artifact(artifact, evidence.artifact_sha256)
            referenced.add(evidence.artifact_file)
            evidence_by_cell[evidence.cell_id] = evidence
            run_ids.add(evidence.run_id)
            artifact_names.add(evidence.artifact_file)
        if referenced != {entry.name for entry in entries}:
            raise SnapshotEvidenceError("snapshot evidence inventory contains orphan files")
        return evidence_by_cell

    def _validate_evidence(self, evidence: SnapshotBenchmarkEvidence, now: datetime) -> None:
        if (
            evidence.vm_provider,
            evidence.base_snapshot_id,
            evidence.guest_machine_id,
        ) != self._profile:
            raise SnapshotEvidenceError("snapshot VM profile mismatch")
        if evidence.finished_at - evidence.started_at > self._max_duration:
            raise SnapshotEvidenceError("snapshot benchmark run duration exceeded")
        if evidence.finished_at < now - self._max_age:
            raise SnapshotEvidenceError("snapshot benchmark evidence is stale")
        if max(evidence.started_at, evidence.finished_at) > now + self._future_skew:
            raise SnapshotEvidenceError("snapshot benchmark evidence is from the future")
        if not evidence.restore_attempted or not evidence.restore_succeeded:
            raise SnapshotEvidenceError("snapshot restore is not confirmed")
        try:
            valid = self._verifier.verify(
                evidence.signature_key_id,
                canonical_snapshot_benchmark_payload(evidence),
                evidence.signature,
            )
        except Exception:
            valid = False
        if not valid:
            raise SnapshotEvidenceError("snapshot benchmark signature is invalid")

    def _verify_artifact(self, path: Path, expected: str) -> None:
        if path.is_symlink() or not path.is_file():
            raise SnapshotEvidenceError("snapshot benchmark artifact is unavailable")
        try:
            size = path.stat().st_size
            if not 1 <= size <= self._max_artifact_bytes:
                raise SnapshotEvidenceError("snapshot benchmark artifact size is invalid")
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except SnapshotEvidenceError:
            raise
        except OSError as error:
            raise SnapshotEvidenceError("snapshot benchmark artifact is unreadable") from error
        if not hmac.compare_digest(digest.hexdigest(), expected):
            raise SnapshotEvidenceError("snapshot benchmark artifact hash mismatch")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotEvidenceError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise SnapshotEvidenceError(f"non-finite JSON number: {value}")
