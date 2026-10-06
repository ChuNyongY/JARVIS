from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import suppress
from enum import IntEnum
from threading import Lock, get_ident

from pydantic import Field

from .always_on_runtime import AlwaysOnState, RuntimeStatusSnapshot
from .models import FrozenModel


class TrayError(RuntimeError):
    pass


class TrayCommand(IntEnum):
    SHOW_STATUS = 1001
    STOP_AGENT = 1002
    EXIT = 1003


class TrayDispatchResult(FrozenModel):
    command: TrayCommand
    succeeded: bool
    reason: str = Field(pattern=r"^[A-Z0-9_]{2,64}$")


TrayCallback = Callable[[], object]


class WindowsTrayController:
    """Own a native notification icon and expose only explicit local controls."""

    def __init__(
        self,
        *,
        show_status: TrayCallback,
        stop_agent: TrayCallback,
        exit_agent: TrayCallback,
        tooltip: str = "JAVIS PC Agent",
    ) -> None:
        if not tooltip or len(tooltip) > 63 or "\0" in tooltip:
            raise ValueError("tooltip must contain 1..63 non-NUL characters")
        self._callbacks = {
            TrayCommand.SHOW_STATUS: show_status,
            TrayCommand.STOP_AGENT: stop_agent,
            TrayCommand.EXIT: exit_agent,
        }
        if any(not callable(callback) for callback in self._callbacks.values()):
            raise TypeError("tray callbacks must be callable")
        self._tooltip = tooltip
        self._lock = Lock()
        self._running = False
        self._owner_thread_id: int | None = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def dispatch(self, command: TrayCommand) -> TrayDispatchResult:
        if not isinstance(command, TrayCommand):
            raise TypeError("command must be a TrayCommand")
        try:
            self._callbacks[command]()
        except Exception:
            return TrayDispatchResult(
                command=command,
                succeeded=False,
                reason="TRAY_CALLBACK_FAILED",
            )
        return TrayDispatchResult(
            command=command,
            succeeded=True,
            reason="TRAY_COMMAND_COMPLETED",
        )

    def run(self) -> None:
        if os.name != "nt":
            raise TrayError("TRAY_WINDOWS_REQUIRED")
        with self._lock:
            if self._running:
                raise TrayError("TRAY_ALREADY_RUNNING")
            self._running = True
            self._owner_thread_id = get_ident()
        try:
            self._run_win32()
        finally:
            with self._lock:
                self._running = False
                self._owner_thread_id = None

    def _run_win32(self) -> None:
        try:
            import win32api  # type: ignore[import-untyped]
            import win32con  # type: ignore[import-untyped]
            import win32gui  # type: ignore[import-untyped]
        except ImportError as error:
            raise TrayError("TRAY_WIN32_UNAVAILABLE") from error

        callback_message = win32con.WM_USER + 20
        class_name = f"JavisPcAgentTray_{os.getpid()}_{get_ident()}"
        window_handle: int | None = None
        icon_registered = False

        def dispatch_safely(command: TrayCommand) -> None:
            with suppress(Exception):
                self.dispatch(command)

        def window_proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
            try:
                if message == callback_message:
                    if lparam == win32con.WM_LBUTTONUP:
                        dispatch_safely(TrayCommand.SHOW_STATUS)
                    elif lparam == win32con.WM_RBUTTONUP:
                        menu = win32gui.CreatePopupMenu()
                        try:
                            win32gui.AppendMenu(
                                menu,
                                win32con.MF_STRING,
                                TrayCommand.SHOW_STATUS.value,
                                "상태 보기",
                            )
                            win32gui.AppendMenu(
                                menu,
                                win32con.MF_STRING,
                                TrayCommand.STOP_AGENT.value,
                                "에이전트 중지",
                            )
                            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, None)
                            win32gui.AppendMenu(
                                menu,
                                win32con.MF_STRING,
                                TrayCommand.EXIT.value,
                                "종료",
                            )
                            x, y = win32gui.GetCursorPos()
                            win32gui.SetForegroundWindow(hwnd)
                            win32gui.TrackPopupMenu(
                                menu,
                                win32con.TPM_LEFTALIGN,
                                x,
                                y,
                                0,
                                hwnd,
                                None,
                            )
                            win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
                        finally:
                            win32gui.DestroyMenu(menu)
                    return 0
                if message == win32con.WM_COMMAND:
                    command_id = wparam & 0xFFFF
                    try:
                        command = TrayCommand(command_id)
                    except ValueError:
                        return 0
                    dispatch_safely(command)
                    if command is TrayCommand.EXIT:
                        win32gui.DestroyWindow(hwnd)
                    return 0
                if message == win32con.WM_DESTROY:
                    with suppress(Exception):
                        win32gui.Shell_NotifyIcon(
                            win32gui.NIM_DELETE,
                            (hwnd, 0),
                        )
                    win32gui.PostQuitMessage(0)
                    return 0
                return int(win32gui.DefWindowProc(hwnd, message, wparam, lparam))
            except Exception:
                return 0

        window_class = win32gui.WNDCLASS()
        window_class.hInstance = win32api.GetModuleHandle(None)
        window_class.lpszClassName = class_name
        window_class.lpfnWndProc = window_proc
        window_class.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        try:
            win32gui.RegisterClass(window_class)
            window_handle = win32gui.CreateWindow(
                class_name,
                class_name,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                window_class.hInstance,
                None,
            )
            icon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
            notification_data = (
                window_handle,
                0,
                win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
                callback_message,
                icon,
                self._tooltip,
            )
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, notification_data)
            icon_registered = True
            win32gui.PumpMessages()
        except Exception as error:
            raise TrayError("TRAY_RUNTIME_FAILED") from error
        finally:
            if icon_registered and window_handle is not None:
                with suppress(Exception):
                    win32gui.Shell_NotifyIcon(
                        win32gui.NIM_DELETE,
                        (window_handle, 0),
                    )
            if window_handle is not None:
                with suppress(Exception):
                    if win32gui.IsWindow(window_handle):
                        win32gui.DestroyWindow(window_handle)
            with suppress(Exception):
                win32gui.UnregisterClass(class_name, window_class.hInstance)


_STATE_MESSAGES = {
    AlwaysOnState.IDLE: "JAVIS가 음성 명령을 기다리고 있습니다.",
    AlwaysOnState.PLANNING: "JAVIS가 명령을 안전하게 검토하고 있습니다.",
    AlwaysOnState.EXECUTING: "JAVIS가 승인된 작업을 실행하고 있습니다.",
    AlwaysOnState.CLOSED: "JAVIS 런타임이 종료되었습니다.",
}


def runtime_status_message(snapshot: RuntimeStatusSnapshot) -> str:
    """Render an allowlisted status without transcript, target, path, or raw reason."""
    message = _STATE_MESSAGES[snapshot.state]
    return f"{message}\n완료 동작: {snapshot.completed_actions}"


def show_runtime_status_message(snapshot: RuntimeStatusSnapshot) -> None:
    try:
        import win32con
        import win32gui

        win32gui.MessageBox(
            0,
            runtime_status_message(snapshot),
            "JAVIS 상태",
            win32con.MB_OK | win32con.MB_ICONINFORMATION,
        )
    except Exception as error:
        raise TrayError("TRAY_STATUS_DISPLAY_FAILED") from error
