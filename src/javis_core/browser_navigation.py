from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from contextlib import AbstractContextManager
from pathlib import Path
from time import perf_counter_ns
from typing import Protocol
from urllib.parse import SplitResult, urlsplit

from .cancellation import CancellationToken
from .models import Action, VerificationResult
from .process_launch import RegisteredApp
from .process_sessions import ProcessSessionInspector
from .process_verifier import WindowsProcessInspector
from .runtime_models import ExecutionResult

_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


class BrowserLauncher(Protocol):
    def launch(
        self,
        executable: Path,
        url: str,
        *,
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> int: ...


class BrowserWindowResolver(Protocol):
    def snapshot(self, app: RegisteredApp) -> frozenset[tuple[int, int]]: ...

    def resolve(
        self,
        app: RegisteredApp,
        launcher_pid: int,
        previous_windows: frozenset[tuple[int, int]],
    ) -> tuple[int, int] | None: ...

    def matching_windows(self, app: RegisteredApp) -> set[tuple[int, int]]: ...


class BrowserLease(Protocol):
    def hold(self, app: RegisteredApp) -> AbstractContextManager[bool]: ...


class SubprocessBrowserLauncher:
    def launch(
        self,
        executable: Path,
        url: str,
        *,
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> int:
        process = subprocess.Popen(  # noqa: S603 - executable and URL are prevalidated
            [str(executable), "--new-window", url],
            cwd=working_directory,
            env=dict(environment),
            shell=False,
            close_fds=True,
        )
        return process.pid


class BrowserNavigateExecutor:
    """Open one HTTPS URL in one newly identified registered browser window."""

    name = "native"

    def __init__(
        self,
        browsers: Mapping[str, RegisteredApp],
        allowed_hosts: Iterable[str],
        *,
        launcher: BrowserLauncher | None = None,
        lease: BrowserLease | None = None,
        inspector: ProcessSessionInspector | None = None,
        window_resolver: BrowserWindowResolver | None = None,
    ) -> None:
        self._browsers = dict(browsers)
        if not self._browsers or any(
            key != app.app_id
            or not app.direct_launch
            or not app.single_instance_handoff
            or app.window_class is None
            for key, app in self._browsers.items()
        ):
            raise ValueError("browser mapping must contain handoff-capable registered apps")
        hosts = tuple(allowed_hosts)
        if (
            not hosts
            or len(hosts) != len(set(hosts))
            or any(_validate_allowed_host(host) is None for host in hosts)
        ):
            raise ValueError("allowed browser hosts must be unique canonical DNS names")
        self._allowed_hosts = frozenset(hosts)
        self._launcher = launcher or SubprocessBrowserLauncher()
        if lease is None:
            from .executable_lease import default_executable_lease

            lease = default_executable_lease()
        self._lease = lease
        self._inspector = inspector or WindowsProcessInspector()
        if window_resolver is None:
            from .window_handoff import WindowsRuntimeWindowResolver

            window_resolver = WindowsRuntimeWindowResolver()
        self._window_resolver = window_resolver

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        validated = self._validate(action)
        if isinstance(validated, str):
            return self._failure(action, started, validated, retry_safe=True)
        url, parsed, browser = validated
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=True)
        try:
            previous_windows = self._window_resolver.snapshot(browser)
        except OSError:
            return self._failure(action, started, "BROWSER_PRECHECK_FAILED", retry_safe=True)
        launch_attempted = False
        try:
            with self._lease.hold(browser) as verified:
                if not verified:
                    return self._failure(
                        action, started, "BROWSER_IDENTITY_CHANGED", retry_safe=True
                    )
                if cancellation is not None and cancellation.is_cancelled:
                    return self._failure(action, started, "STOP_REQUESTED", retry_safe=True)
                launch_attempted = True
                launcher_pid = self._launcher.launch(
                    browser.executable,
                    url,
                    working_directory=browser.executable.parent,
                    environment=self._minimal_environment(),
                )
                identity = self._window_resolver.resolve(
                    browser, launcher_pid, previous_windows
                )
                if identity is None:
                    return self._failure(
                        action, started, "BROWSER_WINDOW_HANDOFF_FAILED", retry_safe=False
                    )
                pid, hwnd = identity
        except OSError:
            return self._failure(
                action,
                started,
                "BROWSER_NAVIGATION_STATE_UNKNOWN"
                if launch_attempted
                else "BROWSER_PRECHECK_FAILED",
                retry_safe=not launch_attempted,
            )
        executable = self._inspector.executable_path(pid)
        creation_time = self._inspector.creation_time_100ns(pid)
        if (
            executable is None
            or creation_time is None
            or not browser.matches_runtime_executable(executable)
        ):
            return self._failure(
                action, started, "BROWSER_NAVIGATION_STATE_UNKNOWN", retry_safe=False
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
                "app_id": browser.app_id,
                "host": parsed.hostname or "",
                "hwnd": hwnd,
                "pid": pid,
                "process_creation_time_100ns": creation_time,
                "scheme": "https",
                "url_sha256": _url_hash(url),
            },
            duration_ms=self._elapsed_ms(started),
        )

    def _validate(self, action: Action) -> tuple[str, SplitResult, RegisteredApp] | str:
        if action.capability_id != "browser.navigate":
            return "UNSUPPORTED_CAPABILITY"
        if set(action.arguments) != {"browser_id", "url"}:
            return "INVALID_ARGUMENTS"
        browser_id = action.arguments.get("browser_id")
        url = action.arguments.get("url")
        if not isinstance(browser_id, str) or not isinstance(url, str):
            return "INVALID_ARGUMENTS"
        browser = self._browsers.get(browser_id)
        if browser is None:
            return "BROWSER_NOT_REGISTERED"
        parsed = _validate_url(url, self._allowed_hosts)
        if parsed is None:
            return "URL_NOT_ALLOWED"
        if action.target_identity != f"browser:{browser_id}:{parsed.hostname}":
            return "TARGET_IDENTITY_MISMATCH"
        if len(action.expected_postconditions) != 1:
            return "INVALID_POSTCONDITION"
        postcondition = action.expected_postconditions[0]
        if postcondition.predicate != "browser.navigation.verified" or postcondition.arguments:
            return "INVALID_POSTCONDITION"
        return url, parsed, browser

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
            error_message="Registered browser navigation was not verified",
            retry_safe=retry_safe,
            duration_ms=cls._elapsed_ms(started),
        )


class BrowserNavigationVerifier:
    def __init__(
        self,
        executor: BrowserNavigateExecutor,
        *,
        inspector: ProcessSessionInspector | None = None,
        window_resolver: BrowserWindowResolver | None = None,
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
            return self._failure("BROWSER_VERIFY_CONTRACT_FAILED")
        url, parsed, browser = validated
        pid = result.observed.get("pid")
        hwnd = result.observed.get("hwnd")
        creation_time = result.observed.get("process_creation_time_100ns")
        digest = _url_hash(url)
        if (
            type(pid) is not int
            or type(hwnd) is not int
            or type(creation_time) is not int
            or result.observed.get("app_id") != browser.app_id
            or result.observed.get("host") != parsed.hostname
            or result.observed.get("scheme") != "https"
            or result.observed.get("url_sha256") != digest
        ):
            return self._failure("BROWSER_VERIFY_BINDING_FAILED")
        executable = self._inspector.executable_path(pid)
        if (
            executable is None
            or self._inspector.creation_time_100ns(pid) != creation_time
            or not browser.matches_runtime_executable(executable)
        ):
            return self._failure("BROWSER_PROCESS_IDENTITY_FAILED")
        if (pid, hwnd) not in self._window_resolver.matching_windows(browser):
            return self._failure("BROWSER_WINDOW_IDENTITY_FAILED")
        return VerificationResult(
            success=True,
            observed={
                "app_id": browser.app_id,
                "host": parsed.hostname or "",
                "url_sha256": digest,
            },
            reason="BROWSER_NAVIGATION_VERIFIED",
        )

    @staticmethod
    def _failure(reason: str) -> VerificationResult:
        return VerificationResult(success=False, reason=reason, recoverable=False)


def _validate_allowed_host(host: str) -> str | None:
    if (
        not isinstance(host, str)
        or not 1 <= len(host) <= 253
        or host != host.casefold()
        or not host.isascii()
        or not _HOST.fullmatch(host)
        or ".." in host
        or "." not in host
    ):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return None


def _validate_url(url: str, allowed_hosts: frozenset[str]) -> SplitResult | None:
    if (
        not 1 <= len(url) <= 2_048
        or not url.isascii()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in url)
        or "\\" in url
    ):
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
        or host is None
        or _validate_allowed_host(host) is None
        or host not in allowed_hosts
        or parsed.netloc.casefold() not in {host, f"{host}:443"}
    ):
        return None
    return parsed


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("ascii")).hexdigest()
