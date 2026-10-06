from __future__ import annotations

from collections.abc import Mapping

from .cancellation import CancellationToken
from .executor import Executor
from .models import Action, ExecutorKind
from .runtime_models import ExecutionResult


class CapabilityExecutorRouter:
    """Route an exact capability ID to an adapter within one executor tier."""

    def __init__(
        self,
        executor_name: ExecutorKind,
        routes: Mapping[str, Executor],
    ) -> None:
        self._executor_name = executor_name
        self._routes = dict(routes)

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        executor = self._routes.get(action.capability_id)
        if executor is None:
            return ExecutionResult(
                action_id=action.action_id,
                executor=self._executor_name,
                success=False,
                error_code="UNSUPPORTED_CAPABILITY",
                error_message="No adapter is registered for this capability",
                retry_safe=True,
                duration_ms=0,
            )
        return executor.execute(action, cancellation)
