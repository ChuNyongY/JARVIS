import hashlib
import io
import json
import wave
from pathlib import Path

import pytest

from javis_core import AudioFrame
from javis_core.voice_evaluation import (
    VoiceCorpusError,
    VoiceCorpusManifest,
    VoiceCorpusSample,
    evaluate_voice_corpus,
    load_voice_corpus_manifest,
    normalize_korean_transcript,
)


class StubStt:
    def __init__(self, outputs: list[str | Exception]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[AudioFrame, ...]] = []

    def transcribe(self, frames: tuple[AudioFrame, ...]) -> str:
        self.calls.append(frames)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


def wav_bytes(*, rate: int = 16_000, channels: int = 1, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(width)
        stream.setframerate(rate)
        stream.writeframes(b"\x00\x00" * 1_600)
    return buffer.getvalue()


def sample(raw: bytes, *, reference: str = "자비스 메모장 열어") -> VoiceCorpusSample:
    return VoiceCorpusSample(
        sample_id="sample-001",
        audio_file="speaker-a/sample.wav",
        audio_sha256=hashlib.sha256(raw).hexdigest(),
        reference_text=reference,
        reference_sha256=hashlib.sha256(reference.encode()).hexdigest(),
        consent_record_id="consent-001",
    )


def prepare_fixture(tmp_path: Path, raw: bytes) -> Path:
    root = tmp_path / "corpus"
    (root / "speaker-a").mkdir(parents=True)
    (root / "speaker-a" / "sample.wav").write_bytes(raw)
    return root


def test_evaluation_computes_metrics_without_exposing_text_or_path(tmp_path: Path) -> None:
    raw = wav_bytes()
    root = prepare_fixture(tmp_path, raw)
    manifest = VoiceCorpusManifest(corpus_id="ko-test", samples=(sample(raw),))
    stt = StubStt([" 자비스   메모장 열어 "])
    times = iter((1.0, 1.025))

    report = evaluate_voice_corpus(manifest, root, stt, clock=lambda: next(times))

    assert report.character_error_rate == 0
    assert report.word_error_rate == 0
    assert report.exact_matches == 1
    assert report.samples[0].latency_ms == pytest.approx(25)
    serialized = report.canonical_json()
    assert "자비스" not in serialized
    assert "sample.wav" not in serialized
    assert len(stt.calls[0]) == 2


def test_inference_failure_is_isolated_and_penalized(tmp_path: Path) -> None:
    raw = wav_bytes()
    root = prepare_fixture(tmp_path, raw)
    manifest = VoiceCorpusManifest(corpus_id="ko-test", samples=(sample(raw),))

    report = evaluate_voice_corpus(manifest, root, StubStt([RuntimeError("failed")]))

    assert report.inference_failures == 1
    assert report.character_error_rate == 1
    assert report.word_error_rate == 1
    assert report.samples[0].error_code == "STT_INFERENCE_FAILED"


def test_all_fixtures_are_validated_before_inference(tmp_path: Path) -> None:
    raw = wav_bytes()
    root = prepare_fixture(tmp_path, raw)
    manifest = VoiceCorpusManifest(
        corpus_id="ko-test",
        samples=(sample(raw).model_copy(update={"audio_sha256": "0" * 64}),),
    )
    stt = StubStt(["unused"])

    with pytest.raises(VoiceCorpusError, match="hash"):
        evaluate_voice_corpus(manifest, root, stt)

    assert stt.calls == []


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (wav_bytes(rate=8_000), "mono 16 kHz"),
        (wav_bytes(channels=2), "mono 16 kHz"),
        (b"not-wave", "valid WAV"),
    ],
)
def test_invalid_wav_format_is_rejected(tmp_path: Path, raw: bytes, message: str) -> None:
    root = prepare_fixture(tmp_path, raw)
    manifest = VoiceCorpusManifest(corpus_id="ko-test", samples=(sample(raw),))

    with pytest.raises(VoiceCorpusError, match=message):
        evaluate_voice_corpus(manifest, root, StubStt(["unused"]))


def test_manifest_rejects_traversal_reference_hash_and_duplicate_json(tmp_path: Path) -> None:
    raw = wav_bytes()
    base = sample(raw).model_dump(mode="json")
    with pytest.raises(ValueError, match="relative POSIX"):
        VoiceCorpusSample.model_validate({**base, "audio_file": "C:\\sample.wav"})
    with pytest.raises(ValueError, match="transcript hash"):
        VoiceCorpusSample.model_validate({**base, "reference_sha256": "0" * 64})
    blank = " \n "
    with pytest.raises(ValueError, match="must not be empty"):
        VoiceCorpusSample.model_validate(
            {
                **base,
                "reference_text": blank,
                "reference_sha256": hashlib.sha256(blank.encode()).hexdigest(),
            }
        )

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"schema_version":"1.0","schema_version":"1.0"}')
    with pytest.raises(VoiceCorpusError, match="duplicate"):
        load_voice_corpus_manifest(manifest_path)


def test_manifest_loader_round_trip(tmp_path: Path) -> None:
    raw = wav_bytes()
    expected = VoiceCorpusManifest(corpus_id="ko-test", samples=(sample(raw),))
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(expected.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )

    assert load_voice_corpus_manifest(path) == expected
    assert normalize_korean_transcript("  ＪＡＶＩＳ\n열어 ") == "JAVIS 열어"
