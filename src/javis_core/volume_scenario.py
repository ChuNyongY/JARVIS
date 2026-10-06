from __future__ import annotations

import secrets
from collections.abc import Callable

from pydantic import Field, model_validator

from .approval import ApprovalTokenService
from .audit import InMemoryAuditSink
from .contracts import validate_plan_contracts
from .execution_broker import ExecutionBroker
from .executor import Executor
from .fault_injection import FaultInjectingExecutor, FaultPhase, FaultSpec
from .models import Action, ActionStatus, FrozenModel, Plan, PolicyContext, Postcondition
from .orchestrator import Orchestrator
from .policy import PolicyEngine
from .registry import default_registry
from .runtime_models import RetryBudget
from .volume_adapter import (
    PycawEndpointVolumeBackend,
    VolumeBackend,
    VolumeMatchesVerifier,
    WindowsVolumeExecutor,
)

_REQUESTED_PERCENT = 30
_READBACK_TOLERANCE = 1.0
_RESTORE_TOLERANCE = 0.25


class VolumeScenarioResult(FrozenModel):
    scenario_id: str = Field(default="S02", pattern=r"^S02$")
    success: bool
    evidence_level: str = Field(
        default="ACTUAL_REVERSIBLE",
        pattern=r"^ACTUAL_REVERSIBLE$",
    )
    verified_assertions: tuple[str, ...] = Field(max_length=1)
    error_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{2,64}$")

    @model_validator(mode="after")
    def consistent_error(self) -> VolumeScenarioResult:
        if self.success == (self.error_code is not None):
            raise ValueError("volume scenario result is inconsistent")
        return self


class S02VolumeScenarioRunner:
    """Run S02 through the production pipeline and restore the original volume."""

    def __init__(
        self,
        backend_factory: Callable[[], VolumeBackend] = PycawEndpointVolumeBackend,
    ) -> None:
        self._backend_factory = backend_factory

    def run(self) -> VolumeScenarioResult:
        return self._run(inject_delay=False)

    def run_app_delay(self) -> VolumeScenarioResult:
        return self._run(inject_delay=True)

    def _run(self, *, inject_delay: bool) -> VolumeScenarioResult:
        backend: VolumeBackend | None = None
        original: float | None = None
        pipeline_verified = False
        delay_verified = not inject_delay
        restored = False
        error_code = "S02_VOLUME_PIPELINE_FAILED"
        try:
            backend = self._backend_factory()
            original = backend.read_percent()
            native = WindowsVolumeExecutor(backend)
            fault_executor: FaultInjectingExecutor | None = None
            if inject_delay:
                fault_executor = FaultInjectingExecutor(
                    native,
                    (
                        FaultSpec(
                            name="app-delay-timeout",
                            phase=FaultPhase.BEFORE_CALL,
                            on_call=1,
                            error_code="APP_DELAY_TIMEOUT",
                            retry_safe=True,
                            executor="native",
                        ),
                    ),
                )
                executor: Executor = fault_executor
            else:
                executor = native

            registry = default_registry()
            action = Action(
                capability_id="audio.volume.set",
                arguments={"percent": _REQUESTED_PERCENT},
                purpose="S02 exact requested volume benchmark",
                target_identity="default-audio-render-endpoint",
                expected_postconditions=(Postcondition(predicate="volume.matches"),),
            )
            plan = Plan(goal="Verify S02 volume and independent readback", steps=(action,))
            validate_plan_contracts(plan, registry)
            outcome = Orchestrator(
                policy=PolicyEngine(registry),
                executor=ExecutionBroker(registry, {"native": executor}),
                verifier=VolumeMatchesVerifier(backend),
                audit=InMemoryAuditSink(),
                approvals=ApprovalTokenService(secrets.token_bytes(32)),
                retry_budget=RetryBudget(
                    max_attempts=2 if inject_delay else 1,
                    max_replans=0,
                ),
            ).run(plan, _trusted_context)
            pipeline_verified = (
                outcome.status is ActionStatus.SUCCEEDED
                and outcome.completed_actions == 1
                and abs(backend.read_percent() - _REQUESTED_PERCENT)
                <= _READBACK_TOLERANCE
            )
            if fault_executor is not None:
                observations = fault_executor.observations
                delay_verified = (
                    len(observations) == 1
                    and observations[0].error_code == "APP_DELAY_TIMEOUT"
                    and observations[0].retry_safe
                    and not observations[0].delegate_called
                    and not observations[0].side_effect_possible
                )
            if not delay_verified:
                error_code = "S02_DELAY_RECOVERY_FAILED"
        except Exception:
            error_code = "S02_VOLUME_PIPELINE_FAILED"
        finally:
            if backend is not None and original is not None:
                try:
                    backend.set_percent(original)
                    restored = (
                        abs(backend.read_percent() - original) <= _RESTORE_TOLERANCE
                    )
                except Exception:
                    restored = False

        if not restored:
            error_code = "S02_VOLUME_RESTORE_FAILED"
        success = pipeline_verified and delay_verified and restored
        return VolumeScenarioResult(
            success=success,
            verified_assertions=("volume_within_tolerance",) if success else (),
            error_code=None if success else error_code,
        )


def _trusted_context(_action: Action) -> PolicyContext:
    return PolicyContext(
        addressed_confidence=1.0,
        intent_confidence=1.0,
        target_confidence=1.0,
        target_matches_plan=True,
    )
