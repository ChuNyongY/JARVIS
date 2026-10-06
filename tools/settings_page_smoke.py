from __future__ import annotations

import json
import time
from contextlib import suppress
from pathlib import Path

from javis_core import (
    Action,
    AppRegistryLoader,
    Postcondition,
    SettingsOpenExecutor,
    SettingsPageVerifier,
    WindowsRuntimeProcessResolver,
)


def _frame_windows(pid: int, *, visible_only: bool) -> tuple[int, ...]:
    import win32gui
    from pywinauto.findwindows import find_elements

    handles: set[int] = set()
    elements = find_elements(
        process=pid,
        control_type="Window",
        class_name="Windows.UI.Core.CoreWindow",
        top_level_only=False,
        visible_only=False,
        enabled_only=False,
        backend="uia",
    )
    for element in elements:
        parent = element.parent
        while parent is not None:
            handle = int(parent.handle or 0)
            if parent.class_name == "ApplicationFrameWindow" and handle:
                if win32gui.IsWindow(handle) and (
                    not visible_only or win32gui.IsWindowVisible(handle)
                ):
                    handles.add(handle)
                break
            parent = parent.parent
    return tuple(sorted(handles))


def main() -> int:
    import win32con
    import win32gui

    root = Path(__file__).resolve().parent.parent
    apps = AppRegistryLoader(require_publisher=True).load(root / "config" / "apps.json")
    app = apps["settings"]
    resolver = WindowsRuntimeProcessResolver(timeout_seconds=5.0)
    existing = resolver.snapshot(app)
    if len(existing) > 1:
        print(json.dumps({"status": "SKIPPED_AMBIGUOUS_RUNTIME"}))
        return 2
    if existing and _frame_windows(next(iter(existing)), visible_only=True):
        print(json.dumps({"status": "SKIPPED_EXISTING_VISIBLE_SETTINGS"}))
        return 2

    foreground = win32gui.GetForegroundWindow()
    executor = SettingsOpenExecutor(app, runtime_resolver=resolver)
    verifier = SettingsPageVerifier(app)
    observations: list[dict[str, object]] = []
    runtime_pid: int | None = None
    try:
        for page_id in ("display", "sound", "network", "bluetooth"):
            action = Action(
                capability_id="settings.open",
                arguments={"page_id": page_id},
                purpose="verify a fixed Windows Settings page",
                expected_postconditions=(Postcondition(predicate="process.exists"),),
                target_identity="settings",
            )
            result = executor.execute(action)
            time.sleep(0.5)
            if result.success and type(result.observed.get("pid")) is int:
                runtime_pid = result.observed["pid"]
                assert isinstance(runtime_pid, int)
            report = verifier.verify(action, result)
            observations.append(
                {
                    "page_id": page_id,
                    "execution_success": result.success,
                    "verification_success": report.success,
                    "reason": report.reason,
                    "page_profile_sha256": report.observed.get(
                        "page_profile_sha256"
                    ),
                }
            )
        passed = all(item["verification_success"] is True for item in observations)
        print(
            json.dumps(
                {"status": "PASS" if passed else "FAIL", "pages": observations},
                ensure_ascii=True,
            )
        )
        return 0 if passed else 1
    finally:
        if runtime_pid is not None:
            for hwnd in _frame_windows(runtime_pid, visible_only=True):
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        if foreground and win32gui.IsWindow(foreground):
            with suppress(Exception):
                win32gui.SetForegroundWindow(foreground)


if __name__ == "__main__":
    raise SystemExit(main())
