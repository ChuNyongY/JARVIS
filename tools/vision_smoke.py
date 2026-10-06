from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import tkinter as tk
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import win32api  # type: ignore[import-untyped]
import win32con  # type: ignore[import-untyped]
import win32gui  # type: ignore[import-untyped]
import win32process  # type: ignore[import-untyped]

from javis_core import (
    Action,
    ExactTemplateVisionGrounder,
    ExecutionResult,
    Postcondition,
    SelectiveCaptureGate,
    VisionClickExecutor,
    VisionTargetProfile,
    VisionWindowChangedPredicate,
    WindowsContextEventSource,
    WindowsForegroundMouseInputBackend,
    WindowsForegroundWindowCaptureBackend,
    load_vision_template,
)


def _request_foreground(window_handle: int) -> None:
    current_thread = int(win32api.GetCurrentThreadId())
    foreground = int(win32gui.GetForegroundWindow())
    foreground_thread = (
        int(win32process.GetWindowThreadProcessId(foreground)[0])
        if foreground > 0
        else current_thread
    )
    attached = foreground_thread != current_thread
    if attached:
        win32process.AttachThreadInput(current_thread, foreground_thread, True)
    try:
        win32gui.ShowWindow(window_handle, win32con.SW_RESTORE)
        with suppress(Exception):
            win32gui.BringWindowToTop(window_handle)
        with suppress(Exception):
            win32gui.SetActiveWindow(window_handle)
        with suppress(Exception):
            win32gui.SetForegroundWindow(window_handle)
    finally:
        if attached:
            win32process.AttachThreadInput(current_thread, foreground_thread, False)


def main() -> int:
    process_id = os.getpid()
    original_cursor = tuple(int(value) for value in win32api.GetCursorPos())
    original_foreground = int(win32gui.GetForegroundWindow())
    context_source = WindowsContextEventSource()
    root = tk.Tk()
    root.title("JAVIS synthetic vision smoke")
    root.geometry("240x160+80+80")
    root.attributes("-topmost", True)
    canvas = tk.Canvas(root, width=220, height=130, background="#202020", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    colors = (
        "#102030",
        "#405060",
        "#708090",
        "#a0b0c0",
        "#c01080",
        "#20d040",
        "#3050e0",
        "#f07010",
        "#11aaee",
        "#dd2299",
        "#44cc33",
        "#8833ff",
        "#f1c232",
        "#6aa84f",
        "#3d85c6",
        "#cc0000",
    )
    pattern_left = 90
    pattern_top = 55
    cell = 4
    for index, color in enumerate(colors):
        x = pattern_left + index % 4 * cell
        y = pattern_top + index // 4 * cell
        canvas.create_rectangle(x, y, x + cell, y + cell, fill=color, outline=color)
    canvas.bind("<Button-1>", lambda _event: canvas.configure(background="#004400"))
    root.update_idletasks()
    root.deiconify()
    root.lift()
    root.focus_force()
    root.update()
    root_hwnd = int(win32gui.GetAncestor(root.winfo_id(), win32con.GA_ROOT))
    _request_foreground(root_hwnd)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        foreground = int(win32gui.GetForegroundWindow())
        if foreground == root_hwnd:
            break
        root.update()
        time.sleep(0.02)
    else:
        raise RuntimeError("SYNTHETIC_WINDOW_NOT_FOREGROUND")
    foreground_pid = int(win32process.GetWindowThreadProcessId(root_hwnd)[1])
    if foreground_pid != process_id:
        raise RuntimeError("SYNTHETIC_WINDOW_IDENTITY_MISMATCH")
    event_verified = False
    event_deadline = time.monotonic() + 2.0
    while time.monotonic() < event_deadline:
        context_event = context_source.poll(timeout_seconds=0.1)
        if (
            context_event is not None
            and context_event.pid == process_id
            and context_event.hwnd == root_hwnd
        ):
            event_verified = True
            break
    if not event_verified:
        raise RuntimeError("SYNTHETIC_CONTEXT_EVENT_MISSING")

    capture_backend = WindowsForegroundWindowCaptureBackend()
    try:
        initial = capture_backend.capture_window(process_id)
        template_width = initial.width
        template_height = initial.height
        template_pixels = initial.pixels_bgra
        if len(template_pixels) != template_width * template_height * 4:
            raise RuntimeError("SYNTHETIC_TEMPLATE_CROP_FAILED")
        with tempfile.TemporaryDirectory(prefix="javis-vision-smoke-") as temporary:
            root_path = Path(temporary)
            (root_path / "target.bgra").write_bytes(template_pixels)
            (root_path / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "width": template_width,
                        "height": template_height,
                        "template": {
                            "path": "target.bgra",
                            "sha256": hashlib.sha256(template_pixels).hexdigest(),
                        },
                    },
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            template = load_vision_template(root_path / "manifest.json")
            cue = "synthetic color target"
            grounder = ExactTemplateVisionGrounder({cue: template})
            grounder.ground(initial, action_id=uuid4(), target_cue=cue)
            preflight = capture_backend.capture_window(process_id)
            if preflight.sha256 != initial.sha256:
                raise RuntimeError("SYNTHETIC_WINDOW_CHANGED_BEFORE_PREFLIGHT")
            grounder.ground(preflight, action_id=uuid4(), target_cue=cue)
            profile = VisionTargetProfile(
                capability_id="smoke.vision.click",
                app_id="vision-smoke",
                target_cue=cue,
                minimum_confidence=0.99,
            )
            def authorized(pid: int, app_id: str) -> bool:
                return pid == process_id and app_id == "vision-smoke"

            action = Action(
                capability_id=profile.capability_id,
                arguments={"process_id": process_id},
                purpose="exercise synthetic local vision target",
                target_identity=f"process:{process_id}",
                expected_postconditions=(
                    Postcondition(predicate="vision.window.changed"),
                ),
            )
            capture_gate = SelectiveCaptureGate(lambda pid: pid == process_id)
            evidence = ExecutionResult(
                action_id=action.action_id,
                executor="uia",
                success=False,
                error_code="UIA_GROUNDING_REQUIRED",
                retry_safe=True,
                duration_ms=0,
            )
            if not capture_gate.authorize_uia_fallback(action, evidence):
                raise RuntimeError("SYNTHETIC_CAPTURE_AUTHORIZATION_FAILED")
            executor = VisionClickExecutor(
                profiles={profile.capability_id: profile},
                capture=capture_backend,
                grounder=grounder,
                mouse=WindowsForegroundMouseInputBackend(),
                process_authorized_app=authorized,
                capture_gate=capture_gate,
            )
            result = executor.execute(action)
            root.update()
            verified = VisionWindowChangedPredicate(
                capture_backend,
                lambda pid: pid == process_id,
                capture_gate,
            )(action.expected_postconditions[0], result)
            print(
                json.dumps(
                    {
                        "success": result.success,
                        "error_code": result.error_code,
                        "context_event_verified": event_verified,
                        "postcondition_verified": verified,
                    },
                    separators=(",", ":"),
                )
            )
            return 0 if result.success and verified and event_verified else 1
    finally:
        with suppress(Exception):
            context_source.close()
        with suppress(Exception):
            root.destroy()
        with suppress(Exception):
            win32api.SetCursorPos(original_cursor)
        if original_foreground > 0:
            with suppress(Exception):
                win32gui.SetForegroundWindow(original_foreground)


def entrypoint() -> int:
    try:
        return main()
    except Exception:
        print(
            json.dumps(
                {
                    "success": False,
                    "error_code": "VISION_SMOKE_FAILED",
                    "context_event_verified": False,
                    "postcondition_verified": False,
                },
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(entrypoint())
