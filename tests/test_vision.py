from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

import javis_core.vision as vision_module
from javis_core import (
    Action,
    CancellationToken,
    CapturedWindow,
    ExactTemplateVisionGrounder,
    Postcondition,
    VisionClickExecutor,
    VisionGroundingProposal,
    VisionTargetProfile,
    VisionTemplate,
    VisionTemplateError,
    VisionWindowChangedPredicate,
    WindowsForegroundMouseInputBackend,
    WindowsForegroundWindowCaptureBackend,
    load_vision_template,
)
from javis_core.selective_capture import CapturePurpose

PID = 4242
PIXELS = b"\x10\x20\x30\xff" * 4


def capture(*, pixels: bytes = PIXELS, left: int = 10) -> CapturedWindow:
    return CapturedWindow(
        process_id=PID,
        window_handle=123,
        left=left,
        top=20,
        width=2,
        height=2,
        pixels_bgra=pixels,
    )


def action(**updates: object) -> Action:
    values: dict[str, object] = {
        "capability_id": "demo.safe.toggle",
        "arguments": {"process_id": PID},
        "purpose": "toggle a demo setting",
        "target_identity": f"process:{PID}",
        "expected_postconditions": (
            Postcondition(predicate="vision.window.changed"),
        ),
    }
    values.update(updates)
    return Action(**values)  # type: ignore[arg-type]


class CaptureBackend:
    def __init__(self, captures: list[CapturedWindow]) -> None:
        self.captures = captures
        self.calls = 0

    def capture_window(self, process_id: int) -> CapturedWindow:
        assert process_id == PID
        item = self.captures[min(self.calls, len(self.captures) - 1)]
        self.calls += 1
        return item


class Grounder:
    local_only = True

    def __init__(self, proposal: VisionGroundingProposal | None = None) -> None:
        self.proposal = proposal
        self.calls = 0

    def ground(
        self,
        item: CapturedWindow,
        *,
        action_id: UUID,
        target_cue: str,
    ) -> VisionGroundingProposal:
        self.calls += 1
        assert target_cue == "trusted demo toggle"
        return self.proposal or VisionGroundingProposal(
            action_id=action_id,
            image_sha256=item.sha256,
            relative_x=1,
            relative_y=1,
            confidence=0.99,
        )


class Mouse:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.clicks: list[tuple[int, int]] = []

    def click(self, process_id: int, window_handle: int, x: int, y: int) -> None:
        assert process_id == PID
        assert window_handle == 123
        self.clicks.append((x, y))
        if self.fail:
            raise RuntimeError("unknown click state")


class AllowingGate:
    def __init__(self) -> None:
        self.consumed: list[tuple[UUID, int, CapturePurpose]] = []
        self.closed: list[UUID] = []

    def consume(self, action_id: UUID, process_id: int, purpose: CapturePurpose) -> bool:
        self.consumed.append((action_id, process_id, purpose))
        return True

    def close(self, action_id: UUID) -> None:
        self.closed.append(action_id)


def executor(
    backend: CaptureBackend,
    grounder: Grounder,
    mouse: Mouse,
    *,
    authorized=lambda _pid, _app_id: True,
) -> VisionClickExecutor:
    profile = VisionTargetProfile(
        capability_id="demo.safe.toggle",
        app_id="demo",
        target_cue="trusted demo toggle",
    )
    return VisionClickExecutor(
        profiles={profile.capability_id: profile},
        capture=backend,
        grounder=grounder,
        mouse=mouse,
        process_authorized_app=authorized,
        capture_gate=AllowingGate(),
    )


def test_click_requires_bound_high_confidence_stable_capture() -> None:
    backend = CaptureBackend([capture(), capture(left=30)])
    mouse = Mouse()

    result = executor(backend, Grounder(), mouse).execute(action())

    assert result.success
    assert result.executor == "vision"
    assert mouse.clicks == [(31, 21)]
    assert result.observed == {
        "process_id": PID,
        "window_handle": 123,
        "pre_image_sha256": capture().sha256,
    }
    assert "pixels" not in result.model_dump_json()


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        ({"target_identity": "process:999"}, "TARGET_IDENTITY_MISMATCH"),
        ({"arguments": {"process_id": PID, "x": 1}}, "INVALID_ARGUMENTS"),
        ({"capability_id": "unknown"}, "UNSUPPORTED_CAPABILITY"),
    ],
)
def test_rejects_unbound_action_before_capture(
    changed: dict[str, object],
    expected: str,
) -> None:
    backend = CaptureBackend([capture()])
    mouse = Mouse()

    result = executor(backend, Grounder(), mouse).execute(action(**changed))

    assert result.error_code == expected
    assert backend.calls == 0
    assert mouse.clicks == []


def test_rejects_stale_or_low_confidence_grounding() -> None:
    item = capture()
    stale_action = action()
    stale = VisionGroundingProposal(
        action_id=stale_action.action_id,
        image_sha256="0" * 64,
        relative_x=1,
        relative_y=1,
        confidence=0.99,
    )
    low_action = action()
    low = VisionGroundingProposal(
        action_id=low_action.action_id,
        image_sha256=item.sha256,
        relative_x=1,
        relative_y=1,
        confidence=0.5,
    )

    stale_result = executor(CaptureBackend([item]), Grounder(stale), Mouse()).execute(
        stale_action
    )
    low_result = executor(CaptureBackend([item]), Grounder(low), Mouse()).execute(low_action)

    assert stale_result.error_code == "GROUNDING_BINDING_MISMATCH"
    assert low_result.error_code == "GROUNDING_CONFIDENCE_LOW"


def test_screen_change_or_revoked_session_prevents_click() -> None:
    changed_pixels = b"\x00\x00\x00\xff" * 4
    mouse = Mouse()
    changed = executor(
        CaptureBackend([capture(), capture(pixels=changed_pixels)]),
        Grounder(),
        mouse,
    ).execute(action())
    authorizations = iter((True, False))
    revoked = executor(
        CaptureBackend([capture(), capture()]),
        Grounder(),
        mouse,
        authorized=lambda _pid, _app: next(authorizations),
    ).execute(action())

    assert changed.error_code == "SCREEN_CHANGED_BEFORE_INPUT"
    assert revoked.error_code == "PROCESS_NOT_AUTHORIZED"
    assert mouse.clicks == []


def test_same_process_different_foreground_window_prevents_click() -> None:
    other_window = CapturedWindow(
        process_id=PID,
        window_handle=456,
        left=10,
        top=20,
        width=2,
        height=2,
        pixels_bgra=PIXELS,
    )
    mouse = Mouse()

    result = executor(
        CaptureBackend([capture(), other_window]),
        Grounder(),
        mouse,
    ).execute(action())

    assert result.error_code == "SCREEN_CHANGED_BEFORE_INPUT"
    assert mouse.clicks == []


def test_external_grounder_and_stop_are_fail_closed() -> None:
    grounder = Grounder()
    grounder.local_only = False
    backend = CaptureBackend([capture()])
    cancellation = CancellationToken()
    cancellation.cancel("LOCAL_STOP")

    external = executor(backend, grounder, Mouse()).execute(action())
    stopped = executor(backend, Grounder(), Mouse()).execute(action(), cancellation)

    assert external.error_code == "EXTERNAL_VISION_DISABLED"
    assert stopped.error_code == "STOP_REQUESTED"
    assert backend.calls == 0


def test_click_exception_is_not_retry_safe() -> None:
    result = executor(
        CaptureBackend([capture(), capture()]),
        Grounder(),
        Mouse(fail=True),
    ).execute(action())

    assert result.error_code == "INPUT_INJECTION_FAILED"
    assert not result.retry_safe


def test_window_changed_predicate_recaptures_without_retaining_pixels() -> None:
    changed = capture(pixels=b"\x99\x88\x77\xff" * 4)
    predicate = VisionWindowChangedPredicate(
        CaptureBackend([changed]),
        lambda pid: pid == PID,
        AllowingGate(),
    )
    result = executor(
        CaptureBackend([capture(), capture()]),
        Grounder(),
        Mouse(),
    ).execute(action())

    assert predicate(Postcondition(predicate="vision.window.changed"), result)
    assert not predicate(
        Postcondition(predicate="vision.window.changed", arguments={"unexpected": True}),
        result,
    )


def test_capture_rejects_unbounded_or_mismatched_pixel_payload() -> None:
    with pytest.raises(ValueError):
        CapturedWindow(
            process_id=PID,
            window_handle=123,
            left=0,
            top=0,
            width=4_097,
            height=1,
            pixels_bgra=b"",
        )
    with pytest.raises(ValueError):
        CapturedWindow(
            process_id=PID,
            window_handle=123,
            left=0,
            top=0,
            width=2,
            height=2,
            pixels_bgra=b"short",
        )


def test_proposal_action_binding_rejects_other_action_id() -> None:
    item = capture()
    current_action = action()
    proposal = VisionGroundingProposal(
        action_id=uuid4(),
        image_sha256=item.sha256,
        relative_x=1,
        relative_y=1,
        confidence=0.99,
    )

    result = executor(CaptureBackend([item]), Grounder(proposal), Mouse()).execute(
        current_action
    )

    assert result.error_code == "GROUNDING_BINDING_MISMATCH"


def test_untrusted_dependency_types_and_authorizer_errors_fail_closed() -> None:
    class InvalidCapture:
        def capture_window(self, process_id: int) -> object:
            del process_id
            return object()

    profile = VisionTargetProfile(
        capability_id="demo.safe.toggle",
        app_id="demo",
        target_cue="trusted demo toggle",
    )
    invalid = VisionClickExecutor(
        profiles={profile.capability_id: profile},
        capture=InvalidCapture(),  # type: ignore[arg-type]
        grounder=Grounder(),
        mouse=Mouse(),
        process_authorized_app=lambda _pid, _app: True,
        capture_gate=AllowingGate(),
    ).execute(action())
    authorization_error = executor(
        CaptureBackend([capture()]),
        Grounder(),
        Mouse(),
        authorized=lambda _pid, _app: (_ for _ in ()).throw(RuntimeError("secret")),
    ).execute(action())

    assert invalid.error_code == "WINDOW_CAPTURE_FAILED"
    assert authorization_error.error_code == "PROCESS_NOT_AUTHORIZED"
    assert "secret" not in authorization_error.model_dump_json()


def test_postcondition_authorizer_error_and_invalid_hash_fail_closed() -> None:
    changed = capture(pixels=b"\xaa\xbb\xcc\xff" * 4)
    result = executor(
        CaptureBackend([capture(), capture()]),
        Grounder(),
        Mouse(),
    ).execute(action())
    authorizer_error = VisionWindowChangedPredicate(
        CaptureBackend([changed]),
        lambda _pid: (_ for _ in ()).throw(RuntimeError("secret")),
        AllowingGate(),
    )
    invalid_hash = result.model_copy(
        update={"observed": {"process_id": PID, "pre_image_sha256": "not-a-hash"}}
    )
    predicate = VisionWindowChangedPredicate(
        CaptureBackend([changed]),
        lambda _pid: True,
        AllowingGate(),
    )

    assert not authorizer_error(Postcondition(predicate="vision.window.changed"), result)
    assert not predicate(Postcondition(predicate="vision.window.changed"), invalid_hash)


class CaptureApi:
    def __init__(self, result: CapturedWindow) -> None:
        self.result = result
        self.calls: list[int] = []

    def capture_foreground_client(self, process_id: int) -> CapturedWindow:
        self.calls.append(process_id)
        return self.result


class MouseApi:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int, int]] = []

    def click_foreground(
        self,
        process_id: int,
        window_handle: int,
        x: int,
        y: int,
    ) -> None:
        assert window_handle == 123
        self.calls.append((process_id, x, y))


def test_windows_backend_wrappers_validate_identity_and_coordinates() -> None:
    capture_api = CaptureApi(capture())
    capture_backend = WindowsForegroundWindowCaptureBackend(capture_api)
    mouse_api = MouseApi()
    mouse_backend = WindowsForegroundMouseInputBackend(mouse_api)

    observed = capture_backend.capture_window(PID)
    mouse_backend.click(PID, 123, -10, 20)

    assert observed.sha256 == capture().sha256
    assert capture_api.calls == [PID]
    assert mouse_api.calls == [(PID, -10, 20)]
    with pytest.raises(ValueError):
        mouse_backend.click(PID, 123, 70_000, 0)


def test_windows_capture_wrapper_rejects_backend_identity_mismatch() -> None:
    other = CapturedWindow(
        process_id=999,
        window_handle=123,
        left=0,
        top=0,
        width=1,
        height=1,
        pixels_bgra=b"\0\0\0\0",
    )
    backend = WindowsForegroundWindowCaptureBackend(CaptureApi(other))

    with pytest.raises(RuntimeError):
        backend.capture_window(PID)


def test_exact_template_grounder_finds_one_bound_target() -> None:
    background = b"\x00\x00\x00\xff"
    target = b"\x01\x02\x03\xff"
    pixels = background * 4 + target + background * 3
    item = CapturedWindow(
        process_id=PID,
        window_handle=123,
        left=0,
        top=0,
        width=4,
        height=2,
        pixels_bgra=pixels,
    )
    template = VisionTemplate(width=1, height=1, pixels_bgra=target)
    grounder = ExactTemplateVisionGrounder({"trusted demo toggle": template})
    action_id = uuid4()

    proposal = grounder.ground(
        item,
        action_id=action_id,
        target_cue="trusted demo toggle",
    )

    assert proposal.action_id == action_id
    assert proposal.image_sha256 == item.sha256
    assert (proposal.relative_x, proposal.relative_y) == (0, 1)
    assert proposal.confidence == 1.0


def test_exact_template_grounder_rejects_missing_ambiguous_or_oversized_input() -> None:
    background = b"\x00\x00\x00\xff"
    target = b"\x01\x02\x03\xff"
    template = VisionTemplate(width=1, height=1, pixels_bgra=target)
    grounder = ExactTemplateVisionGrounder(
        {"trusted demo toggle": template},
        max_scan_pixels=4,
    )
    missing = CapturedWindow(
        process_id=PID,
        window_handle=123,
        left=0,
        top=0,
        width=2,
        height=1,
        pixels_bgra=background * 2,
    )
    ambiguous = CapturedWindow(
        process_id=PID,
        window_handle=123,
        left=0,
        top=0,
        width=2,
        height=1,
        pixels_bgra=target * 2,
    )
    oversized = CapturedWindow(
        process_id=PID,
        window_handle=123,
        left=0,
        top=0,
        width=5,
        height=1,
        pixels_bgra=background * 5,
    )

    for item, cue in (
        (missing, "trusted demo toggle"),
        (ambiguous, "trusted demo toggle"),
        (oversized, "trusted demo toggle"),
        (missing, "unknown cue"),
    ):
        with pytest.raises(RuntimeError):
            grounder.ground(item, action_id=uuid4(), target_cue=cue)


def write_template_bundle(root: Path, *, pixels: bytes = PIXELS) -> Path:
    template_path = root / "button.bgra"
    template_path.write_bytes(pixels)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "width": 2,
                "height": 2,
                "template": {
                    "path": "button.bgra",
                    "sha256": hashlib.sha256(pixels).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_loads_hash_pinned_local_template(tmp_path: Path) -> None:
    loaded = load_vision_template(write_template_bundle(tmp_path))

    assert loaded.width == 2
    assert loaded.height == 2
    assert loaded.sha256 == hashlib.sha256(PIXELS).hexdigest()


def test_template_loader_rejects_tamper_traversal_and_duplicate_keys(tmp_path: Path) -> None:
    manifest = write_template_bundle(tmp_path)
    (tmp_path / "button.bgra").write_bytes(b"\0" * len(PIXELS))
    with pytest.raises(VisionTemplateError):
        load_vision_template(manifest)

    manifest.write_text(
        '{"schema_version":"1.0","width":2,"width":2,"height":2,'
        '"template":{"path":"button.bgra","sha256":"' + "0" * 64 + '"}}',
        encoding="utf-8",
    )
    with pytest.raises(VisionTemplateError):
        load_vision_template(manifest)

    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "width": 2,
                "height": 2,
                "template": {"path": "../button.bgra", "sha256": "0" * 64},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(VisionTemplateError):
        load_vision_template(manifest)


def test_pywin32_capture_api_binds_foreground_pid_and_releases_gdi(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    top = b"\x01\x02\x03\xff" * 2
    bottom = b"\x04\x05\x06\xff" * 2
    cleanup: list[str] = []

    class Bitmap:
        def CreateCompatibleBitmap(self, _dc, width, height):  # type: ignore[no-untyped-def]
            assert (width, height) == (2, 2)

        def GetBitmapBits(self, _signed):  # type: ignore[no-untyped-def]
            return bottom + top

        def GetHandle(self):  # type: ignore[no-untyped-def]
            return 99

    bitmap = Bitmap()

    class MemoryDc:
        def SelectObject(self, _item):  # type: ignore[no-untyped-def]
            return object()

        def BitBlt(self, *args):  # type: ignore[no-untyped-def]
            assert args[1] == (2, 2)
            return None

        def DeleteDC(self):  # type: ignore[no-untyped-def]
            cleanup.append("memory_dc")

    class SourceDc:
        def CreateCompatibleDC(self):  # type: ignore[no-untyped-def]
            return MemoryDc()

        def DeleteDC(self):  # type: ignore[no-untyped-def]
            cleanup.append("source_dc")

    class Gui:
        GetForegroundWindow = staticmethod(lambda: 123)
        IsWindowVisible = staticmethod(lambda _hwnd: True)
        IsIconic = staticmethod(lambda _hwnd: False)
        GetClientRect = staticmethod(lambda _hwnd: (0, 0, 2, 2))
        ClientToScreen = staticmethod(lambda _hwnd, _point: (100, 200))
        GetDC = staticmethod(lambda _hwnd: 88)
        DeleteObject = staticmethod(lambda _handle: cleanup.append("bitmap"))
        ReleaseDC = staticmethod(lambda _hwnd, _dc: cleanup.append("window_dc"))

    modules = {
        "win32con": type("Con", (), {"SRCCOPY": 1, "CAPTUREBLT": 2}),
        "win32gui": Gui,
        "win32process": type(
            "Process",
            (),
            {"GetWindowThreadProcessId": staticmethod(lambda _hwnd: (1, PID))},
        ),
        "win32ui": type(
            "Ui",
            (),
            {
                "CreateDCFromHandle": staticmethod(lambda _handle: SourceDc()),
                "CreateBitmap": staticmethod(lambda: bitmap),
            },
        ),
    }
    monkeypatch.setattr(vision_module, "import_module", lambda name: modules[name])

    observed = vision_module._PyWin32ForegroundCaptureApi().capture_foreground_client(PID)

    assert observed.pixels_bgra == top + bottom
    assert (observed.left, observed.top, observed.width, observed.height) == (100, 200, 2, 2)
    assert cleanup == ["memory_dc", "bitmap", "source_dc", "window_dc"]


def test_pywin32_mouse_api_rechecks_foreground_after_cursor_move(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    hwnds = iter((123, 456))
    events: list[object] = []

    class Api:
        SetCursorPos = staticmethod(lambda point: events.append(("cursor", point)))
        mouse_event = staticmethod(lambda flag, *_args: events.append(("mouse", flag)))

    modules = {
        "win32api": Api,
        "win32con": type(
            "Con",
            (),
            {"MOUSEEVENTF_LEFTDOWN": 1, "MOUSEEVENTF_LEFTUP": 2},
        ),
        "win32gui": type(
            "Gui",
            (),
            {"GetForegroundWindow": staticmethod(lambda: next(hwnds))},
        ),
        "win32process": type(
            "Process",
            (),
            {
                "GetWindowThreadProcessId": staticmethod(
                    lambda hwnd: (1, PID if hwnd == 123 else 999)
                )
            },
        ),
    }
    monkeypatch.setattr(vision_module, "import_module", lambda name: modules[name])

    with pytest.raises(RuntimeError):
        vision_module._PyWin32ForegroundMouseApi().click_foreground(PID, 123, 10, 20)

    assert events == [("cursor", (10, 20))]
