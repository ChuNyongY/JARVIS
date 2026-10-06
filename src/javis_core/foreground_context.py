from __future__ import annotations

import ctypes
import math
import os
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field, model_validator

from .models import FrozenModel
from .process_launch import RegisteredApp
from .process_verifier import ProcessInspector, WindowsProcessInspector


class ForegroundContextError(RuntimeError):
    pass


class ForegroundSnapshot(FrozenModel):
    hwnd: int = Field(ge=1)
    pid: int = Field(ge=1)
    app_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    identity_verified: bool
    captured_at: datetime

    @model_validator(mode="after")
    def validate_identity_and_time(self) -> ForegroundSnapshot:
        if self.identity_verified != (self.app_id is not None):
            raise ValueError("verified foreground identity requires one app ID")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() != timedelta(0):
            raise ValueError("foreground timestamp must be UTC-aware")
        return self


class ForegroundBackend(Protocol):
    def foreground(self) -> tuple[int, int]: ...


class ForegroundCollector(Protocol):
    def capture(self) -> ForegroundSnapshot: ...


class WindowsForegroundBackend:
    def foreground(self) -> tuple[int, int]:
        if os.name != "nt":
            raise ForegroundContextError("foreground context requires Windows")
        user32: Any = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        user32.GetWindowThreadProcessId.restype = ctypes.c_uint32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            raise ForegroundContextError("foreground window is unavailable")
        pid = ctypes.c_uint32()
        thread_id = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not thread_id or pid.value == 0:
            raise ForegroundContextError("foreground PID is unavailable")
        return int(hwnd), int(pid.value)


class ForegroundContextCollector:
    """Collect only structural foreground identity; never title or UI content."""

    def __init__(
        self,
        apps: Mapping[str, RegisteredApp],
        *,
        backend: ForegroundBackend | None = None,
        inspector: ProcessInspector | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._apps = dict(apps)
        if any(key != app.app_id for key, app in self._apps.items()):
            raise ValueError("registered app mapping key must match app_id")
        self._backend = backend or WindowsForegroundBackend()
        self._inspector = inspector or WindowsProcessInspector()
        self._clock = clock

    def capture(self) -> ForegroundSnapshot:
        try:
            hwnd, pid = self._backend.foreground()
        except Exception as error:
            raise ForegroundContextError("could not capture foreground identity") from error
        if type(hwnd) is not int or type(pid) is not int or hwnd <= 0 or pid <= 0:
            raise ForegroundContextError("invalid foreground identity")
        captured_at = self._clock()
        if captured_at.tzinfo is None or captured_at.utcoffset() != timedelta(0):
            raise ForegroundContextError("foreground clock must return UTC")
        executable = self._inspector.executable_path(pid)
        matches = (
            []
            if executable is None
            else [
                app.app_id
                for app in self._apps.values()
                if app.matches_runtime_executable(Path(executable))
            ]
        )
        if len(matches) > 1:
            raise ForegroundContextError("foreground application identity is ambiguous")
        app_id = matches[0] if matches else None
        return ForegroundSnapshot(
            hwnd=hwnd,
            pid=pid,
            app_id=app_id,
            identity_verified=app_id is not None,
            captured_at=captured_at,
        )


class StableForegroundContextCollector:
    """Require a bounded run of identical, privacy-minimized observations."""

    def __init__(
        self,
        collector: ForegroundCollector,
        *,
        sample_count: int = 3,
        interval_seconds: float = 0.05,
        timeout_seconds: float = 0.5,
        require_verified: bool = True,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if type(sample_count) is not int or not 2 <= sample_count <= 10:
            raise ValueError("sample_count must be an integer from 2 through 10")
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or not math.isfinite(interval_seconds)
            or not 0 <= interval_seconds <= 0.5
        ):
            raise ValueError("interval_seconds must be finite and between 0 and 0.5")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 5
            or (sample_count - 1) * interval_seconds > timeout_seconds
        ):
            raise ValueError("timeout_seconds cannot satisfy the bounded sampling contract")
        self._collector = collector
        self._sample_count = sample_count
        self._interval = float(interval_seconds)
        self._timeout = float(timeout_seconds)
        self._require_verified = require_verified
        self._monotonic = monotonic
        self._sleeper = sleeper

    def capture(self) -> ForegroundSnapshot:
        started = self._read_monotonic(None)
        previous_time = started
        expected: tuple[int, int, str | None, bool] | None = None
        latest: ForegroundSnapshot | None = None
        for index in range(self._sample_count):
            if index:
                try:
                    self._sleeper(self._interval)
                except Exception as error:
                    raise ForegroundContextError("foreground stability wait failed") from error
            try:
                snapshot = self._collector.capture()
            except Exception as error:
                raise ForegroundContextError("foreground stability sample failed") from error
            if self._require_verified and not snapshot.identity_verified:
                raise ForegroundContextError("foreground application is not verified")
            identity = (
                snapshot.hwnd,
                snapshot.pid,
                snapshot.app_id,
                snapshot.identity_verified,
            )
            if expected is None:
                expected = identity
            elif identity != expected:
                raise ForegroundContextError("foreground application changed during sampling")
            now = self._read_monotonic(previous_time)
            previous_time = now
            if now - started > self._timeout:
                raise ForegroundContextError("foreground stability sampling timed out")
            latest = snapshot
        assert latest is not None
        return latest

    def capture_planner_context(self) -> dict[str, object]:
        """Return only the verified app ID; process identifiers stay local."""

        snapshot = self.capture()
        if not snapshot.identity_verified or snapshot.app_id is None:
            raise ForegroundContextError("unverified foreground cannot enter planner context")
        return {"foreground_app_id": snapshot.app_id}

    def _read_monotonic(self, previous: float | None) -> float:
        try:
            value = self._monotonic()
        except Exception as error:
            raise ForegroundContextError("foreground monotonic clock failed") from error
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or (previous is not None and value < previous)
        ):
            raise ForegroundContextError("foreground monotonic clock is invalid")
        return float(value)
