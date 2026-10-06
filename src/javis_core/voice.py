from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import Field

from .models import FrozenModel


class AudioFrame(FrozenModel):
    pcm_s16le: bytes = Field(min_length=2, repr=False)
    sample_rate: int = Field(default=16_000, ge=8_000, le=48_000)
    channels: int = Field(default=1, ge=1, le=2)


class CaptureStatus(StrEnum):
    TRANSCRIBED = "TRANSCRIBED"
    ABORTED = "ABORTED"


class VoiceCaptureResult(FrozenModel):
    status: CaptureStatus
    transcript: str | None = None
    wake_confidence: float = Field(ge=0.0, le=1.0)
    frame_count: int = Field(ge=0)
    reason: str


class VoicePipelineDiagnostics(FrozenModel):
    """Privacy-minimized counters for diagnosing an unfinished capture."""

    frame_count: int = Field(ge=0)
    speech_frame_count: int = Field(ge=0)
    max_wake_confidence: float = Field(ge=0.0, le=1.0)
    wake_detected: bool


class WakeWordDetector(Protocol):
    def score(self, frame: AudioFrame) -> float: ...


class VoiceActivityDetector(Protocol):
    def is_speech(self, frame: AudioFrame) -> bool: ...


class SpeechToText(Protocol):
    def transcribe(self, frames: tuple[AudioFrame, ...]) -> str: ...


class VoicePipeline:
    """Bounded wake/VAD/STT state machine with no microphone or network dependency."""

    def __init__(
        self,
        wake_detector: WakeWordDetector,
        vad: VoiceActivityDetector,
        stt: SpeechToText,
        *,
        wake_threshold: float = 0.75,
        end_silence_frames: int = 5,
        max_capture_frames: int = 1_500,
    ) -> None:
        if not 0.0 <= wake_threshold <= 1.0:
            raise ValueError("wake_threshold must be between 0 and 1")
        if end_silence_frames < 1 or max_capture_frames < end_silence_frames:
            raise ValueError("invalid voice capture limits")
        self._wake_detector = wake_detector
        self._vad = vad
        self._stt = stt
        self._wake_threshold = wake_threshold
        self._end_silence_frames = end_silence_frames
        self._max_capture_frames = max_capture_frames
        self._frames: list[AudioFrame] = []
        self._silence_frames = 0
        self._wake_confidence = 0.0
        self._diagnostic_frame_count = 0
        self._diagnostic_speech_frame_count = 0
        self._diagnostic_max_wake_confidence = 0.0
        self._diagnostic_wake_detected = False

    @property
    def is_capturing(self) -> bool:
        return bool(self._frames)

    @property
    def diagnostics(self) -> VoicePipelineDiagnostics:
        return VoicePipelineDiagnostics(
            frame_count=self._diagnostic_frame_count,
            speech_frame_count=self._diagnostic_speech_frame_count,
            max_wake_confidence=self._diagnostic_max_wake_confidence,
            wake_detected=self._diagnostic_wake_detected,
        )

    def feed(self, frame: AudioFrame) -> VoiceCaptureResult | None:
        speech = self._vad.is_speech(frame)
        self._diagnostic_frame_count += 1
        if speech:
            self._diagnostic_speech_frame_count += 1
        if not self.is_capturing:
            wake_confidence = self._wake_detector.score(frame)
            self._diagnostic_max_wake_confidence = max(
                self._diagnostic_max_wake_confidence,
                wake_confidence,
            )
            if not speech or wake_confidence < self._wake_threshold:
                return None
            self._frames.append(frame)
            self._wake_confidence = wake_confidence
            self._diagnostic_wake_detected = True
            return None

        self._frames.append(frame)
        self._silence_frames = 0 if speech else self._silence_frames + 1
        if len(self._frames) >= self._max_capture_frames:
            return self._finish(
                CaptureStatus.ABORTED,
                transcript=None,
                reason="MAX_CAPTURE_FRAMES_EXCEEDED",
            )
        if self._silence_frames < self._end_silence_frames:
            return None

        captured = tuple(self._frames[: -self._silence_frames])
        try:
            transcript = self._stt.transcribe(captured).strip()
        except Exception:
            return self._finish(
                CaptureStatus.ABORTED,
                transcript=None,
                reason="STT_FAILED",
            )
        if not transcript:
            return self._finish(
                CaptureStatus.ABORTED,
                transcript=None,
                reason="EMPTY_TRANSCRIPT",
            )
        return self._finish(
            CaptureStatus.TRANSCRIBED,
            transcript=transcript,
            reason="UTTERANCE_COMPLETE",
        )

    def cancel(self, *, reset_diagnostics: bool = True) -> None:
        self._reset()
        if reset_diagnostics:
            self._reset_diagnostics()

    def _finish(
        self,
        status: CaptureStatus,
        *,
        transcript: str | None,
        reason: str,
    ) -> VoiceCaptureResult:
        result = VoiceCaptureResult(
            status=status,
            transcript=transcript,
            wake_confidence=self._wake_confidence,
            frame_count=len(self._frames),
            reason=reason,
        )
        self._reset()
        self._reset_diagnostics()
        return result

    def _reset(self) -> None:
        self._frames.clear()
        self._silence_frames = 0
        self._wake_confidence = 0.0

    def _reset_diagnostics(self) -> None:
        self._diagnostic_frame_count = 0
        self._diagnostic_speech_frame_count = 0
        self._diagnostic_max_wake_confidence = 0.0
        self._diagnostic_wake_detected = False

