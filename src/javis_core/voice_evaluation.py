from __future__ import annotations

import hashlib
import io
import json
import time
import unicodedata
import wave
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

from pydantic import Field, TypeAdapter, field_validator, model_validator

from .models import FrozenModel
from .voice import AudioFrame, SpeechToText


class VoiceCorpusError(ValueError):
    """Raised before inference when corpus inputs are not trustworthy."""


class VoiceCorpusSample(FrozenModel):
    sample_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    audio_file: str = Field(min_length=1, max_length=512)
    audio_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_text: str = Field(min_length=1, max_length=2_000, repr=False)
    reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    consent_record_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
    )

    @field_validator("audio_file")
    @classmethod
    def audio_path_must_be_portable_and_relative(cls, value: str) -> str:
        if "\\" in value or ":" in value:
            raise ValueError("audio_file must use a relative POSIX path")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("audio_file must not traverse directories")
        return value

    @model_validator(mode="after")
    def reference_hash_must_match(self) -> VoiceCorpusSample:
        actual = hashlib.sha256(self.reference_text.encode("utf-8")).hexdigest()
        if actual != self.reference_sha256:
            raise ValueError("reference transcript hash mismatch")
        if not normalize_korean_transcript(self.reference_text):
            raise ValueError("normalized reference transcript must not be empty")
        return self


class VoiceCorpusManifest(FrozenModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    corpus_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    locale: str = Field(default="ko-KR", pattern=r"^ko-KR$")
    samples: tuple[VoiceCorpusSample, ...] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def sample_ids_must_be_unique(self) -> VoiceCorpusManifest:
        identifiers = [sample.sample_id for sample in self.samples]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("sample_id values must be unique")
        return self


class VoiceSampleReport(FrozenModel):
    sample_id: str
    reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hypothesis_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_characters: int = Field(ge=1)
    hypothesis_characters: int = Field(ge=0)
    character_edits: int = Field(ge=0)
    reference_words: int = Field(ge=1)
    hypothesis_words: int = Field(ge=0)
    word_edits: int = Field(ge=0)
    latency_ms: float = Field(ge=0)
    error_code: str | None = None


class VoiceCorpusReport(FrozenModel):
    corpus_id: str
    sample_count: int = Field(ge=1)
    inference_failures: int = Field(ge=0)
    exact_matches: int = Field(ge=0)
    character_error_rate: float = Field(ge=0)
    word_error_rate: float = Field(ge=0)
    mean_latency_ms: float = Field(ge=0)
    samples: tuple[VoiceSampleReport, ...]

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def load_voice_corpus_manifest(
    path: Path,
    *,
    max_bytes: int = 1024 * 1024,
) -> VoiceCorpusManifest:
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
        return TypeAdapter(VoiceCorpusManifest).validate_python(document)
    except VoiceCorpusError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise VoiceCorpusError("invalid voice corpus manifest") from exc


def evaluate_voice_corpus(
    manifest: VoiceCorpusManifest,
    corpus_root: Path,
    stt: SpeechToText,
    *,
    max_sample_bytes: int = 2 * 1024 * 1024,
    max_total_bytes: int = 100 * 1024 * 1024,
    max_duration_seconds: float = 30.0,
    frame_ms: int = 80,
    clock: Callable[[], float] = time.perf_counter,
) -> VoiceCorpusReport:
    if min(max_sample_bytes, max_total_bytes) < 1 or max_duration_seconds <= 0:
        raise ValueError("corpus bounds must be positive")
    if frame_ms < 10 or 16_000 * frame_ms % 1000:
        raise ValueError("invalid frame duration")
    fixtures = _validate_all_fixtures(
        manifest,
        corpus_root,
        max_sample_bytes=max_sample_bytes,
        max_total_bytes=max_total_bytes,
        max_duration_seconds=max_duration_seconds,
        frame_ms=frame_ms,
    )
    reports: list[VoiceSampleReport] = []
    for sample, frames in fixtures:
        reference = normalize_korean_transcript(sample.reference_text)
        started = clock()
        error_code: str | None = None
        try:
            hypothesis = normalize_korean_transcript(stt.transcribe(frames))
        except Exception:  # model/runtime is an isolated inference boundary
            hypothesis = ""
            error_code = "STT_INFERENCE_FAILED"
        latency_ms = max(0.0, (clock() - started) * 1000)
        reference_characters = tuple(reference.replace(" ", ""))
        hypothesis_characters = tuple(hypothesis.replace(" ", ""))
        reference_words = tuple(reference.split())
        hypothesis_words = tuple(hypothesis.split())
        reports.append(
            VoiceSampleReport(
                sample_id=sample.sample_id,
                reference_sha256=_text_hash(reference),
                hypothesis_sha256=_text_hash(hypothesis),
                reference_characters=len(reference_characters),
                hypothesis_characters=len(hypothesis_characters),
                character_edits=_levenshtein(reference_characters, hypothesis_characters),
                reference_words=len(reference_words),
                hypothesis_words=len(hypothesis_words),
                word_edits=_levenshtein(reference_words, hypothesis_words),
                latency_ms=latency_ms,
                error_code=error_code,
            )
        )
    total_characters = sum(item.reference_characters for item in reports)
    total_words = sum(item.reference_words for item in reports)
    return VoiceCorpusReport(
        corpus_id=manifest.corpus_id,
        sample_count=len(reports),
        inference_failures=sum(item.error_code is not None for item in reports),
        exact_matches=sum(
            item.character_edits == 0 and item.word_edits == 0 and item.error_code is None
            for item in reports
        ),
        character_error_rate=sum(item.character_edits for item in reports) / total_characters,
        word_error_rate=sum(item.word_edits for item in reports) / total_words,
        mean_latency_ms=sum(item.latency_ms for item in reports) / len(reports),
        samples=tuple(reports),
    )


def normalize_korean_transcript(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _validate_all_fixtures(
    manifest: VoiceCorpusManifest,
    corpus_root: Path,
    *,
    max_sample_bytes: int,
    max_total_bytes: int,
    max_duration_seconds: float,
    frame_ms: int,
) -> tuple[tuple[VoiceCorpusSample, tuple[AudioFrame, ...]], ...]:
    if corpus_root.is_symlink() or not corpus_root.is_dir():
        raise VoiceCorpusError("corpus root must be a regular non-symlink directory")
    resolved_root = corpus_root.resolve(strict=True)
    total_bytes = 0
    fixtures: list[tuple[VoiceCorpusSample, tuple[AudioFrame, ...]]] = []
    for sample in manifest.samples:
        relative = PurePosixPath(sample.audio_file)
        candidate = resolved_root.joinpath(*relative.parts)
        _reject_symlink_components(resolved_root, candidate)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise VoiceCorpusError("audio fixture is unavailable") from exc
        if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
            raise VoiceCorpusError("audio fixture escaped corpus root")
        size = resolved.stat().st_size
        if size > max_sample_bytes:
            raise VoiceCorpusError("audio fixture exceeded sample size limit")
        total_bytes += size
        if total_bytes > max_total_bytes:
            raise VoiceCorpusError("audio corpus exceeded total size limit")
        raw = resolved.read_bytes()
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != sample.audio_sha256:
            raise VoiceCorpusError("audio fixture hash mismatch")
        fixtures.append(
            (
                sample,
                _decode_pcm_wav(
                    raw,
                    max_duration_seconds=max_duration_seconds,
                    frame_ms=frame_ms,
                ),
            )
        )
    return tuple(fixtures)


def _reject_symlink_components(root: Path, candidate: Path) -> None:
    relative = candidate.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise VoiceCorpusError("audio fixture path must not contain symlinks")


def _decode_pcm_wav(
    raw: bytes,
    *,
    max_duration_seconds: float,
    frame_ms: int,
) -> tuple[AudioFrame, ...]:
    try:
        with wave.open(io.BytesIO(raw), "rb") as stream:
            if (
                stream.getnchannels() != 1
                or stream.getframerate() != 16_000
                or stream.getsampwidth() != 2
                or stream.getcomptype() != "NONE"
            ):
                raise VoiceCorpusError("audio fixture must be mono 16 kHz signed 16-bit PCM")
            frame_count = stream.getnframes()
            if frame_count < 1 or frame_count / 16_000 > max_duration_seconds:
                raise VoiceCorpusError("audio fixture duration is invalid")
            pcm = stream.readframes(frame_count)
    except (EOFError, wave.Error) as exc:
        raise VoiceCorpusError("audio fixture is not a valid WAV file") from exc
    if len(pcm) != frame_count * 2:
        raise VoiceCorpusError("audio fixture PCM data is truncated")
    chunk_bytes = 16_000 * frame_ms // 1000 * 2
    return tuple(
        AudioFrame(pcm_s16le=pcm[offset : offset + chunk_bytes])
        for offset in range(0, len(pcm), chunk_bytes)
    )


def _levenshtein(left: Sequence[str], right: Sequence[str]) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_item in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_item in enumerate(right, start=1):
            current.append(
                min(
                    current[right_index - 1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_item != right_item),
                )
            )
        previous = current
    return previous[-1]


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise VoiceCorpusError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise VoiceCorpusError(f"non-finite JSON number: {value}")
