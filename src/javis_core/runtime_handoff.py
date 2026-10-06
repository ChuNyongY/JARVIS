from __future__ import annotations

import ctypes
import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol

from .process_launch import RegisteredApp
from .process_verifier import ProcessInspector, WindowsProcessInspector


class RuntimePidSource(Protocol):
    def __call__(self, image_name: str) -> Iterable[int]: ...


class WindowsRuntimeProcessResolver:
    """Resolve one exact new runtime PID after a registered launcher handoff."""

    def __init__(
        self,
        *,
        inspector: ProcessInspector | None = None,
        pid_source: RuntimePidSource | None = None,
        timeout_seconds: float = 2.0,
        poll_seconds: float = 0.05,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 0 < timeout_seconds <= 10 or not 0 < poll_seconds <= timeout_seconds:
            raise ValueError("invalid runtime handoff timing")
        self._inspector = inspector or WindowsProcessInspector()
        self._pid_source = pid_source or _toolhelp_pids
        self._timeout = timeout_seconds
        self._poll = poll_seconds
        self._clock = clock
        self._sleeper = sleeper

    def snapshot(self, app: RegisteredApp) -> frozenset[int]:
        return frozenset(self._matching_pids(app))

    def resolve(
        self,
        app: RegisteredApp,
        launcher_pid: int,
        previous_pids: frozenset[int],
    ) -> int | None:
        deadline = self._clock() + self._timeout
        stable_candidate: int | None = None
        while True:
            candidates = self._matching_pids(app) - previous_pids
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

    def _matching_pids(self, app: RegisteredApp) -> set[int]:
        matches: set[int] = set()
        for pid in self._pid_source(app.runtime_path.name):
            if type(pid) is not int or pid <= 0:
                continue
            image = self._inspector.executable_path(pid)
            if image is not None and app.matches_runtime_executable(image):
                matches.add(pid)
        return matches


def _toolhelp_pids(image_name: str) -> tuple[int, ...]:
    if os.name != "nt":
        return ()

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = (
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_wchar * 260),
        )

    kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32FirstW.restype = ctypes.c_int
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry32W)]
    kernel32.Process32NextW.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    invalid = ctypes.c_void_p(-1).value
    handle = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if handle == invalid or handle is None:
        return ()
    identifiers: list[int] = []
    try:
        entry = ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(ProcessEntry32W)
        success = kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while success:
            if Path(entry.szExeFile).name.casefold() == image_name.casefold():
                identifiers.append(int(entry.th32ProcessID))
            success = kernel32.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(handle)
    return tuple(identifiers)
