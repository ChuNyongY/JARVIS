from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from typing import Literal, Protocol

from pydantic import Field, TypeAdapter

from .models import FrozenModel
from .uia_observation import UiaElementSnapshot, UiaSelector
from .worker_process import UIA_WORKER_TOKEN, fixed_worker_command


class UiaWorkerError(RuntimeError):
    pass


class UiaFindRequest(FrozenModel):
    operation: Literal["find"] = "find"
    selector: UiaSelector
    max_results: int = Field(ge=1, le=100)


class UiaReplaceRequest(FrozenModel):
    operation: Literal["replace"] = "replace"
    selector: UiaSelector
    runtime_id: tuple[int, ...] = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=100_000, repr=False)


class UiaTextHashRequest(FrozenModel):
    operation: Literal["text_hash"] = "text_hash"
    selector: UiaSelector
    runtime_id: tuple[int, ...] = Field(min_length=1, max_length=64)


UiaWorkerRequest = UiaFindRequest | UiaReplaceRequest | UiaTextHashRequest


class UiaWorkerResponse(FrozenModel):
    success: bool
    operation: Literal["find", "replace", "text_hash"]
    snapshots: tuple[UiaElementSnapshot, ...] = ()
    text_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_code: str | None = Field(default=None, max_length=64)


class UiaRequestRunner(Protocol):
    def run(self, request: UiaWorkerRequest) -> UiaWorkerResponse: ...


ProcessRunner = Callable[..., subprocess.CompletedProcess[bytes]]


class UiaWorkerRunner:
    _MAX_REQUEST_BYTES = 200 * 1024
    _MAX_RESPONSE_BYTES = 64 * 1024

    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        process_runner: ProcessRunner = subprocess.run,
    ) -> None:
        if not 0 < timeout_seconds <= 10:
            raise ValueError("UIA worker timeout must be within 0..10 seconds")
        self._timeout = timeout_seconds
        self._process_runner = process_runner

    def run(self, request: UiaWorkerRequest) -> UiaWorkerResponse:
        payload = request.model_dump_json().encode("utf-8")
        if len(payload) > self._MAX_REQUEST_BYTES:
            raise UiaWorkerError("UIA worker request exceeded size limit")
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
            result = self._process_runner(
                fixed_worker_command("javis_core.uia_worker", UIA_WORKER_TOKEN),
                input=payload,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=self._timeout,
                check=False,
                shell=False,
                env=environment,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UiaWorkerError("UIA worker failed or timed out") from exc
        if result.returncode != 0 or len(result.stdout) > self._MAX_RESPONSE_BYTES:
            raise UiaWorkerError("UIA worker returned an invalid response")
        try:
            response = UiaWorkerResponse.model_validate_json(result.stdout)
        except ValueError as exc:
            raise UiaWorkerError("UIA worker returned malformed JSON") from exc
        if not response.success:
            raise UiaWorkerError(response.error_code or "UIA_WORKER_FAILED")
        return response


class SubprocessUiaBackend:
    def __init__(self, runner: UiaRequestRunner | None = None) -> None:
        self._runner = runner or UiaWorkerRunner()

    def find(
        self,
        selector: UiaSelector,
        *,
        max_results: int,
    ) -> tuple[UiaElementSnapshot, ...]:
        response = self._runner.run(
            UiaFindRequest(selector=selector, max_results=max_results)
        )
        if response.operation != "find" or response.text_sha256 is not None:
            raise UiaWorkerError("UIA worker response operation mismatch")
        return response.snapshots


class SubprocessNotepadTextBackend:
    def __init__(self, runner: UiaRequestRunner | None = None) -> None:
        self._runner = runner or UiaWorkerRunner()

    def replace_text(
        self,
        selector: UiaSelector,
        expected_runtime_id: tuple[int, ...],
        text: str,
    ) -> None:
        response = self._runner.run(
            UiaReplaceRequest(
                selector=selector,
                runtime_id=expected_runtime_id,
                text=text,
            )
        )
        if response.operation != "replace" or response.snapshots or response.text_sha256:
            raise UiaWorkerError("UIA worker response operation mismatch")

    def text_sha256(
        self,
        selector: UiaSelector,
        expected_runtime_id: tuple[int, ...],
    ) -> str:
        response = self._runner.run(
            UiaTextHashRequest(selector=selector, runtime_id=expected_runtime_id)
        )
        if response.operation != "text_hash" or response.snapshots:
            raise UiaWorkerError("UIA worker response operation mismatch")
        if response.text_sha256 is None:
            raise UiaWorkerError("UIA worker omitted text hash")
        return response.text_sha256


def parse_worker_request(raw: bytes) -> UiaWorkerRequest:
    if len(raw) > UiaWorkerRunner._MAX_REQUEST_BYTES:
        raise UiaWorkerError("UIA worker request exceeded size limit")
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        return TypeAdapter(UiaWorkerRequest).validate_python(document)
    except UiaWorkerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise UiaWorkerError("invalid UIA worker request") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise UiaWorkerError("duplicate UIA worker JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise UiaWorkerError(f"non-finite JSON number: {value}")
