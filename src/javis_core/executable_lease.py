from __future__ import annotations

import ctypes
import hashlib
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any, Protocol

from .process_launch import RegisteredApp


class ExecutableLeaseError(OSError):
    pass


class ExecutableLease(Protocol):
    def hold(self, app: RegisteredApp) -> AbstractContextManager[bool]: ...


class PortableExecutableLease:
    """Testable fallback; it validates an open stream but cannot deny replacement."""

    @contextmanager
    def hold(self, app: RegisteredApp) -> Iterator[bool]:
        try:
            resolved = app.executable.resolve(strict=True)
            if resolved != app.executable or resolved.is_symlink() or not resolved.is_file():
                yield False
                return
            with resolved.open("rb") as stream:
                yield _stream_hash(stream) == app.sha256
        except OSError as exc:
            raise ExecutableLeaseError("executable lease could not be acquired") from exc


class WindowsExecutableLease:
    """Hold a deny-write/delete Windows handle across verification and launch."""

    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 0x00000001
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _BUFFER_BYTES = 1024 * 1024
    _MAX_FINAL_PATH = 32_768

    @contextmanager
    def hold(self, app: RegisteredApp) -> Iterator[bool]:
        if os.name != "nt":
            raise ExecutableLeaseError("Windows executable lease is unavailable")
        kernel32 = self._kernel32()
        handle = kernel32.CreateFileW(
            str(app.executable),
            self._GENERIC_READ,
            self._FILE_SHARE_READ,
            None,
            self._OPEN_EXISTING,
            self._FILE_ATTRIBUTE_NORMAL | self._FILE_FLAG_SEQUENTIAL_SCAN,
            None,
        )
        if handle == self._INVALID_HANDLE_VALUE or handle is None:
            self._raise_last_error("executable lease could not be acquired")
        close_error: ExecutableLeaseError | None = None
        try:
            final_path = self._final_path(kernel32, handle)
            path_matches = _same_windows_path(final_path, app.executable)
            digest_matches = self._handle_hash(kernel32, handle) == app.sha256
            yield path_matches and digest_matches
        finally:
            if not kernel32.CloseHandle(handle):
                close_error = self._last_error("executable lease could not be released")
            if close_error is not None:
                raise close_error

    @classmethod
    def _kernel32(cls) -> Any:
        kernel32: Any = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.GetFinalPathNameByHandleW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        kernel32.GetFinalPathNameByHandleW.restype = ctypes.c_uint32
        kernel32.ReadFile.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        kernel32.ReadFile.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        return kernel32

    @classmethod
    def _final_path(cls, kernel32: Any, handle: int) -> Path:
        buffer = ctypes.create_unicode_buffer(cls._MAX_FINAL_PATH)
        length = kernel32.GetFinalPathNameByHandleW(
            handle,
            buffer,
            cls._MAX_FINAL_PATH,
            0,
        )
        if length == 0:
            cls._raise_last_error("executable final path could not be verified")
        if length >= cls._MAX_FINAL_PATH:
            raise ExecutableLeaseError("executable final path exceeded limit")
        return Path(_strip_extended_path_prefix(buffer.value))

    @classmethod
    def _handle_hash(cls, kernel32: Any, handle: int) -> str:
        digest = hashlib.sha256()
        buffer = ctypes.create_string_buffer(cls._BUFFER_BYTES)
        while True:
            read = ctypes.c_uint32()
            if not kernel32.ReadFile(
                handle,
                buffer,
                cls._BUFFER_BYTES,
                ctypes.byref(read),
                None,
            ):
                cls._raise_last_error("executable contents could not be verified")
            if read.value == 0:
                return digest.hexdigest()
            digest.update(buffer.raw[: read.value])

    @staticmethod
    def _last_error(message: str) -> ExecutableLeaseError:
        error = ctypes.get_last_error()
        return ExecutableLeaseError(error, message)

    @classmethod
    def _raise_last_error(cls, message: str) -> None:
        raise cls._last_error(message)


def default_executable_lease() -> ExecutableLease:
    return WindowsExecutableLease() if os.name == "nt" else PortableExecutableLease()


def _stream_hash(stream: Any) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _strip_extended_path_prefix(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def _same_windows_path(left: Path, right: Path) -> bool:
    try:
        left_value = left.resolve(strict=True)
        right_value = right.resolve(strict=True)
    except OSError:
        return False
    return os.path.normcase(str(left_value)) == os.path.normcase(str(right_value))
