from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path, PurePosixPath
from time import perf_counter_ns
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import Field

from .cancellation import CancellationToken
from .models import Action, FrozenModel, Postcondition
from .runtime_models import ExecutionResult
from .selective_capture import CapturePurpose, SelectiveCaptureAuthorizer


class VisionTemplateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CapturedWindow:
    process_id: int
    window_handle: int
    left: int
    top: int
    width: int
    height: int
    pixels_bgra: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.process_id) is not int or self.process_id < 1:
            raise ValueError("invalid capture process")
        if type(self.window_handle) is not int or self.window_handle == 0:
            raise ValueError("invalid capture window")
        if any(type(value) is not int for value in (self.left, self.top, self.width, self.height)):
            raise ValueError("invalid capture geometry")
        if not 1 <= self.width <= 4_096 or not 1 <= self.height <= 2_160:
            raise ValueError("capture geometry exceeded limits")
        if not -65_536 <= self.left <= 65_536 or not -65_536 <= self.top <= 65_536:
            raise ValueError("capture origin exceeded limits")
        expected = self.width * self.height * 4
        if type(self.pixels_bgra) is not bytes or len(self.pixels_bgra) != expected:
            raise ValueError("capture payload did not match BGRA geometry")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.pixels_bgra).hexdigest()


class VisionTargetProfile(FrozenModel):
    capability_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    app_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    target_cue: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[^\x00-\x1f\x7f]+$",
        repr=False,
    )
    minimum_confidence: float = Field(default=0.92, ge=0.8, le=1.0)


class VisionGroundingProposal(FrozenModel):
    action_id: UUID
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    relative_x: int = Field(ge=0, le=4_095)
    relative_y: int = Field(ge=0, le=2_159)
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True, slots=True)
class VisionTemplate:
    width: int
    height: int
    pixels_bgra: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.width) is not int or type(self.height) is not int:
            raise ValueError("invalid template geometry")
        if not 1 <= self.width <= 512 or not 1 <= self.height <= 512:
            raise ValueError("template geometry exceeded limits")
        if type(self.pixels_bgra) is not bytes:
            raise ValueError("invalid template payload")
        if len(self.pixels_bgra) != self.width * self.height * 4:
            raise ValueError("template payload did not match BGRA geometry")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.pixels_bgra).hexdigest()


class VisionTemplateFile(FrozenModel):
    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class VisionTemplateManifest(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    width: int = Field(ge=1, le=512)
    height: int = Field(ge=1, le=512)
    template: VisionTemplateFile


def load_vision_template(
    manifest_path: Path,
    *,
    max_manifest_bytes: int = 8 * 1024,
) -> VisionTemplate:
    if max_manifest_bytes < 1:
        raise ValueError("manifest limit must be positive")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise VisionTemplateError("template manifest must be a regular non-symlink file")
    root = manifest_path.parent
    if root.is_symlink() or not root.is_dir():
        raise VisionTemplateError("template root must be a non-symlink directory")
    root = root.resolve(strict=True)
    try:
        if manifest_path.stat().st_size > max_manifest_bytes:
            raise VisionTemplateError("template manifest exceeded size limit")
        raw = manifest_path.read_bytes()
        if len(raw) > max_manifest_bytes:
            raise VisionTemplateError("template manifest exceeded size limit")
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_template_keys,
            parse_constant=_reject_template_json_constant,
        )
        manifest = VisionTemplateManifest.model_validate(document)
    except VisionTemplateError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise VisionTemplateError("invalid template manifest") from error
    relative = manifest.template.path
    if "\\" in relative or ":" in relative:
        raise VisionTemplateError("template path must be relative POSIX")
    parsed = PurePosixPath(relative)
    if (
        parsed.is_absolute()
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or parsed.suffix.lower() != ".bgra"
    ):
        raise VisionTemplateError("template path must be a local BGRA file")
    candidate = root.joinpath(*parsed.parts)
    _reject_template_symlink_components(root, candidate)
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise VisionTemplateError("template escaped its root")
        expected_size = manifest.width * manifest.height * 4
        if resolved.stat().st_size != expected_size:
            raise VisionTemplateError("template size did not match geometry")
        pixels = resolved.read_bytes()
    except VisionTemplateError:
        raise
    except OSError as error:
        raise VisionTemplateError("template file is unavailable") from error
    if len(pixels) != expected_size:
        raise VisionTemplateError("template size did not match geometry")
    if hashlib.sha256(pixels).hexdigest() != manifest.template.sha256:
        raise VisionTemplateError("template hash mismatch")
    return VisionTemplate(
        width=manifest.width,
        height=manifest.height,
        pixels_bgra=pixels,
    )


def _reject_duplicate_template_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise VisionTemplateError("duplicate JSON key")
        result[key] = value
    return result


def _reject_template_json_constant(value: str) -> object:
    raise VisionTemplateError(f"non-finite JSON number: {value}")


def _reject_template_symlink_components(root: Path, candidate: Path) -> None:
    current = root
    for part in candidate.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise VisionTemplateError("template path must not contain symlinks")


class WindowCaptureBackend(Protocol):
    def capture_window(self, process_id: int) -> CapturedWindow: ...


class LocalVisionGrounder(Protocol):
    local_only: bool

    def ground(
        self,
        capture: CapturedWindow,
        *,
        action_id: UUID,
        target_cue: str,
    ) -> VisionGroundingProposal: ...


class MouseInputBackend(Protocol):
    def click(self, process_id: int, window_handle: int, x: int, y: int) -> None: ...


class ExactTemplateVisionGrounder:
    """Local deterministic grounding that rejects missing or ambiguous templates."""

    local_only = True

    def __init__(
        self,
        templates: Mapping[str, VisionTemplate],
        *,
        max_scan_pixels: int = 2_000_000,
    ) -> None:
        if not templates or not 1 <= max_scan_pixels <= 8_847_360:
            raise ValueError("invalid template grounder configuration")
        if any(not cue or len(cue) > 256 for cue in templates):
            raise ValueError("invalid template cue")
        self._templates = dict(templates)
        self._max_scan_pixels = max_scan_pixels

    def ground(
        self,
        capture: CapturedWindow,
        *,
        action_id: UUID,
        target_cue: str,
    ) -> VisionGroundingProposal:
        template = self._templates.get(target_cue)
        if template is None:
            raise RuntimeError("trusted vision template is unavailable")
        if capture.width * capture.height > self._max_scan_pixels:
            raise RuntimeError("capture exceeded template scan limit")
        match = self._find_unique(capture, template)
        if match is None:
            raise RuntimeError("vision template did not match uniquely")
        x, y = match
        return VisionGroundingProposal(
            action_id=action_id,
            image_sha256=capture.sha256,
            relative_x=x + template.width // 2,
            relative_y=y + template.height // 2,
            confidence=1.0,
        )

    @staticmethod
    def _find_unique(
        capture: CapturedWindow,
        template: VisionTemplate,
    ) -> tuple[int, int] | None:
        if template.width > capture.width or template.height > capture.height:
            return None
        capture_stride = capture.width * 4
        template_stride = template.width * 4
        first_row = template.pixels_bgra[:template_stride]
        found: tuple[int, int] | None = None
        for y in range(capture.height - template.height + 1):
            row_start = y * capture_stride
            search_start = row_start
            row_end = row_start + capture_stride
            while True:
                offset = capture.pixels_bgra.find(first_row, search_start, row_end)
                if offset < 0:
                    break
                search_start = offset + 1
                relative = offset - row_start
                if relative % 4:
                    continue
                x = relative // 4
                if x + template.width > capture.width:
                    continue
                if all(
                    capture.pixels_bgra[
                        (y + row) * capture_stride + x * 4 :
                        (y + row) * capture_stride + x * 4 + template_stride
                    ]
                    == template.pixels_bgra[
                        row * template_stride : (row + 1) * template_stride
                    ]
                    for row in range(1, template.height)
                ):
                    if found is not None:
                        return None
                    found = (x, y)
        return found


class ForegroundCaptureApi(Protocol):
    def capture_foreground_client(self, process_id: int) -> CapturedWindow: ...


class ForegroundMouseApi(Protocol):
    def click_foreground(
        self,
        process_id: int,
        window_handle: int,
        x: int,
        y: int,
    ) -> None: ...


class WindowsForegroundWindowCaptureBackend:
    """Capture only the visible, non-minimized foreground window client area."""

    def __init__(self, api: ForegroundCaptureApi | None = None) -> None:
        self._api = api or _PyWin32ForegroundCaptureApi()

    def capture_window(self, process_id: int) -> CapturedWindow:
        if type(process_id) is not int or process_id < 1:
            raise ValueError("invalid process ID")
        capture = self._api.capture_foreground_client(process_id)
        if not isinstance(capture, CapturedWindow) or capture.process_id != process_id:
            raise RuntimeError("foreground capture identity mismatch")
        return capture


class WindowsForegroundMouseInputBackend:
    """Inject one left click only while the authorized PID remains foreground."""

    def __init__(self, api: ForegroundMouseApi | None = None) -> None:
        self._api = api or _PyWin32ForegroundMouseApi()

    def click(self, process_id: int, window_handle: int, x: int, y: int) -> None:
        if (
            any(type(value) is not int for value in (process_id, window_handle, x, y))
            or process_id < 1
            or window_handle == 0
        ):
            raise ValueError("invalid input coordinates")
        if not -65_536 <= x <= 69_631 or not -65_536 <= y <= 67_695:
            raise ValueError("input coordinates exceeded limits")
        self._api.click_foreground(process_id, window_handle, x, y)


class VisionClickExecutor:
    """Fail-closed local vision click for trusted, capability-specific profiles."""

    name = "vision"

    def __init__(
        self,
        *,
        profiles: Mapping[str, VisionTargetProfile],
        capture: WindowCaptureBackend,
        grounder: LocalVisionGrounder,
        mouse: MouseInputBackend,
        process_authorized_app: Callable[[int, str], bool],
        capture_gate: SelectiveCaptureAuthorizer,
    ) -> None:
        self._profiles = dict(profiles)
        if any(key != profile.capability_id for key, profile in self._profiles.items()):
            raise ValueError("vision profile key must match capability_id")
        self._capture = capture
        self._grounder = grounder
        self._mouse = mouse
        self._process_authorized_app = process_authorized_app
        self._capture_gate = capture_gate

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=False)
        profile = self._profiles.get(action.capability_id)
        if profile is None:
            return self._failure(action, started, "UNSUPPORTED_CAPABILITY", retry_safe=True)
        process_id = self._process_id(action)
        if process_id is None:
            return self._failure(action, started, "INVALID_ARGUMENTS", retry_safe=True)
        if action.target_identity != f"process:{process_id}":
            return self._failure(action, started, "TARGET_IDENTITY_MISMATCH", retry_safe=True)
        if not self._is_authorized(process_id, profile.app_id):
            return self._failure(action, started, "PROCESS_NOT_AUTHORIZED", retry_safe=True)
        try:
            local_only = self._grounder.local_only
        except Exception:
            local_only = False
        if local_only is not True:
            return self._failure(action, started, "EXTERNAL_VISION_DISABLED", retry_safe=True)

        before = self._capture_window(
            action,
            process_id,
            CapturePurpose.GROUNDING,
            started,
        )
        if isinstance(before, ExecutionResult):
            return before
        try:
            proposal = self._grounder.ground(
                before,
                action_id=action.action_id,
                target_cue=profile.target_cue,
            )
        except Exception:
            return self._failure(action, started, "VISION_GROUNDING_FAILED", retry_safe=True)
        if not isinstance(proposal, VisionGroundingProposal):
            return self._failure(action, started, "VISION_GROUNDING_FAILED", retry_safe=True)
        if proposal.action_id != action.action_id or proposal.image_sha256 != before.sha256:
            return self._failure(action, started, "GROUNDING_BINDING_MISMATCH", retry_safe=True)
        if proposal.confidence < profile.minimum_confidence:
            return self._failure(action, started, "GROUNDING_CONFIDENCE_LOW", retry_safe=True)
        if proposal.relative_x >= before.width or proposal.relative_y >= before.height:
            return self._failure(action, started, "GROUNDING_OUT_OF_BOUNDS", retry_safe=True)
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=False)

        current = self._capture_window(
            action,
            process_id,
            CapturePurpose.PRE_INPUT,
            started,
        )
        if isinstance(current, ExecutionResult):
            return current
        if (
            current.window_handle != before.window_handle
            or current.width != before.width
            or current.height != before.height
            or current.sha256 != before.sha256
        ):
            return self._failure(action, started, "SCREEN_CHANGED_BEFORE_INPUT", retry_safe=True)
        if not self._is_authorized(process_id, profile.app_id):
            return self._failure(action, started, "PROCESS_NOT_AUTHORIZED", retry_safe=True)
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=False)
        try:
            self._mouse.click(
                process_id,
                current.window_handle,
                current.left + proposal.relative_x,
                current.top + proposal.relative_y,
            )
        except Exception:
            return self._failure(action, started, "INPUT_INJECTION_FAILED", retry_safe=False)
        return ExecutionResult(
            action_id=action.action_id,
            executor="vision",
            success=True,
            observed={
                "process_id": process_id,
                "window_handle": before.window_handle,
                "pre_image_sha256": before.sha256,
            },
            duration_ms=self._elapsed_ms(started),
        )

    def _capture_window(
        self,
        action: Action,
        process_id: int,
        purpose: CapturePurpose,
        started: int,
    ) -> CapturedWindow | ExecutionResult:
        if not self._capture_gate.consume(action.action_id, process_id, purpose):
            return self._failure(
                action,
                started,
                "SELECTIVE_CAPTURE_DENIED",
                retry_safe=True,
            )
        try:
            capture = self._capture.capture_window(process_id)
        except Exception:
            return self._failure(action, started, "WINDOW_CAPTURE_FAILED", retry_safe=True)
        if not isinstance(capture, CapturedWindow):
            return self._failure(action, started, "WINDOW_CAPTURE_FAILED", retry_safe=True)
        if capture.process_id != process_id:
            return self._failure(action, started, "CAPTURE_PROCESS_MISMATCH", retry_safe=True)
        return capture

    def _is_authorized(self, process_id: int, app_id: str) -> bool:
        try:
            return self._process_authorized_app(process_id, app_id) is True
        except Exception:
            return False

    @staticmethod
    def _process_id(action: Action) -> int | None:
        if set(action.arguments) != {"process_id"}:
            return None
        process_id = action.arguments.get("process_id")
        return process_id if type(process_id) is int and process_id > 0 else None

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (perf_counter_ns() - started) // 1_000_000)

    @classmethod
    def _failure(
        cls,
        action: Action,
        started: int,
        error_code: str,
        *,
        retry_safe: bool,
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="vision",
            success=False,
            error_code=error_code,
            error_message="Vision fallback did not complete the authorized input",
            retry_safe=retry_safe,
            duration_ms=cls._elapsed_ms(started),
        )


class VisionWindowChangedPredicate:
    """Independently recapture an authorized window and compare volatile pixels."""

    def __init__(
        self,
        capture: WindowCaptureBackend,
        process_authorized: Callable[[int], bool],
        capture_gate: SelectiveCaptureAuthorizer,
    ) -> None:
        self._capture = capture
        self._process_authorized = process_authorized
        self._capture_gate = capture_gate

    def __call__(self, postcondition: Postcondition, result: ExecutionResult) -> bool:
        try:
            if postcondition.arguments or not result.success or result.executor != "vision":
                return False
            process_id = result.observed.get("process_id")
            previous = result.observed.get("pre_image_sha256")
            window_handle = result.observed.get("window_handle")
            if (
                type(process_id) is not int
                or type(window_handle) is not int
                or not isinstance(previous, str)
            ):
                return False
            if re.fullmatch(r"[0-9a-f]{64}", previous) is None:
                return False
            try:
                authorized = self._process_authorized(process_id) is True
            except Exception:
                authorized = False
            if not authorized or not self._capture_gate.consume(
                result.action_id,
                process_id,
                CapturePurpose.VERIFICATION,
            ):
                return False
            try:
                current = self._capture.capture_window(process_id)
            except Exception:
                return False
            return (
                isinstance(current, CapturedWindow)
                and current.process_id == process_id
                and current.window_handle == window_handle
                and current.sha256 != previous
            )
        finally:
            self._capture_gate.close(result.action_id)


class _PyWin32ForegroundCaptureApi:
    def capture_foreground_client(self, process_id: int) -> CapturedWindow:
        try:
            win32con = import_module("win32con")
            win32gui = import_module("win32gui")
            win32process = import_module("win32process")
            win32ui = import_module("win32ui")
        except ImportError as error:
            raise RuntimeError("pywin32 capture dependency is unavailable") from error
        hwnd = int(win32gui.GetForegroundWindow())
        if hwnd <= 0:
            raise RuntimeError("foreground window is unavailable")
        actual_pid = int(win32process.GetWindowThreadProcessId(hwnd)[1])
        if actual_pid != process_id:
            raise RuntimeError("foreground process changed")
        if not bool(win32gui.IsWindowVisible(hwnd)) or bool(win32gui.IsIconic(hwnd)):
            raise RuntimeError("foreground window is not capturable")
        left, top, right, bottom = tuple(int(value) for value in win32gui.GetClientRect(hwnd))
        if (left, top) != (0, 0):
            raise RuntimeError("unexpected client origin")
        width = right - left
        height = bottom - top
        screen_left, screen_top = tuple(
            int(value) for value in win32gui.ClientToScreen(hwnd, (0, 0))
        )
        if not 1 <= width <= 4_096 or not 1 <= height <= 2_160:
            raise RuntimeError("foreground client geometry exceeded limits")

        window_dc_handle: int | None = None
        source_dc: Any | None = None
        memory_dc: Any | None = None
        bitmap: Any | None = None
        previous_bitmap: Any | None = None
        try:
            window_dc_handle = int(win32gui.GetDC(hwnd))
            if window_dc_handle == 0:
                raise RuntimeError("window device context is unavailable")
            source_dc = win32ui.CreateDCFromHandle(window_dc_handle)
            memory_dc = source_dc.CreateCompatibleDC()
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(source_dc, width, height)
            previous_bitmap = memory_dc.SelectObject(bitmap)
            copied = memory_dc.BitBlt(
                (0, 0),
                (width, height),
                source_dc,
                (0, 0),
                int(win32con.SRCCOPY) | int(getattr(win32con, "CAPTUREBLT", 0x40000000)),
            )
            if copied is False:
                raise RuntimeError("window capture failed")
            raw = bytes(bitmap.GetBitmapBits(True))
            stride = width * 4
            if len(raw) != stride * height:
                raise RuntimeError("window capture returned invalid pixels")
            pixels = b"".join(
                raw[row * stride : (row + 1) * stride]
                for row in range(height - 1, -1, -1)
            )
            return CapturedWindow(
                process_id=process_id,
                window_handle=hwnd,
                left=screen_left,
                top=screen_top,
                width=width,
                height=height,
                pixels_bgra=pixels,
            )
        finally:
            if memory_dc is not None and previous_bitmap is not None:
                with suppress(Exception):
                    memory_dc.SelectObject(previous_bitmap)
            if memory_dc is not None:
                with suppress(Exception):
                    memory_dc.DeleteDC()
            if bitmap is not None:
                with suppress(Exception):
                    win32gui.DeleteObject(bitmap.GetHandle())
            if source_dc is not None:
                with suppress(Exception):
                    source_dc.DeleteDC()
            if window_dc_handle is not None:
                with suppress(Exception):
                    win32gui.ReleaseDC(hwnd, window_dc_handle)


class _PyWin32ForegroundMouseApi:
    def click_foreground(
        self,
        process_id: int,
        window_handle: int,
        x: int,
        y: int,
    ) -> None:
        try:
            win32api = import_module("win32api")
            win32con = import_module("win32con")
            win32gui = import_module("win32gui")
            win32process = import_module("win32process")
        except ImportError as error:
            raise RuntimeError("pywin32 input dependency is unavailable") from error
        hwnd = int(win32gui.GetForegroundWindow())
        if (
            hwnd != window_handle
            or int(win32process.GetWindowThreadProcessId(hwnd)[1]) != process_id
        ):
            raise RuntimeError("foreground process changed")
        win32api.SetCursorPos((x, y))
        hwnd = int(win32gui.GetForegroundWindow())
        if (
            hwnd != window_handle
            or int(win32process.GetWindowThreadProcessId(hwnd)[1]) != process_id
        ):
            raise RuntimeError("foreground process changed")
        win32api.mouse_event(int(win32con.MOUSEEVENTF_LEFTDOWN), 0, 0, 0, 0)
        try:
            win32api.mouse_event(int(win32con.MOUSEEVENTF_LEFTUP), 0, 0, 0, 0)
        except Exception:
            with suppress(Exception):
                win32api.mouse_event(int(win32con.MOUSEEVENTF_LEFTUP), 0, 0, 0, 0)
            raise
