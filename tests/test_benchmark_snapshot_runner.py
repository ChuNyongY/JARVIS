from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from javis_core.benchmark_collection import build_benchmark_schedule
from javis_core.benchmark_snapshot_runner import (
    SnapshotBenchmarkConditionRunner,
    SnapshotBenchmarkEvidence,
    build_snapshot_benchmark_requests,
    sign_snapshot_benchmark_evidence,
    write_snapshot_benchmark_requests,
)
from javis_core.benchmark_tasks import (
    BenchmarkTask,
    BenchmarkTaskError,
    load_benchmark_tasks,
)
from javis_core.cli import main
from javis_core.evaluation import TrialResult
from javis_core.integrated_scenarios import (
    ScenarioEnvironment,
    load_integrated_scenarios,
)
from javis_core.snapshot_batch import HmacSha256SnapshotVerifier
from javis_core.snapshot_vm import SnapshotEvidenceError

TASKS = load_benchmark_tasks(Path("evaluation/benchmark_tasks.json").resolve())
SCENARIOS = load_integrated_scenarios(Path("evaluation/integrated_scenarios.json").resolve())
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
KEY = b"snapshot-benchmark-test-key-0001"
KEY_ID = "vm-harness-1"
PROVIDER = "hyper-v"
SNAPSHOT = "baseline-sha256-01"
GUEST = "javis-evaluation-guest-01"


def _snapshot_cell_and_task():  # type: ignore[no-untyped-def]
    cell = next(
        item
        for item in build_benchmark_schedule(TASKS, SCENARIOS, 1)
        if item.execution_environment is ScenarioEnvironment.SNAPSHOT_VM_ONLY
    )
    task = next(item for item in TASKS.tasks if item.task_id == cell.task_id)
    return cell, task


def _write_signed_evidence(
    directory: Path,
    *,
    restore_succeeded: bool = True,
    at: datetime = NOW,
) -> tuple[SnapshotBenchmarkEvidence, BenchmarkTask]:
    cell, task = _snapshot_cell_and_task()
    artifact = directory / f"{cell.cell_id}.artifact"
    artifact.write_bytes(b"external VM harness observation\n")
    result = TrialResult(
        trial_id=cell.run_id,
        intended_command=task.intended_command,
        ground_truth_risk=task.ground_truth_risk,
        predicted_risk=task.ground_truth_risk,
        executed=task.intended_command,
        approval_required=task.ground_truth_risk >= 3,
        approval_obtained=task.ground_truth_risk >= 3,
        task_success=task.intended_command,
        verifier_declared_success=task.intended_command,
        actual_success=task.intended_command,
        confirmations=1 if task.ground_truth_risk >= 3 else 0,
        latency_ms={"total": 25.0},
    )
    unsigned = SnapshotBenchmarkEvidence(
        cell_id=cell.cell_id,
        run_id=cell.run_id,
        task_id=cell.task_id,
        condition=cell.condition,
        repetition=cell.repetition,
        declared_environment=cell.declared_environment,
        execution_environment=cell.execution_environment,
        result=result,
        assertion_results={name: True for name in task.expected_assertions},
        vm_provider=PROVIDER,
        base_snapshot_id=SNAPSHOT,
        guest_machine_id=GUEST,
        started_at=at - timedelta(minutes=2),
        finished_at=at - timedelta(minutes=1),
        restore_attempted=True,
        restore_succeeded=restore_succeeded,
        artifact_file=artifact.name,
        artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        signature_key_id=KEY_ID,
        signature="placeholder-signature",
    )
    signed = sign_snapshot_benchmark_evidence(unsigned, key=KEY)
    (directory / f"{cell.cell_id}.json").write_text(
        signed.model_dump_json(),
        encoding="utf-8",
    )
    return signed, task


def _runner(directory: Path) -> SnapshotBenchmarkConditionRunner:
    return SnapshotBenchmarkConditionRunner(
        directory.resolve(),
        HmacSha256SnapshotVerifier(KEY_ID, KEY),
        vm_provider=PROVIDER,
        base_snapshot_id=SNAPSHOT,
        guest_machine_id=GUEST,
        clock=lambda: NOW,
    )


def test_request_set_is_exact_snapshot_vm_grid(tmp_path: Path) -> None:
    request_set = build_snapshot_benchmark_requests(TASKS, SCENARIOS)
    scheduled = tuple(
        cell
        for cell in build_benchmark_schedule(TASKS, SCENARIOS, 1)
        if cell.execution_environment is ScenarioEnvironment.SNAPSHOT_VM_ONLY
    )

    assert len(request_set.requests) == 50
    assert tuple(item.cell_id for item in request_set.requests) == tuple(
        item.cell_id for item in scheduled
    )
    assert len({item.run_id for item in request_set.requests}) == 50
    assert all(
        request.declared_environment is cell.declared_environment
        and request.execution_environment is ScenarioEnvironment.SNAPSHOT_VM_ONLY
        for request, cell in zip(request_set.requests, scheduled, strict=True)
    )

    destination = (tmp_path / "snapshot-requests.json").resolve()
    write_snapshot_benchmark_requests(destination, request_set)
    assert destination.is_file()
    assert '"requests"' in destination.read_text(encoding="utf-8")
    with pytest.raises(SnapshotEvidenceError, match="new and absolute"):
        write_snapshot_benchmark_requests(destination, request_set)

    drifted_task = TASKS.tasks[0].model_copy(
        update={"environment": ScenarioEnvironment.SNAPSHOT_VM_ONLY}
    )
    drifted = TASKS.model_copy(update={"tasks": (drifted_task, *TASKS.tasks[1:])})
    with pytest.raises(BenchmarkTaskError, match="environment drifted"):
        build_snapshot_benchmark_requests(drifted, SCENARIOS)


def test_runner_accepts_exact_signed_restored_evidence(tmp_path: Path) -> None:
    directory = (tmp_path / "evidence").resolve()
    directory.mkdir()
    signed, task = _write_signed_evidence(directory)
    cell, _ = _snapshot_cell_and_task()

    runner = _runner(directory)
    assert runner.supports(cell, task)
    outcome = runner.run(cell, task)

    assert outcome.result == signed.result
    assert outcome.assertion_results == signed.assertion_results
    assert "signature" not in outcome.model_dump_json()


def test_runner_rejects_artifact_tampering_and_invalid_signature(tmp_path: Path) -> None:
    artifact_directory = (tmp_path / "artifact-tamper").resolve()
    artifact_directory.mkdir()
    signed, _ = _write_signed_evidence(artifact_directory)
    (artifact_directory / signed.artifact_file).write_bytes(b"tampered")

    with pytest.raises(SnapshotEvidenceError, match="hash mismatch"):
        _runner(artifact_directory)

    signature_directory = (tmp_path / "signature-tamper").resolve()
    signature_directory.mkdir()
    signed, _ = _write_signed_evidence(signature_directory)
    evidence_path = signature_directory / f"{signed.cell_id}.json"
    evidence_path.write_text(
        signed.model_copy(update={"signature": "invalid-signature"}).model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(SnapshotEvidenceError, match="signature is invalid"):
        _runner(signature_directory)


def test_runner_rejects_unrestored_or_orphaned_inventory(tmp_path: Path) -> None:
    unrestored_directory = (tmp_path / "unrestored").resolve()
    unrestored_directory.mkdir()
    _write_signed_evidence(unrestored_directory, restore_succeeded=False)
    with pytest.raises(SnapshotEvidenceError, match="restore is not confirmed"):
        _runner(unrestored_directory)

    orphan_directory = (tmp_path / "orphan").resolve()
    orphan_directory.mkdir()
    _write_signed_evidence(orphan_directory)
    (orphan_directory / "unreferenced.artifact").write_bytes(b"orphan")
    with pytest.raises(SnapshotEvidenceError, match="orphan"):
        _runner(orphan_directory)


def test_runner_rejects_artifact_reuse_across_cells(tmp_path: Path) -> None:
    directory = (tmp_path / "reused-artifact").resolve()
    directory.mkdir()
    first, _ = _write_signed_evidence(directory)
    first_cell, _ = _snapshot_cell_and_task()
    second_cell = next(
        item
        for item in build_benchmark_schedule(TASKS, SCENARIOS, 1)
        if item.execution_environment is ScenarioEnvironment.SNAPSHOT_VM_ONLY
        and item.cell_id != first_cell.cell_id
    )
    second_task = next(item for item in TASKS.tasks if item.task_id == second_cell.task_id)
    second_result = first.result.model_copy(
        update={
            "trial_id": second_cell.run_id,
            "intended_command": second_task.intended_command,
            "ground_truth_risk": second_task.ground_truth_risk,
            "predicted_risk": second_task.ground_truth_risk,
            "executed": second_task.intended_command,
            "task_success": second_task.intended_command,
            "verifier_declared_success": second_task.intended_command,
            "actual_success": second_task.intended_command,
        }
    )
    second = first.model_copy(
        update={
            "cell_id": second_cell.cell_id,
            "run_id": second_cell.run_id,
            "task_id": second_cell.task_id,
            "condition": second_cell.condition,
            "repetition": second_cell.repetition,
            "declared_environment": second_cell.declared_environment,
            "execution_environment": second_cell.execution_environment,
            "result": second_result,
            "assertion_results": {name: True for name in second_task.expected_assertions},
            "signature": "placeholder-signature",
        }
    )
    second = sign_snapshot_benchmark_evidence(second, key=KEY)
    (directory / f"{second.cell_id}.json").write_text(
        second.model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(SnapshotEvidenceError, match="artifact is duplicated"):
        _runner(directory)


def test_snapshot_cli_exports_and_collects_verified_partial_grid(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = Path.cwd().resolve()
    tasks_path = project / "evaluation" / "benchmark_tasks.json"
    scenarios_path = project / "evaluation" / "integrated_scenarios.json"
    request_path = (tmp_path / "requests.json").resolve()

    assert (
        main(
            (
                "export-snapshot-benchmark-requests",
                str(tasks_path),
                str(scenarios_path),
                str(request_path),
            )
        )
        == 0
    )
    assert '"request_count": 50' in capsys.readouterr().out

    evidence_directory = (tmp_path / "evidence").resolve()
    evidence_directory.mkdir()
    _write_signed_evidence(evidence_directory, at=datetime.now(UTC))
    key_file = (tmp_path / "snapshot.key").resolve()
    key_file.write_bytes(KEY)
    collection_root = (tmp_path / "collection").resolve()

    assert (
        main(
            (
                "collect-snapshot-benchmark",
                str(tasks_path),
                str(scenarios_path),
                str(collection_root),
                str(evidence_directory),
                str(key_file),
                KEY_ID,
                PROVIDER,
                SNAPSHOT,
                GUEST,
            )
        )
        == 0
    )
    output = capsys.readouterr().out
    assert '"collected_this_run": 1' in output
    assert '"completed": 1' in output
    assert '"environment_unavailable": 70' in output
    assert '"runner_unavailable": 49' in output

    assert (
        main(
            (
                "collect-snapshot-benchmark",
                str(tasks_path),
                str(scenarios_path),
                str(collection_root),
                str(evidence_directory),
                str(key_file),
                KEY_ID,
                PROVIDER,
                SNAPSHOT,
                GUEST,
            )
        )
        == 0
    )
    assert '"collected_this_run": 0' in capsys.readouterr().out
