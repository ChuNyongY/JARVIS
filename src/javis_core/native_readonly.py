from __future__ import annotations

import heapq
import platform
import socket
from collections.abc import Iterable
from pathlib import Path
from time import perf_counter_ns

from .cancellation import CancellationToken
from .models import Action
from .runtime_models import ExecutionResult


class NativeReadOnlyExecutor:
    """Read-only native adapter for a deliberately small capability set."""

    name = "native"

    def __init__(self, allowed_roots: Iterable[Path], *, max_entries: int = 500) -> None:
        if not 1 <= max_entries <= 10_000:
            raise ValueError("max_entries must be between 1 and 10000")
        roots: list[Path] = []
        for root in allowed_roots:
            resolved = root.resolve(strict=True)
            if not resolved.is_dir():
                raise ValueError("allowed roots must be existing directories")
            roots.append(resolved)
        self._roots = tuple(roots)
        self._max_entries = max_entries

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")
        if action.capability_id == "system.info.read":
            return self._system_info(action, started)
        if action.capability_id == "file.list":
            return self._file_list(action, started, cancellation)
        return self._failure(action, started, "UNSUPPORTED_CAPABILITY")

    def _system_info(self, action: Action, started: int) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed={
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "hostname": socket.gethostname(),
            },
            duration_ms=self._elapsed_ms(started),
        )

    def _file_list(
        self,
        action: Action,
        started: int,
        cancellation: CancellationToken | None,
    ) -> ExecutionResult:
        raw_path = action.arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return self._failure(action, started, "INVALID_ARGUMENTS")
        try:
            candidate = Path(raw_path).resolve(strict=True)
        except OSError:
            return self._failure(action, started, "PATH_NOT_FOUND")
        root_index = self._matching_root(candidate)
        if root_index is None:
            return self._failure(action, started, "PATH_OUTSIDE_ALLOWED_ROOTS")
        if not candidate.is_dir():
            return self._failure(action, started, "PATH_NOT_DIRECTORY")
        if action.target_identity != str(candidate):
            return self._failure(action, started, "TARGET_IDENTITY_MISMATCH")
        try:
            names = heapq.nsmallest(
                self._max_entries + 1,
                (entry.name for entry in candidate.iterdir()),
            )
        except OSError:
            return self._failure(action, started, "PATH_ACCESS_DENIED")
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")
        truncated = len(names) > self._max_entries
        names = names[: self._max_entries]
        relative_path = str(candidate.relative_to(self._roots[root_index]))
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed={
                "entries": names,
                "count": len(names),
                "truncated": truncated,
                "root_index": root_index,
                "relative_path": relative_path,
            },
            duration_ms=self._elapsed_ms(started),
        )

    def _matching_root(self, candidate: Path) -> int | None:
        for index, root in enumerate(self._roots):
            if candidate.is_relative_to(root):
                return index
        return None

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (perf_counter_ns() - started) // 1_000_000)

    @classmethod
    def _failure(
        cls,
        action: Action,
        started: int,
        error_code: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=False,
            error_code=error_code,
            error_message="Native read-only action was not completed",
            retry_safe=error_code != "STOP_REQUESTED",
            duration_ms=cls._elapsed_ms(started),
        )
