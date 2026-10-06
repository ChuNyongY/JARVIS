from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from .benchmark_collection import BenchmarkCollectionCell, BenchmarkRunOutcome
from .benchmark_tasks import BenchmarkTask, FaultProfile
from .evaluation import TrialResult
from .fault_injection import FaultInjectingExecutor, FaultPhase, FaultSpec
from .integrated_scenarios import IntegratedScenarioManifest, ScenarioEnvironment
from .models import Action, Decision, PolicyContext, Postcondition, RiskLevel
from .native_readonly import NativeReadOnlyExecutor
from .policy import PolicyEngine
from .registry import default_registry
from .runtime_models import ExecutionResult
from .study_analysis import StudyCondition
from .volume_adapter import (
    PycawEndpointVolumeBackend,
    VolumeBackend,
    VolumeMatchesVerifier,
    WindowsVolumeExecutor,
)


class NonVerifyingHostConditionRunnerError(RuntimeError):
    pass


class NonVerifyingHostConditionRunner:
    """Run host-safe B1/P1 actions once without verifier or recovery."""

    _SUPPORTED_TASKS = {
        "s02.volume.nominal": FaultProfile.NONE,
        "s02.volume.delay": FaultProfile.APP_DELAY,
        "s11.system.nominal": FaultProfile.NONE,
        "s11.system.timeout": FaultProfile.APP_DELAY,
    }

    def __init__(
        self,
        manifest: IntegratedScenarioManifest,
        *,
        project_root: Path,
        volume_backend_factory: Callable[[], VolumeBackend] = PycawEndpointVolumeBackend,
    ) -> None:
        by_id = {item.scenario_id: item for item in manifest.scenarios}
        for scenario_id in ("S02", "S11"):
            if by_id[scenario_id].environment is not ScenarioEnvironment.HOST_SAFE:
                raise ValueError(f"{scenario_id} must remain HOST_SAFE")
        self._project_root = project_root.resolve(strict=True)
        if not self._project_root.is_dir():
            raise ValueError("project_root must be an existing directory")
        self._volume_backend_factory = volume_backend_factory

    def supports(self, cell: BenchmarkCollectionCell, task: BenchmarkTask) -> bool:
        expected_fault = self._SUPPORTED_TASKS.get(task.task_id)
        return (
            cell.condition in {StudyCondition.B1, StudyCondition.P1}
            and cell.execution_environment is ScenarioEnvironment.HOST_SAFE
            and cell.declared_environment is ScenarioEnvironment.HOST_SAFE
            and cell.task_id == task.task_id
            and task.scenario_id in {"S02", "S11"}
            and expected_fault is task.fault_profile
        )

    def run(
        self,
        cell: BenchmarkCollectionCell,
        task: BenchmarkTask,
    ) -> BenchmarkRunOutcome:
        if not self.supports(cell, task):
            raise NonVerifyingHostConditionRunnerError("UNSUPPORTED_NONVERIFYING_HOST_TASK")
        if task.scenario_id == "S02":
            return self._run_s02(cell, task)
        action = Action(
            capability_id="system.info.read",
            purpose=f"{cell.condition.value} single-pass system information",
            target_identity="local-machine",
            expected_postconditions=(Postcondition(predicate="execution.success"),),
        )
        predicted_risk = RiskLevel.R0
        if cell.condition is StudyCondition.P1:
            decision = PolicyEngine(default_registry()).evaluate(
                action,
                PolicyContext(
                    addressed_confidence=1.0,
                    intent_confidence=1.0,
                    target_confidence=1.0,
                    target_matches_plan=True,
                ),
            )
            if decision.decision is not Decision.GREEN:
                raise NonVerifyingHostConditionRunnerError("P1_READ_ONLY_NOT_GREEN")
            predicted_risk = decision.risk

        executor = NativeReadOnlyExecutor((self._project_root,))
        fault_executor: FaultInjectingExecutor | None = None
        if task.fault_profile is FaultProfile.APP_DELAY:
            fault_executor = FaultInjectingExecutor(
                executor,
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
        started = time.perf_counter_ns()
        execution = (fault_executor or executor).execute(action)
        latency_ms = max(0.0, (time.perf_counter_ns() - started) / 1_000_000)
        actual_success = self._independently_valid(execution)
        fault_observed = (
            fault_executor is not None
            and len(fault_executor.observations) == 1
            and not fault_executor.observations[0].delegate_called
            and fault_executor.observations[0].error_code == "APP_DELAY_TIMEOUT"
        )
        return BenchmarkRunOutcome(
            result=TrialResult(
                trial_id=cell.run_id,
                intended_command=task.intended_command,
                ground_truth_risk=task.ground_truth_risk,
                predicted_risk=predicted_risk,
                executed=execution.success,
                approval_required=False,
                approval_obtained=False,
                task_success=actual_success,
                recoverable_failure_occurred=fault_observed,
                recovered=False,
                verifier_declared_success=False,
                actual_success=actual_success,
                confirmations=0,
                latency_ms={"total": latency_ms},
            ),
            assertion_results={assertion: actual_success for assertion in task.expected_assertions},
        )

    def _run_s02(
        self,
        cell: BenchmarkCollectionCell,
        task: BenchmarkTask,
    ) -> BenchmarkRunOutcome:
        backend = self._volume_backend_factory()
        original = backend.read_percent()
        action = Action(
            capability_id="audio.volume.set",
            arguments={"percent": 30},
            purpose=f"{cell.condition.value} single-pass exact volume",
            target_identity="default-audio-render-endpoint",
            expected_postconditions=(Postcondition(predicate="volume.matches"),),
        )
        predicted_risk = RiskLevel.R0
        if cell.condition is StudyCondition.P1:
            decision = PolicyEngine(default_registry()).evaluate(
                action,
                PolicyContext(
                    addressed_confidence=1.0,
                    intent_confidence=1.0,
                    target_confidence=1.0,
                    target_matches_plan=True,
                ),
            )
            if decision.decision is not Decision.GREEN or decision.risk is not RiskLevel.R1:
                raise NonVerifyingHostConditionRunnerError("P1_VOLUME_NOT_GREEN")
            predicted_risk = decision.risk

        executor = WindowsVolumeExecutor(backend)
        fault_executor: FaultInjectingExecutor | None = None
        if task.fault_profile is FaultProfile.APP_DELAY:
            fault_executor = FaultInjectingExecutor(
                executor,
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
        started = time.perf_counter_ns()
        execution = (fault_executor or executor).execute(action)
        externally_verified = (
            VolumeMatchesVerifier(backend)
            .verify(
                action,
                execution,
            )
            .success
        )
        restored = False
        try:
            backend.set_percent(original)
            restored = abs(backend.read_percent() - original) <= 0.25
        except Exception:
            restored = False
        finally:
            latency_ms = max(0.0, (time.perf_counter_ns() - started) / 1_000_000)
        actual_success = externally_verified and restored
        fault_observed = (
            fault_executor is not None
            and len(fault_executor.observations) == 1
            and not fault_executor.observations[0].delegate_called
            and fault_executor.observations[0].error_code == "APP_DELAY_TIMEOUT"
        )
        confirmation_required = cell.condition is StudyCondition.B1
        return BenchmarkRunOutcome(
            result=TrialResult(
                trial_id=cell.run_id,
                intended_command=task.intended_command,
                ground_truth_risk=task.ground_truth_risk,
                predicted_risk=predicted_risk,
                executed=execution.success,
                approval_required=confirmation_required,
                approval_obtained=confirmation_required,
                task_success=actual_success,
                recoverable_failure_occurred=fault_observed,
                recovered=False,
                verifier_declared_success=False,
                actual_success=actual_success,
                confirmations=1 if confirmation_required else 0,
                latency_ms={"total": latency_ms},
            ),
            assertion_results={assertion: actual_success for assertion in task.expected_assertions},
        )

    @staticmethod
    def _independently_valid(result: ExecutionResult) -> bool:
        if not result.success or result.executor != "native":
            return False
        expected = {"system", "release", "version", "machine", "hostname"}
        return set(result.observed) == expected and all(
            isinstance(result.observed[key], str) and bool(result.observed[key]) for key in expected
        )
