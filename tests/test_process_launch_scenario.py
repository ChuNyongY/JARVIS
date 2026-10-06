from __future__ import annotations

import hashlib
from pathlib import Path

from javis_core.cancellation import CancellationToken
from javis_core.models import Action, VerificationResult
from javis_core.process_launch import RegisteredApp
from javis_core.process_launch_scenario import S01ProcessLaunchScenarioRunner
from javis_core.runtime_models import ExecutionResult


class _Resolver:
    def __init__(self) -> None:
        self.windows: set[tuple[int, int]] = set()

    def snapshot(self, _app: RegisteredApp) -> frozenset[tuple[int, int]]:
        return frozenset(self.windows)

    def matching_windows(self, _app: RegisteredApp) -> set[tuple[int, int]]:
        return set(self.windows)


class _Executor:
    name = "native"

    def __init__(self, resolver: _Resolver) -> None:
        self.resolver = resolver
        self.calls = 0

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        del cancellation
        self.calls += 1
        self.resolver.windows.add((101, 202))
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed={"app_id": "chrome", "pid": 101, "hwnd": 202},
            duration_ms=1,
        )


class _Verifier:
    def __init__(self, resolver: _Resolver) -> None:
        self.resolver = resolver

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        success = (
            result.action_id == action.action_id
            and result.success
            and result.observed.get("app_id") == "chrome"
            and (result.observed.get("pid"), result.observed.get("hwnd"))
            in self.resolver.windows
        )
        return VerificationResult(
            success=success,
            reason="VERIFIED" if success else "FAILED",
        )


def _app(tmp_path: Path) -> RegisteredApp:
    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"registered fixture")
    return RegisteredApp(
        app_id="chrome",
        executable=executable.resolve(),
        sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        single_instance_handoff=True,
        window_class="Chrome_WidgetWin_1",
    )


def test_nominal_verifies_and_closes_only_observed_window(tmp_path: Path) -> None:
    resolver = _Resolver()
    executor = _Executor(resolver)
    app = _app(tmp_path)
    closed: list[tuple[int, int]] = []

    def close(_app: RegisteredApp, pid: int, hwnd: int) -> bool:
        closed.append((pid, hwnd))
        resolver.windows.remove((pid, hwnd))
        return True

    result = S01ProcessLaunchScenarioRunner(
        tmp_path.resolve(),
        apps_loader=lambda: {"chrome": app},
        executor_factory=lambda _apps: executor,
        verifier_factory=lambda _apps: _Verifier(resolver),
        window_resolver=resolver,
        window_closer=close,
    ).run()

    assert result.success
    assert result.verified_assertions == (
        "registered_browser_process_exists",
        "window_identity_matches",
    )
    assert executor.calls == 1
    assert closed == [(101, 202)]
    assert resolver.windows == set()


def test_launch_failure_is_pre_call_and_has_no_window_effect(tmp_path: Path) -> None:
    resolver = _Resolver()
    executor = _Executor(resolver)
    app = _app(tmp_path)

    result = S01ProcessLaunchScenarioRunner(
        tmp_path.resolve(),
        apps_loader=lambda: {"chrome": app},
        executor_factory=lambda _apps: executor,
        verifier_factory=lambda _apps: _Verifier(resolver),
        window_resolver=resolver,
        window_closer=lambda _app, _pid, _hwnd: False,
    ).run_app_launch_failure()

    assert not result.success
    assert result.error_code == "APP_LAUNCH_FAILURE"
    assert result.verified_assertions == ()
    assert executor.calls == 0
    assert resolver.windows == set()
