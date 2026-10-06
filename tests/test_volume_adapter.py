from __future__ import annotations

from dataclasses import dataclass

import pytest
from pytest import MonkeyPatch

from javis_core import (
    Action,
    CancellationToken,
    Postcondition,
    PycawEndpointVolumeBackend,
    VolumeBackendError,
    VolumeMatchesVerifier,
    WindowsVolumeExecutor,
)
from javis_core.runtime_models import ExecutionResult


@dataclass
class FakeVolumeBackend:
    percent: float = 50.0
    read_error_at: int | None = None
    set_error: bool = False
    forced_post_percent: float | None = None
    cancel_on_set: CancellationToken | None = None
    reads: int = 0
    sets: int = 0

    def read_percent(self) -> float:
        self.reads += 1
        if self.read_error_at == self.reads:
            raise VolumeBackendError("private backend detail")
        if self.reads >= 2 and self.forced_post_percent is not None:
            return self.forced_post_percent
        return self.percent

    def set_percent(self, percent: float) -> None:
        self.sets += 1
        if self.cancel_on_set is not None:
            self.cancel_on_set.cancel()
        if self.set_error:
            raise VolumeBackendError("private backend detail")
        self.percent = percent


def action(percent: object = 30, *, target: str = "default-audio-render-endpoint") -> Action:
    return Action(
        capability_id="audio.volume.set",
        arguments={"percent": percent},  # type: ignore[dict-item]
        purpose="set requested master volume",
        target_identity=target,
        expected_postconditions=(Postcondition(predicate="volume.matches"),),
    )


def read_action(*, target: str = "default-audio-render-endpoint") -> Action:
    return Action(
        capability_id="audio.volume.read",
        arguments={},
        purpose="read current master volume",
        target_identity=target,
        expected_postconditions=(Postcondition(predicate="volume.observed"),),
    )


def test_executor_sets_and_rechecks_volume_without_device_details() -> None:
    backend = FakeVolumeBackend()

    result = WindowsVolumeExecutor(backend).execute(action())

    assert result.success
    assert result.observed == {"volume_percent": 30.0}
    assert backend.reads == 2
    assert backend.sets == 1
    assert "device" not in result.model_dump_json()


def test_executor_reads_volume_without_mutation_or_device_details() -> None:
    backend = FakeVolumeBackend(percent=41.5)

    result = WindowsVolumeExecutor(backend).execute(read_action())

    assert result.success
    assert result.observed == {"volume_percent": 41.5}
    assert backend.reads == 1
    assert backend.sets == 0
    assert "device" not in result.model_dump_json()


def test_read_rejects_arguments_target_and_postcondition() -> None:
    backend = FakeVolumeBackend()
    candidates = (
        read_action().model_copy(update={"arguments": {"percent": 30}}),
        read_action(target="other-endpoint"),
        read_action().model_copy(
            update={"expected_postconditions": (Postcondition(predicate="volume.matches"),)}
        ),
    )

    for candidate in candidates:
        result = WindowsVolumeExecutor(backend).execute(candidate)
        assert result.error_code == "INVALID_ARGUMENTS"
    assert backend.reads == 0
    assert backend.sets == 0


@pytest.mark.parametrize("percent", [-1, 101, True, "30"])
def test_executor_rejects_invalid_percent_without_touching_backend(percent: object) -> None:
    backend = FakeVolumeBackend()

    result = WindowsVolumeExecutor(backend).execute(action(percent))

    assert not result.success
    assert result.error_code == "INVALID_ARGUMENTS"
    assert result.retry_safe
    assert backend.reads == 0
    assert backend.sets == 0


def test_executor_rejects_wrong_target_and_postcondition() -> None:
    backend = FakeVolumeBackend()
    wrong_post = action().model_copy(
        update={"expected_postconditions": (Postcondition(predicate="execution.success"),)}
    )

    for candidate in (action(target="other-endpoint"), wrong_post):
        result = WindowsVolumeExecutor(backend).execute(candidate)
        assert result.error_code == "INVALID_ARGUMENTS"
    assert backend.reads == 0
    assert backend.sets == 0


def test_stop_before_input_is_retry_safe() -> None:
    backend = FakeVolumeBackend()
    token = CancellationToken()
    token.cancel()

    result = WindowsVolumeExecutor(backend).execute(action(), token)

    assert result.error_code == "STOP_REQUESTED"
    assert result.retry_safe
    assert backend.sets == 0


def test_busy_executor_fails_with_bounded_retry_safe_result() -> None:
    backend = FakeVolumeBackend()
    executor = WindowsVolumeExecutor(backend, lock_timeout_seconds=0.01)
    assert executor._lock.acquire()  # noqa: SLF001 - deliberate contention injection
    try:
        result = executor.execute(action())
    finally:
        executor._lock.release()  # noqa: SLF001 - deliberate contention injection

    assert result.error_code == "VOLUME_BUSY"
    assert result.retry_safe
    assert backend.sets == 0


def test_stop_after_input_is_not_retry_safe_and_skips_post_read() -> None:
    token = CancellationToken()
    backend = FakeVolumeBackend(cancel_on_set=token)

    result = WindowsVolumeExecutor(backend).execute(action(), token)

    assert result.error_code == "STOP_REQUESTED_AFTER_INPUT"
    assert not result.retry_safe
    assert backend.reads == 1
    assert backend.sets == 1


def test_set_exception_is_input_uncertainty_and_hides_detail() -> None:
    backend = FakeVolumeBackend(set_error=True)

    result = WindowsVolumeExecutor(backend).execute(action())

    assert result.error_code == "VOLUME_INPUT_UNCERTAIN"
    assert not result.retry_safe
    assert "private backend detail" not in result.model_dump_json()


def test_post_read_failure_and_mismatch_are_not_retry_safe() -> None:
    read_failure = WindowsVolumeExecutor(FakeVolumeBackend(read_error_at=2)).execute(action())
    mismatch = WindowsVolumeExecutor(
        FakeVolumeBackend(forced_post_percent=10.0)
    ).execute(action())

    assert read_failure.error_code == "VOLUME_STATE_UNKNOWN"
    assert not read_failure.retry_safe
    assert mismatch.error_code == "VOLUME_POSTCONDITION_FAILED"
    assert not mismatch.retry_safe


def test_independent_verifier_reads_backend_and_does_not_trust_result() -> None:
    requested = action()
    forged = ExecutionResult(
        action_id=requested.action_id,
        executor="native",
        success=True,
        observed={"volume_percent": 30.0},
        duration_ms=1,
    )

    failed = VolumeMatchesVerifier(FakeVolumeBackend(percent=40.0)).verify(requested, forged)
    passed = VolumeMatchesVerifier(FakeVolumeBackend(percent=30.5)).verify(requested, forged)

    assert not failed.success
    assert failed.reason == "VOLUME_VERIFY_MISMATCH"
    assert not failed.recoverable
    assert passed.success
    assert passed.observed == {"volume_percent": 30.5}

    wrong_executor = forged.model_copy(update={"executor": "mock"})
    rejected = VolumeMatchesVerifier(FakeVolumeBackend(percent=30.0)).verify(
        requested,
        wrong_executor,
    )
    assert rejected.reason == "EXECUTOR_MISMATCH"


def test_verifier_preserves_retry_safety_for_pre_input_failure() -> None:
    requested = action()
    failed = ExecutionResult(
        action_id=requested.action_id,
        executor="native",
        success=False,
        error_code="APP_DELAY_TIMEOUT",
        retry_safe=True,
        duration_ms=1,
    )

    verification = VolumeMatchesVerifier(FakeVolumeBackend()).verify(requested, failed)

    assert not verification.success
    assert verification.reason == "APP_DELAY_TIMEOUT"
    assert verification.recoverable


def test_read_verifier_rereads_backend_and_rejects_forged_observation() -> None:
    requested = read_action()
    forged = ExecutionResult(
        action_id=requested.action_id,
        executor="native",
        success=True,
        observed={"volume_percent": 10.0},
        duration_ms=1,
    )

    failed = VolumeMatchesVerifier(FakeVolumeBackend(percent=40.0)).verify(requested, forged)
    passed = VolumeMatchesVerifier(FakeVolumeBackend(percent=40.0)).verify(
        requested,
        forged.model_copy(update={"observed": {"volume_percent": 40.0}}),
    )

    assert failed.reason == "VOLUME_VERIFY_MISMATCH"
    assert passed.success
    assert passed.reason == "VOLUME_OBSERVED"
    assert passed.observed == {"volume_percent": 40.0}


def test_pycaw_dependency_failure_is_redacted(monkeypatch: MonkeyPatch) -> None:
    def fail_import(_name: str) -> object:
        raise ImportError("private import detail")

    monkeypatch.setattr("javis_core.volume_adapter.importlib.import_module", fail_import)

    with pytest.raises(VolumeBackendError, match="audio backend unavailable") as caught:
        PycawEndpointVolumeBackend()

    assert "private import detail" not in str(caught.value)
