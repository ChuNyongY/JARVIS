from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from javis_core import (
    AudioFrame,
    CancellationToken,
    ConsentResult,
    LoadedProductHostConfig,
    ProductHostConfig,
    ProductVoiceStack,
    ValidationStep,
    VoicePipeline,
    WindowsUserValidationRunners,
)


class Frames:
    def frames(
        self, cancellation: CancellationToken, *, max_frames: int
    ) -> Iterable[AudioFrame]:
        assert not cancellation.is_cancelled
        assert max_frames == 125
        yield AudioFrame(pcm_s16le=b"\x01\x00")
        yield AudioFrame(pcm_s16le=b"\x01\x00")
        yield AudioFrame(pcm_s16le=b"\x00\x00")


class Wake:
    def score(self, _frame: AudioFrame) -> float:
        return 1.0


class Vad:
    def is_speech(self, frame: AudioFrame) -> bool:
        return frame.pcm_s16le != b"\x00\x00"


class Stt:
    def __init__(self, transcript: str) -> None:
        self.transcript = transcript

    def transcribe(self, _frames: tuple[AudioFrame, ...]) -> str:
        return self.transcript


class Assessment:
    def __init__(self, *, correct: bool = True, rating: int | None = 4) -> None:
        self.correct = correct
        self.rating = rating
        self.transcripts: list[str] = []

    def transcript_correct(self, transcript: str) -> bool:
        self.transcripts.append(transcript)
        return self.correct

    def tts_rating(self) -> int | None:
        return self.rating


class Speaker:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)


class Hello:
    def __init__(self, result: ConsentResult) -> None:
        self.result = result

    def request_verification(self, _message: str) -> ConsentResult:
        return self.result


class Timer:
    def __init__(self, values: list[float]) -> None:
        self.values = iter(values)

    def __call__(self) -> float:
        return next(self.values)


class NoWake:
    def score(self, _frame: AudioFrame) -> float:
        return 0.1


def configuration(tmp_path: Path) -> LoadedProductHostConfig:
    artifacts = []
    for name in ("apps.json", "hello.json", "wake.json", "stt.json"):
        path = tmp_path / name
        path.write_text("{}", encoding="utf-8")
        artifacts.append(path)
    return LoadedProductHostConfig(
        settings=ProductHostConfig(
            app_registry="config/apps.json",
            hello_manifest="config/hello.json",
            wakeword_manifest="models/wake.json",
            stt_manifest="models/stt.json",
            browser_allowed_hosts=("github.com",),
            nim_model="z-ai/glm-5.3",
            wake_threshold=0.75,
            vad_rms_threshold=500,
            end_silence_frames=5,
            max_capture_frames=1500,
            microphone_frame_ms=80,
            microphone_queue_capacity=32,
            hud_poll_interval_ms=250,
        ),
        app_registry=artifacts[0],
        hello_manifest=artifacts[1],
        wakeword_manifest=artifacts[2],
        stt_manifest=artifacts[3],
    )


def test_actual_runner_factory_uses_transient_voice_tts_hello_and_stop(
    tmp_path: Path,
) -> None:
    transcripts = iter(("헤이 자비스 시스템 정보", "자비스 스톱"))

    def voice_factory(_config: LoadedProductHostConfig) -> ProductVoiceStack:
        return ProductVoiceStack(
            frame_source=Frames(),
            pipeline=VoicePipeline(
                Wake(), Vad(), Stt(next(transcripts)), end_silence_frames=1
            ),
        )

    assessment = Assessment()
    speaker = Speaker()
    callbacks = WindowsUserValidationRunners(
        configuration(tmp_path),
        tmp_path.resolve(),
        assessment,
        voice_factory=voice_factory,
        tts_factory=lambda _root: speaker,
        hello_loader=lambda _manifest, _root: Hello(ConsentResult.VERIFIED),
        timer=Timer([1.0, 1.5, 2.0, 2.024]),
    ).build()

    microphone = callbacks[ValidationStep.MICROPHONE]()
    tts = callbacks[ValidationStep.TTS_SPEAKER]()
    hello = callbacks[ValidationStep.WINDOWS_HELLO]()
    stop = callbacks[ValidationStep.STOP_LATENCY]()

    assert microphone.passed and microphone.code == "VOICE_CONFIRMED"
    assert assessment.transcripts == ["헤이 자비스 시스템 정보"]
    assert tts.passed and tts.rating == 4
    assert speaker.spoken == ["자비스 음성 안내 품질을 확인합니다."]
    assert hello.passed and hello.code == "HELLO_VERIFIED"
    assert stop.passed and stop.latency_ms == 24


def test_actual_runners_report_human_rejection_without_faking_pass(tmp_path: Path) -> None:
    def voice_factory(_config: LoadedProductHostConfig) -> ProductVoiceStack:
        return ProductVoiceStack(
            frame_source=Frames(),
            pipeline=VoicePipeline(Wake(), Vad(), Stt("다른 말"), end_silence_frames=1),
        )

    callbacks = WindowsUserValidationRunners(
        configuration(tmp_path),
        tmp_path.resolve(),
        Assessment(correct=False, rating=2),
        voice_factory=voice_factory,
        tts_factory=lambda _root: Speaker(),
        hello_loader=lambda _manifest, _root: Hello(ConsentResult.CANCELED),
        timer=Timer([1.0, 1.1, 2.0, 2.1]),
    ).build()

    assert not callbacks[ValidationStep.MICROPHONE]().passed
    assert not callbacks[ValidationStep.TTS_SPEAKER]().passed
    assert not callbacks[ValidationStep.WINDOWS_HELLO]().passed
    assert not callbacks[ValidationStep.STOP_LATENCY]().passed


def test_microphone_distinguishes_wakeword_failure_from_missing_audio(
    tmp_path: Path,
) -> None:
    def voice_factory(_config: LoadedProductHostConfig) -> ProductVoiceStack:
        return ProductVoiceStack(
            frame_source=Frames(),
            pipeline=VoicePipeline(NoWake(), Vad(), Stt("사용되지 않음")),
        )

    callbacks = WindowsUserValidationRunners(
        configuration(tmp_path),
        tmp_path.resolve(),
        Assessment(),
        voice_factory=voice_factory,
        timer=Timer([1.0, 1.1]),
    ).build()

    result = callbacks[ValidationStep.MICROPHONE]()

    assert not result.passed
    assert result.code == "WAKE_NOT_DETECTED"
