from __future__ import annotations

import sys

from .notepad_adapter import PywinautoNotepadTextBackend
from .uia_ipc import (
    UiaFindRequest,
    UiaReplaceRequest,
    UiaTextHashRequest,
    UiaWorkerError,
    UiaWorkerResponse,
    parse_worker_request,
)
from .uia_observation import PywinautoUiaBackend


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(200 * 1024 + 1)
        request = parse_worker_request(raw)
        if isinstance(request, UiaFindRequest):
            snapshots = PywinautoUiaBackend().find(
                request.selector,
                max_results=request.max_results,
            )
            response = UiaWorkerResponse(
                success=True,
                operation="find",
                snapshots=snapshots,
            )
        elif isinstance(request, UiaReplaceRequest):
            PywinautoNotepadTextBackend().replace_text(
                request.selector,
                request.runtime_id,
                request.text,
            )
            response = UiaWorkerResponse(success=True, operation="replace")
        else:
            assert isinstance(request, UiaTextHashRequest)
            text_hash = PywinautoNotepadTextBackend().text_sha256(
                request.selector,
                request.runtime_id,
            )
            response = UiaWorkerResponse(
                success=True,
                operation="text_hash",
                text_sha256=text_hash,
            )
    except Exception:
        response = UiaWorkerResponse(
            success=False,
            operation="find",
            error_code="UIA_WORKER_FAILED",
        )
    payload = response.model_dump_json().encode("utf-8")
    if len(payload) > 64 * 1024:
        raise UiaWorkerError("UIA worker response exceeded size limit")
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
