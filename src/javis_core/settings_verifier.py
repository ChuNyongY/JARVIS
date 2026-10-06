from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Protocol

from .models import Action, Postcondition, VerificationResult
from .process_launch import RegisteredApp
from .process_verifier import ProcessExistsPredicate, ProcessInspector
from .runtime_models import ExecutionResult
from .uia_ipc import SubprocessUiaBackend, UiaWorkerRunner
from .uia_observation import UiaElementSnapshot, UiaObserver, UiaSelector


class SettingsStructureObserver(Protocol):
    def find_unique(self, selector: UiaSelector) -> UiaElementSnapshot: ...


_PAGE_IDENTITIES: Mapping[str, tuple[str, str, str]] = MappingProxyType(
    {
        "display": (
            "SystemSettings_Display_BlueLight_AutomaticOnScheduleWithTime_ButtonEntityItem",
            "Group",
            "NamedContainerAutomationPeer",
        ),
        "sound": (
            "SystemSettings_Audio_OutputCollectionHeader_EntityItem",
            "Group",
            "NamedContainerAutomationPeer",
        ),
        "network": (
            "NetworkHeroControlProfileLayout",
            "Group",
            "NamedContainerAutomationPeer",
        ),
        "bluetooth": (
            "SystemSettings_Device_Discovery_Button",
            "Button",
            "Button",
        ),
    }
)


class SettingsPageVerifier:
    """Verify a fixed Settings page without trusting executor-reported success."""

    def __init__(
        self,
        app: RegisteredApp,
        *,
        observer: SettingsStructureObserver | None = None,
        inspector: ProcessInspector | None = None,
        max_observation_attempts: int = 3,
        poll_seconds: float = 0.5,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if app.app_id != "settings" or app.direct_launch:
            raise ValueError("Settings app must be registered with direct launch disabled")
        if not 1 <= max_observation_attempts <= 5 or not 0 <= poll_seconds <= 1:
            raise ValueError("invalid Settings observation timing")
        self._process_exists = ProcessExistsPredicate(
            {app.app_id: app},
            inspector=inspector,
        )
        self._observer = observer or UiaObserver(
            SubprocessUiaBackend(UiaWorkerRunner(timeout_seconds=3.0))
        )
        self._max_observation_attempts = max_observation_attempts
        self._poll_seconds = poll_seconds
        self._sleeper = sleeper

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        if result.action_id != action.action_id:
            return self._failure("ACTION_ID_MISMATCH", recoverable=False)
        if not result.success:
            return self._failure(
                result.error_code or "EXECUTION_FAILED",
                recoverable=result.retry_safe,
            )
        page_id = action.arguments.get("page_id")
        identity = _PAGE_IDENTITIES.get(page_id) if isinstance(page_id, str) else None
        if (
            action.capability_id != "settings.open"
            or action.target_identity != "settings"
            or set(action.arguments) != {"page_id"}
            or identity is None
            or result.executor != "native"
            or set(result.observed) != {"app_id", "page_id", "pid"}
            or result.observed.get("app_id") != "settings"
            or result.observed.get("page_id") != page_id
            or type(result.observed.get("pid")) is not int
        ):
            return self._failure("SETTINGS_VERIFICATION_CONTRACT_INVALID", recoverable=False)
        if not self._process_exists(Postcondition(predicate="process.exists"), result):
            return self._failure("SETTINGS_PROCESS_IDENTITY_FAILED", recoverable=False)

        assert isinstance(page_id, str)
        pid = result.observed["pid"]
        assert isinstance(pid, int)
        automation_id, control_type, class_name = identity
        selector = UiaSelector(
            process_id=pid,
            automation_id=automation_id,
            control_type=control_type,
            class_name=class_name,
        )
        verified = False
        for attempt in range(self._max_observation_attempts):
            try:
                verified = self._observer.find_unique(selector).visible
            except Exception:
                verified = False
            if verified:
                break
            if attempt + 1 < self._max_observation_attempts and self._poll_seconds:
                self._sleeper(self._poll_seconds)
        if not verified:
            return self._failure("SETTINGS_PAGE_NOT_VERIFIED", recoverable=True)
        return VerificationResult(
            success=True,
            observed={
                "app_id": "settings",
                "page_id": page_id,
                "pid": pid,
                "page_profile_sha256": _profile_sha256(page_id, identity),
            },
            reason="SETTINGS_PAGE_VERIFIED",
        )

    @staticmethod
    def _failure(reason: str, *, recoverable: bool) -> VerificationResult:
        return VerificationResult(success=False, reason=reason, recoverable=recoverable)


def _profile_sha256(page_id: str, identity: tuple[str, str, str]) -> str:
    payload = json.dumps(
        [page_id, *identity],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()
