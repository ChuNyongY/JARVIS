from __future__ import annotations

import hashlib
import ntpath
import os
import stat
import subprocess
from collections.abc import Mapping
from contextlib import AbstractContextManager
from pathlib import Path, PureWindowsPath
from time import perf_counter_ns
from typing import Protocol

from .cancellation import CancellationToken
from .explorer_folder_ipc import (
    ExplorerFolderChecker,
    ExplorerWorkerError,
    PollingExplorerFolderChecker,
)
from .models import Action, VerificationResult
from .process_launch import RegisteredApp
from .process_sessions import ProcessSessionInspector
from .process_verifier import WindowsProcessInspector
from .runtime_models import ExecutionResult


class ExplorerFolderLauncher(Protocol):
    def launch(
        self,
        executable: Path,
        directory: Path,
        *,
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> int: ...


class ExplorerWindowResolver(Protocol):
    def snapshot(self, app: RegisteredApp) -> frozenset[tuple[int, int]]: ...

    def resolve(
        self,
        app: RegisteredApp,
        launcher_pid: int,
        previous_windows: frozenset[tuple[int, int]],
    ) -> tuple[int, int] | None: ...

    def matching_windows(self, app: RegisteredApp) -> set[tuple[int, int]]: ...


class ExplorerExecutableLease(Protocol):
    def hold(self, app: RegisteredApp) -> AbstractContextManager[bool]: ...


class SubprocessExplorerFolderLauncher:
    def launch(
        self,
        executable: Path,
        directory: Path,
        *,
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> int:
        process = subprocess.Popen(  # noqa: S603 - both inputs are independently validated
            [str(executable), "/n,", str(directory)],
            cwd=working_directory,
            env=dict(environment),
            shell=False,
            close_fds=True,
        )
        return process.pid


class ExplorerFolderOpenExecutor:
    """Open one existing allowlisted directory in one newly identified Explorer window."""

    name = "native"

    def __init__(
        self,
        roots: Mapping[str, Path],
        explorer: RegisteredApp,
        *,
        launcher: ExplorerFolderLauncher | None = None,
        lease: ExplorerExecutableLease | None = None,
        inspector: ProcessSessionInspector | None = None,
        window_resolver: ExplorerWindowResolver | None = None,
        folder_checker: ExplorerFolderChecker | None = None,
    ) -> None:
        self._roots = _trusted_roots(roots)
        if (
            explorer.app_id != "explorer"
            or not explorer.direct_launch
            or not explorer.single_instance_handoff
            or explorer.window_class != "CabinetWClass"
        ):
            raise ValueError("Explorer must be a registered CabinetWClass handoff app")
        self._explorer = explorer
        self._launcher = launcher or SubprocessExplorerFolderLauncher()
        if lease is None:
            from .executable_lease import default_executable_lease

            lease = default_executable_lease()
        self._lease = lease
        self._inspector = inspector or WindowsProcessInspector()
        if window_resolver is None:
            from .window_handoff import WindowsRuntimeWindowResolver

            window_resolver = WindowsRuntimeWindowResolver()
        self._window_resolver = window_resolver
        self._folder_checker = folder_checker or PollingExplorerFolderChecker()

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        validated = self._validate(action)
        if isinstance(validated, str):
            return self._failure(action, started, validated, retry_safe=True)
        root_id, directory, digest = validated
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=True)
        try:
            previous_windows = self._window_resolver.snapshot(self._explorer)
        except OSError:
            return self._failure(action, started, "EXPLORER_PRECHECK_FAILED", retry_safe=True)
        launch_attempted = False
        try:
            with self._lease.hold(self._explorer) as verified:
                if not verified:
                    return self._failure(
                        action, started, "EXPLORER_IDENTITY_CHANGED", retry_safe=True
                    )
                revalidated = self._resolve_directory(root_id, action.arguments["relative_path"])
                if revalidated is None or _directory_digest(revalidated) != digest:
                    return self._failure(
                        action, started, "DIRECTORY_IDENTITY_CHANGED", retry_safe=True
                    )
                if cancellation is not None and cancellation.is_cancelled:
                    return self._failure(action, started, "STOP_REQUESTED", retry_safe=True)
                launch_attempted = True
                launcher_pid = self._launcher.launch(
                    self._explorer.executable,
                    directory,
                    working_directory=self._explorer.executable.parent,
                    environment=self._minimal_environment(),
                )
                identity = self._window_resolver.resolve(
                    self._explorer, launcher_pid, previous_windows
                )
                if identity is None:
                    return self._failure(
                        action, started, "EXPLORER_WINDOW_HANDOFF_FAILED", retry_safe=False
                    )
                pid, hwnd = identity
                if not self._folder_checker.matches(hwnd, directory):
                    return self._failure(
                        action, started, "EXPLORER_FOLDER_MISMATCH", retry_safe=False
                    )
        except (OSError, ExplorerWorkerError):
            return self._failure(
                action,
                started,
                "EXPLORER_OPEN_STATE_UNKNOWN" if launch_attempted else "EXPLORER_PRECHECK_FAILED",
                retry_safe=not launch_attempted,
            )
        executable = self._inspector.executable_path(pid)
        creation_time = self._inspector.creation_time_100ns(pid)
        if (
            executable is None
            or creation_time is None
            or not self._explorer.matches_runtime_executable(executable)
        ):
            return self._failure(
                action, started, "EXPLORER_OPEN_STATE_UNKNOWN", retry_safe=False
            )
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(
                action, started, "STOP_REQUESTED_AFTER_INPUT", retry_safe=False
            )
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed={
                "app_id": self._explorer.app_id,
                "root_id": root_id,
                "directory_sha256": digest,
                "pid": pid,
                "hwnd": hwnd,
                "process_creation_time_100ns": creation_time,
            },
            duration_ms=self._elapsed_ms(started),
        )

    def _validate(self, action: Action) -> tuple[str, Path, str] | str:
        if action.capability_id != "explorer.folder.open":
            return "UNSUPPORTED_CAPABILITY"
        if set(action.arguments) != {"root_id", "relative_path"}:
            return "INVALID_ARGUMENTS"
        root_id = action.arguments.get("root_id")
        relative_path = action.arguments.get("relative_path")
        if not isinstance(root_id, str) or not isinstance(relative_path, str):
            return "INVALID_ARGUMENTS"
        directory = self._resolve_directory(root_id, relative_path)
        if directory is None:
            return "DIRECTORY_NOT_ALLOWED"
        digest = _directory_digest(directory)
        if action.target_identity != f"explorer-folder:{digest}":
            return "TARGET_IDENTITY_MISMATCH"
        if len(action.expected_postconditions) != 1:
            return "INVALID_POSTCONDITION"
        postcondition = action.expected_postconditions[0]
        if postcondition.predicate != "explorer.folder.verified" or postcondition.arguments:
            return "INVALID_POSTCONDITION"
        return root_id, directory, digest

    def _resolve_directory(self, root_id: str, relative_path: object) -> Path | None:
        if not isinstance(relative_path, str):
            return None
        return _resolve_from_trusted_roots(self._roots, root_id, relative_path)

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
    def _failure(
        cls,
        action: Action,
        started: int,
        error_code: str,
        *,
        retry_safe: bool,
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=False,
            error_code=error_code,
            error_message="Explorer folder open was not verified",
            retry_safe=retry_safe,
            duration_ms=cls._elapsed_ms(started),
        )


class ExplorerFolderVerifier:
    def __init__(
        self,
        executor: ExplorerFolderOpenExecutor,
        *,
        inspector: ProcessSessionInspector | None = None,
        window_resolver: ExplorerWindowResolver | None = None,
        folder_checker: ExplorerFolderChecker | None = None,
    ) -> None:
        self._executor = executor
        self._inspector = inspector or WindowsProcessInspector()
        if window_resolver is None:
            from .window_handoff import WindowsRuntimeWindowResolver

            window_resolver = WindowsRuntimeWindowResolver()
        self._window_resolver = window_resolver
        self._folder_checker = folder_checker or PollingExplorerFolderChecker()

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        if result.action_id != action.action_id:
            return self._failure("ACTION_ID_MISMATCH")
        if result.executor != "native" or not result.success:
            return self._failure(result.error_code or "EXECUTION_FAILED")
        validated = self._executor._validate(action)
        if isinstance(validated, str):
            return self._failure("EXPLORER_VERIFY_CONTRACT_FAILED")
        root_id, directory, digest = validated
        pid = result.observed.get("pid")
        hwnd = result.observed.get("hwnd")
        creation_time = result.observed.get("process_creation_time_100ns")
        if (
            type(pid) is not int
            or type(hwnd) is not int
            or type(creation_time) is not int
            or result.observed.get("app_id") != self._executor._explorer.app_id
            or result.observed.get("root_id") != root_id
            or result.observed.get("directory_sha256") != digest
            or set(result.observed)
            != {
                "app_id",
                "root_id",
                "directory_sha256",
                "pid",
                "hwnd",
                "process_creation_time_100ns",
            }
        ):
            return self._failure("EXPLORER_VERIFY_BINDING_FAILED")
        executable = self._inspector.executable_path(pid)
        if (
            executable is None
            or self._inspector.creation_time_100ns(pid) != creation_time
            or not self._executor._explorer.matches_runtime_executable(executable)
        ):
            return self._failure("EXPLORER_PROCESS_IDENTITY_FAILED")
        if (pid, hwnd) not in self._window_resolver.matching_windows(
            self._executor._explorer
        ):
            return self._failure("EXPLORER_WINDOW_IDENTITY_FAILED")
        try:
            if not self._folder_checker.matches(hwnd, directory):
                return self._failure("EXPLORER_FOLDER_IDENTITY_FAILED")
        except ExplorerWorkerError:
            return self._failure("EXPLORER_FOLDER_INSPECTION_FAILED")
        return VerificationResult(
            success=True,
            observed={"root_id": root_id, "directory_sha256": digest},
            reason="EXPLORER_FOLDER_VERIFIED",
        )

    @staticmethod
    def _failure(reason: str) -> VerificationResult:
        return VerificationResult(success=False, reason=reason, recoverable=False)


def _trusted_roots(roots: Mapping[str, Path]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for root_id, path in roots.items():
        simple_id = root_id.replace("-", "").replace("_", "").isalnum()
        if not root_id or root_id in result or not simple_id:
            raise ValueError("Explorer root identifiers must be unique simple names")
        if not path.is_absolute() or _is_link_or_reparse(path):
            raise ValueError("Explorer roots must be absolute non-reparse directories")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError("Explorer root is unavailable") from exc
        if not resolved.is_dir() or resolved in result.values():
            raise ValueError("Explorer roots must be unique directories")
        result[root_id] = resolved
    if not result:
        raise ValueError("at least one Explorer root is required")
    return result


def explorer_folder_target_identity(
    roots: Mapping[str, Path], root_id: str, relative_path: str
) -> str | None:
    """Return a privacy-preserving identity only for an allowed existing directory."""
    try:
        trusted = _trusted_roots(roots)
    except ValueError:
        return None
    directory = _resolve_from_trusted_roots(trusted, root_id, relative_path)
    if directory is None:
        return None
    return f"explorer-folder:{_directory_digest(directory)}"


def _resolve_from_trusted_roots(
    roots: Mapping[str, Path], root_id: str, relative_path: str
) -> Path | None:
    root = roots.get(root_id)
    if root is None:
        return None
    parts = _relative_parts(relative_path)
    if parts is None:
        return None
    candidate = root.joinpath(*parts)
    try:
        if any(_is_link_or_reparse(path) for path in _path_chain(root, candidate)):
            return None
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if not resolved.is_dir() or not resolved.is_relative_to(root):
        return None
    return resolved


def _relative_parts(value: str) -> tuple[str, ...] | None:
    if not 1 <= len(value) <= 1024 or "\0" in value:
        return None
    path = PureWindowsPath(value)
    if path.is_absolute() or path.drive or path.root:
        return None
    parts = tuple(part for part in path.parts if part != ".")
    if any(part in {"", ".."} or ":" in part for part in parts):
        return None
    return parts


def _path_chain(root: Path, candidate: Path) -> tuple[Path, ...]:
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return (candidate,)
    current = root
    chain = [root]
    for part in relative.parts:
        current = current / part
        chain.append(current)
    return tuple(chain)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & 0x400)


def _directory_digest(path: Path) -> str:
    canonical = ntpath.normcase(str(path)).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
