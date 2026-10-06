from __future__ import annotations

import ntpath
import os
import sys
from pathlib import Path
from typing import Any

from .explorer_folder_ipc import (
    ExplorerFolderResponse,
    ExplorerWorkerError,
    SubprocessExplorerFolderChecker,
    parse_explorer_request,
)


def _folder_matches(hwnd: int, expected_path: str) -> bool:
    if os.name != "nt":
        return False
    import pythoncom  # type: ignore[import-untyped]
    import win32com.client  # type: ignore[import-untyped]

    expected = Path(expected_path).resolve(strict=True)
    if not expected.is_dir():
        return False
    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    try:
        shell: Any = win32com.client.Dispatch("Shell.Application")
        windows: Any = shell.Windows()
        matches = 0
        for index in range(int(windows.Count)):
            window: Any = windows.Item(index)
            try:
                if int(window.HWND) != hwnd:
                    continue
                current = Path(str(window.Document.Folder.Self.Path)).resolve(strict=True)
                if current.is_dir() and ntpath.normcase(str(current)) == ntpath.normcase(
                    str(expected)
                ):
                    matches += 1
            except Exception:
                continue
        return matches == 1
    finally:
        pythoncom.CoUninitialize()


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(SubprocessExplorerFolderChecker._MAX_REQUEST_BYTES + 1)
        request = parse_explorer_request(raw)
        response = ExplorerFolderResponse(
            success=True,
            matches=_folder_matches(request.hwnd, request.expected_path),
        )
    except Exception:
        response = ExplorerFolderResponse(
            success=False,
            error_code="EXPLORER_WORKER_FAILED",
        )
    payload = response.model_dump_json().encode("utf-8")
    if len(payload) > SubprocessExplorerFolderChecker._MAX_RESPONSE_BYTES:
        raise ExplorerWorkerError("Explorer worker response exceeded size limit")
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
