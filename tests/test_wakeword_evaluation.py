from __future__ import annotations

import hashlib
import io
import json
import wave
from pathlib import Path

import pytest

from javis_core import AudioFrame
from javis_core.voice_evaluation import VoiceCorpusError
from javis_core.wakeword_evaluation import (
    WakeWordCorpusManifest,
    WakeWordCorpusSample,
    evaluate_wakeword_corpus,
    load_wakeword_corpus_manifest,
)


class SequencedDetector:
    def __init__(self, sequences: list[list[float | Exception]]) -> None:
        self._sequences = iter(sequences)
        self._current: iter[float | Exception] | None = None
        self.reset_calls = 0

    def reset(self) -> None:
        self.reset_calls += 1
        self._current = iter(next(self._sequences))

    def score(self, _frame: AudioFrame) -> float:
        assert self._current is not None
        result = next(self._current)
        if isinstance(result, Exception):
            raise result
        return result


def wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16_000)
        stream.writeframes(b"\x01\x00" * 1_600)
    return buffer.getvalue()


def prepare_manifest(
    tmp_path: Path,
    labels: tuple[bool, ...] = (True, False, True, False),
) -> tuple[WakeWordCorpusManifest, Path]:
    root = tmp_path / "corpus"
    root.mkdir()
    samples = []
    for index, label in enumerate(labels):
        raw = wav_bytes()
        filename = f"sample-{index}.wav"
        (root / filename).write_bytes(raw)
        samples.append(
            WakeWordCorpusSample(
                sample_id=f"sample-{index}",
                audio_file=filename,
                audio_sha256=hashlib.sha256(raw).hexdigest(),
                target_present=label,
                consent_record_id=f"consent-{index}",
            )
        )
    return WakeWordCorpusManifest(corpus_id="ko-wake", samples=tuple(samples)), root


def test_evaluation_computes_far_frr_and_resets_each_sample(tmp_path: Path) -> None:
    manifest, root = prepare_manifest(tmp_path)
    detector = SequencedDetector(
        [
            [0.2, 0.8],
            [0.7, 0.1],
            [0.1, 0.2],
            [0.1, 0.2],
        ]
    )

    report = evaluate_wakeword_corpus(manifest, root, detector, threshold=0.5)

    assert (
        report.true_positives,
        report.true_negatives,
        report.false_positives,
        report.false_negatives,
    ) == (1, 1, 1, 1)
    assert report.false_acceptance_rate == pytest.approx(0.5)
    assert report.false_rejection_rate == pytest.approx(0.5)
    assert report.samples[0].first_detection_ms == 100
    assert detector.reset_calls == len(manifest.samples)
    serialized = report.canonical_json()
    assert "sample-0.wav" not in serialized
    assert "consent-0" not in serialized
    assert "audio" not in serialized.casefold()


def test_inference_failures_receive_conservative_adverse_outcomes(
    tmp_path: Path,
) -> None:
    manifest, root = prepare_manifest(tmp_path, labels=(True, False))
    detector = SequencedDetector(
        [[RuntimeError("private positive")], [RuntimeError("private negative")]]
    )

    report = evaluate_wakeword_corpus(manifest, root, detector)

    assert report.inference_failures == 2
    assert report.false_negatives == 1
    assert report.false_positives == 1
    assert report.false_rejection_rate == 1
    assert report.false_acceptance_rate == 1
    assert all(item.detected is None for item in report.samples)
    assert "private" not in report.canonical_json()


def test_all_audio_is_validated_before_detector_is_reset(tmp_path: Path) -> None:
    manifest, root = prepare_manifest(tmp_path, labels=(True, False))
    invalid = manifest.model_copy(
        update={
            "samples": (
                manifest.samples[0],
                manifest.samples[1].model_copy(update={"audio_sha256": "0" * 64}),
            )
        }
    )
    detector = SequencedDetector([[0.9, 0.9], [0.1, 0.1]])

    with pytest.raises(VoiceCorpusError, match="hash"):
        evaluate_wakeword_corpus(invalid, root, detector)

    assert detector.reset_calls == 0


def test_manifest_rejects_unbalanced_duplicate_and_non_strict_labels(
    tmp_path: Path,
) -> None:
    balanced, _root = prepare_manifest(tmp_path, labels=(True, False))
    with pytest.raises(ValueError, match="positive and negative"):
        WakeWordCorpusManifest(
            corpus_id="only-positive",
            samples=(
                balanced.samples[0],
                balanced.samples[0].model_copy(update={"sample_id": "another"}),
            ),
        )
    with pytest.raises(ValueError, match="unique"):
        WakeWordCorpusManifest(
            corpus_id="duplicate",
            samples=(
                balanced.samples[0],
                balanced.samples[1].model_copy(update={"sample_id": "sample-0"}),
            ),
        )
    document = balanced.model_dump(mode="json")
    document["samples"][0]["target_present"] = "true"
    with pytest.raises(ValueError):
        WakeWordCorpusManifest.model_validate(document)


def test_manifest_loader_rejects_duplicate_json(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"schema_version":"1.0","schema_version":"1.0"}')

    with pytest.raises(VoiceCorpusError, match="duplicate"):
        load_wakeword_corpus_manifest(path)


def test_manifest_loader_round_trip(tmp_path: Path) -> None:
    manifest, _root = prepare_manifest(tmp_path, labels=(True, False))
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False),
        encoding="utf-8",
    )

    assert load_wakeword_corpus_manifest(path) == manifest


@pytest.mark.parametrize("threshold", [True, 1, 0.49, 1.0])
def test_evaluation_rejects_non_float_or_out_of_range_thresholds(
    tmp_path: Path,
    threshold: object,
) -> None:
    manifest, root = prepare_manifest(tmp_path, labels=(True, False))

    with pytest.raises(ValueError, match="threshold"):
        evaluate_wakeword_corpus(
            manifest,
            root,
            SequencedDetector([[0.9, 0.9], [0.1, 0.1]]),
            threshold=threshold,  # type: ignore[arg-type]
        )
