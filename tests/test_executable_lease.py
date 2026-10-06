import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from javis_core import Action, ProcessLaunchExecutor, RegisteredApp
from javis_core.executable_lease import PortableExecutableLease, WindowsExecutableLease


class RaisingLauncher:
    def launch(
        self,
        executable: Path,
        *,
        fixed_argument: str | None,
        working_directory: Path,
        environment: Mapping[str, str],
    ) -> int:
        raise OSError("launch failed")


def action() -> Action:
    return Action(
        capability_id="process.launch",
        arguments={"app_id": "fixture"},
        purpose="test lease cleanup",
        target_identity="fixture",
    )


def test_portable_lease_rejects_changed_content(tmp_path: Path) -> None:
    executable = tmp_path / "fixture.exe"
    executable.write_bytes(b"original")
    app = RegisteredApp.from_path("fixture", executable)
    executable.write_bytes(b"changed")

    with PortableExecutableLease().hold(app) as verified:
        assert not verified


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics required")
def test_windows_lease_denies_write_and_replacement_while_held(tmp_path: Path) -> None:
    executable = tmp_path / "fixture.exe"
    replacement = tmp_path / "replacement.exe"
    executable.write_bytes(b"original")
    replacement.write_bytes(b"replacement")
    app = RegisteredApp.from_path("fixture", executable)

    with WindowsExecutableLease().hold(app) as verified:
        assert verified
        with pytest.raises(OSError):
            executable.write_bytes(b"changed")
        with pytest.raises(OSError):
            os.replace(replacement, executable)

    executable.write_bytes(b"changed-after-close")


@pytest.mark.skipif(os.name != "nt", reason="Windows handle cleanup required")
def test_launcher_exception_releases_windows_lease(tmp_path: Path) -> None:
    executable = tmp_path / "fixture.exe"
    executable.write_bytes(b"original")
    app = RegisteredApp.from_path("fixture", executable)
    executor = ProcessLaunchExecutor(
        {"fixture": app},
        launcher=RaisingLauncher(),
        lease=WindowsExecutableLease(),
    )

    result = executor.execute(action())

    assert result.error_code == "PROCESS_LAUNCH_FAILED"
    executable.write_bytes(b"released")


def test_path_comparison_rejects_different_file(tmp_path: Path) -> None:
    from javis_core.executable_lease import _same_windows_path

    left = tmp_path / "left.exe"
    right = tmp_path / "right.exe"
    left.write_bytes(b"same")
    right.write_bytes(b"same")

    assert not _same_windows_path(left, right)
