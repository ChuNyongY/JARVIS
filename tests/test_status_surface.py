from __future__ import annotations

from collections.abc import Callable

import pytest

from javis_core.always_on_runtime import (
    AlwaysOnState,
    RuntimeEventType,
    RuntimeStatusSnapshot,
)
from javis_core.status_surface import RuntimeStatusDisplay, RuntimeStatusPresenter


class FakeView:
    def __init__(self) -> None:
        self.on_stop: Callable[[], None] | None = None
        self.on_hide: Callable[[], None] | None = None
        self.rendered: list[RuntimeStatusDisplay] = []
        self.scheduled: list[tuple[int, Callable[[], None]]] = []
        self.closed = False
        self.ran = False

    def bind_handlers(
        self,
        *,
        on_stop: Callable[[], None],
        on_hide: Callable[[], None],
    ) -> None:
        self.on_stop = on_stop
        self.on_hide = on_hide

    def render(self, display: RuntimeStatusDisplay) -> None:
        self.rendered.append(display)

    def schedule(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self.scheduled.append((delay_ms, callback))

    def close(self) -> None:
        self.closed = True

    def run(self) -> None:
        self.ran = True


def snapshot(
    state: AlwaysOnState = AlwaysOnState.IDLE,
    *,
    reason: str = "RUNTIME_READY",
) -> RuntimeStatusSnapshot:
    return RuntimeStatusSnapshot(
        state=state,
        last_event_type=RuntimeEventType.ROUTED,
        last_reason=reason,
        sequence=3,
    )


def test_presenter_polls_coarse_status_and_hide_does_not_stop_agent() -> None:
    view = FakeView()
    calls: list[str] = []

    def stop_capture() -> bool:
        calls.append("capture")
        return True

    presenter = RuntimeStatusPresenter(
        status_provider=snapshot,
        request_capture_stop=stop_capture,
        request_runtime_stop=lambda: calls.append("runtime"),
        view=view,
    )

    presenter.run()
    assert view.ran
    assert view.rendered[-1].state_label == "대기 중"
    assert view.scheduled[0][0] == 250

    assert view.on_hide is not None
    view.on_hide()
    assert view.closed
    assert calls == []


def test_explicit_stop_requests_capture_then_runtime_shutdown() -> None:
    view = FakeView()
    calls: list[str] = []

    def stop_capture() -> bool:
        calls.append("capture")
        return True

    presenter = RuntimeStatusPresenter(
        status_provider=lambda: snapshot(AlwaysOnState.CLOSED, reason="RUNTIME_CLOSED"),
        request_capture_stop=stop_capture,
        request_runtime_stop=lambda: calls.append("runtime"),
        view=view,
    )
    presenter.run()

    assert view.on_stop is not None
    view.on_stop()

    assert calls == ["capture", "runtime"]
    assert view.rendered[-1].stop_enabled is False


def test_unknown_reason_and_provider_failure_do_not_leak_details() -> None:
    view = FakeView()
    presenter = RuntimeStatusPresenter(
        status_provider=lambda: snapshot(reason="COMMAND_DECISION_INVALID"),
        request_capture_stop=lambda: False,
        request_runtime_stop=lambda: None,
        view=view,
    )
    presenter.run()
    assert view.rendered[-1].detail_label == "작업 상태가 변경되었습니다."
    assert "COMMAND" not in view.rendered[-1].detail_label

    failing_view = FakeView()

    def fail() -> RuntimeStatusSnapshot:
        raise RuntimeError("secret transcript and C:\\private\\file.txt")

    RuntimeStatusPresenter(
        status_provider=fail,
        request_capture_stop=lambda: False,
        request_runtime_stop=lambda: None,
        view=failing_view,
    ).run()
    rendered = failing_view.rendered[-1].model_dump_json()
    assert "secret" not in rendered
    assert "private" not in rendered


def test_presenter_rejects_invalid_poll_interval() -> None:
    for invalid in (99, 5_001, True):
        with pytest.raises(ValueError, match="poll_interval_ms"):
            RuntimeStatusPresenter(
                status_provider=snapshot,
                request_capture_stop=lambda: False,
                request_runtime_stop=lambda: None,
                view=FakeView(),
                poll_interval_ms=invalid,
            )
