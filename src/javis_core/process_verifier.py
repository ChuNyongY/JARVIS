from __future__ import annotations

import ctypes
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from .models import Postcondition
from .process_launch import RegisteredApp
from .runtime_models import ExecutionResult


class ProcessInspector(Protocol):
    def executable_path(self, pid: int) -> Path | None: ...


class WindowsProcessInspector:
    """Query a process image path without opening it for mutation."""

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def executable_path(self, pid: int) -> Path | None:
        if os.name != "nt" or pid <= 0:
            return None
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int

        handle = kernel32.OpenProcess(
            self._PROCESS_QUERY_LIMITED_INFORMATION,
            0,
            pid,
        )
        if not handle:
            return None
        try:
            capacity = 32_768
            buffer = ctypes.create_unicode_buffer(capacity)
            size = ctypes.c_uint32(capacity)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return Path(buffer.value)
        finally:
            kernel32.CloseHandle(handle)

    def creation_time_100ns(self, pid: int) -> int | None:
        """Return a creation timestamp that distinguishes reused process IDs."""

        if os.name != "nt" or pid <= 0:
            return None

        class FileTime(ctypes.Structure):
            _fields_ = (("low", ctypes.c_uint32), ("high", ctypes.c_uint32))

        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetProcessTimes.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        ]
        kernel32.GetProcessTimes.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(self._PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if not handle:
            return None
        try:
            creation = FileTime()
            exit_time = FileTime()
            kernel_time = FileTime()
            user_time = FileTime()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel_time),
                ctypes.byref(user_time),
            ):
                return None
            return int((creation.high << 32) | creation.low)
        finally:
            kernel32.CloseHandle(handle)


class ProcessExistsPredicate:
    """Verifier predicate for a PID and its registered executable identity."""

    def __init__(
        self,
        apps: Mapping[str, RegisteredApp],
        inspector: ProcessInspector | None = None,
    ) -> None:
        self._apps = dict(apps)
        self._inspector = inspector or WindowsProcessInspector()

    def __call__(self, postcondition: Postcondition, result: ExecutionResult) -> bool:
        if postcondition.arguments:
            return False
        pid = result.observed.get("pid")
        app_id = result.observed.get("app_id")
        if type(pid) is not int or not isinstance(app_id, str):
            return False
        app = self._apps.get(app_id)
        if app is None:
            return False
        executable = self._inspector.executable_path(pid)
        return executable is not None and app.matches_runtime_executable(executable)
