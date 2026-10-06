from collections.abc import Iterable
from pathlib import Path

from javis_core import Action, Postcondition, RegisteredApp
from javis_core.runtime_models import ExecutionResult
from javis_core.window_handoff import (
    WindowIdentity,
    WindowIdentityPredicate,
    WindowsRuntimeWindowResolver,
)


class Inspector:
    def __init__(self, paths: dict[int, Path]) -> None:
        self.paths = paths

    def executable_path(self, pid: int) -> Path | None:
        return self.paths.get(pid)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, delay: float) -> None:
        self.now += delay


class WindowSequence:
    def __init__(self, values: list[tuple[WindowIdentity, ...]]) -> None:
        self.values = values
        self.calls = 0

    def __call__(self) -> Iterable[WindowIdentity]:
        index = min(self.calls, len(self.values) - 1)
        self.calls += 1
        return self.values[index]


def app(tmp_path: Path) -> tuple[RegisteredApp, Path]:
    executable = tmp_path / "browser.exe"
    executable.write_bytes(b"browser")
    registration = RegisteredApp.from_path("browser", executable)
    return (
        registration.model_copy(
            update={
                "single_instance_handoff": True,
                "window_class": "BrowserWindow",
            }
        ),
        executable,
    )


def resolver(
    application: RegisteredApp,
    executable: Path,
    source: WindowSequence,
) -> WindowsRuntimeWindowResolver:
    clock = FakeClock()
    return WindowsRuntimeWindowResolver(
        inspector=Inspector({10: executable, 20: executable, 30: executable}),
        source=source,
        timeout_seconds=0.2,
        poll_seconds=0.1,
        clock=clock,
        sleeper=clock.sleep,
    )


def test_resolver_accepts_one_stable_new_exact_window(tmp_path: Path) -> None:
    application, executable = app(tmp_path)
    old = WindowIdentity(100, 10, "BrowserWindow")
    new = WindowIdentity(200, 20, "BrowserWindow")
    source = WindowSequence([(old, new), (old, new), (old, new)])
    subject = resolver(application, executable, source)

    assert subject.resolve(application, 999, frozenset({(10, 100)})) == (20, 200)
    assert source.calls == 3


def test_resolver_rejects_multiple_new_windows(tmp_path: Path) -> None:
    application, executable = app(tmp_path)
    source = WindowSequence(
        [
            (
                WindowIdentity(200, 20, "BrowserWindow"),
                WindowIdentity(300, 30, "BrowserWindow"),
            )
        ]
    )

    assert resolver(application, executable, source).resolve(
        application, 999, frozenset()
    ) is None


def test_resolver_rejects_disappearing_window(tmp_path: Path) -> None:
    application, executable = app(tmp_path)
    source = WindowSequence(
        [(WindowIdentity(200, 20, "BrowserWindow"),), ()]
    )

    assert resolver(application, executable, source).resolve(
        application, 999, frozenset()
    ) is None


def test_wrong_class_or_executable_never_matches(tmp_path: Path) -> None:
    application, executable = app(tmp_path)
    other = tmp_path / "other.exe"
    other.write_bytes(b"other")
    source = WindowSequence(
        [
            (
                WindowIdentity(200, 20, "WrongClass"),
                WindowIdentity(300, 99, "BrowserWindow"),
            )
        ]
    )
    clock = FakeClock()
    subject = WindowsRuntimeWindowResolver(
        inspector=Inspector({20: executable, 99: other}),
        source=source,
        timeout_seconds=0.1,
        poll_seconds=0.1,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert subject.resolve(application, 999, frozenset()) is None


def test_independent_predicate_reenumerates_exact_identity(tmp_path: Path) -> None:
    application, executable = app(tmp_path)
    source = WindowSequence([(WindowIdentity(200, 20, "BrowserWindow"),)])
    predicate = WindowIdentityPredicate(
        {"browser": application},
        inspector=Inspector({20: executable}),
        source=source,
    )
    result = ExecutionResult(
        action_id=Action(capability_id="process.launch", purpose="test").action_id,
        executor="native",
        success=True,
        observed={"app_id": "browser", "pid": 20, "hwnd": 200},
        duration_ms=0,
    )

    assert predicate(Postcondition(predicate="window.identity.matches"), result)
    forged = result.model_copy(update={"observed": {**result.observed, "hwnd": 201}})
    assert not predicate(Postcondition(predicate="window.identity.matches"), forged)
