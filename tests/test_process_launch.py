from collections.abc import Mapping
from pathlib import Path

from pytest import MonkeyPatch

from javis_core import (
    Action,
    CancellationToken,
    CapabilityExecutorRouter,
    ProcessLaunchExecutor,
    RegisteredApp,
)


class FakeLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str | None, Path, dict[str, str]]] = []

    def launch(
        self,
        executable: Path,
        *,
        fixed_argument: str | None,
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> int:
        self.calls.append((executable, fixed_argument, working_directory, dict(environment)))
        return 4242


class FakeRuntimeResolver:
    def __init__(self, runtime_pid: int | None) -> None:
        self.runtime_pid = runtime_pid
        self.snapshots: list[str] = []
        self.resolutions: list[tuple[int, frozenset[int]]] = []

    def snapshot(self, app: RegisteredApp) -> frozenset[int]:
        self.snapshots.append(app.app_id)
        return frozenset({7})

    def resolve(
        self,
        app: RegisteredApp,
        launcher_pid: int,
        previous_pids: frozenset[int],
    ) -> int | None:
        self.resolutions.append((launcher_pid, previous_pids))
        return self.runtime_pid


class FakeWindowResolver:
    def __init__(self, identity: tuple[int, int] | None) -> None:
        self.identity = identity
        self.snapshots: list[str] = []
        self.resolutions: list[tuple[int, frozenset[tuple[int, int]]]] = []

    def snapshot(self, app: RegisteredApp) -> frozenset[tuple[int, int]]:
        self.snapshots.append(app.app_id)
        return frozenset({(7, 70)})

    def resolve(
        self,
        app: RegisteredApp,
        launcher_pid: int,
        previous_windows: frozenset[tuple[int, int]],
    ) -> tuple[int, int] | None:
        self.resolutions.append((launcher_pid, previous_windows))
        return self.identity


def registered_executor(tmp_path: Path) -> tuple[ProcessLaunchExecutor, FakeLauncher, Path]:
    executable = tmp_path / "safe-app.exe"
    executable.write_bytes(b"test executable fixture")
    app = RegisteredApp.from_path("safe-app", executable)
    launcher = FakeLauncher()
    return ProcessLaunchExecutor({"safe-app": app}, launcher=launcher), launcher, executable


def action(**arguments: object) -> Action:
    return Action(
        capability_id="process.launch",
        arguments=arguments or {"app_id": "safe-app"},
        purpose="launch registered app",
        target_identity="safe-app",
    )


def test_registered_app_launches_without_dynamic_arguments(tmp_path: Path) -> None:
    executor, launcher, executable = registered_executor(tmp_path)

    result = executor.execute(action())

    assert result.success
    assert result.observed == {"app_id": "safe-app", "pid": 4242}
    assert launcher.calls[0][0] == executable.resolve()
    assert launcher.calls[0][1] is None


def test_registered_fixed_new_window_argument_is_not_action_controlled(tmp_path: Path) -> None:
    executor, launcher, _executable = registered_executor(tmp_path)
    application = executor._apps["safe-app"].model_copy(  # noqa: SLF001 - contract fixture
        update={
            "single_instance_handoff": True,
            "window_class": "FixtureWindow",
            "launch_argument": "--new-window",
        }
    )
    result = ProcessLaunchExecutor(
        {"safe-app": application},
        launcher=launcher,
        window_resolver=FakeWindowResolver((9002, 9010)),
    ).execute(action())

    assert result.success
    assert launcher.calls[0][1] == "--new-window"


def test_additional_arguments_are_rejected(tmp_path: Path) -> None:
    executor, launcher, _executable = registered_executor(tmp_path)

    result = executor.execute(action(app_id="safe-app", args=["--unsafe"]))

    assert not result.success
    assert result.error_code == "INVALID_ARGUMENTS"
    assert launcher.calls == []


def test_target_identity_must_match_app_id(tmp_path: Path) -> None:
    executor, launcher, _executable = registered_executor(tmp_path)
    mismatched = action().model_copy(update={"target_identity": "other-app"})

    result = executor.execute(mismatched)

    assert not result.success
    assert result.error_code == "TARGET_IDENTITY_MISMATCH"
    assert launcher.calls == []


def test_direct_launch_can_be_disabled_for_uri_activated_apps(tmp_path: Path) -> None:
    executor, launcher, _executable = registered_executor(tmp_path)
    blocked = executor._apps["safe-app"].model_copy(  # noqa: SLF001 - contract fixture
        update={"direct_launch": False}
    )
    result = ProcessLaunchExecutor(
        {"safe-app": blocked}, launcher=launcher
    ).execute(action())
    assert not result.success
    assert result.error_code == "DIRECT_LAUNCH_NOT_ALLOWED"
    assert launcher.calls == []


def test_changed_executable_is_rejected(tmp_path: Path) -> None:
    executor, launcher, executable = registered_executor(tmp_path)
    executable.write_bytes(b"substituted")

    result = executor.execute(action())

    assert not result.success
    assert result.error_code == "EXECUTABLE_CHANGED"
    assert launcher.calls == []


def test_cancel_before_launch_is_respected(tmp_path: Path) -> None:
    executor, launcher, _executable = registered_executor(tmp_path)
    cancellation = CancellationToken()
    cancellation.cancel()

    result = executor.execute(action(), cancellation)

    assert not result.success
    assert result.error_code == "STOP_REQUESTED"
    assert launcher.calls == []


def test_secret_environment_is_not_inherited(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    executor, launcher, _executable = registered_executor(tmp_path)
    monkeypatch.setenv("NVIDIA_API_KEY", "secret")

    result = executor.execute(action())

    assert result.success
    assert "NVIDIA_API_KEY" not in launcher.calls[0][3]


def test_distinct_runtime_handoff_replaces_launcher_pid(tmp_path: Path) -> None:
    executor, launcher, executable = registered_executor(tmp_path)
    runtime = tmp_path / "runtime.exe"
    runtime.write_bytes(b"runtime")
    app = executor._apps["safe-app"].model_copy(  # noqa: SLF001 - contract fixture
        update={
            "runtime_executable": runtime.resolve(),
            "runtime_sha256": RegisteredApp.from_path("runtime", runtime).sha256,
        }
    )
    resolver = FakeRuntimeResolver(9001)
    executor = ProcessLaunchExecutor(
        {"safe-app": app},
        launcher=launcher,
        runtime_resolver=resolver,
    )

    result = executor.execute(action())

    assert result.success
    assert result.observed["pid"] == 9001
    assert resolver.resolutions == [(4242, frozenset({7}))]
    assert executable.exists()


def test_single_instance_app_requires_one_new_exact_pid(tmp_path: Path) -> None:
    executor, launcher, _executable = registered_executor(tmp_path)
    app = executor._apps["safe-app"].model_copy(  # noqa: SLF001 - contract fixture
        update={
            "single_instance_handoff": True,
            "window_class": "FixtureWindow",
        }
    )
    resolver = FakeWindowResolver((9002, 9010))
    executor = ProcessLaunchExecutor(
        {"safe-app": app},
        launcher=launcher,
        window_resolver=resolver,
    )

    result = executor.execute(action())

    assert result.success
    assert result.observed == {"app_id": "safe-app", "pid": 9002, "hwnd": 9010}
    assert resolver.snapshots == ["safe-app"]
    assert resolver.resolutions == [(4242, frozenset({(7, 70)}))]


def test_single_instance_app_never_accepts_preexisting_pid(tmp_path: Path) -> None:
    executor, launcher, _executable = registered_executor(tmp_path)
    app = executor._apps["safe-app"].model_copy(  # noqa: SLF001 - contract fixture
        update={
            "single_instance_handoff": True,
            "window_class": "FixtureWindow",
        }
    )
    resolver = FakeWindowResolver(None)
    result = ProcessLaunchExecutor(
        {"safe-app": app},
        launcher=launcher,
        window_resolver=resolver,
    ).execute(action())

    assert not result.success
    assert result.error_code == "WINDOW_HANDOFF_FAILED"
    assert resolver.resolutions == [(4242, frozenset({(7, 70)}))]


def test_ambiguous_runtime_handoff_fails_closed(tmp_path: Path) -> None:
    executor, launcher, _executable = registered_executor(tmp_path)
    runtime = tmp_path / "runtime.exe"
    runtime.write_bytes(b"runtime")
    app = executor._apps["safe-app"].model_copy(  # noqa: SLF001 - contract fixture
        update={
            "runtime_executable": runtime.resolve(),
            "runtime_sha256": RegisteredApp.from_path("runtime", runtime).sha256,
        }
    )
    executor = ProcessLaunchExecutor(
        {"safe-app": app},
        launcher=launcher,
        runtime_resolver=FakeRuntimeResolver(None),
    )

    result = executor.execute(action())

    assert not result.success
    assert result.error_code == "RUNTIME_HANDOFF_FAILED"
    assert not result.retry_safe


def test_capability_router_fails_closed_for_unregistered_route(tmp_path: Path) -> None:
    executor, _launcher, _executable = registered_executor(tmp_path)
    router = CapabilityExecutorRouter("native", {"process.launch": executor})
    unsupported = Action(capability_id="file.list", purpose="not routed")

    result = router.execute(unsupported)

    assert not result.success
    assert result.error_code == "UNSUPPORTED_CAPABILITY"
