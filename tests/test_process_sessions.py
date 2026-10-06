from pathlib import Path
from uuid import uuid4

from javis_core import AuthorizedProcessRegistry, ExecutionResult, RegisteredApp


class Inspector:
    def __init__(self, paths: dict[int, Path]) -> None:
        self.paths = paths
        self.creation_times = {pid: 100 for pid in paths}

    def executable_path(self, pid: int) -> Path | None:
        return self.paths.get(pid)

    def creation_time_100ns(self, pid: int) -> int | None:
        return self.creation_times.get(pid)


def successful(pid: int = 4242, app_id: str = "safe-app") -> ExecutionResult:
    return ExecutionResult(
        action_id=uuid4(),
        executor="native",
        success=True,
        observed={"pid": pid, "app_id": app_id},
        duration_ms=0,
    )


def setup_registry(tmp_path: Path) -> tuple[AuthorizedProcessRegistry, Inspector, Path]:
    executable = tmp_path / "safe.exe"
    executable.write_bytes(b"fixture")
    app = RegisteredApp.from_path("safe-app", executable)
    inspector = Inspector({4242: executable.resolve()})
    return AuthorizedProcessRegistry({"safe-app": app}, inspector), inspector, executable


def test_verified_launch_authorizes_and_revalidates_process(tmp_path: Path) -> None:
    registry, _inspector, _executable = setup_registry(tmp_path)
    assert registry.authorize_launch(successful())
    assert registry.is_authorized(4242)
    assert registry.is_authorized_app(4242, "safe-app")
    assert not registry.is_authorized_app(4242, "other")
    assert registry.sessions[0].app_id == "safe-app"


def test_unknown_or_mismatched_launch_is_not_authorized(tmp_path: Path) -> None:
    registry, inspector, executable = setup_registry(tmp_path)
    assert not registry.authorize_launch(successful(app_id="unknown"))
    inspector.paths[4242] = executable.with_name("other.exe")
    assert not registry.authorize_launch(successful())


def test_changed_executable_revokes_existing_session(tmp_path: Path) -> None:
    registry, _inspector, executable = setup_registry(tmp_path)
    assert registry.authorize_launch(successful())
    executable.write_bytes(b"changed")

    assert not registry.is_authorized(4242)
    assert registry.sessions == ()


def test_pid_reuse_revokes_existing_session(tmp_path: Path) -> None:
    registry, inspector, _executable = setup_registry(tmp_path)
    assert registry.authorize_launch(successful())
    inspector.creation_times[4242] = 200

    assert not registry.is_authorized(4242)
    assert registry.sessions == ()


def test_revoke_and_clear_remove_authority(tmp_path: Path) -> None:
    registry, _inspector, _executable = setup_registry(tmp_path)
    assert registry.authorize_launch(successful())
    registry.revoke(4242)
    assert not registry.is_authorized(4242)

    assert registry.authorize_launch(successful())
    registry.clear()
    assert registry.sessions == ()


def test_session_binds_distinct_runtime_not_launcher(tmp_path: Path) -> None:
    launcher = tmp_path / "launcher.exe"
    runtime = tmp_path / "runtime.exe"
    launcher.write_bytes(b"launcher")
    runtime.write_bytes(b"runtime")
    base = RegisteredApp.from_path("safe-app", launcher)
    runtime_identity = RegisteredApp.from_path("runtime", runtime)
    app = base.model_copy(
        update={
            "runtime_executable": runtime.resolve(),
            "runtime_sha256": runtime_identity.sha256,
        }
    )
    inspector = Inspector({4242: runtime.resolve()})
    registry = AuthorizedProcessRegistry({"safe-app": app}, inspector)

    assert registry.authorize_launch(successful())
    assert registry.sessions[0].executable == runtime.resolve()
    inspector.paths[4242] = launcher.resolve()
    assert not registry.is_authorized(4242)
