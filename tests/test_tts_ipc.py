import hashlib
import json
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from javis_core import CancellationToken
from javis_core.tts_ipc import (
    TtsError,
    TtsProbeRequest,
    TtsRenderRequest,
    TtsSpeakRequest,
    TtsWorkerResponse,
    TtsWorkerRunner,
    parse_worker_request,
    parse_worker_response,
)


class FakeProcess:
    def __init__(
        self,
        command: list[str],
        kwargs: dict[str, Any],
        *,
        corrupt_hash: bool = False,
    ) -> None:
        self.command = command
        self.kwargs = kwargs
        self.corrupt_hash = corrupt_hash
        self.returncode: int | None = None
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self.last_input: bytes | None = None

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, None]:
        assert input is not None
        self.last_input = input
        request = parse_worker_request(input)
        if isinstance(request, TtsRenderRequest):
            with wave.open(str(request.output_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16_000)
                wav.writeframes(b"\0\0" * 160)
            payload = request.output_path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            if self.corrupt_hash:
                digest = "0" * 64
            response = TtsWorkerResponse(
                success=True,
                operation="render_wav",
                wav_bytes=len(payload),
                wav_sha256=digest,
            )
        elif isinstance(request, TtsProbeRequest):
            response = TtsWorkerResponse(success=True, operation="probe")
        else:
            response = TtsWorkerResponse(success=True, operation="speak")
        self.returncode = 0
        return response.model_dump_json().encode(), None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -1

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        assert self.returncode is not None
        return self.returncode


class FakeProcessFactory:
    def __init__(self, *, corrupt_hash: bool = False) -> None:
        self.corrupt_hash = corrupt_hash
        self.calls: list[dict[str, Any]] = []
        self.process: FakeProcess | None = None

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        call = {"command": command, **kwargs}
        self.calls.append(call)
        self.process = FakeProcess(command, kwargs, corrupt_hash=self.corrupt_hash)
        return self.process  # type: ignore[return-value]


class BlockingProcess(FakeProcess):
    def __init__(self, command: list[str], kwargs: dict[str, Any]) -> None:
        super().__init__(command, kwargs)
        self.terminated = False
        self.communicate_calls = 0

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, None]:
        self.communicate_calls += 1
        time.sleep(min(timeout or 0, 0.01))
        raise subprocess.TimeoutExpired(self.command, timeout or 0.0)

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -1


class BlockingProcessFactory:
    def __init__(self) -> None:
        self.process: BlockingProcess | None = None

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
        self.process = BlockingProcess(command, kwargs)
        return self.process  # type: ignore[return-value]


class ImmediateBytesProcess(FakeProcess):
    def __init__(self, payload: bytes, *, returncode: int = 0) -> None:
        super().__init__(["worker"], {})
        self.payload = payload
        self.returncode = returncode

    def communicate(
        self, input: bytes | None = None, timeout: float | None = None
    ) -> tuple[bytes, None]:
        return self.payload, None


class KillRequiredProcess(BlockingProcess):
    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired(self.command, timeout or 0.0)
        return self.returncode


def test_request_models_enforce_text_rate_volume_and_plain_nul() -> None:
    with pytest.raises(ValidationError):
        TtsSpeakRequest(text="")
    with pytest.raises(ValidationError):
        TtsSpeakRequest(text="x" * 501)
    with pytest.raises(ValidationError):
        TtsSpeakRequest(text="bad\0text")
    with pytest.raises(ValidationError):
        TtsSpeakRequest(text="hello", rate=6)
    with pytest.raises(ValidationError):
        TtsSpeakRequest(text="hello", volume=101)


def test_strict_parser_rejects_duplicate_keys_nan_and_oversize() -> None:
    with pytest.raises(TtsError, match="duplicate"):
        parse_worker_request(b'{"operation":"speak","text":"a","text":"b"}')
    with pytest.raises(TtsError, match="non-finite"):
        parse_worker_request(
            b'{"operation":"speak","text":"a","rate":NaN,"volume":100}'
        )
    with pytest.raises(TtsError, match="size limit"):
        parse_worker_request(b"x" * (8 * 1024 + 1))


def test_speak_uses_fixed_isolated_worker_and_minimal_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "private")
    process = FakeProcessFactory()
    TtsWorkerRunner(tmp_path.resolve(), process_factory=process).speak("안녕하세요")
    call = process.calls[0]
    assert call["command"][1:] == ["-I", "-m", "javis_core.tts_worker"]
    assert call["shell"] is False
    assert "NVIDIA_API_KEY" not in call["env"]
    assert call["stderr"] is subprocess.DEVNULL


def test_probe_uses_worker_without_text_or_audio(tmp_path: Path) -> None:
    process = FakeProcessFactory()
    TtsWorkerRunner(tmp_path.resolve(), process_factory=process).probe()
    assert process.process is not None and process.process.last_input is not None
    request = parse_worker_request(process.process.last_input)
    assert isinstance(request, TtsProbeRequest)


def test_render_wav_rechecks_size_hash_and_wave_structure(tmp_path: Path) -> None:
    output = tmp_path / "speech.wav"
    response = TtsWorkerRunner(
        tmp_path.resolve(), process_factory=FakeProcessFactory()
    ).render_wav("테스트", output)
    assert output.is_file()
    assert response.wav_bytes == output.stat().st_size
    assert response.wav_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()


def test_render_rejects_existing_outside_nonwav_and_corrupt_metadata(tmp_path: Path) -> None:
    runner = TtsWorkerRunner(tmp_path.resolve(), process_factory=FakeProcessFactory())
    existing = tmp_path / "existing.wav"
    existing.write_bytes(b"existing")
    with pytest.raises(TtsError, match="must not already exist"):
        runner.render_wav("test", existing)
    with pytest.raises(TtsError, match="invalid"):
        runner.render_wav("test", tmp_path / "speech.mp3")
    with pytest.raises(TtsError, match="outside"):
        runner.render_wav("test", tmp_path.parent / "outside.wav")
    with pytest.raises(TtsError, match="metadata mismatch"):
        TtsWorkerRunner(
            tmp_path.resolve(), process_factory=FakeProcessFactory(corrupt_hash=True)
        ).render_wav("test", tmp_path / "corrupt.wav")
    assert not (tmp_path / "corrupt.wav").exists()


def test_cancel_and_timeout_fail_without_leaking_text(tmp_path: Path) -> None:
    token = CancellationToken()
    token.cancel()
    process = FakeProcessFactory()
    with pytest.raises(TtsError, match="cancelled"):
        TtsWorkerRunner(tmp_path.resolve(), process_factory=process).speak(
            "private text", cancellation=token
        )
    assert process.calls == []

    blocking = BlockingProcessFactory()
    active_token = CancellationToken()
    timer = threading.Timer(0.03, active_token.cancel)
    timer.start()
    try:
        with pytest.raises(TtsError, match="cancelled") as raised:
            TtsWorkerRunner(
                tmp_path.resolve(),
                process_factory=blocking,
                poll_interval_seconds=0.01,
            ).speak("private text", cancellation=active_token)
    finally:
        timer.cancel()
    assert "private text" not in str(raised.value)
    assert blocking.process is not None
    assert blocking.process.terminated
    assert blocking.process.returncode is not None


def test_response_contract_rejects_inconsistent_or_duplicate_metadata() -> None:
    with pytest.raises(ValidationError):
        TtsWorkerResponse(success=True, operation="render_wav")
    with pytest.raises(ValidationError):
        TtsWorkerResponse(success=True, operation="speak", wav_bytes=44)
    with pytest.raises(TtsError, match="duplicate"):
        parse_worker_response(
            json.dumps({"success": True, "operation": "speak"})[:-1].encode()
            + b',"success":true}'
        )


def test_worker_timeout_terminates_and_invalid_responses_are_redacted(tmp_path: Path) -> None:
    blocking = BlockingProcessFactory()
    with pytest.raises(TtsError, match="timed out"):
        TtsWorkerRunner(
            tmp_path.resolve(),
            timeout_seconds=0.02,
            poll_interval_seconds=0.01,
            process_factory=blocking,
        ).speak("secret")
    assert blocking.process is not None and blocking.process.terminated

    invalid = ImmediateBytesProcess(b"not-json")
    with pytest.raises(TtsError, match="invalid TTS IPC payload") as raised:
        TtsWorkerRunner(
            tmp_path.resolve(),
            process_factory=lambda *args, **kwargs: cast(
                subprocess.Popen[bytes], invalid
            ),
        ).speak("secret")
    assert "secret" not in str(raised.value)

    failed = ImmediateBytesProcess(
        TtsWorkerResponse(
            success=False, operation="speak", error_code="TTS_WORKER_FAILED"
        ).model_dump_json().encode()
    )
    with pytest.raises(TtsError, match="TTS_WORKER_FAILED"):
        TtsWorkerRunner(
            tmp_path.resolve(),
            process_factory=lambda *args, **kwargs: cast(
                subprocess.Popen[bytes], failed
            ),
        ).speak("secret")


def test_worker_start_failure_and_kill_fallback_are_bounded(tmp_path: Path) -> None:
    def fail_start(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        raise OSError("private internal failure")

    with pytest.raises(TtsError, match="failed or timed out") as raised:
        TtsWorkerRunner(tmp_path.resolve(), process_factory=fail_start).speak("secret")
    assert "private" not in str(raised.value)

    process = KillRequiredProcess(["worker"], {})
    TtsWorkerRunner._stop_process(process)  # type: ignore[arg-type]
    assert process.terminated
    assert process.returncode == -9


def test_runner_configuration_and_path_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timeout"):
        TtsWorkerRunner(tmp_path.resolve(), timeout_seconds=0)
    with pytest.raises(ValueError, match="poll interval"):
        TtsWorkerRunner(tmp_path.resolve(), poll_interval_seconds=0.001)
    with pytest.raises(ValueError, match="absolute"):
        TtsWorkerRunner(Path("relative"))
    with pytest.raises(ValueError, match="directory"):
        file_root = tmp_path / "file"
        file_root.write_text("x", encoding="utf-8")
        TtsWorkerRunner(file_root.resolve())
    with pytest.raises(TtsError, match="parent is unavailable"):
        TtsWorkerRunner(tmp_path.resolve()).render_wav(
            "test", tmp_path / "missing" / "speech.wav"
        )
