from __future__ import annotations

import pytest

from javis_core.always_on_runtime import AlwaysOnState, RuntimeStatusSnapshot
from javis_core.windows_tray import (
    TrayCommand,
    WindowsTrayController,
    runtime_status_message,
)


def test_tray_dispatches_only_the_selected_callback() -> None:
    calls: list[str] = []
    tray = WindowsTrayController(
        show_status=lambda: calls.append("show"),
        stop_agent=lambda: calls.append("stop"),
        exit_agent=lambda: calls.append("exit"),
    )

    result = tray.dispatch(TrayCommand.STOP_AGENT)

    assert result.succeeded
    assert result.reason == "TRAY_COMMAND_COMPLETED"
    assert calls == ["stop"]


def test_tray_redacts_callback_exception() -> None:
    def fail() -> None:
        raise RuntimeError("private path and transcript")

    tray = WindowsTrayController(
        show_status=fail,
        stop_agent=lambda: None,
        exit_agent=lambda: None,
    )

    result = tray.dispatch(TrayCommand.SHOW_STATUS)

    assert not result.succeeded
    assert result.reason == "TRAY_CALLBACK_FAILED"
    assert "private" not in result.model_dump_json()


@pytest.mark.parametrize("tooltip", ("", "x" * 64, "bad\0tooltip"))
def test_tray_rejects_invalid_tooltip(tooltip: str) -> None:
    with pytest.raises(ValueError, match="tooltip"):
        WindowsTrayController(
            show_status=lambda: None,
            stop_agent=lambda: None,
            exit_agent=lambda: None,
            tooltip=tooltip,
        )


def test_runtime_status_message_exposes_only_coarse_state() -> None:
    snapshot = RuntimeStatusSnapshot(
        state=AlwaysOnState.EXECUTING,
        last_reason="SENSITIVE_INTERNAL_REASON",
        completed_actions=3,
        sequence=99,
    )

    message = runtime_status_message(snapshot)

    assert message == "JAVIS가 승인된 작업을 실행하고 있습니다.\n완료 동작: 3"
    assert "SENSITIVE" not in message
