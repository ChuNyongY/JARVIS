from javis_core import AudioFrame, CaptureStatus, VoicePipeline


class FakeWakeDetector:
    def score(self, frame: AudioFrame) -> float:
        return 0.95 if frame.pcm_s16le.startswith(b"W") else 0.1


class FakeVad:
    def is_speech(self, frame: AudioFrame) -> bool:
        return not frame.pcm_s16le.startswith(b"S")


class FakeStt:
    def __init__(self, transcript: str = "자비스 크롬 열어", *, fail: bool = False) -> None:
        self.transcript = transcript
        self.fail = fail
        self.captured: tuple[AudioFrame, ...] = ()

    def transcribe(self, frames: tuple[AudioFrame, ...]) -> str:
        self.captured = frames
        if self.fail:
            raise RuntimeError("injected")
        return self.transcript


def frame(marker: bytes) -> AudioFrame:
    return AudioFrame(pcm_s16le=marker + b"0")


def pipeline(stt: FakeStt, **limits: int) -> VoicePipeline:
    return VoicePipeline(
        FakeWakeDetector(),
        FakeVad(),
        stt,
        end_silence_frames=limits.get("end_silence_frames", 2),
        max_capture_frames=limits.get("max_capture_frames", 10),
    )


def test_non_wake_audio_is_ignored() -> None:
    voice = pipeline(FakeStt())
    assert voice.feed(frame(b"N")) is None
    assert not voice.is_capturing
    assert voice.diagnostics.frame_count == 1
    assert voice.diagnostics.speech_frame_count == 1
    assert voice.diagnostics.max_wake_confidence == 0.1
    assert not voice.diagnostics.wake_detected


def test_wake_speech_and_silence_produce_transcript() -> None:
    stt = FakeStt()
    voice = pipeline(stt)

    assert voice.feed(frame(b"W")) is None
    assert voice.feed(frame(b"A")) is None
    assert voice.feed(frame(b"S")) is None
    result = voice.feed(frame(b"S"))

    assert result is not None
    assert result.status is CaptureStatus.TRANSCRIBED
    assert result.transcript == "자비스 크롬 열어"
    assert len(stt.captured) == 2
    assert not voice.is_capturing


def test_capture_buffer_is_bounded() -> None:
    voice = pipeline(FakeStt(), end_silence_frames=2, max_capture_frames=3)
    voice.feed(frame(b"W"))
    voice.feed(frame(b"A"))
    result = voice.feed(frame(b"A"))

    assert result is not None
    assert result.status is CaptureStatus.ABORTED
    assert result.reason == "MAX_CAPTURE_FRAMES_EXCEEDED"


def test_stt_failure_resets_pipeline() -> None:
    voice = pipeline(FakeStt(fail=True), end_silence_frames=1)
    voice.feed(frame(b"W"))
    result = voice.feed(frame(b"S"))

    assert result is not None
    assert result.status is CaptureStatus.ABORTED
    assert result.reason == "STT_FAILED"
    assert not voice.is_capturing


def test_manual_cancel_discards_audio_without_transcription() -> None:
    stt = FakeStt()
    voice = pipeline(stt)
    voice.feed(frame(b"W"))
    assert voice.is_capturing

    voice.cancel()

    assert not voice.is_capturing
    assert stt.captured == ()
    assert voice.diagnostics.frame_count == 0


def test_cancel_can_preserve_privacy_minimized_diagnostics() -> None:
    voice = pipeline(FakeStt())
    voice.feed(frame(b"N"))

    voice.cancel(reset_diagnostics=False)

    assert not voice.is_capturing
    assert voice.diagnostics.frame_count == 1
    assert voice.diagnostics.speech_frame_count == 1
    assert voice.diagnostics.max_wake_confidence == 0.1
