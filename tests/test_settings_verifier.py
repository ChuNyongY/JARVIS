from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from javis_core import (
    Action,
    ExecutionResult,
    RegisteredApp,
    SettingsPageVerifier,
    UiaElementSnapshot,
    UiaObservationError,
    UiaSelector,
)


class Inspector:
    def __init__(self, path: Path | None) -> None:
        self.path = path

    def executable_path(self, pid: int) -> Path | None:
        return self.path


class Observer:
    def __init__(
        self, *, visible: bool = True, fail: bool = False, failures: int = 0
    ) -> None:
        self.visible = visible
        self.fail = fail
        self.failures = failures
        self.selectors: list[UiaSelector] = []

    def find_unique(self, selector: UiaSelector) -> UiaElementSnapshot:
        self.selectors.append(selector)
        if self.fail or len(self.selectors) <= self.failures:
            raise UiaObservationError("private UIA detail")
        return UiaElementSnapshot(
            process_id=selector.process_id,
            runtime_id=(1, 2, 3),
            automation_id=selector.automation_id or "",
            control_type=selector.control_type or "",
            name="localized private label",
            class_name=selector.class_name or "",
            enabled=True,
            visible=self.visible,
        )


def app(tmp_path: Path) -> RegisteredApp:
    executable = tmp_path / "SystemSettings.exe"
    executable.write_bytes(b"settings")
    return RegisteredApp.from_path("settings", executable).model_copy(
        update={"direct_launch": False}
    )


def action(page_id: str = "display") -> Action:
    return Action(
        capability_id="settings.open",
        arguments={"page_id": page_id},
        purpose="open a fixed settings page",
        target_identity="settings",
    )


def execution(candidate: Action, page_id: str = "display") -> ExecutionResult:
    return ExecutionResult(
        action_id=candidate.action_id,
        executor="native",
        success=True,
        observed={"app_id": "settings", "page_id": page_id, "pid": 73},
        duration_ms=1,
    )


@pytest.mark.parametrize(
    ("page_id", "automation_id"),
    [
        (
            "display",
            "SystemSettings_Display_BlueLight_AutomaticOnScheduleWithTime_ButtonEntityItem",
        ),
        ("sound", "SystemSettings_Audio_OutputCollectionHeader_EntityItem"),
        ("network", "NetworkHeroControlProfileLayout"),
        ("bluetooth", "SystemSettings_Device_Discovery_Button"),
    ],
)
def test_fixed_page_profile_is_verified_without_returning_ui_text(
    tmp_path: Path, page_id: str, automation_id: str
) -> None:
    registered = app(tmp_path)
    observer = Observer()
    verifier = SettingsPageVerifier(
        registered,
        observer=observer,
        inspector=Inspector(registered.runtime_path),
    )
    candidate = action(page_id)
    report = verifier.verify(candidate, execution(candidate, page_id))
    assert report.success
    assert report.reason == "SETTINGS_PAGE_VERIFIED"
    assert report.observed["page_profile_sha256"]
    assert "localized" not in report.model_dump_json()
    assert observer.selectors[0].automation_id == automation_id
    assert observer.selectors[0].process_id == 73


@pytest.mark.parametrize(
    "mutate",
    [
        lambda a, r: (a.model_copy(update={"action_id": uuid4()}), r),
        lambda a, r: (a.model_copy(update={"capability_id": "process.launch"}), r),
        lambda a, r: (a.model_copy(update={"target_identity": "other"}), r),
        lambda a, r: (a.model_copy(update={"arguments": {"page_id": "home"}}), r),
        lambda a, r: (
            a,
            r.model_copy(update={"observed": {**r.observed, "name": "private"}}),
        ),
        lambda a, r: (a, r.model_copy(update={"executor": "uia"})),
    ],
)
def test_contract_drift_fails_closed(tmp_path: Path, mutate: object) -> None:
    registered = app(tmp_path)
    candidate = action()
    result = execution(candidate)
    changed_action, changed_result = mutate(candidate, result)  # type: ignore[operator]
    report = SettingsPageVerifier(
        registered,
        observer=Observer(),
        inspector=Inspector(registered.runtime_path),
    ).verify(changed_action, changed_result)
    assert not report.success
    assert report.recoverable is False


def test_process_identity_and_page_observation_fail_closed(tmp_path: Path) -> None:
    registered = app(tmp_path)
    candidate = action()
    result = execution(candidate)
    wrong_process = SettingsPageVerifier(
        registered,
        observer=Observer(),
        inspector=Inspector(tmp_path / "other.exe"),
    ).verify(candidate, result)
    assert wrong_process.reason == "SETTINGS_PROCESS_IDENTITY_FAILED"

    observer_failure = SettingsPageVerifier(
        registered,
        observer=Observer(fail=True),
        inspector=Inspector(registered.runtime_path),
    ).verify(candidate, result)
    assert observer_failure.reason == "SETTINGS_PAGE_NOT_VERIFIED"
    assert "private" not in observer_failure.model_dump_json()
    assert observer_failure.recoverable


def test_hidden_page_anchor_is_not_accepted(tmp_path: Path) -> None:
    registered = app(tmp_path)
    candidate = action()
    report = SettingsPageVerifier(
        registered,
        observer=Observer(visible=False),
        inspector=Inspector(registered.runtime_path),
    ).verify(candidate, execution(candidate))
    assert not report.success
    assert report.reason == "SETTINGS_PAGE_NOT_VERIFIED"


def test_transient_observation_is_bounded_and_can_recover(tmp_path: Path) -> None:
    registered = app(tmp_path)
    candidate = action()
    observer = Observer(failures=2)
    sleeps: list[float] = []
    report = SettingsPageVerifier(
        registered,
        observer=observer,
        inspector=Inspector(registered.runtime_path),
        max_observation_attempts=3,
        poll_seconds=0.1,
        sleeper=sleeps.append,
    ).verify(candidate, execution(candidate))
    assert report.success
    assert len(observer.selectors) == 3
    assert sleeps == [0.1, 0.1]


def test_invalid_observation_timing_is_rejected(tmp_path: Path) -> None:
    registered = app(tmp_path)
    with pytest.raises(ValueError, match="timing"):
        SettingsPageVerifier(registered, max_observation_attempts=6)
