from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Protocol

from pydantic import Field, StrictBool, TypeAdapter, model_validator

from .models import FrozenModel
from .voice import AudioFrame
from .voice_evaluation import VoiceCorpusError, load_audio_fixtures


class ResettableWakeWordDetector(Protocol):
    def score(self, frame: AudioFrame) -> float: ...

    def reset(self) -> None: ...


class WakeWordCorpusSample(FrozenModel):
    sample_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    audio_file: str = Field(min_length=1, max_length=512)
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_present: StrictBool
    consent_record_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
    )

    @model_validator(mode="after")
    def audio_path_is_portable_and_relative(self) -> WakeWordCorpusSample:
        value = self.audio_file
        if "\\" in value or ":" in value or "\0" in value:
            raise ValueError("audio_file must use a relative POSIX path")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("audio_file must not traverse directories")
        return self


class WakeWordCorpusManifest(FrozenModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    corpus_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    locale: str = Field(default="ko-KR", pattern=r"^ko-KR$")
    samples: tuple[WakeWordCorpusSample, ...] = Field(min_length=2, max_length=1_000)

    @model_validator(mode="after")
    def samples_are_unique_and_balanced(self) -> WakeWordCorpusManifest:
        identifiers = tuple(sample.sample_id for sample in self.samples)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("sample_id values must be unique")
        labels = {sample.target_present for sample in self.samples}
        if labels != {False, True}:
            raise ValueError("wake-word corpus requires positive and negative samples")
        return self


class WakeWordSampleReport(FrozenModel):
    sample_id: str
    target_present: StrictBool
    detected: StrictBool | None
    max_score: float | None = Field(default=None, ge=0.0, le=1.0)
    first_detection_ms: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{2,64}$")

    @model_validator(mode="after")
    def result_is_consistent(self) -> WakeWordSampleReport:
        failed = self.error_code is not None
        if failed != (self.detected is None or self.max_score is None):
            raise ValueError("wake-word sample result is inconsistent")
        if self.first_detection_ms is not None and self.detected is not True:
            raise ValueError("detection latency requires a detection")
        return self


class WakeWordCorpusReport(FrozenModel):
    corpus_id: str
    threshold: float = Field(ge=0.5, le=0.99)
    sample_count: int = Field(ge=2)
    positive_count: int = Field(ge=1)
    negative_count: int = Field(ge=1)
    true_positives: int = Field(ge=0)
    true_negatives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    inference_failures: int = Field(ge=0)
    false_acceptance_rate: float = Field(ge=0.0, le=1.0)
    false_rejection_rate: float = Field(ge=0.0, le=1.0)
    samples: tuple[WakeWordSampleReport, ...]

    @model_validator(mode="after")
    def aggregate_is_consistent(self) -> WakeWordCorpusReport:
        if self.positive_count + self.negative_count != self.sample_count:
            raise ValueError("wake-word corpus counts are inconsistent")
        if len(self.samples) != self.sample_count:
            raise ValueError("wake-word sample count is inconsistent")
        if (
            self.true_positives
            + self.true_negatives
            + self.false_positives
            + self.false_negatives
            != self.sample_count
        ):
            raise ValueError("wake-word confusion counts are inconsistent")
        if sum(item.error_code is not None for item in self.samples) != self.inference_failures:
            raise ValueError("wake-word failure count is inconsistent")
        return self

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def load_wakeword_corpus_manifest(
    path: Path,
    *,
    max_bytes: int = 1024 * 1024,
) -> WakeWordCorpusManifest:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if path.is_symlink() or not path.is_file():
        raise VoiceCorpusError("manifest must be a regular non-symlink file")
    try:
        if path.stat().st_size > max_bytes:
            raise VoiceCorpusError("manifest exceeded size limit")
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raise VoiceCorpusError("manifest exceeded size limit")
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        return TypeAdapter(WakeWordCorpusManifest).validate_python(document)
    except VoiceCorpusError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise VoiceCorpusError("invalid wake-word corpus manifest") from exc


def evaluate_wakeword_corpus(
    manifest: WakeWordCorpusManifest,
    corpus_root: Path,
    detector: ResettableWakeWordDetector,
    *,
    threshold: float = 0.5,
    max_sample_bytes: int = 2 * 1024 * 1024,
    max_total_bytes: int = 100 * 1024 * 1024,
    max_duration_seconds: float = 30.0,
    frame_ms: int = 80,
) -> WakeWordCorpusReport:
    if type(threshold) is not float or not 0.5 <= threshold <= 0.99:
        raise ValueError("wake-word threshold must be between 0.5 and 0.99")
    fixtures = load_audio_fixtures(
        manifest.samples,
        corpus_root,
        max_sample_bytes=max_sample_bytes,
        max_total_bytes=max_total_bytes,
        max_duration_seconds=max_duration_seconds,
        frame_ms=frame_ms,
    )
    reports = tuple(
        _evaluate_sample(sample, frames, detector, float(threshold))
        for sample, frames in zip(manifest.samples, fixtures, strict=True)
    )
    positive_count = sum(item.target_present for item in reports)
    negative_count = len(reports) - positive_count
    true_positives = sum(item.target_present and item.detected is True for item in reports)
    true_negatives = sum(not item.target_present and item.detected is False for item in reports)
    # Failures remain in the denominator and receive the conservative adverse outcome.
    false_positives = sum(
        not item.target_present and item.detected is not False for item in reports
    )
    false_negatives = sum(
        item.target_present and item.detected is not True for item in reports
    )
    return WakeWordCorpusReport(
        corpus_id=manifest.corpus_id,
        threshold=float(threshold),
        sample_count=len(reports),
        positive_count=positive_count,
        negative_count=negative_count,
        true_positives=true_positives,
        true_negatives=true_negatives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        inference_failures=sum(item.error_code is not None for item in reports),
        false_acceptance_rate=false_positives / negative_count,
        false_rejection_rate=false_negatives / positive_count,
        samples=reports,
    )


def _evaluate_sample(
    sample: WakeWordCorpusSample,
    frames: Sequence[AudioFrame],
    detector: ResettableWakeWordDetector,
    threshold: float,
) -> WakeWordSampleReport:
    try:
        detector.reset()
        max_score = 0.0
        elapsed_ms = 0
        first_detection_ms: int | None = None
        for frame in frames:
            score = detector.score(frame)
            max_score = max(max_score, score)
            elapsed_ms += round(
                len(frame.pcm_s16le)
                / (2 * frame.channels * frame.sample_rate)
                * 1_000
            )
            if first_detection_ms is None and score >= threshold:
                first_detection_ms = elapsed_ms
        return WakeWordSampleReport(
            sample_id=sample.sample_id,
            target_present=sample.target_present,
            detected=max_score >= threshold,
            max_score=max_score,
            first_detection_ms=first_detection_ms,
        )
    except Exception:
        return WakeWordSampleReport(
            sample_id=sample.sample_id,
            target_present=sample.target_present,
            detected=None,
            error_code="WAKEWORD_INFERENCE_FAILED",
        )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise VoiceCorpusError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise VoiceCorpusError(f"non-finite JSON number: {value}")
