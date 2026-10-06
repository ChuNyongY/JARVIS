from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import BinaryIO


class FileLockTimeout(TimeoutError):
    pass


class InterprocessFileLock:
    """Small cross-process advisory lock backed by one locked byte."""

    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float = 5.0,
        hardener: Callable[[Path], None] | None = None,
    ) -> None:
        if os.name != "nt":
            raise RuntimeError("interprocess lock requires Windows")
        if timeout_seconds <= 0:
            raise ValueError("lock timeout must be positive")
        if path.is_symlink():
            raise ValueError("lock path must not be a symlink")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._timeout = timeout_seconds
        self._hardener = hardener
        self._handle: BinaryIO | None = None

    def __enter__(self) -> InterprocessFileLock:
        handle = self._path.open("a+b", buffering=0)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            os.fsync(handle.fileno())
        if self._hardener is not None:
            self._hardener(self._path)
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                self._acquire(handle)
                self._handle = handle
                return self
            except OSError as error:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise FileLockTimeout("interprocess lock timed out") from error
                time.sleep(0.05)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            self._release(handle)
        finally:
            handle.close()

    @staticmethod
    def _acquire(handle: BinaryIO) -> None:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    @staticmethod
    def _release(handle: BinaryIO) -> None:
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
