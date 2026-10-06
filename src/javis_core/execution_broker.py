from __future__ import annotations

from collections.abc import Mapping

from .cancellation import CancellationToken
from .executor import Executor
from .models import Action, ExecutorKind
from .registry import CapabilityRegistry
from .runtime_models import ExecutionResult
from .selective_capture import SelectiveCaptureGate


class ExecutionBroker:
    """Select adapters in fixed least-privilege order and fail closed."""

    _ORDER: tuple[ExecutorKind, ...] = ("native", "powershell", "uia", "vision")
    _SAFE_FALLBACK_ERRORS = frozenset(
        {"UNSUPPORTED_CAPABILITY", "ADAPTER_UNAVAILABLE", "UIA_GROUNDING_REQUIRED"}
    )

    def __init__(
        self,
        registry: CapabilityRegistry,
        adapters: Mapping[ExecutorKind, Executor],
        vision_capture_gate: SelectiveCaptureGate | None = None,
    ) -> None:
        self._registry = registry
        self._adapters = dict(adapters)
        self._vision_capture_gate = vision_capture_gate

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        capability = self._registry.get(action.capability_id)
        if capability is None:
            return self._failure(action, "UNKNOWN_CAPABILITY", "native")
        last_result: ExecutionResult | None = None
        for executor_name in self._ORDER:
            if executor_name not in capability.allowed_executors:
                continue
            adapter = self._adapters.get(executor_name)
            if adapter is None:
                continue
            if executor_name == "vision" and (
                self._vision_capture_gate is None
                or last_result is None
                or not self._vision_capture_gate.authorize_uia_fallback(
                    action,
                    last_result,
                )
            ):
                return self._failure(
                    action,
                    "VISION_FALLBACK_NOT_AUTHORIZED",
                    "vision",
                )
            try:
                result = adapter.execute(action, cancellation)
            except Exception:
                if executor_name == "vision" and self._vision_capture_gate is not None:
                    self._vision_capture_gate.close(action.action_id)
                raise
            if (
                executor_name == "vision"
                and (
                    not result.success
                    or result.action_id != action.action_id
                    or result.executor != "vision"
                )
                and self._vision_capture_gate is not None
            ):
                self._vision_capture_gate.close(action.action_id)
            if result.success:
                return result
            last_result = result
            if result.error_code not in self._SAFE_FALLBACK_ERRORS:
                return result
        if last_result is not None:
            return last_result
        fallback_name = capability.allowed_executors[0]
        return self._failure(action, "NO_EXECUTOR_AVAILABLE", fallback_name)

    @staticmethod
    def _failure(
        action: Action,
        error_code: str,
        executor: ExecutorKind,
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor=executor,
            success=False,
            error_code=error_code,
            error_message="No authorized executor completed the action",
            retry_safe=True,
            duration_ms=0,
        )
