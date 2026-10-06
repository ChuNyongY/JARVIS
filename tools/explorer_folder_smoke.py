from __future__ import annotations

import ctypes
import json
import time
from pathlib import Path
from typing import Any

from javis_core.app_registry import AppRegistryLoader
from javis_core.explorer_folder import (
    ExplorerFolderOpenExecutor,
    ExplorerFolderVerifier,
    explorer_folder_target_identity,
)
from javis_core.models import Action, Postcondition
from javis_core.window_handoff import WindowsRuntimeWindowResolver

_WM_CLOSE = 0x0010


def _close_exact_window(hwnd: int) -> bool:
    user32: Any = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = [ctypes.c_void_p]
    user32.IsWindow.restype = ctypes.c_bool
    user32.PostMessageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    user32.PostMessageW.restype = ctypes.c_bool
    if not user32.IsWindow(hwnd) or not user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0):
        return False
    deadline = time.monotonic() + 3.0
    while user32.IsWindow(hwnd) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not user32.IsWindow(hwnd)


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    apps = AppRegistryLoader(require_publisher=True).load(
        (root / "config" / "apps.json").resolve(strict=True)
    )
    explorer = apps.get("explorer")
    target = explorer_folder_target_identity({"workspace": root}, "workspace", ".")
    if explorer is None or target is None:
        print(json.dumps({"status": "FAIL", "code": "PRECHECK_FAILED"}))
        return 1
    resolver = WindowsRuntimeWindowResolver(timeout_seconds=4.0)
    baseline = resolver.snapshot(explorer)
    executor = ExplorerFolderOpenExecutor(
        {"workspace": root}, explorer, window_resolver=resolver
    )
    action = Action(
        capability_id="explorer.folder.open",
        arguments={"root_id": "workspace", "relative_path": "."},
        purpose="verify exact allowlisted folder arrival",
        target_identity=target,
        expected_postconditions=(
            Postcondition(predicate="explorer.folder.verified"),
        ),
    )
    result = executor.execute(action)
    if not result.success:
        created = resolver.matching_windows(explorer) - baseline
        cleanup_count = sum(_close_exact_window(hwnd) for _pid, hwnd in created)
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "code": result.error_code or "EXECUTION_FAILED",
                    "new_window_candidates": len(created),
                    "cleanup_count": cleanup_count,
                },
                sort_keys=True,
            )
        )
        return 1
    verification = ExplorerFolderVerifier(
        executor, window_resolver=resolver
    ).verify(action, result)
    hwnd = result.observed.get("hwnd")
    cleanup = type(hwnd) is int and _close_exact_window(hwnd)
    report = {
        "status": "PASS" if verification.success and cleanup else "FAIL",
        "verification": verification.reason,
        "directory_digest_bound": "directory_sha256" in verification.observed,
        "raw_path_exposed": any(
            str(root) in str(value) for value in result.observed.values()
        ),
        "cleanup": "WINDOW_CLOSED" if cleanup else "CLEANUP_FAILED",
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if verification.success and cleanup else 1


if __name__ == "__main__":
    raise SystemExit(main())
