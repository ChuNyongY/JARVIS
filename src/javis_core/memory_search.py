from __future__ import annotations

import hashlib
import json
import re
from time import perf_counter_ns
from typing import Protocol

from .cancellation import CancellationToken
from .memory import SemanticMemoryRecord
from .models import Action, VerificationResult
from .runtime_models import ExecutionResult


class MemorySearchBackend(Protocol):
    def search(self, query: str, *, k: int = 10) -> tuple[SemanticMemoryRecord, ...]: ...


class MemorySearchExecutor:
    name = "native"

    def __init__(self, backend: MemorySearchBackend, *, store_id: str) -> None:
        if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", store_id) is None:
            raise ValueError("invalid memory store ID")
        self._backend = backend
        self._target = f"memory-store:{store_id}"

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        request = self._validated_request(action)
        if request is None:
            return self._failure(action, started, "INVALID_ARGUMENTS")
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")
        query, k = request
        try:
            records = self._backend.search(query, k=k)
        except Exception:
            return self._failure(action, started, "MEMORY_SEARCH_FAILED")
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed=self._observation(records),
            duration_ms=self._elapsed_ms(started),
        )

    def _validated_request(self, action: Action) -> tuple[str, int] | None:
        if (
            action.capability_id != "memory.search"
            or action.target_identity != self._target
            or set(action.arguments) not in ({"query"}, {"query", "k"})
            or len(action.expected_postconditions) != 1
        ):
            return None
        query = action.arguments.get("query")
        k = action.arguments.get("k", 10)
        postcondition = action.expected_postconditions[0]
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 256
            or type(k) is not int
            or not 1 <= k <= 10
            or postcondition.predicate != "memory.search.matches"
            or postcondition.arguments
        ):
            return None
        return query, k

    @staticmethod
    def _observation(records: tuple[SemanticMemoryRecord, ...]) -> dict[str, object]:
        payload = [
            {
                "memory_id": str(record.memory_id),
                "key": record.key,
                "value": record.value,
            }
            for record in records
        ]
        digest = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        return {
            "match_count": len(records),
            "results_digest": digest,
            "unique_memory_id": str(records[0].memory_id) if len(records) == 1 else None,
        }

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
            error_message="Memory search was not completed",
            retry_safe=True,
            duration_ms=cls._elapsed_ms(started),
        )


class MemorySearchVerifier:
    """Rerun the bounded query and compare a privacy-minimized result binding."""

    def __init__(self, executor: MemorySearchExecutor) -> None:
        self._executor = executor

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        if result.action_id != action.action_id:
            return self._failure("ACTION_ID_MISMATCH")
        if result.executor != "native":
            return self._failure("EXECUTOR_MISMATCH")
        if not result.success:
            return self._failure(result.error_code or "EXECUTION_FAILED")
        request = self._executor._validated_request(action)  # noqa: SLF001
        if request is None:
            return self._failure("INVALID_MEMORY_SEARCH_CONTRACT")
        query, k = request
        try:
            records = self._executor._backend.search(query, k=k)  # noqa: SLF001
            expected = self._executor._observation(records)  # noqa: SLF001
        except Exception:
            return self._failure("MEMORY_SEARCH_VERIFY_FAILED")
        if result.observed != expected:
            return self._failure("MEMORY_SEARCH_VERIFY_MISMATCH")
        return VerificationResult(
            success=True,
            observed=expected,
            reason="MEMORY_SEARCH_MATCHED",
        )

    @staticmethod
    def _failure(reason: str) -> VerificationResult:
        return VerificationResult(success=False, reason=reason, recoverable=False)
