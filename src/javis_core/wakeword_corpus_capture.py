from __future__ import annotations

import json
import os
import secrets
import sys
import wave
from collections.abc import Callable, Iterable
from hashlib import sha256
from pathlib import Path
from typing import Protocol, TextIO
from uuid import UUID, uuid4

from pydantic import Field, StrictBool, TypeAdapter, model_validator

from .cancellation import CancellationToken
from .models import FrozenModel
from .voice import AudioFrame
from .wakeword_evaluation import WakeWordCorpusManifest, WakeWordCorpusSample
from .windows_acl import WindowsUserOnlyAcl


class WakeWordCaptureError(RuntimeError):
    pass


class WakeWordCaptureSample(FrozenModel):
    sample_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    target_present: StrictBool


class WakeWordCapturePlan(FrozenModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    corpus_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    locale: str = Field(default="ko-KR", pattern=r"^ko-KR$")
    duration_ms: int = Field(default=3_200, ge=1_600, le=10_000)
    samples: tuple[WakeWordCaptureSample, ...] = Field(min_length=2, max_length=100)

    @model_validator(mode="after")
    def duration_and_samples_are_valid(self) -> WakeWordCapturePlan:
        if self.duration_ms % 80:
            raise ValueError("duration_ms must be an 80 ms multiple")
        identifiers = tuple(sample.sample_id for sample in self.samples)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("capture sample IDs must be unique")
        if {sample.target_present for sample in self.samples} != {False, True}:
            raise ValueError("capture plan requires target and non-target samples")
        return self


class CaptureFrameSource(Protocol):
    def frames(
        self,
        cancellation: CancellationToken,
        *,
        max_frames: int,
    ) -> Iterable[AudioFrame]: ...


CaptureSourceFactory = Callable[[], CaptureFrameSource]
CaptureReadiness = Callable[[WakeWordCaptureSample], bool]
FileHardener = Callable[[Path], None]


class ConsoleWakeWordCaptureConsent:
    """Require a fresh exact phrase before any microphone source can be created."""

    def __init__(
        self,
        *,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._input = input_stream or sys.stdin
        self._output = output_stream or sys.stdout
        self._token_factory = token_factory or (lambda: secrets.token_hex(3))
        self._used_tokens: set[str] = set()

    def request(self, plan: WakeWordCapturePlan) -> UUID | None:
        if not self._input.isatty():
            return None
        token = self._token_factory()
        if (
            not token
            or len(token) > 32
            or token in self._used_tokens
            or any(character not in "0123456789abcdef" for character in token)
        ):
            raise WakeWordCaptureError("fresh capture consent token could not be created")
        self._used_tokens.add(token)
        phrase = f"동의 {token}"
        self._output.write(
            f"[웨이크워드 자료 수집] 마이크로 {len(plan.samples)}개 표본을 "
            f"각 {plan.duration_ms / 1000:g}초 녹음합니다. 원음 WAV는 삭제하지 않고 "
            f"지정한 로컬 폴더에 보관됩니다. 실행하려면 '{phrase}'를 정확히 "
            "입력하세요. Enter는 취소합니다: "
        )
        self._output.flush()
        response = self._input.readline(128)
        if not response or response.strip() != phrase:
            return None
        return uuid4()


class ConsoleWakeWordCaptureReadiness:
    """Require a deliberate acknowledgement immediately before each recording."""

    def __init__(
        self,
        *,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
    ) -> None:
        self._input = input_stream or sys.stdin
        self._output = output_stream or sys.stdout

    def __call__(self, sample: WakeWordCaptureSample) -> bool:
        if not self._input.isatty():
            return False
        guidance = (
            "'헤이 자비스'라고 자연스럽게 말하세요"
            if sample.target_present
            else "'헤이 자비스'를 말하지 말고 평소 문장이나 주변 소리를 들려주세요"
        )
        self._output.write(
            f"[{sample.sample_id}] {guidance}. 녹음 직전에 '준비'를 입력하세요. "
            "그 외 입력은 전체 수집을 취소합니다: "
        )
        self._output.flush()
        response = self._input.readline(128)
        return bool(response and response.strip() == "준비")


def load_wakeword_capture_plan(
    path: Path,
    *,
    max_bytes: int = 128 * 1024,
) -> WakeWordCapturePlan:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if path.is_symlink() or not path.is_file():
        raise WakeWordCaptureError("capture plan must be a regular non-symlink file")
    try:
        if path.stat().st_size > max_bytes:
            raise WakeWordCaptureError("capture plan exceeded size limit")
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raise WakeWordCaptureError("capture plan exceeded size limit")
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        return TypeAdapter(WakeWordCapturePlan).validate_python(document)
    except WakeWordCaptureError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise WakeWordCaptureError("invalid wake-word capture plan") from error


def capture_wakeword_corpus(
    plan: WakeWordCapturePlan,
    destination: Path,
    consent_id: UUID,
    source_factory: CaptureSourceFactory,
    readiness: CaptureReadiness,
    *,
    cancellation: CancellationToken | None = None,
    hardener: FileHardener | None = None,
) -> WakeWordCorpusManifest:
    parent = _validate_destination(destination)
    token = cancellation or CancellationToken()
    protect = hardener or WindowsUserOnlyAcl().harden
    frame_count = plan.duration_ms // 80
    created: list[Path] = []
    destination_created = False
    try:
        destination.mkdir(mode=0o700)
        destination_created = True
        if destination.resolve(strict=True).parent != parent:
            raise WakeWordCaptureError("capture destination identity changed")
        manifest_samples: list[WakeWordCorpusSample] = []
        for specification in plan.samples:
            if token.is_cancelled or not readiness(specification):
                raise WakeWordCaptureError("capture was not confirmed")
            frames = tuple(
                source_factory().frames(
                    token,
                    max_frames=frame_count,
                )
            )
            if len(frames) != frame_count:
                raise WakeWordCaptureError("capture did not return the required frames")
            if any(
                frame.sample_rate != 16_000
                or frame.channels != 1
                or len(frame.pcm_s16le) != 2_560
                for frame in frames
            ):
                raise WakeWordCaptureError("capture returned an invalid audio frame")
            filename = f"{specification.sample_id}.wav"
            output = destination / filename
            digest = _write_protected_wav(output, frames, protect)
            created.append(output)
            manifest_samples.append(
                WakeWordCorpusSample(
                    sample_id=specification.sample_id,
                    audio_file=filename,
                    audio_sha256=digest,
                    target_present=specification.target_present,
                    consent_record_id=str(consent_id),
                )
            )
        manifest = WakeWordCorpusManifest(
            corpus_id=plan.corpus_id,
            locale=plan.locale,
            samples=tuple(manifest_samples),
        )
        manifest_path = destination / "manifest.json"
        _write_protected_file(
            manifest_path,
            json.dumps(
                manifest.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            protect,
        )
        created.append(manifest_path)
        return manifest
    except Exception as error:
        cleanup_failed = _cleanup_created_capture(created, destination, destination_created)
        if cleanup_failed:
            raise WakeWordCaptureError("capture failed and cleanup was incomplete") from error
        if isinstance(error, WakeWordCaptureError):
            raise
        raise WakeWordCaptureError("wake-word corpus capture failed") from error


def _validate_destination(destination: Path) -> Path:
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise WakeWordCaptureError("capture destination must be a new absolute path")
    try:
        parent = destination.parent.resolve(strict=True)
    except OSError as error:
        raise WakeWordCaptureError("capture destination parent is unavailable") from error
    if not parent.is_dir() or destination.parent.is_symlink():
        raise WakeWordCaptureError("capture destination parent must be a non-symlink directory")
    return parent


def _write_protected_wav(
    path: Path,
    frames: tuple[AudioFrame, ...],
    hardener: FileHardener,
) -> str:
    created_here = False
    try:
        with path.open("xb") as stream:
            created_here = True
            hardener(path)
            with wave.open(stream, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16_000)
                output.writeframes(b"".join(frame.pcm_s16le for frame in frames))
            stream.flush()
            os.fsync(stream.fileno())
        hardener(path)
        return _file_hash(path)
    except Exception as error:
        _remove_partial_file(path, created_here)
        raise WakeWordCaptureError("capture WAV could not be written") from error


def _write_protected_file(path: Path, payload: bytes, hardener: FileHardener) -> None:
    created_here = False
    try:
        with path.open("xb") as stream:
            created_here = True
            hardener(path)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        hardener(path)
    except Exception as error:
        _remove_partial_file(path, created_here)
        raise WakeWordCaptureError("capture manifest could not be written") from error


def _remove_partial_file(path: Path, created_here: bool) -> None:
    if not created_here:
        return
    try:
        if path.is_symlink() or not path.is_file():
            raise WakeWordCaptureError("partial capture output identity changed")
        path.unlink()
    except OSError as error:
        raise WakeWordCaptureError("partial capture output cleanup failed") from error


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cleanup_created_capture(
    created: list[Path],
    destination: Path,
    destination_created: bool,
) -> bool:
    failed = False
    for path in reversed(created):
        try:
            if path.is_symlink() or not path.is_file():
                failed = True
                continue
            path.unlink()
        except OSError:
            failed = True
    if destination_created:
        try:
            destination.rmdir()
        except OSError:
            failed = True
    return failed


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise WakeWordCaptureError("duplicate capture plan key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise WakeWordCaptureError(f"non-finite JSON number: {value}")
