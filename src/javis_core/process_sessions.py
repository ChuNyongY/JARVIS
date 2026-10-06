from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from .models import FrozenModel
from .process_launch import RegisteredApp
from .process_verifier import ProcessInspector, WindowsProcessInspector
from .runtime_models import ExecutionResult


class AuthorizedProcess(FrozenModel):
    pid: int
    app_id: str
    executable: Path
    sha256: str
    creation_time_100ns: int


class ProcessSessionInspector(ProcessInspector, Protocol):
    def creation_time_100ns(self, pid: int) -> int | None: ...


class AuthorizedProcessRegistry:
    """Track verified launch results and revalidate identity before UI access."""

    def __init__(
        self,
        apps: Mapping[str, RegisteredApp],
        inspector: ProcessSessionInspector | None = None,
    ) -> None:
        self._apps = dict(apps)
        if any(key != app.app_id for key, app in self._apps.items()):
            raise ValueError("registered app mapping key must match app_id")
        self._inspector = inspector or WindowsProcessInspector()
        self._sessions: dict[int, AuthorizedProcess] = {}
        self._lock = threading.Lock()

    def authorize_launch(self, result: ExecutionResult) -> bool:
        if not result.success:
            return False
        pid = result.observed.get("pid")
        app_id = result.observed.get("app_id")
        if type(pid) is not int or not isinstance(app_id, str):
            return False
        app = self._apps.get(app_id)
        if app is None:
            return False
        executable = self._inspector.executable_path(pid)
        creation_time = self._inspector.creation_time_100ns(pid)
        if (
            executable is None
            or creation_time is None
            or not app.matches_runtime_executable(executable)
        ):
            return False
        session = AuthorizedProcess(
            pid=pid,
            app_id=app_id,
            executable=app.runtime_path,
            sha256=app.runtime_hash,
            creation_time_100ns=creation_time,
        )
        with self._lock:
            self._sessions[pid] = session
        return True

    def is_authorized(self, pid: int) -> bool:
        with self._lock:
            session = self._sessions.get(pid)
        if session is None:
            return False
        app = self._apps.get(session.app_id)
        executable = self._inspector.executable_path(pid)
        creation_time = self._inspector.creation_time_100ns(pid)
        valid = (
            app is not None
            and executable is not None
            and app.runtime_path == session.executable
            and app.runtime_hash == session.sha256
            and creation_time == session.creation_time_100ns
            and app.matches_runtime_executable(executable)
        )
        if not valid:
            self.revoke(pid)
        return valid

    def is_authorized_app(self, pid: int, app_id: str) -> bool:
        if not self.is_authorized(pid):
            return False
        with self._lock:
            session = self._sessions.get(pid)
            return session is not None and session.app_id == app_id

    def revoke(self, pid: int) -> None:
        with self._lock:
            self._sessions.pop(pid, None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    @property
    def sessions(self) -> tuple[AuthorizedProcess, ...]:
        with self._lock:
            return tuple(self._sessions[pid] for pid in sorted(self._sessions))
