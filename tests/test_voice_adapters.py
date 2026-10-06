import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from javis_core import (
    AudioCaptureError,
    AudioFrame,
    CancellationToken,
    FasterWhisperSpeechToText,
    OpenWakeWordDetector,
    PcmEnergyVad,
    SoundDeviceFrameSource,
)


class WakeModel:
    def __init__(self, prediction: object) -> None:
        self.prediction = prediction
        self.inputs: list[object] = []

    def predict(self, audio: object) -> dict[str, object]:
        self.inputs.append(audio)
        return {"hey_jarvis": self.prediction}


@dataclass
class Segment:
    text: str


class WhisperModel:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.calls: list[tuple[object, dict[str, object]]] = []

    def transcribe(self, audio: object, **options: object):  # type: ignore[no-untyped-def]
        self.calls.append((audio, options))
        return iter(Segment(text) for text in self.texts), object()


def audio(raw: bytes = b"\x00\x00") -> AudioFrame:
    return AudioFrame(pcm_s16le=raw)


def test_openwakeword_selects_named_score_and_clamps() -> None:
    model = WakeModel(1.4)
    detector = OpenWakeWordDetector(model, "hey_jarvis", pcm_converter=lambda raw: raw)

    assert detector.score(audio()) == 1.0
    assert model.inputs == [b"\x00\x00"]


@pytest.mark.parametrize("prediction", [None, "bad", float("nan")])
def test_invalid_wake_score_fails_to_zero(prediction: object) -> None:
    detector = OpenWakeWordDetector(
        WakeModel(prediction),
        "hey_jarvis",
        pcm_converter=lambda raw: raw,
    )
    assert detector.score(audio()) == 0.0


def test_openwakeword_rejects_wrong_audio_format() -> None:
    detector = OpenWakeWordDetector(WakeModel(0.9), "hey_jarvis")
    with pytest.raises(ValueError, match="16 kHz"):
        detector.score(AudioFrame(pcm_s16le=b"\x00\x00", sample_rate=8_000))


def test_openwakeword_local_bundle_is_explicitly_onnx_and_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_path = tmp_path / "hey_jarvis.onnx"
    melspec = tmp_path / "melspectrogram.onnx"
    embedding = tmp_path / "embedding_model.onnx"
    for path in (model_path, melspec, embedding):
        path.write_bytes(b"fixture")
    calls: list[dict[str, object]] = []

    class Model:
        def __init__(self, **options: object) -> None:
            calls.append(options)

        def predict(self, audio: object) -> dict[str, object]:
            return {"hey_jarvis": 0.9}

    package = ModuleType("openwakeword")
    package.__path__ = []  # type: ignore[attr-defined]
    module = ModuleType("openwakeword.model")
    module.Model = Model  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openwakeword", package)
    monkeypatch.setitem(sys.modules, "openwakeword.model", module)

    detector = OpenWakeWordDetector.from_local_model(model_path, "hey_jarvis")

    assert detector.score(audio()) == 0.9
    assert calls == [
        {
            "wakeword_models": [str(model_path.resolve())],
            "inference_framework": "onnx",
            "melspec_model_path": str(melspec.resolve()),
            "embedding_model_path": str(embedding.resolve()),
        }
    ]


def test_openwakeword_local_bundle_rejects_missing_features(tmp_path: Path) -> None:
    model_path = tmp_path / "hey_jarvis.onnx"
    model_path.write_bytes(b"fixture")

    with pytest.raises(ValueError, match="melspectrogram"):
        OpenWakeWordDetector.from_local_model(model_path, "hey_jarvis")


def test_openwakeword_local_bundle_rejects_non_onnx_model(tmp_path: Path) -> None:
    model_path = tmp_path / "hey_jarvis.tflite"
    model_path.write_bytes(b"fixture")

    with pytest.raises(ValueError, match="ONNX"):
        OpenWakeWordDetector.from_local_model(model_path, "hey_jarvis")


def test_pcm_energy_vad_detects_signal() -> None:
    vad = PcmEnergyVad(rms_threshold=500)
    assert not vad.is_speech(audio(b"\x00\x00" * 8))
    assert vad.is_speech(audio((1000).to_bytes(2, "little", signed=True) * 8))


def test_faster_whisper_joins_segments_with_offline_options() -> None:
    model = WhisperModel([" 자비스", "크롬 열어 "])
    stt = FasterWhisperSpeechToText(model, audio_converter=lambda frames: b"converted")

    assert stt.transcribe((audio(),)) == "자비스 크롬 열어"
    converted, options = model.calls[0]
    assert converted == b"converted"
    assert options["language"] == "ko"
    assert options["condition_on_previous_text"] is False
    assert options["vad_filter"] is False


def test_faster_whisper_bounds_transcript() -> None:
    stt = FasterWhisperSpeechToText(
        WhisperModel(["too long"]),
        max_transcript_characters=3,
        audio_converter=lambda frames: b"converted",
    )
    with pytest.raises(ValueError, match="size"):
        stt.transcribe((audio(),))


class FakeStream:
    def __init__(self, configuration: dict[str, object], *, status: object = None) -> None:
        self.configuration = configuration
        self.status = status
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        callback = self.configuration["callback"]
        blocksize = self.configuration["blocksize"]
        assert callable(callback)
        assert isinstance(blocksize, int)
        callback(b"\x00\x00" * blocksize, blocksize, None, self.status)

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class StreamFactory:
    def __init__(self, *, status: object = None) -> None:
        self.status = status
        self.streams: list[FakeStream] = []

    def __call__(self, **configuration: object) -> FakeStream:
        stream = FakeStream(configuration, status=self.status)
        self.streams.append(stream)
        return stream


def test_microphone_source_yields_bounded_frame_and_cleans_up() -> None:
    factory = StreamFactory()
    source = SoundDeviceFrameSource(stream_factory=factory)

    frames = list(source.frames(CancellationToken(), max_frames=1))

    assert len(frames) == 1
    assert frames[0].sample_rate == 16_000
    assert len(frames[0].pcm_s16le) == 2_560
    assert factory.streams[0].stopped
    assert factory.streams[0].closed


def test_microphone_device_error_fails_and_cleans_up() -> None:
    factory = StreamFactory(status="overflow")
    source = SoundDeviceFrameSource(stream_factory=factory)

    with pytest.raises(AudioCaptureError, match="STATUS"):
        list(source.frames(CancellationToken(), max_frames=1))

    assert factory.streams[0].stopped
    assert factory.streams[0].closed
