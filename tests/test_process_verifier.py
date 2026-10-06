import os
from pathlib import Path

from javis_core import (
    ExecutionResult,
    Postcondition,
    ProcessExistsPredicate,
    RegisteredApp,
    WindowsProcessInspector,
)


class FakeInspector:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.pids: list[int] = []

    def executable_path(self, pid: int) -> Path | None:
        self.pids.append(pid)
        return self.path


def fixture_app(tmp_path: Path) -> tuple[RegisteredApp, Path]:
    executable = tmp_path / "registered.exe"
    executable.write_bytes(b"registered binary")
    return RegisteredApp.from_path("registered", executable), executable


def result(app_id: str = "registered", pid: int = 123) -> ExecutionResult:
    return ExecutionResult(
        action_id="00000000-0000-0000-0000-000000000001",
        executor="native",
        success=True,
        observed={"app_id": app_id, "pid": pid},
        duration_ms=1,
    )


def test_process_predicate_matches_registered_image(tmp_path: Path) -> None:
    app, executable = fixture_app(tmp_path)
    inspector = FakeInspector(executable)
    predicate = ProcessExistsPredicate({app.app_id: app}, inspector)

    assert predicate(Postcondition(predicate="process.exists"), result())
    assert inspector.pids == [123]


def test_process_predicate_rejects_different_image(tmp_path: Path) -> None:
    app, _executable = fixture_app(tmp_path)
    other = tmp_path / "other.exe"
    other.write_bytes(b"other")
    predicate = ProcessExistsPredicate({app.app_id: app}, FakeInspector(other))

    assert not predicate(Postcondition(predicate="process.exists"), result())


def test_process_predicate_uses_distinct_runtime_identity(tmp_path: Path) -> None:
    app, launcher = fixture_app(tmp_path)
    runtime = tmp_path / "runtime.exe"
    runtime.write_bytes(b"runtime")
    app = app.model_copy(
        update={
            "runtime_executable": runtime.resolve(),
            "runtime_sha256": RegisteredApp.from_path("runtime", runtime).sha256,
        }
    )
    predicate = ProcessExistsPredicate({app.app_id: app}, FakeInspector(runtime))

    assert predicate(Postcondition(predicate="process.exists"), result())
    assert not ProcessExistsPredicate({app.app_id: app}, FakeInspector(launcher))(
        Postcondition(predicate="process.exists"),
        result(),
    )


def test_process_predicate_rejects_unknown_app_without_query(tmp_path: Path) -> None:
    app, executable = fixture_app(tmp_path)
    inspector = FakeInspector(executable)
    predicate = ProcessExistsPredicate({app.app_id: app}, inspector)

    assert not predicate(Postcondition(predicate="process.exists"), result("unknown"))
    assert inspector.pids == []


def test_windows_inspector_can_resolve_current_process() -> None:
    inspector = WindowsProcessInspector()
    path = inspector.executable_path(os.getpid())
    assert path is not None
    assert path.name.lower().startswith("python")
    assert inspector.creation_time_100ns(os.getpid()) is not None
