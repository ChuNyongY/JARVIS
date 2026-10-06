from __future__ import annotations

import ctypes
import json
import secrets
import time
from pathlib import Path

from javis_core import (
    Action,
    ActionStatus,
    AppRegistryLoader,
    ApprovalTokenService,
    ExecutionBroker,
    ExecutionResult,
    InMemoryAuditSink,
    Orchestrator,
    Plan,
    PolicyContext,
    PolicyEngine,
    Postcondition,
    PredicateVerifier,
    ProcessExistsPredicate,
    ProcessLaunchExecutor,
    RegisteredApp,
    RetryBudget,
    WindowIdentityPredicate,
    WindowsRuntimeWindowResolver,
    default_registry,
    validate_plan_contracts,
)
from javis_core.cancellation import CancellationToken


class RecordingExecutor:
    def __init__(self, executor: ProcessLaunchExecutor) -> None:
        self._executor = executor
        self.result: ExecutionResult | None = None

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        self.result = self._executor.execute(action, cancellation)
        return self.result


def _close_exact_window(
    resolver: WindowsRuntimeWindowResolver,
    app: RegisteredApp,
    pid: object,
    hwnd: object,
) -> bool:
    if type(pid) is not int or type(hwnd) is not int:
        return False
    if (pid, hwnd) not in resolver.matching_windows(app):
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.PostMessageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]
    user32.PostMessageW.restype = ctypes.c_bool
    if not user32.PostMessageW(hwnd, 0x0010, 0, 0):
        return False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if (pid, hwnd) not in resolver.matching_windows(app):
            return True
        time.sleep(0.05)
    return False


def main() -> int:
    pipeline_verified = False
    window_closed = False
    error_code: str | None = "S01_CHROME_SMOKE_FAILED"
    outcome_reason: str | None = None
    executor_error_code: str | None = None
    observed_pid_present = False
    observed_hwnd_present = False
    recorder: RecordingExecutor | None = None
    apps: dict[str, RegisteredApp] | None = None
    try:
        apps = AppRegistryLoader(require_publisher=True).load(
            Path("config/apps.json").resolve(strict=True)
        )
        registry = default_registry()
        action = Action(
            capability_id="process.launch",
            arguments={"app_id": "chrome"},
            purpose="S01 registered Chrome process and window smoke",
            target_identity="chrome",
            expected_postconditions=(
                Postcondition(predicate="process.exists"),
                Postcondition(predicate="window.identity.matches"),
            ),
        )
        plan = Plan(goal="Verify S01 exact Chrome process and new window", steps=(action,))
        validate_plan_contracts(plan, registry)
        recorder = RecordingExecutor(ProcessLaunchExecutor(apps))
        outcome = Orchestrator(
            policy=PolicyEngine(registry),
            executor=ExecutionBroker(registry, {"native": recorder}),
            verifier=PredicateVerifier(
                {
                    "process.exists": ProcessExistsPredicate(apps),
                    "window.identity.matches": WindowIdentityPredicate(apps),
                }
            ),
            audit=InMemoryAuditSink(),
            approvals=ApprovalTokenService(secrets.token_bytes(32)),
            retry_budget=RetryBudget(max_attempts=1, max_replans=0),
        ).run(
            plan,
            lambda _action: PolicyContext(
                addressed_confidence=1.0,
                intent_confidence=1.0,
                target_confidence=1.0,
                target_matches_plan=True,
            ),
        )
        pipeline_verified = outcome.status is ActionStatus.SUCCEEDED
        outcome_reason = outcome.reason
        if recorder.result is not None:
            executor_error_code = recorder.result.error_code
            observed_pid_present = type(recorder.result.observed.get("pid")) is int
            observed_hwnd_present = type(recorder.result.observed.get("hwnd")) is int
        error_code = None if pipeline_verified else "S01_PIPELINE_FAILED"
    except Exception:
        error_code = "S01_CHROME_SMOKE_FAILED"
    finally:
        if apps is not None and recorder is not None and recorder.result is not None:
            observed = recorder.result.observed
            try:
                window_closed = _close_exact_window(
                    WindowsRuntimeWindowResolver(),
                    apps["chrome"],
                    observed.get("pid"),
                    observed.get("hwnd"),
                )
            except Exception:
                window_closed = False
        if (
            recorder is not None
            and recorder.result is not None
            and not window_closed
            and recorder.result.observed.get("hwnd") is not None
        ):
            error_code = "S01_WINDOW_CLEANUP_FAILED"

    success = pipeline_verified and window_closed
    print(
        json.dumps(
            {
                "scenario_id": "S01",
                "success": success,
                "evidence_level": "ACTUAL_REVERSIBLE",
                "registered_process_verified": pipeline_verified,
                "new_window_identity_verified": pipeline_verified,
                "new_window_closed": window_closed,
                "outcome_reason": outcome_reason,
                "executor_error_code": executor_error_code,
                "observed_pid_present": observed_pid_present,
                "observed_hwnd_present": observed_hwnd_present,
                "window_content_emitted": False,
                "error_code": None if success else error_code,
            },
            separators=(",", ":"),
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
