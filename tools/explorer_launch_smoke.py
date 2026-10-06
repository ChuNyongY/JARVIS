from __future__ import annotations

import ctypes
import json
from pathlib import Path
from typing import Any

from javis_core.app_registry import AppRegistryLoader
from javis_core.models import Action, Postcondition
from javis_core.process_launch import ProcessLaunchExecutor
from javis_core.process_verifier import ProcessExistsPredicate
from javis_core.verifier import PredicateVerifier
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
    return bool(user32.IsWindow(hwnd) and user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0))


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    apps = AppRegistryLoader(require_publisher=True).load(
        (root / "config" / "apps.json").resolve(strict=True)
    )
    explorer = apps.get("explorer")
    if explorer is None:
        print(json.dumps({"status": "FAIL", "code": "EXPLORER_NOT_REGISTERED"}))
        return 1
    resolver = WindowsRuntimeWindowResolver()
    baseline = resolver.snapshot(explorer)
    executor = ProcessLaunchExecutor({"explorer": explorer}, window_resolver=resolver)
    action = Action(
        capability_id="process.launch",
        arguments={"app_id": "explorer"},
        purpose="verify one registered File Explorer window",
        target_identity="explorer",
        expected_postconditions=(Postcondition(predicate="process.exists"),),
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
    verification = PredicateVerifier(
        {"process.exists": ProcessExistsPredicate({"explorer": explorer})}
    ).verify(action, result)
    hwnd = result.observed.get("hwnd")
    window_verified = (
        type(hwnd) is int
        and (result.observed.get("pid"), hwnd) in resolver.matching_windows(explorer)
    )
    cleanup = type(hwnd) is int and _close_exact_window(hwnd)
    passed = verification.success and window_verified and cleanup
    print(
        json.dumps(
            {
                "status": "PASS" if passed else "FAIL",
                "process_verification": verification.reason,
                "window_identity_verified": window_verified,
                "cleanup": "WM_CLOSE_SENT" if cleanup else "CLEANUP_FAILED",
            },
            sort_keys=True,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
