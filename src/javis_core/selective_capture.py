from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from threading import Lock
from time import monotonic
from typing import Protocol
from uuid import UUID

from .models import Action
from .runtime_models import ExecutionResult


class CapturePurpose(StrEnum):
    GROUNDING = "GROUNDING"
    PRE_INPUT = "PRE_INPUT"
    VERIFICATION = "VERIFICATION"


class SelectiveCaptureAuthorizer(Protocol):
    def consume(self, action_id: UUID, process_id: int, purpose: CapturePurpose) -> bool: ...

    def close(self, action_id: UUID) -> None: ...


@dataclass(slots=True)
class _CaptureAuthorization:
    process_id: int
    issued_at: float
    expires_at: float
    remaining: set[CapturePurpose]


class SelectiveCaptureGate:
    """Issue three one-shot captures only after a bound, pre-input UIA failure."""

    _ALLOWED_UIA_ERRORS = frozenset(
        {"UNSUPPORTED_CAPABILITY", "ADAPTER_UNAVAILABLE", "UIA_GROUNDING_REQUIRED"}
    )

    def __init__(
        self,
        process_authorized: Callable[[int], bool],
        *,
        authorization_seconds: float = 30.0,
        max_active_actions: int = 100,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if (
            isinstance(authorization_seconds, bool)
            or not 1.0 <= authorization_seconds <= 300.0
            or type(max_active_actions) is not int
            or not 1 <= max_active_actions <= 1_000
        ):
            raise ValueError("invalid selective capture gate configuration")
        self._process_authorized = process_authorized
        self._authorization_seconds = authorization_seconds
        self._max_active_actions = max_active_actions
        self._clock = clock
        self._lock = Lock()
        self._authorizations: dict[UUID, _CaptureAuthorization] = {}

    def authorize_uia_fallback(self, action: Action, result: ExecutionResult) -> bool:
        process_id = action.arguments.get("process_id")
        if (
            result.action_id != action.action_id
            or result.success
            or result.executor != "uia"
            or result.error_code not in self._ALLOWED_UIA_ERRORS
            or result.retry_safe is not True
            or set(action.arguments) != {"process_id"}
            or type(process_id) is not int
            or process_id < 1
            or action.target_identity != f"process:{process_id}"
            or not self._is_process_authorized(process_id)
        ):
            return False
        now = self._safe_now()
        if now is None:
            return False
        with self._lock:
            self._purge_locked(now)
            if (
                action.action_id in self._authorizations
                or len(self._authorizations) >= self._max_active_actions
            ):
                return False
            self._authorizations[action.action_id] = _CaptureAuthorization(
                process_id=process_id,
                issued_at=now,
                expires_at=now + self._authorization_seconds,
                remaining=set(CapturePurpose),
            )
        return True

    def consume(self, action_id: UUID, process_id: int, purpose: CapturePurpose) -> bool:
        if type(process_id) is not int or process_id < 1 or not isinstance(
            purpose, CapturePurpose
        ):
            return False
        now = self._safe_now()
        if now is None or not self._is_process_authorized(process_id):
            return False
        with self._lock:
            self._purge_locked(now)
            authorization = self._authorizations.get(action_id)
            if (
                authorization is None
                or authorization.process_id != process_id
                or now < authorization.issued_at
                or purpose not in authorization.remaining
            ):
                return False
            authorization.remaining.remove(purpose)
        return True

    def close(self, action_id: UUID) -> None:
        with self._lock:
            self._authorizations.pop(action_id, None)

    def _is_process_authorized(self, process_id: int) -> bool:
        try:
            return self._process_authorized(process_id) is True
        except Exception:
            return False

    def _safe_now(self) -> float | None:
        try:
            now = float(self._clock())
        except Exception:
            return None
        return now if isfinite(now) and now >= 0.0 else None

    def _purge_locked(self, now: float) -> None:
        expired = [
            action_id
            for action_id, authorization in self._authorizations.items()
            if authorization.expires_at <= now
        ]
        for action_id in expired:
            self._authorizations.pop(action_id, None)
