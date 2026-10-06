from __future__ import annotations

import hashlib
import sys
from importlib import import_module
from pathlib import Path
from typing import Literal

from .tts_ipc import (
    TtsRenderRequest,
    TtsWorkerResponse,
    _validate_output_path,
    _validate_root,
    parse_worker_request,
)


def main() -> int:
    operation: Literal["probe", "speak", "render_wav", "unknown"] = "unknown"
    output: Path | None = None
    try:
        request = parse_worker_request(sys.stdin.buffer.read(8 * 1024 + 1))
        operation = request.operation
        pythoncom = import_module("pythoncom")
        win32com_client = import_module("win32com.client")

        pythoncom.CoInitialize()
        try:
            voice = win32com_client.Dispatch("SAPI.SpVoice")
            if isinstance(request, TtsRenderRequest):
                voice.Rate = request.rate
                voice.Volume = request.volume
                root = _validate_root(request.allowed_root)
                output = _validate_output_path(request.output_path, root)
                stream = win32com_client.Dispatch("SAPI.SpFileStream")
                try:
                    stream.Open(str(output), 3, False)
                    voice.AudioOutputStream = stream
                    voice.Speak(request.text, 16)
                finally:
                    stream.Close()
                size = output.stat().st_size
                if not 44 <= size <= 100_000_000:
                    raise ValueError("rendered WAV size is invalid")
                response = TtsWorkerResponse(
                    success=True,
                    operation="render_wav",
                    wav_bytes=size,
                    wav_sha256=_file_hash(output),
                )
            elif request.operation == "speak":
                voice.Rate = request.rate
                voice.Volume = request.volume
                voice.Speak(request.text, 16)
                response = TtsWorkerResponse(success=True, operation="speak")
            else:
                response = TtsWorkerResponse(success=True, operation="probe")
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        if output is not None:
            try:
                if output.is_file() and not output.is_symlink():
                    output.unlink()
            except OSError:
                pass
        response = TtsWorkerResponse(
            success=False,
            operation=operation,
            error_code="TTS_WORKER_FAILED",
        )
    encoded = response.model_dump_json().encode("utf-8")
    if len(encoded) > 8 * 1024:
        return 2
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()
    return 0


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
