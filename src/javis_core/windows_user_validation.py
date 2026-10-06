from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, TextIO

from .cancellation import CancellationToken
from .intent import LocalStopDetector
from .local_approval import ConsentResult
from .product_composition import ProductVoiceStack, build_product_voice_stack
from .product_host import LoadedProductHostConfig
from .tts_ipc import TtsWorkerRunner
from .user_validation import ValidationMeasurement, ValidationRunner, ValidationStep
from .voice import CaptureStatus, VoiceCaptureResult
from .windows_hello import load_windows_hello_verifier

_MICROPHONE_SECONDS = 10
_TTS_PHRASE = "자비스 음성 안내 품질을 확인합니다."


class HumanAssessment(Protocol):
    def transcript_correct(self, transcript: str) -> bool: ...

    def tts_rating(self) -> int | None: ...


class TtsSpeaker(Protocol):
    def speak(self, text: str) -> None: ...


class HelloVerifier(Protocol):
    def request_verification(self, message: str) -> ConsentResult: ...


class ConsoleHumanAssessment:
    """Display transient transcript feedback; never persist it in the report."""

    def __init__(
        self,
        *,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
    ) -> None:
        self._input = input_stream or sys.stdin
        self._output = output_stream or sys.stdout

    def transcript_correct(self, transcript: str) -> bool:
        if not self._input.isatty():
            return False
        self._output.write(f"일시적 인식 결과: {transcript}\n정확하면 '예'를 입력하세요: ")
        self._output.flush()
        response = self._input.readline(16)
        return bool(response and response.strip() == "예")

    def tts_rating(self) -> int | None:
        if not self._input.isatty():
            return None
        self._output.write("방금 들은 고정 한국어 음성 품질을 1~5로 평가하세요: ")
        self._output.flush()
        response = self._input.readline(16)
        try:
            rating = int(response.strip())
        except (AttributeError, ValueError):
            return None
        return rating if 1 <= rating <= 5 else None


VoiceFactory = Callable[[LoadedProductHostConfig], ProductVoiceStack]
TtsFactory = Callable[[Path], TtsSpeaker]
HelloLoader = Callable[[Path, Path], HelloVerifier]


class WindowsUserValidationRunners:
    """Actual device callbacks; each closure is invoked only after fresh consent."""

    def __init__(
        self,
        configuration: LoadedProductHostConfig,
        project_root: Path,
        assessment: HumanAssessment,
        *,
        voice_factory: VoiceFactory = build_product_voice_stack,
        tts_factory: TtsFactory = TtsWorkerRunner,
        hello_loader: HelloLoader = load_windows_hello_verifier,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not project_root.is_absolute() or project_root.is_symlink():
            raise ValueError("validation project root must be absolute and non-symlink")
        root = project_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("validation project root must be a directory")
        self._configuration = configuration
        self._root = root
        self._assessment = assessment
        self._voice_factory = voice_factory
        self._tts_factory = tts_factory
        self._hello_loader = hello_loader
        self._timer = timer

    def build(self) -> Mapping[ValidationStep, ValidationRunner]:
        return {
            ValidationStep.MICROPHONE: self._microphone,
            ValidationStep.TTS_SPEAKER: self._tts_speaker,
            ValidationStep.WINDOWS_HELLO: self._windows_hello,
            ValidationStep.STOP_LATENCY: self._stop_latency,
        }

    def _microphone(self) -> ValidationMeasurement:
        result, _latency, failure_code = self._capture()
        if result is None:
            return ValidationMeasurement(passed=False, code=failure_code)
        if result.status is not CaptureStatus.TRANSCRIBED or result.transcript is None:
            return ValidationMeasurement(passed=False, code="VOICE_CAPTURE_ABORTED")
        correct = self._assessment.transcript_correct(result.transcript)
        return ValidationMeasurement(
            passed=correct,
            code="VOICE_CONFIRMED" if correct else "VOICE_NOT_CONFIRMED",
        )

    def _tts_speaker(self) -> ValidationMeasurement:
        state = self._root / "state"
        state.mkdir(parents=True, exist_ok=True)
        self._tts_factory(state.resolve(strict=True)).speak(_TTS_PHRASE)
        rating = self._assessment.tts_rating()
        if rating is None:
            return ValidationMeasurement(passed=False, code="TTS_RATING_INVALID")
        return ValidationMeasurement(
            passed=rating >= 3,
            code="TTS_ACCEPTED" if rating >= 3 else "TTS_REJECTED",
            rating=rating,
        )

    def _windows_hello(self) -> ValidationMeasurement:
        verifier = self._hello_loader(
            self._configuration.hello_manifest,
            self._root,
        )
        result = verifier.request_verification("JAVIS 사용자 참여 검증")
        return ValidationMeasurement(
            passed=result is ConsentResult.VERIFIED,
            code="HELLO_VERIFIED"
            if result is ConsentResult.VERIFIED
            else "HELLO_NOT_VERIFIED",
        )

    def _stop_latency(self) -> ValidationMeasurement:
        result, latency_ms, failure_code = self._capture()
        detected = (
            result is not None
            and result.status is CaptureStatus.TRANSCRIBED
            and result.transcript is not None
            and LocalStopDetector().matches(result.transcript)
        )
        return ValidationMeasurement(
            passed=detected,
            code=(
                "STOP_DETECTED"
                if detected
                else "STOP_NOT_DETECTED"
                if result is not None
                else failure_code
            ),
            latency_ms=latency_ms,
        )

    def _capture(self) -> tuple[VoiceCaptureResult | None, int, str]:
        voice = self._voice_factory(self._configuration)
        token = CancellationToken()
        frame_ms = self._configuration.settings.microphone_frame_ms
        max_frames = max(1, _MICROPHONE_SECONDS * 1_000 // frame_ms)
        started = self._timer()
        result: VoiceCaptureResult | None = None
        try:
            for frame in voice.frame_source.frames(token, max_frames=max_frames):
                result = voice.pipeline.feed(frame)
                if result is not None:
                    break
        finally:
            token.cancel()
            diagnostics = voice.pipeline.diagnostics
            voice.pipeline.cancel(reset_diagnostics=False)
        elapsed_ms = max(0, round((self._timer() - started) * 1_000))
        if result is not None:
            failure_code = "VOICE_CAPTURE_RESULT"
        elif diagnostics.frame_count == 0:
            failure_code = "AUDIO_NO_FRAMES"
        elif diagnostics.speech_frame_count == 0:
            failure_code = "AUDIO_NO_SPEECH"
        elif not diagnostics.wake_detected:
            failure_code = "WAKE_NOT_DETECTED"
        else:
            failure_code = "VOICE_UTTERANCE_INCOMPLETE"
        return result, min(elapsed_ms, 120_000), failure_code
