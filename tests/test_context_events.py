from __future__ import annotations

from datetime import UTC, datetime

import pytest

from javis_core import (
    ContextEvent,
    ContextEventError,
    ContextEventType,
    EventDrivenContextManager,
    ForegroundSnapshot,
)


def event(
    sequence: int,
    *,
    observed_at: float | None = None,
    hwnd: int = 10,
    pid: int = 20,
    event_type: ContextEventType = ContextEventType.FOREGROUND_CHANGED,
) -> ContextEvent:
    return ContextEvent(
        sequence=sequence,
        event_type=event_type,
        hwnd=hwnd,
        pid=pid,
        observed_at=float(sequence) if observed_at is None else observed_at,
    )


def snapshot(*, hwnd: int = 10, pid: int = 20, verified: bool = True) -> ForegroundSnapshot:
    return ForegroundSnapshot(
        hwnd=hwnd,
        pid=pid,
        app_id="editor" if verified else None,
        identity_verified=verified,
        captured_at=datetime(2026, 9, 29, tzinfo=UTC),
    )


class Source:
    def __init__(self, events: list[ContextEvent | None], *, fail: bool = False) -> None:
        self.events = iter(events)
        self.fail = fail
        self.closed = False

    def poll(self, *, timeout_seconds: float) -> ContextEvent | None:
        assert 0 <= timeout_seconds <= 5
        if self.fail:
            raise RuntimeError("private event source detail")
        return next(self.events)

    def close(self, *, timeout_seconds: float = 2.0) -> None:
        assert timeout_seconds > 0
        if self.fail:
            raise RuntimeError("private close detail")
        self.closed = True


class Collector:
    def __init__(self, snapshots: list[ForegroundSnapshot], *, fail: bool = False) -> None:
        self.snapshots = iter(snapshots)
        self.fail = fail
        self.calls = 0

    def capture(self) -> ForegroundSnapshot:
        self.calls += 1
        if self.fail:
            raise RuntimeError("private collector detail")
        return next(self.snapshots)


def test_event_triggers_exact_foreground_collection_and_minimal_planner_context() -> None:
    source = Source([event(1)])
    collector = Collector([snapshot()])
    manager = EventDrivenContextManager(source, collector)

    update = manager.next_update(timeout_seconds=0)

    assert update is not None
    assert update.snapshot.identity_verified
    assert update.planner_context() == {"foreground_app_id": "editor"}
    rendered = str(update.planner_context())
    assert "pid" not in rendered
    assert "hwnd" not in rendered
    assert collector.calls == 1


def test_duplicate_event_is_suppressed_without_foreground_poll() -> None:
    source = Source([event(1, observed_at=1.0), event(2, observed_at=1.01)])
    collector = Collector([snapshot()])
    manager = EventDrivenContextManager(source, collector, duplicate_window_seconds=0.05)

    assert manager.next_update(timeout_seconds=0) is not None
    assert manager.next_update(timeout_seconds=0) is None
    assert collector.calls == 1


@pytest.mark.parametrize(
    "events",
    [
        [event(2), event(1, observed_at=3.0)],
        [event(1, observed_at=2.0), event(2, observed_at=1.0)],
    ],
)
def test_event_sequence_or_clock_reversal_fails_closed(events: list[ContextEvent]) -> None:
    manager = EventDrivenContextManager(Source(events), Collector([snapshot(), snapshot()]))
    assert manager.next_update(timeout_seconds=0) is not None
    with pytest.raises(ContextEventError, match="ordering"):
        manager.next_update(timeout_seconds=0)


def test_focus_change_between_event_and_collection_fails_closed() -> None:
    manager = EventDrivenContextManager(Source([event(1)]), Collector([snapshot(hwnd=11)]))

    with pytest.raises(ContextEventError, match="changed"):
        manager.next_update(timeout_seconds=0)


def test_source_and_collector_errors_are_redacted() -> None:
    source_manager = EventDrivenContextManager(Source([], fail=True), Collector([]))
    with pytest.raises(ContextEventError) as source_error:
        source_manager.next_update()
    assert "private" not in str(source_error.value)

    collector_manager = EventDrivenContextManager(
        Source([event(1)]),
        Collector([], fail=True),
    )
    with pytest.raises(ContextEventError) as collector_error:
        collector_manager.next_update()
    assert "private" not in str(collector_error.value)


def test_unverified_update_cannot_enter_planner_context() -> None:
    manager = EventDrivenContextManager(
        Source([event(1)]),
        Collector([snapshot(verified=False)]),
    )
    update = manager.next_update(timeout_seconds=0)

    assert update is not None
    with pytest.raises(ContextEventError, match="unverified"):
        update.planner_context()


def test_close_is_bounded_and_redacts_source_error() -> None:
    source = Source([])
    manager = EventDrivenContextManager(source, Collector([]))
    manager.close(timeout_seconds=0.1)
    assert source.closed

    failing = EventDrivenContextManager(Source([], fail=True), Collector([]))
    with pytest.raises(ContextEventError) as raised:
        failing.close()
    assert "private" not in str(raised.value)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_event_rejects_non_finite_time(value: float) -> None:
    with pytest.raises(ValueError):
        event(1, observed_at=value)
