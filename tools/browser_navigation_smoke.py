from __future__ import annotations

import ctypes
import json
from pathlib import Path
from typing import Any

from javis_core.app_registry import AppRegistryLoader
from javis_core.browser_navigation import BrowserNavigateExecutor, BrowserNavigationVerifier
from javis_core.models import Action, Postcondition
from javis_core.product_host import load_product_host_config
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
    configuration = load_product_host_config(
        (root / "config" / "product_host.json").resolve(strict=True),
        root,
    )
    apps = AppRegistryLoader(require_publisher=True).load(configuration.app_registry)
    browser = apps.get("chrome")
    if browser is None:
        print(json.dumps({"status": "FAIL", "code": "BROWSER_NOT_REGISTERED"}))
        return 1
    resolver = WindowsRuntimeWindowResolver()
    baseline = resolver.snapshot(browser)
    executor = BrowserNavigateExecutor(
        {"chrome": browser},
        configuration.settings.browser_allowed_hosts,
        window_resolver=resolver,
    )
    action = Action(
        capability_id="browser.navigate",
        arguments={"browser_id": "chrome", "url": "https://github.com"},
        purpose="verify one allowlisted HTTPS navigation",
        target_identity="browser:chrome:github.com",
        expected_postconditions=(
            Postcondition(predicate="browser.navigation.verified"),
        ),
    )
    result = executor.execute(action)
    if not result.success:
        created = resolver.matching_windows(browser) - baseline
        cleanup_count = sum(_close_exact_window(hwnd) for _pid, hwnd in created)
        print(json.dumps({
            "status": "FAIL",
            "code": result.error_code or "EXECUTION_FAILED",
            "new_window_candidates": len(created),
            "cleanup_count": cleanup_count,
        }, sort_keys=True))
        return 1
    verification = BrowserNavigationVerifier(
        executor, window_resolver=resolver
    ).verify(action, result)
    hwnd = result.observed.get("hwnd")
    cleanup = type(hwnd) is int and _close_exact_window(hwnd)
    report = {
        "status": "PASS" if verification.success and cleanup else "FAIL",
        "verification": verification.reason,
        "cleanup": "WM_CLOSE_SENT" if cleanup else "CLEANUP_FAILED",
        "host": verification.observed.get("host"),
        "url_digest_bound": "url_sha256" in verification.observed,
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if verification.success and cleanup else 1


if __name__ == "__main__":
    raise SystemExit(main())
