from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from time import perf_counter_ns
from typing import Protocol

from .cancellation import CancellationToken
from .models import Action, VerificationResult
from .process_launch import RegisteredApp
from .process_sessions import ProcessSessionInspector
from .process_verifier import WindowsProcessInspector
from .runtime_models import ExecutionResult

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PdfLauncher(Protocol):
    def launch(
        self,
        viewer: Path,
        document: Path,
        *,
        working_directory: Path,
        environment: Mapping[str, str],
        new_window: bool,
    ) -> int: ...


class PdfWindowResolver(Protocol):
    def snapshot(self, app: RegisteredApp) -> frozenset[tuple[int, int]]: ...

    def resolve(
        self,
        app: RegisteredApp,
        launcher_pid: int,
        previous_windows: frozenset[tuple[int, int]],
    ) -> tuple[int, int] | None: ...

    def matching_windows(self, app: RegisteredApp) -> set[tuple[int, int]]: ...


class PdfLease(Protocol):
    def hold(self, item: RegisteredApp) -> AbstractContextManager[bool]: ...


class SubprocessPdfLauncher:
    def launch(
        self,
        viewer: Path,
        document: Path,
        *,
        working_directory: Path,
        environment: Mapping[str, str],
        new_window: bool,
    ) -> int:
        command = [str(viewer)]
        if new_window:
            command.append("--new-window")
        command.append(str(document))
        process = subprocess.Popen(  # noqa: S603 - viewer and document are prevalidated
            command,
            cwd=working_directory,
            env=dict(environment),
            shell=False,
            close_fds=True,
        )
        return process.pid


class RegisteredPdfOpenExecutor:
    name = "native"

    def __init__(
        self,
        viewers: Mapping[str, RegisteredApp],
        allowed_roots: Iterable[Path],
        *,
        launcher: PdfLauncher | None = None,
        lease: PdfLease | None = None,
        inspector: ProcessSessionInspector | None = None,
        window_resolver: PdfWindowResolver | None = None,
        max_file_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self._viewers = dict(viewers)
        if not self._viewers or any(key != app.app_id for key, app in self._viewers.items()):
            raise ValueError("viewer mapping must be non-empty and keyed by app_id")
        if any(not app.direct_launch for app in self._viewers.values()):
            raise ValueError("PDF viewers must allow direct launch")
        root_inputs = tuple(allowed_roots)
        if any(root.is_symlink() for root in root_inputs):
            raise ValueError("allowed_roots cannot contain symlinks")
        roots = tuple(root.resolve(strict=True) for root in root_inputs)
        if not roots or any(not root.is_dir() for root in roots):
            raise ValueError("allowed_roots must contain existing directories")
        if not 1 <= max_file_bytes <= 2 * 1024 * 1024 * 1024:
            raise ValueError("max_file_bytes is outside the allowed range")
        if lease is None:
            from .executable_lease import default_executable_lease

            lease = default_executable_lease()
        self._roots = roots
        self._launcher = launcher or SubprocessPdfLauncher()
        self._lease = lease
        self._inspector = inspector or WindowsProcessInspector()
        if window_resolver is None:
            from .window_handoff import WindowsRuntimeWindowResolver

            window_resolver = WindowsRuntimeWindowResolver()
        self._window_resolver = window_resolver
        self._max_file_bytes = max_file_bytes

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        validated = self._validate(action)
        if isinstance(validated, str):
            return self._failure(action, started, validated, retry_safe=True)
        document, digest, viewer = validated
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=True)

        document_identity = RegisteredApp(
            app_id="pdf-content",
            executable=document,
            sha256=digest,
        )
        new_window = viewer.document_launch_mode == "browser-new-window"
        previous_windows: frozenset[tuple[int, int]] = frozenset()
        if new_window:
            try:
                previous_windows = self._window_resolver.snapshot(viewer)
            except OSError:
                return self._failure(
                    action,
                    started,
                    "PDF_OPEN_PRECHECK_FAILED",
                    retry_safe=True,
                )
        launch_attempted = False
        hwnd: int | None = None
        try:
            with self._lease.hold(viewer) as viewer_verified:
                if not viewer_verified:
                    return self._failure(
                        action,
                        started,
                        "VIEWER_IDENTITY_CHANGED",
                        retry_safe=True,
                    )
                with self._lease.hold(document_identity) as document_verified:
                    if not document_verified:
                        return self._failure(
                            action,
                            started,
                            "PDF_IDENTITY_CHANGED",
                            retry_safe=True,
                        )
                    if cancellation is not None and cancellation.is_cancelled:
                        return self._failure(
                            action,
                            started,
                            "STOP_REQUESTED",
                            retry_safe=True,
                        )
                    launch_attempted = True
                    pid = self._launcher.launch(
                        viewer.executable,
                        document,
                        working_directory=viewer.executable.parent,
                        environment=self._minimal_environment(),
                        new_window=new_window,
                    )
                    if new_window:
                        window_identity = self._window_resolver.resolve(
                            viewer,
                            pid,
                            previous_windows,
                        )
                        if window_identity is None:
                            return self._failure(
                                action,
                                started,
                                "PDF_WINDOW_HANDOFF_FAILED",
                                retry_safe=False,
                            )
                        pid, hwnd = window_identity
        except OSError:
            return self._failure(
                action,
                started,
                "PDF_OPEN_STATE_UNKNOWN" if launch_attempted else "PDF_OPEN_PRECHECK_FAILED",
                retry_safe=not launch_attempted,
            )
        if type(pid) is not int or pid <= 0:
            return self._failure(
                action,
                started,
                "PDF_OPEN_STATE_UNKNOWN",
                retry_safe=False,
            )
        executable = self._inspector.executable_path(pid)
        creation_time = self._inspector.creation_time_100ns(pid)
        if (
            executable is None
            or creation_time is None
            or not viewer.matches_runtime_executable(executable)
        ):
            return self._failure(
                action,
                started,
                "PDF_OPEN_STATE_UNKNOWN",
                retry_safe=False,
            )
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(
                action,
                started,
                "STOP_REQUESTED_AFTER_INPUT",
                retry_safe=False,
            )
        observed: dict[str, str | int] = {
            "file_sha256": digest,
            "pid": pid,
            "process_creation_time_100ns": creation_time,
            "viewer_app_id": viewer.app_id,
        }
        if hwnd is not None:
            observed["hwnd"] = hwnd
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed=observed,
            duration_ms=self._elapsed_ms(started),
        )

    def _validate(self, action: Action) -> tuple[Path, str, RegisteredApp] | str:
        if action.capability_id != "file.open":
            return "UNSUPPORTED_CAPABILITY"
        if set(action.arguments) != {"path", "sha256", "viewer_app_id"}:
            return "INVALID_ARGUMENTS"
        raw_path = action.arguments.get("path")
        digest = action.arguments.get("sha256")
        viewer_app_id = action.arguments.get("viewer_app_id")
        if (
            not isinstance(raw_path, str)
            or len(raw_path) > 32_767
            or not isinstance(digest, str)
            or not isinstance(viewer_app_id, str)
            or not _SHA256.fullmatch(digest)
        ):
            return "INVALID_ARGUMENTS"
        viewer = self._viewers.get(viewer_app_id)
        if viewer is None:
            return "VIEWER_NOT_REGISTERED"
        candidate = Path(raw_path)
        if not candidate.is_absolute() or candidate.is_symlink():
            return "PDF_PATH_NOT_ALLOWED"
        try:
            document = candidate.resolve(strict=True)
            size = document.stat().st_size
        except OSError:
            return "PDF_NOT_FOUND"
        if (
            not document.is_file()
            or document.suffix.casefold() != ".pdf"
            or not any(document.is_relative_to(root) for root in self._roots)
        ):
            return "PDF_PATH_NOT_ALLOWED"
        if not 1 <= size <= self._max_file_bytes:
            return "PDF_SIZE_NOT_ALLOWED"
        if action.target_identity != f"file-sha256:{digest}":
            return "TARGET_IDENTITY_MISMATCH"
        try:
            hash_matches = _file_hash(document) == digest
        except OSError:
            return "PDF_NOT_READABLE"
        if not hash_matches:
            return "PDF_HASH_MISMATCH"
        if len(action.expected_postconditions) != 1:
            return "INVALID_POSTCONDITION"
        postcondition = action.expected_postconditions[0]
        if postcondition.predicate != "file.opened" or postcondition.arguments:
            return "INVALID_POSTCONDITION"
        return document, digest, viewer

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
            error_message="Registered PDF was not opened",
            retry_safe=retry_safe,
            duration_ms=cls._elapsed_ms(started),
        )


class PdfOpenedVerifier:
    def __init__(
        self,
        executor: RegisteredPdfOpenExecutor,
        *,
        inspector: ProcessSessionInspector | None = None,
        window_resolver: PdfWindowResolver | None = None,
    ) -> None:
        self._executor = executor
        self._inspector = inspector or WindowsProcessInspector()
        if window_resolver is None:
            from .window_handoff import WindowsRuntimeWindowResolver

            window_resolver = WindowsRuntimeWindowResolver()
        self._window_resolver = window_resolver

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        if result.action_id != action.action_id:
            return self._failure("ACTION_ID_MISMATCH")
        if result.executor != "native":
            return self._failure("EXECUTOR_MISMATCH")
        if not result.success:
            return self._failure(result.error_code or "EXECUTION_FAILED")
        validated = self._executor._validate(action)
        if isinstance(validated, str):
            return self._failure("PDF_VERIFY_CONTRACT_FAILED")
        document, digest, viewer = validated
        pid = result.observed.get("pid")
        creation_time = result.observed.get("process_creation_time_100ns")
        if (
            type(pid) is not int
            or type(creation_time) is not int
            or result.observed.get("file_sha256") != digest
            or result.observed.get("viewer_app_id") != viewer.app_id
        ):
            return self._failure("PDF_VERIFY_BINDING_FAILED")
        executable = self._inspector.executable_path(pid)
        observed_creation_time = self._inspector.creation_time_100ns(pid)
        if (
            executable is None
            or observed_creation_time != creation_time
            or not viewer.matches_runtime_executable(executable)
        ):
            return self._failure("PDF_VIEWER_IDENTITY_FAILED")
        if viewer.document_launch_mode == "browser-new-window":
            hwnd = result.observed.get("hwnd")
            if type(hwnd) is not int or (pid, hwnd) not in self._window_resolver.matching_windows(
                viewer
            ):
                return self._failure("PDF_WINDOW_IDENTITY_FAILED")
        try:
            hash_matches = _file_hash(document) == digest
        except OSError:
            return self._failure("PDF_VERIFY_HASH_FAILED")
        if not hash_matches:
            return self._failure("PDF_VERIFY_HASH_FAILED")
        return VerificationResult(
            success=True,
            observed={"file_sha256": digest, "viewer_app_id": viewer.app_id},
            reason="PDF_OPEN_VERIFIED",
        )

    @staticmethod
    def _failure(reason: str) -> VerificationResult:
        return VerificationResult(success=False, reason=reason, recoverable=False)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
