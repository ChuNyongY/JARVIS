from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import pytest

from javis_core import Action, CancellationToken, Postcondition, RegisteredApp
from javis_core.browser_navigation import (
    BrowserNavigateExecutor,
    BrowserNavigationVerifier,
)


class Lease:
    def __init__(self, verified: bool = True) -> None:
        self.verified = verified

    @contextmanager
    def hold(self, _app: RegisteredApp) -> Iterator[bool]:
        yield self.verified


class Launcher:
    def __init__(self, *, fail: bool = False, cancel: CancellationToken | None = None) -> None:
        self.fail = fail
        self.cancel = cancel
        self.calls: list[tuple[Path, str, Path, Mapping[str, str]]] = []

    def launch(
        self,
        executable: Path,
        url: str,
        *,
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> int:
        self.calls.append((executable, url, working_directory, environment))
        if self.cancel is not None:
            self.cancel.cancel()
        if self.fail:
            raise OSError("private launch detail")
        return 700


class Inspector:
    def __init__(self, executable: Path, creation_time: int = 123) -> None:
        self.executable = executable
        self.creation_time = creation_time

    def executable_path(self, pid: int) -> Path | None:
        return self.executable if pid == 701 else None

    def creation_time_100ns(self, pid: int) -> int | None:
        return self.creation_time if pid == 701 else None


class Windows:
    def __init__(self, identity: tuple[int, int] | None = (701, 900)) -> None:
        self.identity = identity
        self.snapshots = 0

    def snapshot(self, _app: RegisteredApp) -> frozenset[tuple[int, int]]:
        self.snapshots += 1
        return frozenset({(1, 2)})

    def resolve(
        self,
        _app: RegisteredApp,
        launcher_pid: int,
        previous_windows: frozenset[tuple[int, int]],
    ) -> tuple[int, int] | None:
        assert launcher_pid == 700
        assert previous_windows == frozenset({(1, 2)})
        return self.identity

    def matching_windows(self, _app: RegisteredApp) -> set[tuple[int, int]]:
        return set() if self.identity is None else {self.identity}


def browser(tmp_path: Path) -> RegisteredApp:
    executable = tmp_path / "browser.exe"
    executable.write_bytes(b"registered browser")
    return RegisteredApp.from_path("chrome", executable).model_copy(
        update={"single_instance_handoff": True, "window_class": "BrowserWindow"}
    )


def action(url: str = "https://github.com/openai/codex?q=private") -> Action:
    return Action(
        capability_id="browser.navigate",
        arguments={"browser_id": "chrome", "url": url},
        purpose="open an allowlisted reference",
        target_identity="browser:chrome:github.com",
        expected_postconditions=(Postcondition(predicate="browser.navigation.verified"),),
    )


def executor(
    tmp_path: Path,
    *,
    launcher: Launcher | None = None,
    lease: Lease | None = None,
    windows: Windows | None = None,
    inspector: Inspector | None = None,
) -> BrowserNavigateExecutor:
    app = browser(tmp_path)
    return BrowserNavigateExecutor(
        {"chrome": app},
        ("github.com",),
        launcher=launcher or Launcher(),
        lease=lease or Lease(),
        inspector=inspector or Inspector(app.executable),
        window_resolver=windows or Windows(),
    )


def test_navigation_opens_new_registered_window_without_observing_url(tmp_path: Path) -> None:
    launch = Launcher()
    navigate = executor(tmp_path, launcher=launch)
    request = action()

    result = navigate.execute(request)

    assert result.success
    assert launch.calls[0][1] == request.arguments["url"]
    assert result.observed["host"] == "github.com"
    assert result.observed["scheme"] == "https"
    assert result.observed["pid"] == 701
    assert result.observed["hwnd"] == 900
    assert "private" not in result.model_dump_json()
    assert request.arguments["url"] not in result.model_dump_json()


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/openai/codex",
        "https://evil.example/openai/codex",
        "https://user@github.com/openai/codex",
        "https://github.com:444/openai/codex",
        "https://github.com:/openai/codex",
        "https://github.com:0443/openai/codex",
        "https://github.com/openai/codex#fragment",
        "https://127.0.0.1/openai/codex",
        "https://github.com\\@evil.example/openai/codex",
        "https://github.com/한글",
        "https://github.com/" + "x" * 2_100,
    ],
)
def test_navigation_rejects_non_https_ambiguous_or_unlisted_urls(
    tmp_path: Path, url: str
) -> None:
    assert executor(tmp_path).execute(action(url)).error_code == "URL_NOT_ALLOWED"


def test_navigation_rejects_target_browser_and_postcondition_mismatch(tmp_path: Path) -> None:
    navigate = executor(tmp_path)
    wrong_target = action().model_copy(update={"target_identity": "browser:chrome:evil.example"})
    wrong_browser = action().model_copy(
        update={"arguments": {"browser_id": "edge", "url": "https://github.com"}}
    )
    wrong_postcondition = action().model_copy(
        update={"expected_postconditions": (Postcondition(predicate="process.exists"),)}
    )

    assert navigate.execute(wrong_target).error_code == "TARGET_IDENTITY_MISMATCH"
    assert navigate.execute(wrong_browser).error_code == "BROWSER_NOT_REGISTERED"
    assert navigate.execute(wrong_postcondition).error_code == "INVALID_POSTCONDITION"


def test_navigation_requires_exact_executable_and_one_new_window(tmp_path: Path) -> None:
    assert executor(tmp_path, lease=Lease(False)).execute(action()).error_code == (
        "BROWSER_IDENTITY_CHANGED"
    )
    failed = executor(tmp_path, windows=Windows(None)).execute(action())
    assert failed.error_code == "BROWSER_WINDOW_HANDOFF_FAILED"
    assert not failed.retry_safe


def test_stop_before_launch_is_safe_and_stop_after_launch_is_ambiguous(tmp_path: Path) -> None:
    before = CancellationToken()
    before.cancel()
    launch = Launcher()
    stopped_before = executor(tmp_path, launcher=launch).execute(action(), before)
    after = CancellationToken()
    stopped_after = executor(tmp_path, launcher=Launcher(cancel=after)).execute(action(), after)

    assert stopped_before.error_code == "STOP_REQUESTED"
    assert stopped_before.retry_safe
    assert launch.calls == []
    assert stopped_after.error_code == "STOP_REQUESTED_AFTER_INPUT"
    assert not stopped_after.retry_safe


def test_launch_exception_is_redacted_and_not_retry_safe(tmp_path: Path) -> None:
    result = executor(tmp_path, launcher=Launcher(fail=True)).execute(action())

    assert result.error_code == "BROWSER_NAVIGATION_STATE_UNKNOWN"
    assert not result.retry_safe
    assert "private launch detail" not in result.model_dump_json()


def test_independent_verifier_rechecks_binding_process_time_and_window(tmp_path: Path) -> None:
    app = browser(tmp_path)
    inspector = Inspector(app.executable)
    windows = Windows()
    navigate = BrowserNavigateExecutor(
        {"chrome": app},
        ("github.com",),
        launcher=Launcher(),
        lease=Lease(),
        inspector=inspector,
        window_resolver=windows,
    )
    request = action()
    result = navigate.execute(request)
    verifier = BrowserNavigationVerifier(
        navigate, inspector=inspector, window_resolver=windows
    )

    verified = verifier.verify(request, result)
    inspector.creation_time = 124
    reused = verifier.verify(request, result)

    assert verified.success
    assert verified.observed["host"] == "github.com"
    assert "private" not in verified.model_dump_json()
    assert not reused.success
    assert reused.reason == "BROWSER_PROCESS_IDENTITY_FAILED"


def test_constructor_rejects_invalid_host_and_non_handoff_browser(tmp_path: Path) -> None:
    app = browser(tmp_path)
    with pytest.raises(ValueError, match="canonical DNS"):
        BrowserNavigateExecutor({"chrome": app}, ("GitHub.com",))
    with pytest.raises(ValueError, match="handoff-capable"):
        BrowserNavigateExecutor(
            {"chrome": app.model_copy(update={"single_instance_handoff": False})},
            ("github.com",),
        )
