from datetime import UTC, datetime
from pathlib import Path

import pytest

from javis_core import RegisteredApp
from javis_core.foreground_context import (
    ForegroundContextCollector,
    ForegroundContextError,
    ForegroundSnapshot,
    StableForegroundContextCollector,
)


class Backend:
    def __init__(self, result: tuple[int, int] = (100, 200), *, fail: bool = False) -> None:
        self.result = result
        self.fail = fail

    def foreground(self) -> tuple[int, int]:
        if self.fail:
            raise OSError("private error")
        return self.result


class Inspector:
    def __init__(self, path: Path | None) -> None:
        self.path = path

    def executable_path(self, pid: int) -> Path | None:
        assert pid == 200
        return self.path


def app(tmp_path: Path, app_id: str = "editor") -> RegisteredApp:
    executable = tmp_path / "editor.exe"
    executable.write_bytes(b"editor")
    return RegisteredApp.from_path(app_id, executable)


def test_exact_registered_foreground_identity_is_verified(tmp_path: Path) -> None:
    registered = app(tmp_path)
    snapshot = ForegroundContextCollector(
        {registered.app_id: registered},
        backend=Backend(),
        inspector=Inspector(registered.executable),
        clock=lambda: datetime(2026, 9, 22, tzinfo=UTC),
    ).capture()
    assert snapshot.app_id == "editor"
    assert snapshot.identity_verified


def test_unregistered_foreground_is_structural_only(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.exe"
    unknown.write_bytes(b"unknown")
    snapshot = ForegroundContextCollector(
        {}, backend=Backend(), inspector=Inspector(unknown.resolve())
    ).capture()
    assert snapshot.app_id is None
    assert not snapshot.identity_verified


def test_ambiguous_registration_fails_closed(tmp_path: Path) -> None:
    first = app(tmp_path, "first")
    second = first.model_copy(update={"app_id": "second"})
    collector = ForegroundContextCollector(
        {"first": first, "second": second},
        backend=Backend(),
        inspector=Inspector(first.executable),
    )
    with pytest.raises(ForegroundContextError, match="ambiguous"):
        collector.capture()


@pytest.mark.parametrize("backend", [Backend((0, 1)), Backend(fail=True)])
def test_api_failure_or_zero_handle_fails_closed(backend: Backend) -> None:
    with pytest.raises(ForegroundContextError):
        ForegroundContextCollector({}, backend=backend, inspector=Inspector(None)).capture()


def test_naive_clock_is_rejected() -> None:
    collector = ForegroundContextCollector(
        {},
        backend=Backend(),
        inspector=Inspector(None),
        clock=lambda: datetime(2026, 9, 22),
    )
    with pytest.raises(ForegroundContextError, match="UTC"):
        collector.capture()


class SequenceCollector:
    def __init__(self, snapshots: list[ForegroundSnapshot], *, fail: bool = False) -> None:
        self.snapshots = iter(snapshots)
        self.fail = fail

    def capture(self) -> ForegroundSnapshot:
        if self.fail:
            raise OSError("private internal detail")
        return next(self.snapshots)


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def snapshot(*, hwnd: int = 10, verified: bool = True) -> ForegroundSnapshot:
    return ForegroundSnapshot(
        hwnd=hwnd,
        pid=20,
        app_id="editor" if verified else None,
        identity_verified=verified,
        captured_at=datetime(2026, 9, 22, tzinfo=UTC),
    )


def test_stable_context_returns_latest_identical_verified_sample() -> None:
    samples = [snapshot(), snapshot(), snapshot()]
    fake_time = FakeTime()
    result = StableForegroundContextCollector(
        SequenceCollector(samples),
        monotonic=fake_time.monotonic,
        sleeper=fake_time.sleep,
    ).capture()
    assert result is samples[-1]
    assert fake_time.value == pytest.approx(0.1)


def test_stable_context_rejects_focus_change() -> None:
    fake_time = FakeTime()
    collector = StableForegroundContextCollector(
        SequenceCollector([snapshot(), snapshot(hwnd=11)]),
        sample_count=2,
        monotonic=fake_time.monotonic,
        sleeper=fake_time.sleep,
    )
    with pytest.raises(ForegroundContextError, match="changed"):
        collector.capture()


def test_stable_context_requires_verified_identity_by_default() -> None:
    collector = StableForegroundContextCollector(
        SequenceCollector([snapshot(verified=False)] * 2), sample_count=2
    )
    with pytest.raises(ForegroundContextError, match="not verified"):
        collector.capture()


def test_stable_context_can_observe_stable_unregistered_identity() -> None:
    samples = [snapshot(verified=False)] * 2
    result = StableForegroundContextCollector(
        SequenceCollector(samples),
        sample_count=2,
        interval_seconds=0,
        require_verified=False,
    ).capture()
    assert result.app_id is None


def test_planner_context_contains_only_verified_app_id() -> None:
    samples = [snapshot(), snapshot()]
    context = StableForegroundContextCollector(
        SequenceCollector(samples), sample_count=2, interval_seconds=0
    ).capture_planner_context()
    assert context == {"foreground_app_id": "editor"}
    rendered = str(context)
    assert "hwnd" not in rendered
    assert "pid" not in rendered
    assert "captured_at" not in rendered


def test_unregistered_identity_cannot_enter_planner_context() -> None:
    collector = StableForegroundContextCollector(
        SequenceCollector([snapshot(verified=False)] * 2),
        sample_count=2,
        interval_seconds=0,
        require_verified=False,
    )
    with pytest.raises(ForegroundContextError, match="cannot enter planner"):
        collector.capture_planner_context()


def test_stable_context_redacts_collector_error() -> None:
    collector = StableForegroundContextCollector(
        SequenceCollector([], fail=True), sample_count=2
    )
    with pytest.raises(ForegroundContextError) as raised:
        collector.capture()
    assert "private internal detail" not in str(raised.value)


def test_stable_context_rejects_timeout_and_clock_reversal() -> None:
    timeout_clock = iter((0.0, 0.0, 1.0))
    timed = StableForegroundContextCollector(
        SequenceCollector([snapshot(), snapshot()]),
        sample_count=2,
        interval_seconds=0,
        timeout_seconds=0.1,
        monotonic=lambda: next(timeout_clock),
        sleeper=lambda _: None,
    )
    with pytest.raises(ForegroundContextError, match="timed out"):
        timed.capture()

    reverse_clock = iter((1.0, 0.0))
    reversed_collector = StableForegroundContextCollector(
        SequenceCollector([snapshot(), snapshot()]),
        sample_count=2,
        interval_seconds=0,
        monotonic=lambda: next(reverse_clock),
        sleeper=lambda _: None,
    )
    with pytest.raises(ForegroundContextError, match="clock is invalid"):
        reversed_collector.capture()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sample_count": 1}, "sample_count"),
        ({"sample_count": True}, "sample_count"),
        ({"interval_seconds": float("nan")}, "interval_seconds"),
        ({"interval_seconds": 0.6}, "interval_seconds"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        (
            {"sample_count": 10, "interval_seconds": 0.5, "timeout_seconds": 1},
            "timeout_seconds",
        ),
    ],
)
def test_stable_context_rejects_unbounded_configuration(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        StableForegroundContextCollector(SequenceCollector([]), **kwargs)  # type: ignore[arg-type]
