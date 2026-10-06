from __future__ import annotations

import math
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from time import perf_counter_ns
from typing import Protocol

from pydantic import Field, JsonValue, model_validator

from .cancellation import CancellationToken
from .models import Action, FrozenModel, VerificationResult
from .runtime_models import ExecutionResult

_ROOT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class DiskUsageSnapshot(FrozenModel):
    total_bytes: int = Field(ge=1)
    used_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent_usage(self) -> DiskUsageSnapshot:
        if self.used_bytes > self.total_bytes or self.free_bytes > self.total_bytes:
            raise ValueError("disk usage values exceed total")
        if self.used_bytes + self.free_bytes > self.total_bytes + 4096:
            raise ValueError("disk usage values are inconsistent")
        return self

    @property
    def free_percent(self) -> float:
        return self.free_bytes * 100.0 / self.total_bytes


class DiskUsageBackend(Protocol):
    def read(self, root: Path) -> DiskUsageSnapshot: ...


class ShutilDiskUsageBackend:
    def read(self, root: Path) -> DiskUsageSnapshot:
        usage = shutil.disk_usage(root)
        return DiskUsageSnapshot(
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
        )


class StorageMonitorExecutor:
    name = "native"

    def __init__(
        self,
        roots: Mapping[str, Path],
        *,
        backend: DiskUsageBackend | None = None,
        low_disk_percent: float = 5.0,
    ) -> None:
        if not roots or any(not _ROOT_ID.fullmatch(key) for key in roots):
            raise ValueError("storage roots must use bounded identifiers")
        if not math.isfinite(low_disk_percent) or not 1.0 <= low_disk_percent <= 25.0:
            raise ValueError("low_disk_percent must be between 1 and 25")
        resolved: dict[str, Path] = {}
        for key, root in roots.items():
            if root.is_symlink():
                raise ValueError("storage roots cannot be symlinks")
            value = root.resolve(strict=True)
            if not value.is_dir():
                raise ValueError("storage roots must be directories")
            resolved[key] = value
        self._roots = resolved
        self._backend = backend or ShutilDiskUsageBackend()
        self._threshold = low_disk_percent

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        validated = self._validate(action)
        if isinstance(validated, str):
            return self._failure(action, started, validated)
        capability, root_id, root = validated
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")
        try:
            usage = self._backend.read(root)
        except Exception:
            return self._failure(action, started, "STORAGE_READ_FAILED")
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")
        low_disk = usage.free_percent <= self._threshold
        observed: dict[str, JsonValue] = {
            "free_percent_bucket": "LE_THRESHOLD" if low_disk else "GT_THRESHOLD",
            "low_disk": low_disk,
            "mutation_count": 0,
            "root_id": root_id,
        }
        if capability == "cleanup.suggest":
            observed["suggestion_code"] = "LOW_DISK_REVIEW" if low_disk else "NO_ACTION"
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed=observed,
            duration_ms=self._elapsed_ms(started),
        )

    def _validate(self, action: Action) -> tuple[str, str, Path] | str:
        predicates = {
            "storage.status.read": "storage.status.valid",
            "cleanup.suggest": "cleanup.suggestion.safe",
        }
        expected = predicates.get(action.capability_id)
        if expected is None:
            return "UNSUPPORTED_CAPABILITY"
        if set(action.arguments) != {"root_id"}:
            return "INVALID_ARGUMENTS"
        root_id = action.arguments.get("root_id")
        if not isinstance(root_id, str) or root_id not in self._roots:
            return "STORAGE_ROOT_NOT_REGISTERED"
        if action.target_identity != f"storage:{root_id}":
            return "TARGET_IDENTITY_MISMATCH"
        if len(action.expected_postconditions) != 1:
            return "INVALID_POSTCONDITION"
        postcondition = action.expected_postconditions[0]
        if postcondition.predicate != expected or postcondition.arguments:
            return "INVALID_POSTCONDITION"
        return action.capability_id, root_id, self._roots[root_id]

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (perf_counter_ns() - started) // 1_000_000)

    @classmethod
    def _failure(cls, action: Action, started: int, error_code: str) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=False,
            error_code=error_code,
            error_message="Storage status was not read",
            retry_safe=error_code == "STORAGE_READ_FAILED",
            duration_ms=cls._elapsed_ms(started),
        )


class StorageMonitorVerifier:
    def __init__(self, executor: StorageMonitorExecutor) -> None:
        self._executor = executor

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        if result.action_id != action.action_id:
            return self._failure("ACTION_ID_MISMATCH")
        if result.executor != "native":
            return self._failure("EXECUTOR_MISMATCH")
        if not result.success:
            return self._failure(result.error_code or "EXECUTION_FAILED")
        validated = self._executor._validate(action)
        if isinstance(validated, str):
            return self._failure("STORAGE_VERIFY_CONTRACT_FAILED")
        capability, root_id, root = validated
        try:
            usage = self._executor._backend.read(root)
        except Exception:
            return self._failure("STORAGE_VERIFY_READ_FAILED")
        low_disk = usage.free_percent <= self._executor._threshold
        expected_bucket = "LE_THRESHOLD" if low_disk else "GT_THRESHOLD"
        if (
            result.observed.get("root_id") != root_id
            or result.observed.get("low_disk") is not low_disk
            or result.observed.get("free_percent_bucket") != expected_bucket
            or result.observed.get("mutation_count") != 0
        ):
            return self._failure("STORAGE_VERIFY_BINDING_FAILED")
        if capability == "cleanup.suggest":
            expected_code = "LOW_DISK_REVIEW" if low_disk else "NO_ACTION"
            if result.observed.get("suggestion_code") != expected_code:
                return self._failure("STORAGE_VERIFY_SUGGESTION_FAILED")
        return VerificationResult(
            success=True,
            observed={
                "free_percent_bucket": expected_bucket,
                "low_disk": low_disk,
                "mutation_count": 0,
                "root_id": root_id,
            },
            reason="STORAGE_STATUS_VERIFIED",
        )

    @staticmethod
    def _failure(reason: str) -> VerificationResult:
        return VerificationResult(success=False, reason=reason, recoverable=False)
