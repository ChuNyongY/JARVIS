from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, TypeAdapter, model_validator

from .cancellation import CancellationToken
from .models import FrozenModel
from .worker_process import TTS_WORKER_TOKEN, fixed_worker_command


class TtsError(RuntimeError):
    pass


class _TtsRequestFields(FrozenModel):
    text: str = Field(min_length=1, max_length=500, repr=False)
    rate: int = Field(default=0, ge=-5, le=5)
    volume: int = Field(default=100, ge=0, le=100)

    @model_validator(mode="after")
    def reject_nul(self) -> _TtsRequestFields:
        if "\0" in self.text:
            raise ValueError("TTS text contains NUL")
        return self


class TtsProbeRequest(FrozenModel):
    operation: Literal["probe"] = "probe"


class TtsSpeakRequest(_TtsRequestFields):
    operation: Literal["speak"] = "speak"


class TtsRenderRequest(_TtsRequestFields):
    operation: Literal["render_wav"] = "render_wav"
    output_path: Path
    allowed_root: Path


TtsWorkerRequest = TtsProbeRequest | TtsSpeakRequest | TtsRenderRequest


class TtsWorkerResponse(FrozenModel):
    success: bool
    operation: Literal["probe", "speak", "render_wav", "unknown"]
    wav_bytes: int | None = Field(default=None, ge=44, le=100_000_000)
    wav_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_code: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def bind_render_metadata(self) -> TtsWorkerResponse:
        has_wav = self.wav_bytes is not None and self.wav_sha256 is not None
        if self.success and self.operation == "render_wav" and not has_wav:
            raise ValueError("successful render requires WAV metadata")
        if self.operation != "render_wav" and (
            self.wav_bytes is not None or self.wav_sha256 is not None
        ):
            raise ValueError("non-render response cannot contain WAV metadata")
        if self.success == (self.error_code is not None):
            raise ValueError("TTS response success/error mismatch")
        return self


ProcessFactory = Callable[..., subprocess.Popen[bytes]]


class TtsWorkerRunner:
    _MAX_REQUEST_BYTES = 8 * 1024
    _MAX_RESPONSE_BYTES = 8 * 1024

    def __init__(
        self,
        allowed_root: Path,
        *,
        timeout_seconds: float = 15.0,
        poll_interval_seconds: float = 0.05,
        process_factory: ProcessFactory = subprocess.Popen,
    ) -> None:
        self._allowed_root = _validate_root(allowed_root)
        if not 0 < timeout_seconds <= 30:
            raise ValueError("TTS worker timeout must be within 0..30 seconds")
        if not 0.01 <= poll_interval_seconds <= 0.25:
            raise ValueError("TTS worker poll interval must be within 0.01..0.25 seconds")
        self._timeout = timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._process_factory = process_factory

    def speak(
        self,
        text: str,
        *,
        rate: int = 0,
        volume: int = 100,
        cancellation: CancellationToken | None = None,
    ) -> None:
        if cancellation is not None and cancellation.is_cancelled:
            raise TtsError("TTS cancelled before start")
        response = self._run(
            TtsSpeakRequest(text=text, rate=rate, volume=volume), cancellation=cancellation
        )
        if response.operation != "speak" or response.wav_bytes is not None:
            raise TtsError("TTS worker response operation mismatch")

    def probe(self) -> None:
        response = self._run(TtsProbeRequest(), cancellation=None)
        if response.operation != "probe" or response.wav_bytes is not None:
            raise TtsError("TTS worker response operation mismatch")

    def render_wav(
        self,
        text: str,
        output_path: Path,
        *,
        rate: int = 0,
        volume: int = 100,
        cancellation: CancellationToken | None = None,
    ) -> TtsWorkerResponse:
        if cancellation is not None and cancellation.is_cancelled:
            raise TtsError("TTS cancelled before start")
        output = _validate_output_path(output_path, self._allowed_root)
        request = TtsRenderRequest(
            text=text,
            rate=rate,
            volume=volume,
            output_path=output,
            allowed_root=self._allowed_root,
        )
        response = self._run(request, cancellation=cancellation)
        if response.operation != "render_wav":
            _remove_partial_output(request)
            raise TtsError("TTS worker response operation mismatch")
        try:
            resolved = output.resolve(strict=True)
        except OSError as error:
            raise TtsError("TTS worker did not create WAV output") from error
        if resolved != output or output.is_symlink() or not output.is_file():
            _remove_partial_output(request)
            raise TtsError("TTS WAV output identity mismatch")
        size = output.stat().st_size
        digest = _file_hash(output)
        if size != response.wav_bytes or digest != response.wav_sha256:
            _remove_partial_output(request)
            raise TtsError("TTS WAV output metadata mismatch")
        try:
            with wave.open(str(output), "rb") as wav:
                if wav.getnchannels() < 1 or wav.getframerate() < 8_000 or wav.getnframes() < 1:
                    _remove_partial_output(request)
                    raise TtsError("TTS WAV output is empty or malformed")
        except (OSError, wave.Error) as error:
            _remove_partial_output(request)
            raise TtsError("TTS WAV output is malformed") from error
        return response

    def _run(
        self,
        request: TtsWorkerRequest,
        *,
        cancellation: CancellationToken | None,
    ) -> TtsWorkerResponse:
        payload = request.model_dump_json().encode("utf-8")
        if len(payload) > self._MAX_REQUEST_BYTES:
            raise TtsError("TTS worker request exceeded size limit")
        environment = {
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP")
            if key in os.environ
        }
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW
        try:
            process = self._process_factory(
                fixed_worker_command("javis_core.tts_worker", TTS_WORKER_TOKEN),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                env=environment,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except OSError as error:
            _remove_partial_output(request)
            raise TtsError("TTS worker failed or timed out") from error

        deadline = time.monotonic() + self._timeout
        first_communicate = True
        stdout = b""
        while True:
            if cancellation is not None and cancellation.is_cancelled:
                self._stop_process(process)
                _remove_partial_output(request)
                raise TtsError("TTS cancelled during execution")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._stop_process(process)
                _remove_partial_output(request)
                raise TtsError("TTS worker failed or timed out")
            try:
                stdout, _ = process.communicate(
                    input=payload if first_communicate else None,
                    timeout=min(self._poll_interval, remaining),
                )
                break
            except subprocess.TimeoutExpired:
                first_communicate = False
            except OSError as error:
                self._stop_process(process)
                _remove_partial_output(request)
                raise TtsError("TTS worker failed or timed out") from error

        if cancellation is not None and cancellation.is_cancelled:
            _remove_partial_output(request)
            raise TtsError("TTS cancelled during execution")
        if process.returncode != 0 or stdout is None or len(stdout) > self._MAX_RESPONSE_BYTES:
            _remove_partial_output(request)
            raise TtsError("TTS worker returned an invalid response")
        try:
            response = parse_worker_response(stdout)
        except TtsError:
            _remove_partial_output(request)
            raise
        if not response.success:
            _remove_partial_output(request)
            raise TtsError(response.error_code or "TTS_WORKER_FAILED")
        return response

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired as error:
                raise TtsError("TTS worker could not be stopped") from error
        except OSError as error:
            raise TtsError("TTS worker could not be stopped") from error
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


def parse_worker_request(raw: bytes) -> TtsWorkerRequest:
    return cast(
        TtsWorkerRequest,
        _parse_model(raw, TtsWorkerRequest, TtsWorkerRunner._MAX_REQUEST_BYTES),
    )


def parse_worker_response(raw: bytes) -> TtsWorkerResponse:
    return cast(
        TtsWorkerResponse,
        _parse_model(raw, TtsWorkerResponse, TtsWorkerRunner._MAX_RESPONSE_BYTES),
    )


def _parse_model(raw: bytes, model: Any, maximum: int) -> Any:
    if len(raw) > maximum:
        raise TtsError("TTS IPC payload exceeded size limit")
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        return TypeAdapter(model).validate_python(document)
    except TtsError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise TtsError("invalid TTS IPC payload") from error


def _validate_root(root: Path) -> Path:
    if not root.is_absolute() or root.is_symlink():
        raise ValueError("TTS allowed root must be absolute and non-symlink")
    try:
        resolved = root.resolve(strict=True)
    except OSError as error:
        raise ValueError("TTS allowed root is unavailable") from error
    if not resolved.is_dir():
        raise ValueError("TTS allowed root must be a directory")
    return resolved


def _validate_output_path(path: Path, root: Path) -> Path:
    if not path.is_absolute() or path.suffix.casefold() != ".wav" or path.is_symlink():
        raise TtsError("TTS output path is invalid")
    if path.exists():
        raise TtsError("TTS output must not already exist")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError as error:
        raise TtsError("TTS output parent is unavailable") from error
    if parent != root and root not in parent.parents:
        raise TtsError("TTS output is outside the allowed root")
    return parent / path.name


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TtsError("duplicate TTS IPC JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise TtsError(f"non-finite TTS JSON number: {value}")


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_partial_output(request: TtsWorkerRequest) -> None:
    if not isinstance(request, TtsRenderRequest):
        return
    output = request.output_path
    try:
        if output.is_file() and not output.is_symlink():
            output.unlink()
    except OSError:
        pass
