from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field

from .models import FrozenModel
from .worker_process import EXPLORER_WORKER_TOKEN, fixed_worker_command


class ExplorerWorkerError(RuntimeError):
    pass


class ExplorerFolderRequest(FrozenModel):
    operation: Literal["folder_matches"] = "folder_matches"
    hwnd: int = Field(gt=0)
    expected_path: str = Field(min_length=1, max_length=32_767, repr=False)


class ExplorerFolderResponse(FrozenModel):
    success: bool
    operation: Literal["folder_matches"] = "folder_matches"
    matches: bool = False
    error_code: str | None = Field(default=None, max_length=64)


class ExplorerFolderChecker(Protocol):
    def matches(self, hwnd: int, expected_path: Path) -> bool: ...


ProcessRunner = Callable[..., subprocess.CompletedProcess[bytes]]


class SubprocessExplorerFolderChecker:
    _MAX_REQUEST_BYTES = 40 * 1024
    _MAX_RESPONSE_BYTES = 4 * 1024

    def __init__(
        self,
        *,
        timeout_seconds: float = 4.0,
        process_runner: ProcessRunner = subprocess.run,
    ) -> None:
        if not 0 < timeout_seconds <= 10:
            raise ValueError("Explorer worker timeout must be within 0..10 seconds")
        self._timeout = timeout_seconds
        self._process_runner = process_runner

    def matches(self, hwnd: int, expected_path: Path) -> bool:
        request = ExplorerFolderRequest(hwnd=hwnd, expected_path=str(expected_path))
        payload = request.model_dump_json().encode("utf-8")
        if len(payload) > self._MAX_REQUEST_BYTES:
            raise ExplorerWorkerError("Explorer worker request exceeded size limit")
        environment = {
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP")
            if key in os.environ
        }
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW
        try:
            completed = self._process_runner(
                fixed_worker_command(
                    "javis_core.explorer_folder_worker", EXPLORER_WORKER_TOKEN
                ),
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=self._timeout,
                check=False,
                shell=False,
                env=environment,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExplorerWorkerError("Explorer worker failed or timed out") from exc
        if completed.returncode != 0 or len(completed.stdout) > self._MAX_RESPONSE_BYTES:
            raise ExplorerWorkerError("Explorer worker returned an invalid response")
        try:
            response = ExplorerFolderResponse.model_validate_json(completed.stdout)
        except ValueError as exc:
            raise ExplorerWorkerError("Explorer worker returned malformed JSON") from exc
        if not response.success:
            raise ExplorerWorkerError(response.error_code or "EXPLORER_WORKER_FAILED")
        return response.matches


class PollingExplorerFolderChecker:
    """Absorb bounded Shell COM registration delay without weakening exact matching."""

    def __init__(
        self,
        checker: ExplorerFolderChecker | None = None,
        *,
        max_attempts: int = 3,
        poll_seconds: float = 0.25,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1 <= max_attempts <= 5 or not 0 <= poll_seconds <= 1:
            raise ValueError("invalid Explorer observation timing")
        self._checker = checker or SubprocessExplorerFolderChecker(timeout_seconds=3.0)
        self._max_attempts = max_attempts
        self._poll_seconds = poll_seconds
        self._sleeper = sleeper

    def matches(self, hwnd: int, expected_path: Path) -> bool:
        final_error: ExplorerWorkerError | None = None
        for attempt in range(self._max_attempts):
            try:
                if self._checker.matches(hwnd, expected_path):
                    return True
                final_error = None
            except ExplorerWorkerError as error:
                final_error = error
            if attempt + 1 < self._max_attempts and self._poll_seconds:
                self._sleeper(self._poll_seconds)
        if final_error is not None:
            raise ExplorerWorkerError("Explorer observation failed") from final_error
        return False


def parse_explorer_request(raw: bytes) -> ExplorerFolderRequest:
    if len(raw) > SubprocessExplorerFolderChecker._MAX_REQUEST_BYTES:
        raise ExplorerWorkerError("Explorer worker request exceeded size limit")
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        return ExplorerFolderRequest.model_validate(document)
    except ExplorerWorkerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ExplorerWorkerError("invalid Explorer worker request") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ExplorerWorkerError("duplicate Explorer worker JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ExplorerWorkerError(f"non-finite JSON number: {value}")
