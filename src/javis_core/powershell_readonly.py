from __future__ import annotations

import base64
import json
import os
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from time import perf_counter_ns
from typing import Protocol

from .cancellation import CancellationToken
from .models import Action
from .runtime_models import ExecutionResult


class ProcessRunner(Protocol):
    def run(
        self,
        arguments: Sequence[str],
        *,
        environment: Mapping[str, str],
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[bytes]: ...


class SubprocessRunner:
    def run(
        self,
        arguments: Sequence[str],
        *,
        environment: Mapping[str, str],
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(  # noqa: S603 - arguments are fixed and shell is never used
            list(arguments),
            env=dict(environment),
            capture_output=True,
            shell=False,
            timeout=timeout_seconds,
            check=False,
        )


_UTF8_SETUP = (
    "$ErrorActionPreference='Stop';"
    "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
)
_SYSTEM_INFO_SCRIPT = _UTF8_SETUP + (
    "$os=Get-CimInstance -ClassName Win32_OperatingSystem;"
    "[pscustomobject]@{system='Windows';release=$os.Caption;"
    "version=$os.Version;machine=$env:PROCESSOR_ARCHITECTURE;hostname=$env:COMPUTERNAME}"
    "|ConvertTo-Json -Compress"
)
_FILE_LIST_SCRIPT = _UTF8_SETUP + (
    "$limit=[int]$env:JAVIS_MAX_ENTRIES;"
    "$items=@(Get-ChildItem -LiteralPath $env:JAVIS_TARGET_PATH -Force -Name"
    "|Sort-Object|Select-Object -First ($limit+1));"
    "$shown=@($items|Select-Object -First $limit);"
    "[pscustomobject]@{entries=$shown;count=$shown.Count;truncated=($items.Count -gt $limit)}"
    "|ConvertTo-Json -Compress -Depth 3"
)


class PowerShellReadOnlyExecutor:
    """Fallback adapter using only internal, fixed, read-only PowerShell scripts."""

    name = "powershell"

    def __init__(
        self,
        allowed_roots: Iterable[Path],
        *,
        runner: ProcessRunner | None = None,
        executable: str = "powershell.exe",
        timeout_seconds: float = 10.0,
        max_entries: int = 500,
        max_output_bytes: int = 1_000_000,
    ) -> None:
        if not 1 <= max_entries <= 10_000:
            raise ValueError("max_entries must be between 1 and 10000")
        if timeout_seconds <= 0 or max_output_bytes < 1024:
            raise ValueError("invalid PowerShell resource limits")
        self._roots = tuple(root.resolve(strict=True) for root in allowed_roots)
        if any(not root.is_dir() for root in self._roots):
            raise ValueError("allowed roots must be existing directories")
        self._runner = runner or SubprocessRunner()
        self._executable = executable
        self._timeout = timeout_seconds
        self._max_entries = max_entries
        self._max_output = max_output_bytes

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")

        environment = self._minimal_environment()
        if action.capability_id == "system.info.read":
            script = _SYSTEM_INFO_SCRIPT
        elif action.capability_id == "file.list":
            candidate = self._authorized_path(action)
            if isinstance(candidate, str):
                return self._failure(action, started, candidate)
            environment["JAVIS_TARGET_PATH"] = str(candidate)
            environment["JAVIS_MAX_ENTRIES"] = str(self._max_entries)
            script = _FILE_LIST_SCRIPT
        else:
            return self._failure(action, started, "UNSUPPORTED_CAPABILITY")

        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        arguments = (
            self._executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Restricted",
            "-EncodedCommand",
            encoded,
        )
        try:
            completed = self._runner.run(
                arguments,
                environment=environment,
                timeout_seconds=self._timeout,
            )
        except subprocess.TimeoutExpired:
            return self._failure(action, started, "POWERSHELL_TIMEOUT")
        except OSError:
            return self._failure(action, started, "POWERSHELL_UNAVAILABLE")
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")
        if completed.returncode != 0:
            return self._failure(action, started, "POWERSHELL_FAILED")
        if len(completed.stdout) > self._max_output:
            return self._failure(action, started, "OUTPUT_LIMIT_EXCEEDED")
        try:
            observed = json.loads(completed.stdout.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._failure(action, started, "INVALID_POWERSHELL_OUTPUT")
        if not isinstance(observed, dict):
            return self._failure(action, started, "INVALID_POWERSHELL_OUTPUT")
        return ExecutionResult(
            action_id=action.action_id,
            executor="powershell",
            success=True,
            observed=observed,
            duration_ms=self._elapsed_ms(started),
        )

    def _authorized_path(self, action: Action) -> Path | str:
        raw_path = action.arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return "INVALID_ARGUMENTS"
        try:
            candidate = Path(raw_path).resolve(strict=True)
        except OSError:
            return "PATH_NOT_FOUND"
        if not candidate.is_dir():
            return "PATH_NOT_DIRECTORY"
        if not any(candidate.is_relative_to(root) for root in self._roots):
            return "PATH_OUTSIDE_ALLOWED_ROOTS"
        return candidate

    @staticmethod
    def _minimal_environment() -> dict[str, str]:
        allowed = ("SystemRoot", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP")
        return {key: os.environ[key] for key in allowed if key in os.environ}

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (perf_counter_ns() - started) // 1_000_000)

    @classmethod
    def _failure(cls, action: Action, started: int, error_code: str) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="powershell",
            success=False,
            error_code=error_code,
            error_message="Restricted PowerShell action was not completed",
            retry_safe=error_code != "STOP_REQUESTED",
            duration_ms=cls._elapsed_ms(started),
        )
