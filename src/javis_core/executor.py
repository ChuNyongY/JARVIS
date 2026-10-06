from __future__ import annotations

from collections.abc import Callable, Mapping
from time import perf_counter_ns
from typing import Protocol

from pydantic import JsonValue

from .cancellation import CancellationToken
from .models import Action
from .runtime_models import ExecutionResult


class Executor(Protocol):
    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult: ...


MockHandler = Callable[[Action], dict[str, JsonValue]]


class MockExecutor:
    """Deterministic executor for tests; it performs no operating-system action."""

    name = "mock"

    def __init__(self, handlers: Mapping[str, MockHandler] | None = None) -> None:
        self._handlers = dict(handlers or {})
        self._calls: list[Action] = []

    @property
    def calls(self) -> tuple[Action, ...]:
        return tuple(self._calls)

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        if cancellation is not None and cancellation.is_cancelled:
            return self._cancelled(action, started)
        self._calls.append(action)
        handler = self._handlers.get(action.capability_id)
        if handler is None:
            return ExecutionResult(
                action_id=action.action_id,
                executor="mock",
                success=False,
                error_code="MOCK_HANDLER_MISSING",
                error_message="No mock handler is registered for this capability",
                retry_safe=True,
                duration_ms=self._elapsed_ms(started),
            )
        try:
            observed = handler(action)
        except Exception as exc:  # test boundary: convert handler failure to data
            return ExecutionResult(
                action_id=action.action_id,
                executor="mock",
                success=False,
                error_code="MOCK_HANDLER_FAILED",
                error_message=type(exc).__name__,
                retry_safe=True,
                duration_ms=self._elapsed_ms(started),
            )
        if cancellation is not None and cancellation.is_cancelled:
            return self._cancelled(action, started)
        return ExecutionResult(
            action_id=action.action_id,
            executor="mock",
            success=True,
            observed=observed,
            duration_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (perf_counter_ns() - started) // 1_000_000)

    @classmethod
    def _cancelled(cls, action: Action, started: int) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="mock",
            success=False,
            error_code="STOP_REQUESTED",
            error_message="Execution cancelled by local stop signal",
            retry_safe=False,
            duration_ms=cls._elapsed_ms(started),
        )
