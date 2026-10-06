from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from time import perf_counter_ns
from typing import Protocol

from .cancellation import CancellationToken
from .file_move import file_sha256
from .models import Action, Postcondition
from .runtime_models import ExecutionResult


class TrashBackend(Protocol):
    def trash(self, path: Path) -> None: ...


class Send2TrashBackend:
    def trash(self, path: Path) -> None:
        try:
            from send2trash import send2trash  # type: ignore[import-untyped]
        except ImportError as error:
            raise RuntimeError("send2trash optional dependency is not installed") from error
        send2trash(str(path))


class FileTrashExecutor:
    """Move one exact regular file to the OS trash; never permanently delete."""

    name = "native"

    def __init__(
        self,
        allowed_roots: Iterable[Path],
        backend: TrashBackend | None = None,
    ) -> None:
        self._roots = tuple(root.resolve(strict=True) for root in allowed_roots)
        if not self._roots or any(not root.is_dir() for root in self._roots):
            raise ValueError("allowed roots must be existing directories")
        self._backend = backend or Send2TrashBackend()

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        source = self._validate(action)
        if isinstance(source, str):
            return self._failure(action, started, source, retry_safe=True)
        source_hash = file_sha256(source)
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=False)
        try:
            if (
                source.is_symlink()
                or source.resolve(strict=True) != source
                or file_sha256(source) != source_hash
            ):
                return self._failure(action, started, "SOURCE_CHANGED", retry_safe=False)
        except OSError:
            return self._failure(action, started, "SOURCE_CHANGED", retry_safe=False)
        try:
            self._backend.trash(source)
        except Exception:
            return self._failure(action, started, "TRASH_STATE_AMBIGUOUS", retry_safe=False)
        if source.exists():
            return self._failure(action, started, "TRASH_VERIFICATION_FAILED", retry_safe=False)
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed={"path": str(source), "source_hash": source_hash},
            duration_ms=self._elapsed_ms(started),
        )

    def _validate(self, action: Action) -> Path | str:
        if action.capability_id != "file.delete.trash":
            return "UNSUPPORTED_CAPABILITY"
        if set(action.arguments) != {"path"}:
            return "INVALID_ARGUMENTS"
        raw_path = action.arguments.get("path")
        if not isinstance(raw_path, str):
            return "INVALID_ARGUMENTS"
        source_input = Path(raw_path)
        if not source_input.is_absolute() or source_input.is_symlink():
            return "UNSAFE_PATH"
        try:
            source = source_input.resolve(strict=True)
        except OSError:
            return "PATH_NOT_FOUND"
        if not source.is_file() or not any(source.is_relative_to(root) for root in self._roots):
            return "PATH_OUTSIDE_ALLOWED_ROOTS"
        if action.target_identity != str(source):
            return "TARGET_IDENTITY_MISMATCH"
        return source

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
            error_message="File trash operation was not verified",
            retry_safe=retry_safe,
            duration_ms=cls._elapsed_ms(started),
        )


class FileAbsentPredicate:
    def __call__(self, postcondition: Postcondition, result: ExecutionResult) -> bool:
        if postcondition.arguments:
            return False
        raw_path = result.observed.get("path")
        if not isinstance(raw_path, str):
            return False
        return not Path(raw_path).exists()
