from __future__ import annotations

import json
import time
from threading import Thread

import win32con  # type: ignore[import-untyped]
import win32gui  # type: ignore[import-untyped]

from javis_core.windows_tray import TrayCommand, WindowsTrayController


def _find_tray_window() -> int | None:
    matches: list[int] = []

    def inspect(hwnd: int, _extra: object) -> bool:
        try:
            if win32gui.GetClassName(hwnd).startswith("JavisPcAgentTray_"):
                matches.append(hwnd)
        except Exception:
            return True
        return True

    win32gui.EnumWindows(inspect, None)
    return matches[0] if len(matches) == 1 else None


def main() -> int:
    calls: list[str] = []
    failures: list[str] = []
    tray = WindowsTrayController(
        show_status=lambda: calls.append("show"),
        stop_agent=lambda: calls.append("stop"),
        exit_agent=lambda: calls.append("exit"),
        tooltip="JAVIS tray smoke",
    )

    def run() -> None:
        try:
            tray.run()
        except Exception:
            failures.append("TRAY_RUN_FAILED")

    worker = Thread(target=run, name="javis-tray-smoke", daemon=False)
    worker.start()
    deadline = time.monotonic() + 5.0
    window: int | None = None
    while time.monotonic() < deadline and window is None and not failures:
        window = _find_tray_window()
        if window is None:
            time.sleep(0.05)
    if window is None:
        failures.append("TRAY_WINDOW_NOT_FOUND")
    else:
        win32gui.PostMessage(
            window,
            win32con.WM_COMMAND,
            TrayCommand.EXIT.value,
            0,
        )
    worker.join(timeout=5.0)
    if worker.is_alive():
        failures.append("TRAY_THREAD_DID_NOT_STOP")
    if calls != ["exit"]:
        failures.append("TRAY_EXIT_CALLBACK_MISMATCH")
    if _find_tray_window() is not None:
        failures.append("TRAY_WINDOW_NOT_CLEANED")
    report = {
        "passed": not failures,
        "exit_callback_count": calls.count("exit"),
        "window_cleaned": _find_tray_window() is None,
        "failure_codes": failures,
    }
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
