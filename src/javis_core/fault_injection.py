from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from .cancellation import CancellationToken
from .executor import Executor
from .models import Action, FrozenModel
from .runtime_models import ExecutionResult, ExecutorName


class FaultPhase(StrEnum):
    BEFORE_CALL = "BEFORE_CALL"
    AFTER_CALL = "AFTER_CALL"


class FaultSpec(FrozenModel):
    name: str = Field(min_length=1)
    phase: FaultPhase
    on_call: int = Field(ge=1)
    error_code: str = Field(min_length=1)
    retry_safe: bool = False
    executor: ExecutorName = "mock"

    @model_validator(mode="after")
    def ambiguous_fault_cannot_be_retry_safe(self) -> FaultSpec:
        if self.phase is FaultPhase.AFTER_CALL and self.retry_safe:
            raise ValueError("after-call fault cannot be retry-safe")
        return self


class FaultObservation(FrozenModel):
    observation_id: UUID = Field(default_factory=uuid4)
    fault_name: str
    call_index: int = Field(ge=1)
    phase: FaultPhase
    delegate_called: bool
    side_effect_possible: bool
    action_id: UUID
    error_code: str
    retry_safe: bool


class FaultInjectingExecutor:
    """Deterministic executor wrapper for failure and ambiguity experiments."""

    def __init__(self, delegate: Executor, faults: tuple[FaultSpec, ...]) -> None:
        call_keys = [(fault.phase, fault.on_call) for fault in faults]
        if len(call_keys) != len(set(call_keys)):
            raise ValueError("only one fault may target a phase and call index")
        self._delegate = delegate
        self._faults = faults
        self._call_count = 0
        self._observations: list[FaultObservation] = []

    @property
    def observations(self) -> tuple[FaultObservation, ...]:
        return tuple(self._observations)

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        self._call_count += 1
        before = self._fault(FaultPhase.BEFORE_CALL, self._call_count)
        if before is not None:
            return self._inject(action, before, delegate_called=False)

        delegate_result = self._delegate.execute(action, cancellation)
        after = self._fault(FaultPhase.AFTER_CALL, self._call_count)
        if after is not None:
            return self._inject(
                action,
                after,
                delegate_called=True,
                executor=delegate_result.executor,
                duration_ms=delegate_result.duration_ms,
            )
        return delegate_result

    def _fault(self, phase: FaultPhase, call_index: int) -> FaultSpec | None:
        return next(
            (
                fault
                for fault in self._faults
                if fault.phase is phase and fault.on_call == call_index
            ),
            None,
        )

    def _inject(
        self,
        action: Action,
        fault: FaultSpec,
        *,
        delegate_called: bool,
        executor: ExecutorName | None = None,
        duration_ms: int = 0,
    ) -> ExecutionResult:
        side_effect_possible = delegate_called
        retry_safe = fault.retry_safe and not side_effect_possible
        observation = FaultObservation(
            fault_name=fault.name,
            call_index=self._call_count,
            phase=fault.phase,
            delegate_called=delegate_called,
            side_effect_possible=side_effect_possible,
            action_id=action.action_id,
            error_code=fault.error_code,
            retry_safe=retry_safe,
        )
        self._observations.append(observation)
        return ExecutionResult(
            action_id=action.action_id,
            executor=executor or fault.executor,
            success=False,
            observed={
                "fault_name": fault.name,
                "fault_phase": fault.phase.value,
                "side_effect_possible": side_effect_possible,
            },
            error_code=fault.error_code,
            error_message="Deterministic injected fault",
            retry_safe=retry_safe,
            duration_ms=duration_ms,
        )


def write_fault_report(path: Path, observations: tuple[FaultObservation, ...]) -> None:
    if path.exists() and path.is_dir():
        raise ValueError("fault report path is a directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b"".join(
        observation.model_dump_json().encode("utf-8") + b"\n"
        for observation in observations
    )
    descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        written = os.write(descriptor, payload)
        if written != len(payload):
            raise OSError("partial fault report write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
