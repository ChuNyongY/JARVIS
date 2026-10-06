from __future__ import annotations

import os
from collections.abc import Mapping
from time import perf_counter_ns
from types import MappingProxyType
from typing import Protocol

from .cancellation import CancellationToken
from .models import Action
from .process_launch import RegisteredApp, RuntimeProcessResolver
from .runtime_handoff import WindowsRuntimeProcessResolver
from .runtime_models import ExecutionResult


class SettingsUriBackend(Protocol):
    def launch(self, uri: str) -> None: ...


class WindowsSettingsUriBackend:
    def launch(self, uri: str) -> None:
        if os.name != "nt" or not uri.startswith("ms-settings:"):
            raise OSError("Windows Settings URI activation is unavailable")
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise OSError("Windows Settings URI activation is unavailable")
        startfile(uri)


class SettingsOpenExecutor:
    """Open only fixed Settings URIs and bind the verified runtime PID."""

    name = "native"
    _PAGES: Mapping[str, str] = MappingProxyType(
        {
            "display": "ms-settings:display",
            "sound": "ms-settings:sound",
            "network": "ms-settings:network-status",
            "bluetooth": "ms-settings:bluetooth",
        }
    )

    def __init__(
        self,
        app: RegisteredApp,
        *,
        backend: SettingsUriBackend | None = None,
        runtime_resolver: RuntimeProcessResolver | None = None,
    ) -> None:
        if app.app_id != "settings" or app.direct_launch:
            raise ValueError("Settings app must be registered with direct launch disabled")
        self._app = app
        self._backend = backend or WindowsSettingsUriBackend()
        self._runtime_resolver = runtime_resolver or WindowsRuntimeProcessResolver()

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")
        if action.capability_id != "settings.open":
            return self._failure(action, started, "UNSUPPORTED_CAPABILITY")
        if set(action.arguments) != {"page_id"}:
            return self._failure(action, started, "INVALID_ARGUMENTS")
        page_id = action.arguments.get("page_id")
        if not isinstance(page_id, str) or page_id not in self._PAGES:
            return self._failure(action, started, "PAGE_NOT_ALLOWED")
        if action.target_identity != "settings":
            return self._failure(action, started, "TARGET_IDENTITY_MISMATCH")
        before = self._runtime_resolver.snapshot(self._app)
        if len(before) > 1:
            return self._failure(action, started, "RUNTIME_AMBIGUOUS")
        try:
            self._backend.launch(self._PAGES[page_id])
            pid = self._runtime_resolver.resolve(self._app, 0, before)
        except Exception:
            return self._failure(action, started, "SETTINGS_ACTIVATION_FAILED")
        if pid is None and len(before) == 1:
            after = self._runtime_resolver.snapshot(self._app)
            if after == before:
                pid = next(iter(before))
        if pid is None:
            return self._failure(action, started, "SETTINGS_RUNTIME_NOT_FOUND")
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed={"app_id": "settings", "page_id": page_id, "pid": pid},
            duration_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (perf_counter_ns() - started) // 1_000_000)

    @classmethod
    def _failure(cls, action: Action, started: int, code: str) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=False,
            error_code=code,
            error_message="Windows Settings was not safely activated",
            retry_safe=False,
            duration_ms=cls._elapsed_ms(started),
        )
