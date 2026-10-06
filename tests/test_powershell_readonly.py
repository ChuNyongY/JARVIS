import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from pytest import MonkeyPatch

from javis_core import Action, PowerShellReadOnlyExecutor


class FakeRunner:
    def __init__(
        self,
        *,
        stdout: bytes = b'{"entries":[],"count":0,"truncated":false}',
        returncode: int = 0,
        timeout: bool = False,
    ) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.timeout = timeout
        self.calls: list[tuple[tuple[str, ...], dict[str, str], float]] = []

    def run(
        self,
        arguments: Sequence[str],
        *,
        environment: Mapping[str, str],
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((tuple(arguments), dict(environment), timeout_seconds))
        if self.timeout:
            raise subprocess.TimeoutExpired(arguments, timeout_seconds)
        return subprocess.CompletedProcess(
            args=arguments,
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=b"sensitive stderr must not escape",
        )


def test_path_is_passed_only_through_limited_environment(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    runner = FakeRunner()
    executor = PowerShellReadOnlyExecutor((tmp_path,), runner=runner)
    target = tmp_path / "folder with spaces;$(unsafe)"
    target.mkdir()
    action = Action(
        capability_id="file.list",
        arguments={"path": str(target)},
        purpose="safe quoting test",
    )

    result = executor.execute(action)

    assert result.success
    arguments, environment, _timeout = runner.calls[0]
    assert str(target) not in " ".join(arguments)
    assert environment["JAVIS_TARGET_PATH"] == str(target.resolve())
    assert "-EncodedCommand" in arguments


def test_unrelated_environment_secret_is_not_inherited(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    runner = FakeRunner(stdout=b'{"system":"Windows"}')
    monkeypatch.setenv("NVIDIA_API_KEY", "must-not-leak")
    executor = PowerShellReadOnlyExecutor((tmp_path,), runner=runner)
    action = Action(capability_id="system.info.read", purpose="environment test")

    result = executor.execute(action)

    assert result.success
    assert "NVIDIA_API_KEY" not in runner.calls[0][1]


def test_outside_path_is_rejected_before_process_launch(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    runner = FakeRunner()
    executor = PowerShellReadOnlyExecutor((allowed,), runner=runner)
    action = Action(
        capability_id="file.list",
        arguments={"path": str(outside)},
        purpose="blocked",
    )

    result = executor.execute(action)

    assert not result.success
    assert result.error_code == "PATH_OUTSIDE_ALLOWED_ROOTS"
    assert runner.calls == []


def test_timeout_returns_stable_error_without_stderr(tmp_path: Path) -> None:
    runner = FakeRunner(timeout=True)
    executor = PowerShellReadOnlyExecutor((tmp_path,), runner=runner)
    action = Action(capability_id="system.info.read", purpose="timeout")

    result = executor.execute(action)

    assert not result.success
    assert result.error_code == "POWERSHELL_TIMEOUT"
    assert "stderr" not in (result.error_message or "")


def test_invalid_json_is_rejected(tmp_path: Path) -> None:
    runner = FakeRunner(stdout=b"not-json")
    executor = PowerShellReadOnlyExecutor((tmp_path,), runner=runner)
    action = Action(capability_id="system.info.read", purpose="invalid output")

    result = executor.execute(action)

    assert not result.success
    assert result.error_code == "INVALID_POWERSHELL_OUTPUT"
