from pathlib import Path

from javis_core import RegisteredApp
from javis_core.runtime_handoff import WindowsRuntimeProcessResolver


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


class PidSequence:
    def __init__(self, values: list[tuple[int, ...]]) -> None:
        self._values = values
        self.calls = 0

    def __call__(self, _name: str) -> tuple[int, ...]:
        index = min(self.calls, len(self._values) - 1)
        self.calls += 1
        return self._values[index]


def runtime_app(tmp_path: Path) -> tuple[RegisteredApp, Path]:
    launcher = tmp_path / "launcher.exe"
    runtime = tmp_path / "runtime.exe"
    launcher.write_bytes(b"launcher")
    runtime.write_bytes(b"runtime")
    base = RegisteredApp.from_path("app", launcher)
    identity = RegisteredApp.from_path("runtime", runtime)
    return (
        base.model_copy(
            update={
                "runtime_executable": runtime.resolve(),
                "runtime_sha256": identity.sha256,
            }
        ),
        runtime,
    )


def test_resolver_selects_only_new_exact_runtime(tmp_path: Path) -> None:
    app, runtime = runtime_app(tmp_path)
    inspector = Inspector({10: runtime, 20: runtime})
    clock = FakeClock()
    resolver = WindowsRuntimeProcessResolver(
        inspector=inspector,
        pid_source=lambda _name: (10, 20),
        timeout_seconds=0.2,
        poll_seconds=0.1,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert resolver.resolve(app, 99, frozenset({10})) == 20


def test_resolver_rejects_multiple_new_candidates(tmp_path: Path) -> None:
    app, runtime = runtime_app(tmp_path)
    clock = FakeClock()
    resolver = WindowsRuntimeProcessResolver(
        inspector=Inspector({20: runtime, 30: runtime}),
        pid_source=lambda _name: (20, 30),
        timeout_seconds=0.2,
        poll_seconds=0.1,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert resolver.resolve(app, 99, frozenset()) is None


def test_resolver_waits_boundedly_when_no_candidate(tmp_path: Path) -> None:
    app, _runtime = runtime_app(tmp_path)
    clock = FakeClock()
    resolver = WindowsRuntimeProcessResolver(
        inspector=Inspector({}),
        pid_source=lambda _name: (),
        timeout_seconds=0.2,
        poll_seconds=0.1,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert resolver.resolve(app, 99, frozenset()) is None
    assert clock.now == 0.2


def test_resolver_polls_until_late_candidate_is_stable(tmp_path: Path) -> None:
    app, runtime = runtime_app(tmp_path)
    clock = FakeClock()
    pids = PidSequence([(), (20,), (20,)])
    resolver = WindowsRuntimeProcessResolver(
        inspector=Inspector({20: runtime}),
        pid_source=pids,
        timeout_seconds=0.2,
        poll_seconds=0.1,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert resolver.resolve(app, 99, frozenset()) == 20
    assert pids.calls == 3


def test_resolver_rejects_candidate_that_disappears(tmp_path: Path) -> None:
    app, runtime = runtime_app(tmp_path)
    clock = FakeClock()
    resolver = WindowsRuntimeProcessResolver(
        inspector=Inspector({20: runtime}),
        pid_source=PidSequence([(20,), ()]),
        timeout_seconds=0.2,
        poll_seconds=0.1,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert resolver.resolve(app, 99, frozenset()) is None


def test_resolver_rejects_candidate_replacement(tmp_path: Path) -> None:
    app, runtime = runtime_app(tmp_path)
    clock = FakeClock()
    resolver = WindowsRuntimeProcessResolver(
        inspector=Inspector({20: runtime, 30: runtime}),
        pid_source=PidSequence([(20,), (30,)]),
        timeout_seconds=0.2,
        poll_seconds=0.1,
        clock=clock,
        sleeper=clock.sleep,
    )

    assert resolver.resolve(app, 99, frozenset()) is None
