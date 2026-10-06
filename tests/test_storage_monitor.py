from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from javis_core import (
    Action,
    CancellationToken,
    DiskUsageSnapshot,
    Postcondition,
    StorageMonitorExecutor,
    StorageMonitorVerifier,
)


@dataclass
class Backend:
    free_bytes: int = 4
    fail: bool = False
    reads: int = 0

    def read(self, root: Path) -> DiskUsageSnapshot:
        self.reads += 1
        if self.fail:
            raise OSError("private disk detail")
        return DiskUsageSnapshot(
            total_bytes=100,
            used_bytes=100 - self.free_bytes,
            free_bytes=self.free_bytes,
        )


def action(capability: str = "cleanup.suggest") -> Action:
    predicate = (
        "cleanup.suggestion.safe"
        if capability == "cleanup.suggest"
        else "storage.status.valid"
    )
    return Action(
        capability_id=capability,
        arguments={"root_id": "system"},
        purpose="evaluate storage status without mutation",
        target_identity="storage:system",
        expected_postconditions=(Postcondition(predicate=predicate),),
    )


def test_low_disk_suggestion_has_zero_mutations_and_independent_readback(
    tmp_path: Path,
) -> None:
    backend = Backend(free_bytes=4)
    executor = StorageMonitorExecutor({"system": tmp_path}, backend=backend)
    request = action()

    result = executor.execute(request)
    verified = StorageMonitorVerifier(executor).verify(request, result)

    assert result.success
    assert result.observed == {
        "free_percent_bucket": "LE_THRESHOLD",
        "low_disk": True,
        "mutation_count": 0,
        "root_id": "system",
        "suggestion_code": "LOW_DISK_REVIEW",
    }
    assert verified.success
    assert verified.observed["mutation_count"] == 0
    assert backend.reads == 2
    assert str(tmp_path) not in result.model_dump_json()


def test_healthy_disk_returns_no_action(tmp_path: Path) -> None:
    backend = Backend(free_bytes=50)
    executor = StorageMonitorExecutor({"system": tmp_path}, backend=backend)

    result = executor.execute(action())

    assert result.observed["low_disk"] is False
    assert result.observed["suggestion_code"] == "NO_ACTION"
    assert result.observed["mutation_count"] == 0


def test_status_read_uses_same_privacy_minimized_contract(tmp_path: Path) -> None:
    executor = StorageMonitorExecutor({"system": tmp_path}, backend=Backend())

    result = executor.execute(action("storage.status.read"))

    assert result.success
    assert "suggestion_code" not in result.observed
    assert "total_bytes" not in result.observed
    assert "free_bytes" not in result.observed


def test_unknown_root_and_target_mismatch_do_not_read_disk(tmp_path: Path) -> None:
    backend = Backend()
    executor = StorageMonitorExecutor({"system": tmp_path}, backend=backend)
    unknown = action().model_copy(update={"arguments": {"root_id": "other"}})
    wrong_target = action().model_copy(update={"target_identity": "storage:other"})

    assert executor.execute(unknown).error_code == "STORAGE_ROOT_NOT_REGISTERED"
    assert executor.execute(wrong_target).error_code == "TARGET_IDENTITY_MISMATCH"
    assert backend.reads == 0


def test_backend_error_is_redacted_and_retry_safe(tmp_path: Path) -> None:
    executor = StorageMonitorExecutor({"system": tmp_path}, backend=Backend(fail=True))

    result = executor.execute(action())

    assert result.error_code == "STORAGE_READ_FAILED"
    assert result.retry_safe
    assert "private disk detail" not in result.model_dump_json()


def test_stop_prevents_disk_read(tmp_path: Path) -> None:
    backend = Backend()
    executor = StorageMonitorExecutor({"system": tmp_path}, backend=backend)
    token = CancellationToken()
    token.cancel()

    result = executor.execute(action(), token)

    assert result.error_code == "STOP_REQUESTED"
    assert not result.retry_safe
    assert backend.reads == 0


def test_verifier_rejects_forged_mutation_count(tmp_path: Path) -> None:
    backend = Backend()
    executor = StorageMonitorExecutor({"system": tmp_path}, backend=backend)
    request = action()
    result = executor.execute(request)
    forged = result.model_copy(
        update={"observed": {**result.observed, "mutation_count": 1}}
    )

    verified = StorageMonitorVerifier(executor).verify(request, forged)

    assert not verified.success
    assert verified.reason == "STORAGE_VERIFY_BINDING_FAILED"
    assert not verified.recoverable
