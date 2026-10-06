from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from javis_core import (
    Action,
    CancellationToken,
    PdfOpenedVerifier,
    Postcondition,
    RegisteredApp,
    RegisteredPdfOpenExecutor,
)


class Lease:
    def __init__(self, outcomes: tuple[bool, ...] = (True, True)) -> None:
        self.outcomes = outcomes
        self.calls: list[RegisteredApp] = []

    @contextmanager
    def hold(self, item: RegisteredApp) -> Iterator[bool]:
        index = len(self.calls)
        self.calls.append(item)
        yield self.outcomes[index]


class Launcher:
    def __init__(
        self,
        *,
        pid: int = 777,
        fail: bool = False,
        cancel: CancellationToken | None = None,
    ) -> None:
        self.pid = pid
        self.fail = fail
        self.cancel = cancel
        self.calls: list[tuple[Path, Path, Path, Mapping[str, str], bool]] = []

    def launch(
        self,
        viewer: Path,
        document: Path,
        *,
        working_directory: Path,
        environment: Mapping[str, str],
        new_window: bool,
    ) -> int:
        self.calls.append((viewer, document, working_directory, environment, new_window))
        if self.cancel is not None:
            self.cancel.cancel()
        if self.fail:
            raise OSError("private launch detail")
        return self.pid


class Inspector:
    def __init__(
        self,
        executable: Path | None,
        creation_time: int | None = 100,
        valid_pids: tuple[int, ...] = (777,),
    ) -> None:
        self.executable = executable
        self.creation_time = creation_time
        self.valid_pids = valid_pids

    def executable_path(self, pid: int) -> Path | None:
        return self.executable if pid in self.valid_pids else None

    def creation_time_100ns(self, pid: int) -> int | None:
        return self.creation_time if pid in self.valid_pids else None


class WindowResolver:
    def __init__(self, identity: tuple[int, int] | None = (888, 900)) -> None:
        self.identity = identity
        self.snapshots = 0
        self.resolutions: list[tuple[int, frozenset[tuple[int, int]]]] = []

    def snapshot(self, app: RegisteredApp) -> frozenset[tuple[int, int]]:
        self.snapshots += 1
        return frozenset({(100, 200)})

    def resolve(
        self,
        app: RegisteredApp,
        launcher_pid: int,
        previous_windows: frozenset[tuple[int, int]],
    ) -> tuple[int, int] | None:
        self.resolutions.append((launcher_pid, previous_windows))
        return self.identity

    def matching_windows(self, app: RegisteredApp) -> set[tuple[int, int]]:
        return set() if self.identity is None else {self.identity}


def fixtures(tmp_path: Path) -> tuple[Path, str, RegisteredApp]:
    document = tmp_path / "study.pdf"
    document.write_bytes(b"%PDF-1.4\nfixture\n%%EOF\n")
    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    viewer_path = tmp_path / "viewer.exe"
    viewer_path.write_bytes(b"viewer")
    return document, digest, RegisteredApp.from_path("viewer", viewer_path)


def open_action(document: Path, digest: str) -> Action:
    return Action(
        capability_id="file.open",
        arguments={
            "path": str(document.resolve()),
            "sha256": digest,
            "viewer_app_id": "viewer",
        },
        purpose="open exact study PDF",
        target_identity=f"file-sha256:{digest}",
        expected_postconditions=(Postcondition(predicate="file.opened"),),
    )


def test_executor_holds_viewer_and_pdf_identity_across_launch(tmp_path: Path) -> None:
    document, digest, viewer = fixtures(tmp_path)
    lease = Lease()
    launcher = Launcher()
    inspector = Inspector(viewer.executable)
    executor = RegisteredPdfOpenExecutor(
        {"viewer": viewer},
        (tmp_path,),
        launcher=launcher,
        lease=lease,
        inspector=inspector,
    )

    result = executor.execute(open_action(document, digest))

    assert result.success
    assert result.observed == {
        "file_sha256": digest,
        "pid": 777,
        "process_creation_time_100ns": 100,
        "viewer_app_id": "viewer",
    }
    assert [item.app_id for item in lease.calls] == ["viewer", "pdf-content"]
    assert launcher.calls[0][0:3] == (
        viewer.executable,
        document.resolve(),
        viewer.executable.parent,
    )
    assert launcher.calls[0][4] is False
    assert str(document) not in result.model_dump_json()


def test_browser_pdf_requires_new_exact_window_and_verifies_it(tmp_path: Path) -> None:
    document, digest, viewer = fixtures(tmp_path)
    viewer = viewer.model_copy(
        update={
            "single_instance_handoff": True,
            "window_class": "BrowserWindow",
            "document_launch_mode": "browser-new-window",
        }
    )
    launcher = Launcher()
    inspector = Inspector(viewer.executable, valid_pids=(888,))
    executor_windows = WindowResolver()
    executor = RegisteredPdfOpenExecutor(
        {"viewer": viewer},
        (tmp_path,),
        launcher=launcher,
        lease=Lease(),
        inspector=inspector,
        window_resolver=executor_windows,
    )
    action = open_action(document, digest)

    result = executor.execute(action)
    verified = PdfOpenedVerifier(
        executor,
        inspector=inspector,
        window_resolver=WindowResolver(),
    ).verify(action, result)

    assert result.success
    assert result.observed["pid"] == 888
    assert result.observed["hwnd"] == 900
    assert launcher.calls[0][4] is True
    assert executor_windows.snapshots == 1
    assert executor_windows.resolutions == [(777, frozenset({(100, 200)}))]
    assert verified.success


def test_browser_pdf_fails_closed_without_one_new_window(tmp_path: Path) -> None:
    document, digest, viewer = fixtures(tmp_path)
    viewer = viewer.model_copy(
        update={
            "single_instance_handoff": True,
            "window_class": "BrowserWindow",
            "document_launch_mode": "browser-new-window",
        }
    )
    result = RegisteredPdfOpenExecutor(
        {"viewer": viewer},
        (tmp_path,),
        launcher=Launcher(),
        lease=Lease(),
        inspector=Inspector(viewer.executable),
        window_resolver=WindowResolver(None),
    ).execute(open_action(document, digest))

    assert not result.success
    assert result.error_code == "PDF_WINDOW_HANDOFF_FAILED"
    assert not result.retry_safe


def test_independent_verifier_rechecks_file_process_and_creation_time(tmp_path: Path) -> None:
    document, digest, viewer = fixtures(tmp_path)
    inspector = Inspector(viewer.executable)
    executor = RegisteredPdfOpenExecutor(
        {"viewer": viewer},
        (tmp_path,),
        launcher=Launcher(),
        lease=Lease(),
        inspector=inspector,
    )
    action = open_action(document, digest)
    result = executor.execute(action)

    verified = PdfOpenedVerifier(executor, inspector=inspector).verify(action, result)
    inspector.creation_time = 101
    reused = PdfOpenedVerifier(executor, inspector=inspector).verify(action, result)

    assert verified.success
    assert verified.observed == {"file_sha256": digest, "viewer_app_id": "viewer"}
    assert not reused.success
    assert reused.reason == "PDF_VIEWER_IDENTITY_FAILED"
    assert not reused.recoverable


def test_executor_rejects_wrong_hash_target_suffix_and_outside_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    document, digest, viewer = fixtures(allowed)
    outside_pdf = outside / "outside.pdf"
    outside_pdf.write_bytes(document.read_bytes())
    text = allowed / "study.txt"
    text.write_bytes(document.read_bytes())
    executor = RegisteredPdfOpenExecutor(
        {"viewer": viewer},
        (allowed,),
        launcher=Launcher(),
        lease=Lease(),
        inspector=Inspector(viewer.executable),
    )
    wrong_hash = open_action(document, "0" * 64)
    wrong_target = open_action(document, digest).model_copy(
        update={"target_identity": "file-sha256:" + "1" * 64}
    )

    assert executor.execute(wrong_hash).error_code == "PDF_HASH_MISMATCH"
    assert executor.execute(wrong_target).error_code == "TARGET_IDENTITY_MISMATCH"
    assert executor.execute(open_action(text, digest)).error_code == "PDF_PATH_NOT_ALLOWED"
    assert executor.execute(open_action(outside_pdf, digest)).error_code == "PDF_PATH_NOT_ALLOWED"


def test_launch_exception_and_stop_after_input_are_not_retry_safe(tmp_path: Path) -> None:
    document, digest, viewer = fixtures(tmp_path)
    failed = RegisteredPdfOpenExecutor(
        {"viewer": viewer},
        (tmp_path,),
        launcher=Launcher(fail=True),
        lease=Lease(),
        inspector=Inspector(viewer.executable),
    ).execute(open_action(document, digest))
    token = CancellationToken()
    stopped = RegisteredPdfOpenExecutor(
        {"viewer": viewer},
        (tmp_path,),
        launcher=Launcher(cancel=token),
        lease=Lease(),
        inspector=Inspector(viewer.executable),
    ).execute(open_action(document, digest), token)

    assert failed.error_code == "PDF_OPEN_STATE_UNKNOWN"
    assert not failed.retry_safe
    assert "private launch detail" not in failed.model_dump_json()
    assert stopped.error_code == "STOP_REQUESTED_AFTER_INPUT"
    assert not stopped.retry_safe


def test_identity_lease_failure_is_retry_safe_before_launch(tmp_path: Path) -> None:
    document, digest, viewer = fixtures(tmp_path)
    launcher = Launcher()
    executor = RegisteredPdfOpenExecutor(
        {"viewer": viewer},
        (tmp_path,),
        launcher=launcher,
        lease=Lease((True, False)),
        inspector=Inspector(viewer.executable),
    )

    result = executor.execute(open_action(document, digest))

    assert result.error_code == "PDF_IDENTITY_CHANGED"
    assert result.retry_safe
    assert launcher.calls == []
