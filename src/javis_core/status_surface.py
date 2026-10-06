from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from pydantic import Field

from .always_on_runtime import AlwaysOnState, RuntimeStatusSnapshot
from .models import FrozenModel


class RuntimeStatusDisplay(FrozenModel):
    state: AlwaysOnState
    state_label: str = Field(min_length=1, max_length=32)
    detail_label: str = Field(min_length=1, max_length=80)
    completed_actions: int = Field(ge=0)
    sequence: int = Field(ge=0)
    stop_enabled: bool


class StatusView(Protocol):
    def bind_handlers(
        self,
        *,
        on_stop: Callable[[], None],
        on_hide: Callable[[], None],
    ) -> None: ...

    def render(self, display: RuntimeStatusDisplay) -> None: ...

    def schedule(self, delay_ms: int, callback: Callable[[], None]) -> None: ...

    def close(self) -> None: ...

    def run(self) -> None: ...


_STATE_LABELS = {
    AlwaysOnState.IDLE: "대기 중",
    AlwaysOnState.PLANNING: "계획 중",
    AlwaysOnState.EXECUTING: "실행 중",
    AlwaysOnState.CLOSED: "종료됨",
}

_REASON_LABELS = {
    "RUNTIME_READY": "음성 명령을 기다리고 있습니다.",
    "PLANNING_STARTED": "명령을 안전하게 검토하고 있습니다.",
    "EXECUTION_STARTED": "승인된 작업을 실행하고 있습니다.",
    "LOCAL_STOP_ACCEPTED": "중지 요청을 처리하고 있습니다.",
    "ACTIVE_TASK_ACCEPTS_STOP_ONLY": "현재 작업 중에는 중지 명령만 받습니다.",
    "RUNTIME_CLOSED": "런타임이 종료되었습니다.",
    "RUNTIME_STOP_TIMEOUT": "런타임 종료가 제한 시간을 초과했습니다.",
}


class RuntimeStatusPresenter:
    """Poll a privacy-minimized runtime snapshot on the owning UI thread."""

    def __init__(
        self,
        *,
        status_provider: Callable[[], RuntimeStatusSnapshot],
        request_capture_stop: Callable[[], bool],
        request_runtime_stop: Callable[[], object],
        view: StatusView,
        poll_interval_ms: int = 250,
    ) -> None:
        if type(poll_interval_ms) is not int or not 100 <= poll_interval_ms <= 5_000:
            raise ValueError("poll_interval_ms must be an integer within 100..5000")
        self._status_provider = status_provider
        self._request_capture_stop = request_capture_stop
        self._request_runtime_stop = request_runtime_stop
        self._view = view
        self._poll_interval_ms = poll_interval_ms
        self._active = False

    def run(self) -> None:
        if self._active:
            raise RuntimeError("status presenter is already running")
        self._active = True
        self._view.bind_handlers(on_stop=self.request_stop, on_hide=self.hide)
        self.refresh()
        self._view.run()

    def refresh(self) -> None:
        if not self._active:
            return
        try:
            snapshot = self._status_provider()
            display = self._display(snapshot)
        except Exception:
            display = RuntimeStatusDisplay(
                state=AlwaysOnState.CLOSED,
                state_label="상태 오류",
                detail_label="런타임 상태를 확인할 수 없습니다.",
                completed_actions=0,
                sequence=0,
                stop_enabled=True,
            )
        self._view.render(display)
        if self._active:
            self._view.schedule(self._poll_interval_ms, self.refresh)

    def request_stop(self) -> None:
        if not self._active:
            return
        try:
            self._request_capture_stop()
            self._request_runtime_stop()
        except Exception:
            self._view.render(
                RuntimeStatusDisplay(
                    state=AlwaysOnState.CLOSED,
                    state_label="중지 오류",
                    detail_label="중지 요청을 완료하지 못했습니다.",
                    completed_actions=0,
                    sequence=0,
                    stop_enabled=True,
                )
            )
            return
        self.refresh()

    def hide(self) -> None:
        """Close only the HUD; agent shutdown requires the explicit Stop action."""
        if not self._active:
            return
        self._active = False
        self._view.close()

    @staticmethod
    def _display(snapshot: RuntimeStatusSnapshot) -> RuntimeStatusDisplay:
        return RuntimeStatusDisplay(
            state=snapshot.state,
            state_label=_STATE_LABELS[snapshot.state],
            detail_label=_REASON_LABELS.get(
                snapshot.last_reason,
                "작업 상태가 변경되었습니다.",
            ),
            completed_actions=snapshot.completed_actions,
            sequence=snapshot.sequence,
            stop_enabled=snapshot.state is not AlwaysOnState.CLOSED,
        )


class TkStatusView:
    """Small tkinter HUD. tkinter is imported only when the HUD is constructed."""

    def __init__(self, *, title: str = "JAVIS 상태") -> None:
        try:
            import tkinter as tk
        except ImportError as error:
            raise RuntimeError("tkinter status HUD is unavailable") from error
        try:
            root = tk.Tk()
        except tk.TclError as error:
            raise RuntimeError("tkinter status HUD could not start") from error
        root.title(title)
        root.resizable(False, False)
        root.attributes("-topmost", True)
        frame = tk.Frame(root, padx=16, pady=12)
        frame.pack()
        self._state = tk.StringVar(value="준비 중")
        self._detail = tk.StringVar(value="상태를 불러오고 있습니다.")
        self._actions = tk.StringVar(value="완료 동작: 0")
        tk.Label(frame, textvariable=self._state, font=("Segoe UI", 12, "bold")).pack()
        tk.Label(frame, textvariable=self._detail, font=("Segoe UI", 9)).pack(pady=(4, 0))
        tk.Label(frame, textvariable=self._actions, font=("Segoe UI", 9)).pack(pady=(2, 8))
        buttons = tk.Frame(frame)
        buttons.pack()
        self._stop_button = tk.Button(buttons, text="중지")
        self._stop_button.pack(side="left", padx=4)
        self._hide_button = tk.Button(buttons, text="숨기기")
        self._hide_button.pack(side="left", padx=4)
        self._root = root

    def bind_handlers(
        self,
        *,
        on_stop: Callable[[], None],
        on_hide: Callable[[], None],
    ) -> None:
        self._stop_button.configure(command=on_stop)
        self._hide_button.configure(command=on_hide)
        self._root.protocol("WM_DELETE_WINDOW", on_hide)

    def render(self, display: RuntimeStatusDisplay) -> None:
        self._state.set(display.state_label)
        self._detail.set(display.detail_label)
        self._actions.set(f"완료 동작: {display.completed_actions}")
        self._stop_button.configure(state="normal" if display.stop_enabled else "disabled")

    def schedule(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self._root.after(delay_ms, callback)

    def close(self) -> None:
        self._root.destroy()

    def run(self) -> None:
        self._root.mainloop()


def run_tk_status_hud(
    *,
    status_provider: Callable[[], RuntimeStatusSnapshot],
    request_capture_stop: Callable[[], bool],
    request_runtime_stop: Callable[[], object],
    poll_interval_ms: int = 250,
) -> None:
    RuntimeStatusPresenter(
        status_provider=status_provider,
        request_capture_stop=request_capture_stop,
        request_runtime_stop=request_runtime_stop,
        view=TkStatusView(),
        poll_interval_ms=poll_interval_ms,
    ).run()
