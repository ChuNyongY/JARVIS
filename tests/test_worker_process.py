from __future__ import annotations

import sys
from pathlib import Path

import pytest

from javis_core.worker_process import (
    EXPLORER_WORKER_TOKEN,
    TTS_WORKER_TOKEN,
    UIA_WORKER_TOKEN,
    WorkerProcessError,
    fixed_worker_command,
)


def test_source_worker_command_remains_isolated() -> None:
    assert fixed_worker_command("javis_core.tts_worker", TTS_WORKER_TOKEN) == [
        sys.executable,
        "-I",
        "-m",
        "javis_core.tts_worker",
    ]


def test_frozen_worker_uses_exact_console_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    console = tmp_path / "JAVIS.exe"
    console.write_bytes(b"fixture")
    tray = tmp_path / "JAVIS-Tray.exe"
    tray.write_bytes(b"fixture")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tray))

    command = fixed_worker_command("javis_core.uia_worker", UIA_WORKER_TOKEN)

    assert command == [str(console.resolve()), UIA_WORKER_TOKEN]


def test_worker_command_rejects_unregistered_module_or_token() -> None:
    with pytest.raises(WorkerProcessError, match="MODULE_INVALID"):
        fixed_worker_command("private.module", TTS_WORKER_TOKEN)
    with pytest.raises(WorkerProcessError, match="TOKEN_INVALID"):
        fixed_worker_command("javis_core.tts_worker", "private-token")


def test_explorer_worker_command_is_registered() -> None:
    assert fixed_worker_command(
        "javis_core.explorer_folder_worker", EXPLORER_WORKER_TOKEN
    ) == [sys.executable, "-I", "-m", "javis_core.explorer_folder_worker"]
