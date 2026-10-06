from __future__ import annotations

import ctypes
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .models import Postcondition
from .process_launch import RegisteredApp
from .process_verifier import ProcessInspector, WindowsProcessInspector
from .runtime_models import ExecutionResult


@dataclass(frozen=True, slots=True)
class WindowIdentity:
    hwnd: int
    pid: int
    class_name: str


class WindowIdentitySource(Protocol):
    def __call__(self) -> Iterable[WindowIdentity]: ...


class WindowsWindowIdentitySource:
    """Enumerate visible unowned top-level windows without reading titles or content."""

    def __call__(self) -> tuple[WindowIdentity, ...]:
        if os.name != "nt":
            return ()
        user32: Any = ctypes.WinDLL("user32", use_last_error=True)
        enum_callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows.argtypes = [enum_callback, ctypes.c_void_p]
        user32.EnumWindows.restype = ctypes.c_bool
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = ctypes.c_bool
        user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user32.GetWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        user32.GetWindowThreadProcessId.restype = ctypes.c_uint32
        user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int

        identities: list[WindowIdentity] = []

        def collect(hwnd: int, _parameter: int) -> bool:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):
                return True
            pid = ctypes.c_uint32()
            if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or pid.value == 0:
                return True
            buffer = ctypes.create_unicode_buffer(129)
            length = int(user32.GetClassNameW(hwnd, buffer, len(buffer)))
            if not 1 <= length < len(buffer) - 1:
                return True
            identities.append(
                WindowIdentity(hwnd=int(hwnd), pid=int(pid.value), class_name=buffer.value)
            )
            return True

        callback = enum_callback(collect)
        if not user32.EnumWindows(callback, 0):
            raise OSError("top-level window enumeration failed")
        return tuple(identities)


class WindowsRuntimeWindowResolver:
    """Resolve one stable new window bound to an exact registered app identity."""

    def __init__(
        self,
        *,
        inspector: ProcessInspector | None = None,
        source: WindowIdentitySource | None = None,
        timeout_seconds: float = 2.0,
        poll_seconds: float = 0.05,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 < timeout_seconds <= 10 or not 0 < poll_seconds <= timeout_seconds:
            raise ValueError("invalid window handoff timing")
        self._inspector = inspector or WindowsProcessInspector()
        self._source = source or WindowsWindowIdentitySource()
        self._timeout = timeout_seconds
        self._poll = poll_seconds
        self._clock = clock
        self._sleeper = sleeper

    def snapshot(self, app: RegisteredApp) -> frozenset[tuple[int, int]]:
        return frozenset(self.matching_windows(app))

    def resolve(
        self,
        app: RegisteredApp,
        launcher_pid: int,
        previous_windows: frozenset[tuple[int, int]],
    ) -> tuple[int, int] | None:
        del launcher_pid
        deadline = self._clock() + self._timeout
        stable_candidate: tuple[int, int] | None = None
        while True:
            candidates = self.matching_windows(app) - previous_windows
            if len(candidates) > 1:
                return None
            if len(candidates) == 1:
                current_candidate = next(iter(candidates))
                if stable_candidate is None:
                    stable_candidate = current_candidate
                elif stable_candidate != current_candidate:
                    return None
            elif stable_candidate is not None:
                return None
            now = self._clock()
            if now >= deadline:
                return stable_candidate
            self._sleeper(min(self._poll, deadline - now))

    def matching_windows(self, app: RegisteredApp) -> set[tuple[int, int]]:
        if app.window_class is None:
            return set()
        matches: set[tuple[int, int]] = set()
        for identity in self._source():
            if (
                type(identity.hwnd) is not int
                or type(identity.pid) is not int
                or identity.hwnd <= 0
                or identity.pid <= 0
                or identity.class_name != app.window_class
            ):
                continue
            executable = self._inspector.executable_path(identity.pid)
            if executable is not None and app.matches_runtime_executable(Path(executable)):
                matches.add((identity.pid, identity.hwnd))
        return matches


class WindowIdentityPredicate:
    """Independently re-enumerate and verify the exact PID/HWND/app identity."""

    def __init__(
        self,
        apps: Mapping[str, RegisteredApp],
        *,
        inspector: ProcessInspector | None = None,
        source: WindowIdentitySource | None = None,
    ) -> None:
        self._apps = dict(apps)
        self._resolver = WindowsRuntimeWindowResolver(
            inspector=inspector,
            source=source,
        )

    def __call__(self, postcondition: Postcondition, result: ExecutionResult) -> bool:
        if postcondition.arguments:
            return False
        app_id = result.observed.get("app_id")
        pid = result.observed.get("pid")
        hwnd = result.observed.get("hwnd")
        if not isinstance(app_id, str) or type(pid) is not int or type(hwnd) is not int:
            return False
        app = self._apps.get(app_id)
        return app is not None and (pid, hwnd) in self._resolver.matching_windows(app)
