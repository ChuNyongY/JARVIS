from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path

from .app_registry import AppRegistryLoader
from .benchmark_collection import BenchmarkCollectionCell, BenchmarkRunOutcome
from .benchmark_tasks import BenchmarkTask, FaultProfile
from .evaluation import TrialResult
from .executor import Executor
from .fault_injection import FaultInjectingExecutor, FaultPhase, FaultSpec
from .integrated_scenarios import IntegratedScenarioManifest, ScenarioEnvironment
from .models import Action, Decision, PolicyContext, Postcondition, RiskLevel
from .policy import PolicyEngine
from .process_launch import ProcessLaunchExecutor, RegisteredApp
from .process_launch_scenario import S01ProcessLaunchScenarioRunner, WindowResolver
from .process_verifier import ProcessExistsPredicate
from .registry import default_registry
from .runtime_models import ExecutionResult
from .study_analysis import StudyCondition
from .verifier import PredicateVerifier, Verifier
from .window_handoff import WindowIdentityPredicate, WindowsRuntimeWindowResolver


class NonVerifyingProcessConditionRunnerError(RuntimeError):
    pass


class NonVerifyingProcessConditionRunner:
    """Run B1/P1 Chrome launch once and evaluate it outside the system path."""

    _SUPPORTED_TASKS = {
        "s01.launch.nominal": FaultProfile.NONE,
        "s01.launch.failure": FaultProfile.APP_LAUNCH_FAILURE,
    }

    def __init__(
        self,
        manifest: IntegratedScenarioManifest,
        *,
        project_root: Path,
        apps_loader: Callable[[], Mapping[str, RegisteredApp]] | None = None,
        executor_factory: Callable[[Mapping[str, RegisteredApp]], Executor] | None = None,
        evaluator_factory: Callable[[Mapping[str, RegisteredApp]], Verifier] | None = None,
        window_resolver: WindowResolver | None = None,
        window_closer: Callable[[RegisteredApp, int, int], bool] | None = None,
    ) -> None:
        scenario = next(item for item in manifest.scenarios if item.scenario_id == "S01")
        if scenario.environment is not ScenarioEnvironment.HOST_SAFE:
            raise ValueError("S01 must remain HOST_SAFE")
        self._project_root = project_root.resolve(strict=True)
        if not self._project_root.is_dir():
            raise ValueError("project_root must be an existing directory")
        self._apps_loader = apps_loader or self._load_apps
        self._executor_factory = executor_factory or ProcessLaunchExecutor
        self._evaluator_factory = evaluator_factory or self._default_evaluator
        self._resolver = window_resolver or WindowsRuntimeWindowResolver()
        self._closer = window_closer or S01ProcessLaunchScenarioRunner._close_exact_window

    def supports(self, cell: BenchmarkCollectionCell, task: BenchmarkTask) -> bool:
        expected_fault = self._SUPPORTED_TASKS.get(task.task_id)
        return (
            cell.condition in {StudyCondition.B1, StudyCondition.P1}
            and cell.execution_environment is ScenarioEnvironment.HOST_SAFE
            and cell.declared_environment is ScenarioEnvironment.HOST_SAFE
            and cell.task_id == task.task_id
            and task.scenario_id == "S01"
            and expected_fault is task.fault_profile
        )

    def run(
        self,
        cell: BenchmarkCollectionCell,
        task: BenchmarkTask,
    ) -> BenchmarkRunOutcome:
        if not self.supports(cell, task):
            raise NonVerifyingProcessConditionRunnerError("UNSUPPORTED_NONVERIFYING_PROCESS_TASK")
        apps = self._apps_loader()
        app = apps.get("chrome")
        if app is None:
            raise NonVerifyingProcessConditionRunnerError("REGISTERED_BROWSER_UNAVAILABLE")
        baseline = self._resolver.snapshot(app)
        action = Action(
            capability_id="process.launch",
            arguments={"app_id": "chrome"},
            purpose=f"{cell.condition.value} single-pass Chrome launch",
            target_identity="chrome",
            expected_postconditions=(
                Postcondition(predicate="process.exists"),
                Postcondition(predicate="window.identity.matches"),
            ),
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
                raise NonVerifyingProcessConditionRunnerError("P1_LAUNCH_NOT_GREEN")
            predicted_risk = decision.risk

        delegate = self._executor_factory(apps)
        fault_executor: FaultInjectingExecutor | None = None
        if task.fault_profile is FaultProfile.APP_LAUNCH_FAILURE:
            fault_executor = FaultInjectingExecutor(
                delegate,
                (
                    FaultSpec(
                        name="app-launch-failure",
                        phase=FaultPhase.BEFORE_CALL,
                        on_call=1,
                        error_code="APP_LAUNCH_FAILURE",
                        retry_safe=False,
                        executor="native",
                    ),
                ),
            )
        started = time.perf_counter_ns()
        execution = (fault_executor or delegate).execute(action)
        external = self._evaluator_factory(apps).verify(action, execution)
        process_matches = external.success
        window_matches = external.success
        cleanup_ok = not execution.success
        if execution.success:
            cleanup_ok = self._close_observed(app, execution)
        latency_ms = max(0.0, (time.perf_counter_ns() - started) / 1_000_000)
        actual_success = process_matches and window_matches and cleanup_ok
        fault_proven = (
            fault_executor is not None
            and len(fault_executor.observations) == 1
            and not fault_executor.observations[0].delegate_called
            and not fault_executor.observations[0].side_effect_possible
            and self._resolver.snapshot(app) == baseline
        )
        if fault_executor is not None and not fault_proven:
            raise NonVerifyingProcessConditionRunnerError("S01_FAILURE_CONTROL_INVALID")
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
                recoverable_failure_occurred=False,
                recovered=False,
                verifier_declared_success=False,
                actual_success=actual_success,
                confirmations=1 if confirmation_required else 0,
                latency_ms={"total": latency_ms},
            ),
            assertion_results={
                assertion: {
                    "registered_browser_process_exists": process_matches,
                    "window_identity_matches": window_matches,
                }.get(assertion, False)
                for assertion in task.expected_assertions
            },
        )

    def _load_apps(self) -> Mapping[str, RegisteredApp]:
        return AppRegistryLoader(require_publisher=True).load(
            (self._project_root / "config" / "apps.json").resolve(strict=True)
        )

    @staticmethod
    def _default_evaluator(apps: Mapping[str, RegisteredApp]) -> Verifier:
        return PredicateVerifier(
            {
                "process.exists": ProcessExistsPredicate(apps),
                "window.identity.matches": WindowIdentityPredicate(apps),
            }
        )

    def _close_observed(self, app: RegisteredApp, result: ExecutionResult) -> bool:
        pid = result.observed.get("pid")
        hwnd = result.observed.get("hwnd")
        if type(pid) is not int or type(hwnd) is not int:
            return False
        if (pid, hwnd) not in self._resolver.matching_windows(app):
            return False
        return self._closer(app, pid, hwnd)
