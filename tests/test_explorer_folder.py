from __future__ import annotations

import json
import subprocess
import sys
from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest

from javis_core import explorer_folder_worker
from javis_core.cancellation import CancellationToken
from javis_core.explorer_folder import (
    ExplorerFolderOpenExecutor,
    ExplorerFolderVerifier,
    SubprocessExplorerFolderLauncher,
    explorer_folder_target_identity,
)
from javis_core.explorer_folder_ipc import (
    ExplorerFolderRequest,
    ExplorerFolderResponse,
    ExplorerWorkerError,
    PollingExplorerFolderChecker,
    SubprocessExplorerFolderChecker,
    parse_explorer_request,
)
from javis_core.models import Action, Postcondition
from javis_core.process_launch import RegisteredApp


class Lease:
    def __init__(self, verified: bool = True) -> None:
        self.verified = verified

    def hold(self, _app: RegisteredApp):  # type: ignore[no-untyped-def]
        return nullcontext(self.verified)


class Launcher:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    def launch(
        self,
        _executable: Path,
        directory: Path,
        *,
        working_directory: Path,
        environment: dict[str, str],
    ) -> int:
        del working_directory, environment
        self.calls.append(directory)
        return 41


class Resolver:
    def __init__(self, identity: tuple[int, int] | None = (51, 61)) -> None:
        self.identity = identity

    def snapshot(self, _app: RegisteredApp) -> frozenset[tuple[int, int]]:
        return frozenset({(11, 21)})

    def resolve(
        self,
        _app: RegisteredApp,
        launcher_pid: int,
        previous_windows: frozenset[tuple[int, int]],
    ) -> tuple[int, int] | None:
        assert launcher_pid == 41
        assert previous_windows == frozenset({(11, 21)})
        return self.identity

    def matching_windows(self, _app: RegisteredApp) -> set[tuple[int, int]]:
        return {self.identity} if self.identity is not None else set()


class Inspector:
    def __init__(self, executable: Path, creation_time: int = 71) -> None:
        self.executable = executable
        self.creation_time = creation_time

    def executable_path(self, pid: int) -> Path | None:
        return self.executable if pid == 51 else None

    def creation_time_100ns(self, pid: int) -> int | None:
        return self.creation_time if pid == 51 else None


class Checker:
    def __init__(self, matched: bool = True) -> None:
        self.matched = matched
        self.calls: list[tuple[int, Path]] = []

    def matches(self, hwnd: int, expected_path: Path) -> bool:
        self.calls.append((hwnd, expected_path))
        return self.matched


def explorer_app(tmp_path: Path) -> RegisteredApp:
    executable = tmp_path / "explorer.exe"
    executable.write_bytes(b"explorer fixture")
    return RegisteredApp.from_path("explorer", executable).model_copy(
        update={"single_instance_handoff": True, "window_class": "CabinetWClass"}
    )


def action_for(root: Path, relative_path: str = "folder") -> Action:
    target = explorer_folder_target_identity(
        {"workspace": root}, "workspace", relative_path
    )
    assert target is not None
    return Action(
        capability_id="explorer.folder.open",
        arguments={"root_id": "workspace", "relative_path": relative_path},
        purpose="open one trusted folder",
        target_identity=target,
        expected_postconditions=(
            Postcondition(predicate="explorer.folder.verified"),
        ),
    )


def subject(
    tmp_path: Path,
    *,
    checker: Checker | None = None,
    resolver: Resolver | None = None,
) -> tuple[ExplorerFolderOpenExecutor, Launcher, Inspector, Resolver, Checker]:
    app = explorer_app(tmp_path)
    launcher = Launcher()
    selected_resolver = resolver or Resolver()
    inspector = Inspector(app.executable)
    selected_checker = checker or Checker()
    executor = ExplorerFolderOpenExecutor(
        {"workspace": tmp_path},
        app,
        launcher=launcher,
        lease=Lease(),
        inspector=inspector,
        window_resolver=selected_resolver,
        folder_checker=selected_checker,
    )
    return executor, launcher, inspector, selected_resolver, selected_checker


def test_open_result_is_bound_and_contains_no_raw_path(tmp_path: Path) -> None:
    directory = tmp_path / "folder"
    directory.mkdir()
    executor, launcher, inspector, resolver, checker = subject(tmp_path)
    action = action_for(tmp_path)

    result = executor.execute(action)
    verification = ExplorerFolderVerifier(
        executor,
        inspector=inspector,
        window_resolver=resolver,
        folder_checker=checker,
    ).verify(action, result)

    assert result.success
    assert set(result.observed) == {
        "app_id",
        "root_id",
        "directory_sha256",
        "pid",
        "hwnd",
        "process_creation_time_100ns",
    }
    assert str(directory) not in json.dumps(result.model_dump(mode="json"))
    assert launcher.calls == [directory.resolve()]
    assert verification.success
    assert checker.calls == [(61, directory.resolve()), (61, directory.resolve())]


@pytest.mark.parametrize(
    "relative_path",
    ("..", "..\\outside", "C:\\Windows", "\\\\server\\share", "x:y", "\0"),
)
def test_path_escape_forms_fail_before_launch(
    tmp_path: Path, relative_path: str
) -> None:
    executor, launcher, *_ = subject(tmp_path)
    result = executor.execute(
        Action(
            capability_id="explorer.folder.open",
            arguments={"root_id": "workspace", "relative_path": relative_path},
            purpose="reject escape",
            target_identity="explorer-folder:" + "0" * 64,
            expected_postconditions=(
                Postcondition(predicate="explorer.folder.verified"),
            ),
        )
    )

    assert not result.success
    assert result.error_code == "DIRECTORY_NOT_ALLOWED"
    assert result.retry_safe
    assert not launcher.calls


def test_folder_mismatch_after_launch_is_not_retryable(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()
    executor, launcher, *_ = subject(tmp_path, checker=Checker(False))

    result = executor.execute(action_for(tmp_path))

    assert not result.success
    assert result.error_code == "EXPLORER_FOLDER_MISMATCH"
    assert not result.retry_safe
    assert launcher.calls


def test_directory_symlink_or_junction_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"outside-{uuid4().hex}"
    outside.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        outside.rmdir()
        pytest.skip("directory symlink creation is unavailable")
    try:
        assert explorer_folder_target_identity(
            {"workspace": tmp_path}, "workspace", "linked"
        ) is None
    finally:
        link.unlink()
        outside.rmdir()


def test_cancelled_action_does_not_launch(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()
    executor, launcher, *_ = subject(tmp_path)
    cancellation = CancellationToken()
    cancellation.cancel()

    result = executor.execute(action_for(tmp_path), cancellation)

    assert result.error_code == "STOP_REQUESTED"
    assert result.retry_safe
    assert not launcher.calls


def test_verifier_rejects_tampered_binding(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()
    executor, _, inspector, resolver, checker = subject(tmp_path)
    action = action_for(tmp_path)
    result = executor.execute(action)
    tampered = result.model_copy(
        update={"observed": {**result.observed, "root_id": "other"}}
    )

    verification = ExplorerFolderVerifier(
        executor,
        inspector=inspector,
        window_resolver=resolver,
        folder_checker=checker,
    ).verify(action, tampered)

    assert not verification.success
    assert verification.reason == "EXPLORER_VERIFY_BINDING_FAILED"


def test_worker_parser_rejects_duplicate_keys() -> None:
    with pytest.raises(ExplorerWorkerError, match="duplicate"):
        parse_explorer_request(
            b'{"operation":"folder_matches","hwnd":1,"hwnd":2,"expected_path":"C:/x"}'
        )


def test_worker_runner_accepts_only_bounded_typed_response(tmp_path: Path) -> None:
    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=ExplorerFolderResponse(success=True, matches=True)
            .model_dump_json()
            .encode(),
        )

    assert SubprocessExplorerFolderChecker(process_runner=run).matches(1, tmp_path)


def test_shell_worker_compares_only_exact_hwnd_and_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialized: list[bool] = []
    pythoncom = ModuleType("pythoncom")
    pythoncom.COINIT_APARTMENTTHREADED = 2  # type: ignore[attr-defined]
    pythoncom.CoInitializeEx = lambda _mode: initialized.append(True)  # type: ignore[attr-defined]
    pythoncom.CoUninitialize = lambda: initialized.append(False)  # type: ignore[attr-defined]

    window = SimpleNamespace(
        HWND=61,
        Document=SimpleNamespace(
            Folder=SimpleNamespace(Self=SimpleNamespace(Path=str(tmp_path)))
        ),
    )

    class Windows:
        Count = 1

        def Item(self, index: int) -> object:
            assert index == 0
            return window

    client = ModuleType("win32com.client")
    client.Dispatch = lambda _name: SimpleNamespace(Windows=lambda: Windows())  # type: ignore[attr-defined]
    win32com = ModuleType("win32com")
    win32com.client = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)

    assert explorer_folder_worker._folder_matches(61, str(tmp_path))
    assert not explorer_folder_worker._folder_matches(62, str(tmp_path))
    assert initialized == [True, False, True, False]


def test_explorer_worker_main_returns_boolean_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = ExplorerFolderRequest(hwnd=61, expected_path=str(tmp_path))

    class Input:
        buffer = BytesIO(request.model_dump_json().encode())

    class Output:
        buffer = BytesIO()

    output = Output()
    monkeypatch.setattr(explorer_folder_worker.sys, "stdin", Input())
    monkeypatch.setattr(explorer_folder_worker.sys, "stdout", output)
    monkeypatch.setattr(explorer_folder_worker, "_folder_matches", lambda *_args: True)

    assert explorer_folder_worker.main() == 0
    response = ExplorerFolderResponse.model_validate_json(output.buffer.getvalue())
    assert response.success and response.matches
    assert str(tmp_path).encode() not in output.buffer.getvalue()


def test_worker_runner_rejects_failed_or_malformed_response(tmp_path: Path) -> None:
    def failed(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        payload = ExplorerFolderResponse(
            success=False, error_code="WORKER_FAILED"
        ).model_dump_json().encode()
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=payload)

    with pytest.raises(ExplorerWorkerError, match="WORKER_FAILED"):
        SubprocessExplorerFolderChecker(process_runner=failed).matches(1, tmp_path)

    def malformed(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=b"not-json")

    with pytest.raises(ExplorerWorkerError, match="malformed"):
        SubprocessExplorerFolderChecker(process_runner=malformed).matches(1, tmp_path)


def test_polling_checker_retries_false_observations(tmp_path: Path) -> None:
    outcomes = iter((False, False, True))

    class TransientChecker:
        def matches(self, _hwnd: int, _expected_path: Path) -> bool:
            return next(outcomes)

    sleeps: list[float] = []
    checker = PollingExplorerFolderChecker(
        TransientChecker(), max_attempts=3, poll_seconds=0.1, sleeper=sleeps.append
    )

    assert checker.matches(61, tmp_path)
    assert sleeps == [0.1, 0.1]


def test_polling_checker_preserves_final_worker_failure(tmp_path: Path) -> None:
    class FailedChecker:
        def matches(self, _hwnd: int, _expected_path: Path) -> bool:
            raise ExplorerWorkerError("transient")

    checker = PollingExplorerFolderChecker(
        FailedChecker(), max_attempts=2, poll_seconds=0, sleeper=lambda _delay: None
    )

    with pytest.raises(ExplorerWorkerError, match="observation failed"):
        checker.matches(61, tmp_path)


def test_launcher_uses_fixed_new_window_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    class Process:
        pid = 91

    def popen(command: list[str], **_kwargs: object) -> Process:
        captured.append(command)
        return Process()

    monkeypatch.setattr(subprocess, "Popen", popen)
    pid = SubprocessExplorerFolderLauncher().launch(
        Path("C:/Windows/explorer.exe"),
        Path("C:/trusted/folder"),
        working_directory=Path("C:/Windows"),
        environment={},
    )

    assert pid == 91
    assert captured == [
        ["C:\\Windows\\explorer.exe", "/n,", "C:\\trusted\\folder"]
    ]


def test_action_id_mismatch_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()
    executor, _, inspector, resolver, checker = subject(tmp_path)
    action = action_for(tmp_path)
    result = executor.execute(action).model_copy(update={"action_id": uuid4()})

    verification = ExplorerFolderVerifier(
        executor,
        inspector=inspector,
        window_resolver=resolver,
        folder_checker=checker,
    ).verify(action, result)

    assert not verification.success
    assert verification.reason == "ACTION_ID_MISMATCH"
