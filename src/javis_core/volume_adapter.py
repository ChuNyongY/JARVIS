from __future__ import annotations

import importlib
import math
import threading
from time import perf_counter_ns
from typing import Protocol, cast

from .cancellation import CancellationToken
from .models import Action, VerificationResult
from .runtime_models import ExecutionResult

_TARGET = "default-audio-render-endpoint"


class VolumeBackendError(RuntimeError):
    """Stable boundary error that does not expose COM or device details."""


class VolumeBackend(Protocol):
    def read_percent(self) -> float: ...

    def set_percent(self, percent: float) -> None: ...


class _PycawAudioDevice(Protocol):
    @property
    def volume_percent(self) -> float: ...

    @volume_percent.setter
    def volume_percent(self, value: float) -> None: ...


class PycawEndpointVolumeBackend:
    """Lazy pycaw wrapper for the default multimedia render endpoint."""

    def __init__(self) -> None:
        try:
            module = importlib.import_module("pycaw.pycaw")
            utilities = module.AudioUtilities
            device = utilities.GetSpeakers()
        except Exception:
            raise VolumeBackendError("audio backend unavailable") from None
        if device is None:
            raise VolumeBackendError("audio backend unavailable")
        self._device = cast(_PycawAudioDevice, device)

    def read_percent(self) -> float:
        try:
            value = float(self._device.volume_percent)
        except Exception:
            raise VolumeBackendError("volume read failed") from None
        if not math.isfinite(value) or not 0.0 <= value <= 100.0:
            raise VolumeBackendError("invalid volume state")
        return value

    def set_percent(self, percent: float) -> None:
        if not math.isfinite(percent) or not 0.0 <= percent <= 100.0:
            raise ValueError("percent must be finite and between 0 and 100")
        try:
            self._device.volume_percent = percent
        except Exception:
            raise VolumeBackendError("volume set failed") from None


class WindowsVolumeExecutor:
    name = "native"

    def __init__(
        self,
        backend: VolumeBackend,
        *,
        tolerance_percent: float = 1.0,
        lock_timeout_seconds: float = 2.0,
    ) -> None:
        if not math.isfinite(tolerance_percent) or not 0.0 <= tolerance_percent <= 5.0:
            raise ValueError("tolerance_percent must be finite and between 0 and 5")
        if not math.isfinite(lock_timeout_seconds) or not 0.01 <= lock_timeout_seconds <= 10.0:
            raise ValueError("lock_timeout_seconds must be finite and between 0.01 and 10")
        self._backend = backend
        self._tolerance = tolerance_percent
        self._lock_timeout = lock_timeout_seconds
        self._lock = threading.Lock()

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        read_only = self._is_valid_read(action)
        percent = None if read_only else self._validated_percent(action)
        if not read_only and percent is None:
            return self._failure(action, started, "INVALID_ARGUMENTS", retry_safe=True)
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=True)

        if not self._lock.acquire(timeout=self._lock_timeout):
            return self._failure(action, started, "VOLUME_BUSY", retry_safe=True)
        try:
            try:
                observed = self._backend.read_percent()
            except Exception:
                return self._failure(action, started, "VOLUME_READ_FAILED", retry_safe=True)
            if read_only:
                if cancellation is not None and cancellation.is_cancelled:
                    return self._failure(
                        action,
                        started,
                        "STOP_REQUESTED",
                        retry_safe=True,
                    )
                return ExecutionResult(
                    action_id=action.action_id,
                    executor="native",
                    success=True,
                    observed={"volume_percent": observed},
                    duration_ms=self._elapsed_ms(started),
                )
            if cancellation is not None and cancellation.is_cancelled:
                return self._failure(action, started, "STOP_REQUESTED", retry_safe=True)
            assert percent is not None
            try:
                self._backend.set_percent(float(percent))
            except Exception:
                return self._failure(
                    action,
                    started,
                    "VOLUME_INPUT_UNCERTAIN",
                    retry_safe=False,
                )
            if cancellation is not None and cancellation.is_cancelled:
                return self._failure(
                    action,
                    started,
                    "STOP_REQUESTED_AFTER_INPUT",
                    retry_safe=False,
                )
            try:
                observed = self._backend.read_percent()
            except Exception:
                return self._failure(
                    action,
                    started,
                    "VOLUME_STATE_UNKNOWN",
                    retry_safe=False,
                )
            if abs(observed - percent) > self._tolerance:
                return self._failure(
                    action,
                    started,
                    "VOLUME_POSTCONDITION_FAILED",
                    retry_safe=False,
                )
        finally:
            self._lock.release()
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed={"volume_percent": observed},
            duration_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _is_valid_read(action: Action) -> bool:
        if (
            action.capability_id != "audio.volume.read"
            or action.target_identity != _TARGET
            or action.arguments
            or len(action.expected_postconditions) != 1
        ):
            return False
        postcondition = action.expected_postconditions[0]
        return postcondition.predicate == "volume.observed" and not postcondition.arguments

    @staticmethod
    def _validated_percent(action: Action) -> int | None:
        percent = action.arguments.get("percent")
        if (
            action.capability_id != "audio.volume.set"
            or action.target_identity != _TARGET
            or type(percent) is not int
            or not 0 <= percent <= 100
        ):
            return None
        if len(action.expected_postconditions) != 1:
            return None
        postcondition = action.expected_postconditions[0]
        if postcondition.predicate != "volume.matches" or postcondition.arguments:
            return None
        return percent

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (perf_counter_ns() - started) // 1_000_000)

    @classmethod
    def _failure(
        cls,
        action: Action,
        started: int,
        error_code: str,
        *,
        retry_safe: bool,
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=False,
            error_code=error_code,
            error_message="Volume action was not completed",
            retry_safe=retry_safe,
            duration_ms=cls._elapsed_ms(started),
        )


class VolumeMatchesVerifier:
    """Independently reads Core Audio state instead of trusting executor output."""

    def __init__(self, backend: VolumeBackend, *, tolerance_percent: float = 1.0) -> None:
        if not math.isfinite(tolerance_percent) or not 0.0 <= tolerance_percent <= 5.0:
            raise ValueError("tolerance_percent must be finite and between 0 and 5")
        self._backend = backend
        self._tolerance = tolerance_percent

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        if result.action_id != action.action_id:
            return self._failure("ACTION_ID_MISMATCH")
        if result.executor != "native":
            return self._failure("EXECUTOR_MISMATCH")
        if not result.success:
            return self._failure(
                result.error_code or "EXECUTION_FAILED",
                recoverable=result.retry_safe,
            )
        read_only = WindowsVolumeExecutor._is_valid_read(action)
        expected = None if read_only else WindowsVolumeExecutor._validated_percent(action)
        if not read_only and expected is None:
            return self._failure("INVALID_VOLUME_CONTRACT")
        try:
            observed = self._backend.read_percent()
        except Exception:
            return self._failure("VOLUME_VERIFY_FAILED")
        if read_only:
            reported = result.observed.get("volume_percent")
            if (
                isinstance(reported, bool)
                or not isinstance(reported, (int, float))
                or not math.isfinite(float(reported))
                or not 0.0 <= float(reported) <= 100.0
                or abs(observed - float(reported)) > self._tolerance
            ):
                return self._failure("VOLUME_VERIFY_MISMATCH")
            return VerificationResult(
                success=True,
                observed={"volume_percent": observed},
                reason="VOLUME_OBSERVED",
            )
        assert expected is not None
        if abs(observed - expected) > self._tolerance:
            return self._failure("VOLUME_VERIFY_MISMATCH")
        return VerificationResult(
            success=True,
            observed={"volume_percent": observed},
            reason="VOLUME_MATCHED",
        )

    @staticmethod
    def _failure(reason: str, *, recoverable: bool = False) -> VerificationResult:
        return VerificationResult(
            success=False,
            reason=reason,
            recoverable=recoverable,
        )
