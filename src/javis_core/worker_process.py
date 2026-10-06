from __future__ import annotations

import sys
from pathlib import Path

TTS_WORKER_TOKEN = "__javis_tts_worker__"
UIA_WORKER_TOKEN = "__javis_uia_worker__"
EXPLORER_WORKER_TOKEN = "__javis_explorer_worker__"


class WorkerProcessError(RuntimeError):
    pass


def fixed_worker_command(module: str, frozen_token: str) -> list[str]:
    if module not in {
        "javis_core.tts_worker",
        "javis_core.uia_worker",
        "javis_core.explorer_folder_worker",
    }:
        raise WorkerProcessError("WORKER_MODULE_INVALID")
    if frozen_token not in {TTS_WORKER_TOKEN, UIA_WORKER_TOKEN, EXPLORER_WORKER_TOKEN}:
        raise WorkerProcessError("WORKER_TOKEN_INVALID")
    if not getattr(sys, "frozen", False):
        return [sys.executable, "-I", "-m", module]
    try:
        current = Path(sys.executable).resolve(strict=True)
        console = current.with_name("JAVIS.exe")
        if console.is_symlink():
            raise OSError("worker executable is a symlink")
        console = console.resolve(strict=True)
    except OSError as error:
        raise WorkerProcessError("FROZEN_WORKER_EXECUTABLE_INVALID") from error
    if not console.is_file():
        raise WorkerProcessError("FROZEN_WORKER_EXECUTABLE_INVALID")
    return [str(console), frozen_token]
