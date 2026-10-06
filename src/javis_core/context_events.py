from __future__ import annotations

import ctypes
import math
import os
import queue
import threading
import time
from contextlib import suppress
from ctypes import wintypes
from enum import StrEnum
from typing import Any, Protocol

from pydantic import Field, model_validator

from .foreground_context import (
    ForegroundCollector,
    ForegroundSnapshot,
)
from .models import FrozenModel


class ContextEventError(RuntimeError):
    pass


class ContextEventType(StrEnum):
    FOREGROUND_CHANGED = "FOREGROUND_CHANGED"
    FOCUS_CHANGED = "FOCUS_CHANGED"


class ContextEvent(FrozenModel):
    sequence: int = Field(ge=1)
    event_type: ContextEventType
    hwnd: int = Field(ge=1)
    pid: int = Field(ge=1)
    observed_at: float = Field(ge=0.0)

    @model_validator(mode="after")
    def require_finite_time(self) -> ContextEvent:
        if not math.isfinite(self.observed_at):
            raise ValueError("context event time must be finite")
        return self


class ContextUpdate(FrozenModel):
    sequence: int = Field(ge=1)
    event_type: ContextEventType
    snapshot: ForegroundSnapshot

    def planner_context(self) -> dict[str, object]:
        if not self.snapshot.identity_verified or self.snapshot.app_id is None:
            raise ContextEventError("unverified context cannot enter planner context")
        return {"foreground_app_id": self.snapshot.app_id}


class ContextEventSource(Protocol):
    def poll(self, *, timeout_seconds: float) -> ContextEvent | None: ...

    def close(self, *, timeout_seconds: float = 2.0) -> None: ...


class EventDrivenContextManager:
    """Collect verified foreground identity only after a bounded structural event."""

    def __init__(
        self,
        source: ContextEventSource,
        collector: ForegroundCollector,
        *,
        duplicate_window_seconds: float = 0.05,
    ) -> None:
        if (
            isinstance(duplicate_window_seconds, bool)
            or not isinstance(duplicate_window_seconds, (int, float))
            or not math.isfinite(duplicate_window_seconds)
            or not 0 <= duplicate_window_seconds <= 1.0
        ):
            raise ValueError("duplicate window must be finite and between 0 and 1")
        self._source = source
        self._collector = collector
        self._duplicate_window = float(duplicate_window_seconds)
        self._last_sequence = 0
        self._last_observed_at = 0.0
        self._last_signature: tuple[ContextEventType, int, int] | None = None

    def next_update(self, *, timeout_seconds: float = 1.0) -> ContextUpdate | None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 <= timeout_seconds <= 5.0
        ):
            raise ValueError("timeout_seconds must be finite and between 0 and 5")
        try:
            event = self._source.poll(timeout_seconds=float(timeout_seconds))
        except Exception as error:
            raise ContextEventError("context event source failed") from error
        if event is None:
            return None
        if event.sequence <= self._last_sequence or event.observed_at < self._last_observed_at:
            raise ContextEventError("context event ordering is invalid")
        signature = (event.event_type, event.hwnd, event.pid)
        duplicate = (
            signature == self._last_signature
            and event.observed_at - self._last_observed_at < self._duplicate_window
        )
        self._last_sequence = event.sequence
        self._last_observed_at = event.observed_at
        self._last_signature = signature
        if duplicate:
            return None
        try:
            snapshot = self._collector.capture()
        except Exception as error:
            raise ContextEventError("event-triggered foreground collection failed") from error
        if snapshot.hwnd != event.hwnd or snapshot.pid != event.pid:
            raise ContextEventError("foreground changed after context event")
        return ContextUpdate(
            sequence=event.sequence,
            event_type=event.event_type,
            snapshot=snapshot,
        )

    def close(self, *, timeout_seconds: float = 2.0) -> None:
        try:
            self._source.close(timeout_seconds=timeout_seconds)
        except Exception as error:
            raise ContextEventError("context event source close failed") from error


class WindowsContextEventSource:
    """Bounded WinEvent hook for foreground/focus structure only; reads no UI text."""

    _EVENT_SYSTEM_FOREGROUND = 0x0003
    _EVENT_OBJECT_FOCUS = 0x8005
    _WINEVENT_OUTOFCONTEXT = 0x0000
    _WM_QUIT = 0x0012
    _GA_ROOT = 2

    def __init__(
        self,
        *,
        queue_capacity: int = 128,
        startup_timeout_seconds: float = 2.0,
    ) -> None:
        if type(queue_capacity) is not int or not 1 <= queue_capacity <= 4_096:
            raise ValueError("queue_capacity must be between 1 and 4096")
        if (
            isinstance(startup_timeout_seconds, bool)
            or not 0.1 <= startup_timeout_seconds <= 5.0
        ):
            raise ValueError("startup timeout must be between 0.1 and 5 seconds")
        if os.name != "nt":
            raise ContextEventError("Windows context events require Windows")
        self._events: queue.Queue[ContextEvent] = queue.Queue(maxsize=queue_capacity)
        self._overflow = threading.Event()
        self._ready = threading.Event()
        self._closed = threading.Event()
        self._start_failed = False
        self._thread_id = 0
        self._sequence = 0
        self._native_lock = threading.Lock()
        self._user32: Any | None = None
        self._callback: object | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="javis-context-events",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=startup_timeout_seconds) or self._start_failed:
            self.close(timeout_seconds=startup_timeout_seconds)
            raise ContextEventError("Windows context event hook failed to start")

    def poll(self, *, timeout_seconds: float) -> ContextEvent | None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 <= timeout_seconds <= 5.0
        ):
            raise ValueError("timeout_seconds must be finite and between 0 and 5")
        if self._closed.is_set():
            raise ContextEventError("context event source is closed")
        if self._overflow.is_set():
            raise ContextEventError("context event queue overflowed")
        try:
            return self._events.get(timeout=float(timeout_seconds))
        except queue.Empty:
            return None

    def close(self, *, timeout_seconds: float = 2.0) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 5.0
        ):
            raise ValueError("timeout_seconds must be finite and between 0 and 5")
        if self._closed.is_set():
            return
        with self._native_lock:
            user32 = self._user32
            thread_id = self._thread_id
        if user32 is not None and thread_id:
            user32.PostThreadMessageW(thread_id, self._WM_QUIT, 0, 0)
        self._thread.join(timeout=float(timeout_seconds))
        if self._thread.is_alive():
            raise ContextEventError("context event hook did not stop")
        self._closed.set()

    def _run(self) -> None:
        hooks: list[object] = []
        user32: Any | None = None
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
            callback_type = ctypes.WINFUNCTYPE(
                None,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_long,
                ctypes.c_long,
                ctypes.c_uint32,
                ctypes.c_uint32,
            )
            callback = callback_type(self._on_event)
            user32.SetWinEventHook.argtypes = [
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_void_p,
                callback_type,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_uint32,
            ]
            user32.SetWinEventHook.restype = ctypes.c_void_p
            user32.UnhookWinEvent.argtypes = [ctypes.c_void_p]
            user32.UnhookWinEvent.restype = wintypes.BOOL
            user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            user32.GetAncestor.restype = ctypes.c_void_p
            user32.GetWindowThreadProcessId.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
            ]
            user32.GetWindowThreadProcessId.restype = ctypes.c_uint32
            user32.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_uint32,
            ]
            user32.PeekMessageW.restype = wintypes.BOOL
            user32.GetMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
            ]
            user32.GetMessageW.restype = ctypes.c_int
            user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.TranslateMessage.restype = wintypes.BOOL
            user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.DispatchMessageW.restype = ctypes.c_ssize_t
            user32.PostThreadMessageW.argtypes = [
                ctypes.c_uint32,
                ctypes.c_uint32,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.PostThreadMessageW.restype = wintypes.BOOL
            kernel32.GetCurrentThreadId.argtypes = []
            kernel32.GetCurrentThreadId.restype = ctypes.c_uint32
            for event_id in (self._EVENT_SYSTEM_FOREGROUND, self._EVENT_OBJECT_FOCUS):
                hook = user32.SetWinEventHook(
                    event_id,
                    event_id,
                    None,
                    callback,
                    0,
                    0,
                    self._WINEVENT_OUTOFCONTEXT,
                )
                if not hook:
                    raise OSError("WinEvent hook unavailable")
                hooks.append(hook)
            thread_id = int(kernel32.GetCurrentThreadId())
            message = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            with self._native_lock:
                self._user32 = user32
                self._thread_id = thread_id
            self._callback = callback
            self._ready.set()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception:
            self._start_failed = not self._ready.is_set()
            self._ready.set()
        finally:
            if user32 is not None:
                for hook in hooks:
                    with suppress(Exception):
                        user32.UnhookWinEvent(hook)
            self._closed.set()

    def _on_event(
        self,
        _hook: object,
        event_id: int,
        hwnd: int,
        _object_id: int,
        _child_id: int,
        _event_thread: int,
        _event_time_ms: int,
    ) -> None:
        if not hwnd:
            return
        with self._native_lock:
            user32 = self._user32
        if user32 is None:
            return
        root = user32.GetAncestor(hwnd, self._GA_ROOT)
        root_hwnd = int(root) if root else int(hwnd)
        pid = ctypes.c_uint32()
        if not user32.GetWindowThreadProcessId(root_hwnd, ctypes.byref(pid)) or not pid.value:
            return
        event_type = (
            ContextEventType.FOREGROUND_CHANGED
            if event_id == self._EVENT_SYSTEM_FOREGROUND
            else ContextEventType.FOCUS_CHANGED
        )
        try:
            observed_at = time.monotonic()
            self._sequence += 1
            event = ContextEvent(
                sequence=self._sequence,
                event_type=event_type,
                hwnd=root_hwnd,
                pid=int(pid.value),
                observed_at=observed_at,
            )
            self._events.put_nowait(event)
        except (ValueError, queue.Full):
            self._overflow.set()
