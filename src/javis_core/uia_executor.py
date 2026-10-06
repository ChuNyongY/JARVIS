from __future__ import annotations

from collections.abc import Callable
from time import perf_counter_ns

from .cancellation import CancellationToken
from .models import Action
from .runtime_models import ExecutionResult
from .uia_observation import UiaObservationError, UiaObserver, UiaSelector


class UiaInspectExecutor:
    """Read structural UIA metadata only for a runtime-authorized process."""

    name = "uia"
    _ALLOWED_ARGUMENTS = frozenset(
        {"process_id", "automation_id", "control_type", "name", "class_name"}
    )

    def __init__(
        self,
        observer: UiaObserver,
        process_authorized: Callable[[int], bool],
    ) -> None:
        self._observer = observer
        self._process_authorized = process_authorized

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=False)
        selector = self._selector(action)
        if isinstance(selector, str):
            return self._failure(action, started, selector, retry_safe=True)
        if action.target_identity != f"process:{selector.process_id}":
            return self._failure(action, started, "TARGET_IDENTITY_MISMATCH", retry_safe=True)
        if not self._process_authorized(selector.process_id):
            return self._failure(action, started, "PROCESS_NOT_AUTHORIZED", retry_safe=True)
        try:
            snapshot = self._observer.find_unique(selector)
        except UiaObservationError:
            return self._failure(action, started, "UIA_OBSERVATION_FAILED", retry_safe=True)
        return ExecutionResult(
            action_id=action.action_id,
            executor="uia",
            success=True,
            observed={
                "process_id": snapshot.process_id,
                "runtime_id": list(snapshot.runtime_id),
                "automation_id": snapshot.automation_id,
                "control_type": snapshot.control_type,
                "class_name": snapshot.class_name,
                "enabled": snapshot.enabled,
                "visible": snapshot.visible,
            },
            duration_ms=self._elapsed_ms(started),
        )

    def _selector(self, action: Action) -> UiaSelector | str:
        if action.capability_id != "ui.element.inspect":
            return "UNSUPPORTED_CAPABILITY"
        keys = set(action.arguments)
        if "process_id" not in keys:
            return "INVALID_ARGUMENTS"
        if not keys <= self._ALLOWED_ARGUMENTS:
            return "INVALID_ARGUMENTS"
        process_id = action.arguments.get("process_id")
        automation_id = action.arguments.get("automation_id")
        control_type = action.arguments.get("control_type")
        name = action.arguments.get("name")
        class_name = action.arguments.get("class_name")
        if type(process_id) is not int:
            return "INVALID_ARGUMENTS"
        if automation_id is not None and not isinstance(automation_id, str):
            return "INVALID_ARGUMENTS"
        if control_type is not None and not isinstance(control_type, str):
            return "INVALID_ARGUMENTS"
        if name is not None and not isinstance(name, str):
            return "INVALID_ARGUMENTS"
        if class_name is not None and not isinstance(class_name, str):
            return "INVALID_ARGUMENTS"
        try:
            return UiaSelector(
                process_id=process_id,
                automation_id=automation_id,
                control_type=control_type,
                name=name,
                class_name=class_name,
            )
        except ValueError:
            return "INVALID_ARGUMENTS"

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
            executor="uia",
            success=False,
            error_code=error_code,
            error_message="UI Automation element was not observed",
            retry_safe=retry_safe,
            duration_ms=cls._elapsed_ms(started),
        )
