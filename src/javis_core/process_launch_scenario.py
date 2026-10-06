from __future__ import annotations

import ctypes
import secrets
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from pydantic import Field, model_validator

from .app_registry import AppRegistryLoader
from .approval import ApprovalTokenService
from .audit import InMemoryAuditSink
from .cancellation import CancellationToken
from .contracts import validate_plan_contracts
from .execution_broker import ExecutionBroker
from .executor import Executor
from .fault_injection import FaultInjectingExecutor, FaultPhase, FaultSpec
from .models import Action, ActionStatus, FrozenModel, Plan, PolicyContext, Postcondition
from .orchestrator import Orchestrator
from .policy import PolicyEngine
from .process_launch import ProcessLaunchExecutor, RegisteredApp
from .process_verifier import ProcessExistsPredicate
from .registry import default_registry
from .runtime_models import ExecutionResult, RetryBudget, TaskOutcome
from .verifier import PredicateVerifier, Verifier
from .window_handoff import WindowIdentityPredicate, WindowsRuntimeWindowResolver


class ProcessLaunchScenarioResult(FrozenModel):
    scenario_id: str = Field(default="S01", pattern=r"^S01$")
    success: bool
    evidence_level: str = Field(
        default="ACTUAL_REVERSIBLE",
        pattern=r"^ACTUAL_REVERSIBLE$",
    )
    verified_assertions: tuple[str, ...] = Field(max_length=2)
    error_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{2,64}$")

    @model_validator(mode="after")
    def consistent_error(self) -> ProcessLaunchScenarioResult:
        if self.success == (self.error_code is not None):
            raise ValueError("process launch scenario result is inconsistent")
        return self


class ProcessLaunchScenarioError(RuntimeError):
    pass


class WindowResolver(Protocol):
    def snapshot(self, app: RegisteredApp) -> frozenset[tuple[int, int]]: ...

    def matching_windows(self, app: RegisteredApp) -> set[tuple[int, int]]: ...


class _RecordingExecutor:
    def __init__(self, delegate: Executor) -> None:
        self._delegate = delegate
        self.result: ExecutionResult | None = None

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        self.result = self._delegate.execute(action, cancellation)
        return self.result


class S01ProcessLaunchScenarioRunner:
    """Launch only registered Chrome and close only its independently verified new window."""

    def __init__(
        self,
        project_root: Path,
        *,
        apps_loader: Callable[[], Mapping[str, RegisteredApp]] | None = None,
        executor_factory: Callable[[Mapping[str, RegisteredApp]], Executor] | None = None,
        verifier_factory: Callable[[Mapping[str, RegisteredApp]], Verifier] | None = None,
        window_resolver: WindowResolver | None = None,
        window_closer: Callable[[RegisteredApp, int, int], bool] | None = None,
    ) -> None:
        if not project_root.is_absolute() or project_root.is_symlink():
            raise ValueError("project root must be an absolute non-symlink directory")
        self._project_root = project_root.resolve(strict=True)
        if not self._project_root.is_dir():
            raise ValueError("project root must be a directory")
        self._apps_loader = apps_loader or self._load_apps
        self._executor_factory = executor_factory or ProcessLaunchExecutor
        self._verifier_factory = verifier_factory or self._default_verifier
        self._resolver = window_resolver or WindowsRuntimeWindowResolver()
        self._closer = window_closer or self._close_exact_window

    def run(self) -> ProcessLaunchScenarioResult:
        apps: Mapping[str, RegisteredApp] = {}
        app: RegisteredApp | None = None
        baseline: frozenset[tuple[int, int]] = frozenset()
        recorder: _RecordingExecutor | None = None
        pipeline_verified = False
        window_closed = False
        error_code = "S01_PROCESS_PIPELINE_FAILED"
        try:
            apps = self._apps_loader()
            app = apps.get("chrome")
            if app is None:
                raise RuntimeError("registered browser unavailable")
            baseline = self._resolver.snapshot(app)
            recorder = _RecordingExecutor(self._executor_factory(apps))
            outcome = self._run_pipeline(apps, recorder)
            pipeline_verified = (
                outcome.status is ActionStatus.SUCCEEDED
                and outcome.completed_actions == 1
                and recorder.result is not None
            )
            if pipeline_verified and recorder.result is not None:
                window_closed = self._close_observed(app, recorder.result)
                if not window_closed:
                    error_code = "S01_WINDOW_CLEANUP_FAILED"
        except Exception:
            error_code = "S01_PROCESS_PIPELINE_FAILED"
        finally:
            if app is not None and not window_closed:
                try:
                    if recorder is not None and recorder.result is not None:
                        window_closed = self._close_observed(app, recorder.result)
                    if not window_closed:
                        candidates = self._resolver.matching_windows(app) - baseline
                        if len(candidates) == 1:
                            pid, hwnd = next(iter(candidates))
                            window_closed = self._closer(app, pid, hwnd)
                except Exception:
                    window_closed = False
                if pipeline_verified and not window_closed:
                    error_code = "S01_WINDOW_CLEANUP_FAILED"

        success = pipeline_verified and window_closed
        return ProcessLaunchScenarioResult(
            success=success,
            verified_assertions=(
                ("registered_browser_process_exists", "window_identity_matches")
                if success
                else ()
            ),
            error_code=None if success else error_code,
        )

    def run_app_launch_failure(self) -> ProcessLaunchScenarioResult:
        try:
            apps = self._apps_loader()
            app = apps.get("chrome")
            if app is None:
                raise RuntimeError("registered browser unavailable")
            baseline = self._resolver.snapshot(app)
            fault_executor = FaultInjectingExecutor(
                self._executor_factory(apps),
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
            outcome = self._run_pipeline(apps, fault_executor)
            observations = fault_executor.observations
            failure_proven = (
                outcome.status is ActionStatus.ABORTED
                and len(observations) == 1
                and observations[0].error_code == "APP_LAUNCH_FAILURE"
                and not observations[0].retry_safe
                and not observations[0].delegate_called
                and not observations[0].side_effect_possible
                and self._resolver.snapshot(app) == baseline
            )
        except Exception:
            failure_proven = False
        if not failure_proven:
            raise ProcessLaunchScenarioError("S01_FAILURE_CONTROL_INVALID")
        return ProcessLaunchScenarioResult(
            success=False,
            verified_assertions=(),
            error_code="APP_LAUNCH_FAILURE",
        )

    def _run_pipeline(
        self,
        apps: Mapping[str, RegisteredApp],
        executor: Executor,
    ) -> TaskOutcome:
        registry = default_registry()
        action = Action(
            capability_id="process.launch",
            arguments={"app_id": "chrome"},
            purpose="S01 registered Chrome process and window benchmark",
            target_identity="chrome",
            expected_postconditions=(
                Postcondition(predicate="process.exists"),
                Postcondition(predicate="window.identity.matches"),
            ),
        )
        plan = Plan(goal="Verify S01 exact Chrome process and new window", steps=(action,))
        validate_plan_contracts(plan, registry)
        return Orchestrator(
            policy=PolicyEngine(registry),
            executor=ExecutionBroker(registry, {"native": executor}),
            verifier=self._verifier_factory(apps),
            audit=InMemoryAuditSink(),
            approvals=ApprovalTokenService(secrets.token_bytes(32)),
            retry_budget=RetryBudget(max_attempts=1, max_replans=0),
        ).run(plan, _trusted_context)

    def _load_apps(self) -> Mapping[str, RegisteredApp]:
        return AppRegistryLoader(require_publisher=True).load(
            (self._project_root / "config" / "apps.json").resolve(strict=True)
        )

    @staticmethod
    def _default_verifier(apps: Mapping[str, RegisteredApp]) -> Verifier:
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

    @staticmethod
    def _close_exact_window(app: RegisteredApp, pid: int, hwnd: int) -> bool:
        resolver = WindowsRuntimeWindowResolver()
        if (pid, hwnd) not in resolver.matching_windows(app):
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.IsWindow.argtypes = [ctypes.c_void_p]
        user32.IsWindow.restype = ctypes.c_bool
        user32.PostMessageW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        user32.PostMessageW.restype = ctypes.c_bool
        if not user32.IsWindow(hwnd) or not user32.PostMessageW(hwnd, 0x0010, 0, 0):
            return False
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if (pid, hwnd) not in resolver.matching_windows(app):
                return True
            time.sleep(0.05)
        return False


def _trusted_context(_action: Action) -> PolicyContext:
    return PolicyContext(
        addressed_confidence=1.0,
        intent_confidence=1.0,
        target_confidence=1.0,
        target_matches_plan=True,
    )
