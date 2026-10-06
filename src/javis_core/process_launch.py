from __future__ import annotations

import hashlib
import os
import subprocess
from collections.abc import Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from time import perf_counter_ns
from typing import Literal, Protocol

from pydantic import Field, StrictBool, model_validator

from .cancellation import CancellationToken
from .models import Action, FrozenModel
from .runtime_models import ExecutionResult


class RegisteredApp(FrozenModel):
    app_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    executable: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    publisher: str | None = Field(default=None, min_length=1)
    runtime_executable: Path | None = None
    runtime_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    runtime_publisher: str | None = Field(default=None, min_length=1)
    direct_launch: bool = True
    single_instance_handoff: StrictBool = False
    window_class: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    )
    document_launch_mode: Literal["path-only", "browser-new-window"] = "path-only"
    launch_argument: Literal["--new-window"] | None = None

    @model_validator(mode="after")
    def runtime_identity_must_be_complete(self) -> RegisteredApp:
        if (self.runtime_executable is None) != (self.runtime_sha256 is None):
            raise ValueError("runtime executable and hash must be provided together")
        if self.runtime_publisher is not None and self.runtime_executable is None:
            raise ValueError("runtime publisher requires a runtime executable")
        if self.single_instance_handoff and self.runtime_executable is not None:
            raise ValueError("single-instance handoff requires the launcher runtime identity")
        if self.single_instance_handoff and not self.direct_launch:
            raise ValueError("single-instance handoff requires direct launch")
        if self.single_instance_handoff and self.window_class is None:
            raise ValueError("single-instance handoff requires an exact window class")
        if self.document_launch_mode == "browser-new-window" and not self.single_instance_handoff:
            raise ValueError("browser document launch requires single-instance handoff")
        if self.launch_argument is not None and not self.single_instance_handoff:
            raise ValueError("fixed launch argument requires single-instance handoff")
        return self

    @classmethod
    def from_path(cls, app_id: str, executable: Path) -> RegisteredApp:
        resolved = executable.resolve(strict=True)
        if not resolved.is_file():
            raise ValueError("registered executable must be a file")
        return cls(app_id=app_id, executable=resolved, sha256=_file_hash(resolved))

    def matches_executable(self, executable: Path) -> bool:
        try:
            resolved = executable.resolve(strict=True)
            return resolved == self.executable and _file_hash(resolved) == self.sha256
        except OSError:
            return False

    @property
    def runtime_path(self) -> Path:
        return self.runtime_executable or self.executable

    @property
    def runtime_hash(self) -> str:
        return self.runtime_sha256 or self.sha256

    def matches_runtime_executable(self, executable: Path) -> bool:
        try:
            resolved = executable.resolve(strict=True)
            return resolved == self.runtime_path and _file_hash(resolved) == self.runtime_hash
        except OSError:
            return False


class ProcessLauncher(Protocol):
    def launch(
        self,
        executable: Path,
        *,
        fixed_argument: str | None,
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> int: ...


class ExecutableLeaseLike(Protocol):
    def hold(self, app: RegisteredApp) -> AbstractContextManager[bool]: ...


class RuntimeProcessResolver(Protocol):
    def snapshot(self, app: RegisteredApp) -> frozenset[int]: ...

    def resolve(
        self,
        app: RegisteredApp,
        launcher_pid: int,
        previous_pids: frozenset[int],
    ) -> int | None: ...


class RuntimeWindowResolver(Protocol):
    def snapshot(self, app: RegisteredApp) -> frozenset[tuple[int, int]]: ...

    def resolve(
        self,
        app: RegisteredApp,
        launcher_pid: int,
        previous_windows: frozenset[tuple[int, int]],
    ) -> tuple[int, int] | None: ...


class SubprocessLauncher:
    def launch(
        self,
        executable: Path,
        *,
        fixed_argument: str | None,
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> int:
        command = [str(executable)]
        if fixed_argument is not None:
            command.append(fixed_argument)
        process = subprocess.Popen(  # noqa: S603 - executable is pre-registered and hashed
            command,
            cwd=working_directory,
            env=dict(environment),
            shell=False,
            close_fds=True,
        )
        return process.pid


class ProcessLaunchExecutor:
    """Launch only pre-registered executables without LLM-provided arguments."""

    name = "native"

    def __init__(
        self,
        apps: Mapping[str, RegisteredApp],
        *,
        launcher: ProcessLauncher | None = None,
        lease: ExecutableLeaseLike | None = None,
        runtime_resolver: RuntimeProcessResolver | None = None,
        window_resolver: RuntimeWindowResolver | None = None,
    ) -> None:
        self._apps = dict(apps)
        if any(key != app.app_id for key, app in self._apps.items()):
            raise ValueError("registered app mapping key must match app_id")
        self._launcher = launcher or SubprocessLauncher()
        if lease is None:
            from .executable_lease import default_executable_lease

            lease = default_executable_lease()
        self._lease = lease
        if runtime_resolver is None and os.name == "nt":
            from .runtime_handoff import WindowsRuntimeProcessResolver

            runtime_resolver = WindowsRuntimeProcessResolver()
        self._runtime_resolver = runtime_resolver
        if window_resolver is None and os.name == "nt":
            from .window_handoff import WindowsRuntimeWindowResolver

            window_resolver = WindowsRuntimeWindowResolver()
        self._window_resolver = window_resolver

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")
        if action.capability_id != "process.launch":
            return self._failure(action, started, "UNSUPPORTED_CAPABILITY")
        if set(action.arguments) != {"app_id"}:
            return self._failure(action, started, "INVALID_ARGUMENTS")
        app_id = action.arguments.get("app_id")
        if not isinstance(app_id, str):
            return self._failure(action, started, "INVALID_ARGUMENTS")
        app = self._apps.get(app_id)
        if app is None:
            return self._failure(action, started, "APP_NOT_REGISTERED")
        if not app.direct_launch:
            return self._failure(action, started, "DIRECT_LAUNCH_NOT_ALLOWED")
        if action.target_identity != app_id:
            return self._failure(action, started, "TARGET_IDENTITY_MISMATCH")
        previous_runtime_pids: frozenset[int] = frozenset()
        previous_windows: frozenset[tuple[int, int]] = frozenset()
        distinct_runtime = app.runtime_executable is not None
        if distinct_runtime:
            if self._runtime_resolver is None:
                return self._failure(action, started, "RUNTIME_RESOLVER_UNAVAILABLE")
            previous_runtime_pids = self._runtime_resolver.snapshot(app)
        elif app.single_instance_handoff:
            if self._window_resolver is None:
                return self._failure(action, started, "WINDOW_RESOLVER_UNAVAILABLE")
            previous_windows = self._window_resolver.snapshot(app)
        launch_attempted = False
        try:
            with self._lease.hold(app) as verified:
                if not verified:
                    return self._failure(action, started, "EXECUTABLE_CHANGED")
                if cancellation is not None and cancellation.is_cancelled:
                    return self._failure(action, started, "STOP_REQUESTED")
                launch_attempted = True
                pid = self._launcher.launch(
                    app.executable,
                    fixed_argument=app.launch_argument,
                    working_directory=app.executable.parent,
                    environment=self._minimal_environment(),
                )
                hwnd: int | None = None
                if distinct_runtime:
                    assert self._runtime_resolver is not None
                    runtime_pid = self._runtime_resolver.resolve(
                        app,
                        pid,
                        previous_runtime_pids,
                    )
                    if runtime_pid is None:
                        return self._failure(action, started, "RUNTIME_HANDOFF_FAILED")
                    pid = runtime_pid
                elif app.single_instance_handoff:
                    assert self._window_resolver is not None
                    window_identity = self._window_resolver.resolve(
                        app,
                        pid,
                        previous_windows,
                    )
                    if window_identity is None:
                        return self._failure(action, started, "WINDOW_HANDOFF_FAILED")
                    pid, hwnd = window_identity
        except OSError:
            error_code = "PROCESS_LAUNCH_FAILED" if launch_attempted else "EXECUTABLE_LEASE_FAILED"
            return self._failure(action, started, error_code)
        observed: dict[str, str | int] = {"app_id": app_id, "pid": pid}
        if hwnd is not None:
            observed["hwnd"] = hwnd
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed=observed,
            duration_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _minimal_environment() -> dict[str, str]:
        allowed = (
            "SystemRoot",
            "WINDIR",
            "PATH",
            "PATHEXT",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
        )
        return {key: os.environ[key] for key in allowed if key in os.environ}

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (perf_counter_ns() - started) // 1_000_000)

    @classmethod
    def _failure(cls, action: Action, started: int, error_code: str) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=False,
            error_code=error_code,
            error_message="Registered application was not launched",
            retry_safe=False,
            duration_ms=cls._elapsed_ms(started),
        )


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
