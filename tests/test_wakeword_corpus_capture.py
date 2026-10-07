from __future__ import annotations

import hashlib
import io
import json
import wave
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from javis_core import (
    AudioFrame,
    ConsoleWakeWordCaptureConsent,
    ConsoleWakeWordCaptureReadiness,
    WakeWordCaptureError,
    WakeWordCapturePlan,
    WakeWordCaptureSample,
    capture_wakeword_corpus,
    load_wakeword_capture_plan,
)
from javis_core.cancellation import CancellationToken


class FrameSource:
    def __init__(self, frames: tuple[AudioFrame, ...]) -> None:
        self._frames = frames
        self.limits: list[int] = []

    def frames(
        self,
        cancellation: CancellationToken,
        *,
        max_frames: int,
    ) -> tuple[AudioFrame, ...]:
        self.limits.append(max_frames)
        if cancellation.is_cancelled:
            return ()
        return self._frames[:max_frames]


class TtyInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def plan() -> WakeWordCapturePlan:
    return WakeWordCapturePlan(
        corpus_id="ko-jarvis-smoke",
        duration_ms=1_600,
        samples=(
            WakeWordCaptureSample(sample_id="positive-01", target_present=True),
            WakeWordCaptureSample(sample_id="negative-01", target_present=False),
        ),
    )


def frames(count: int = 20) -> tuple[AudioFrame, ...]:
    return tuple(
        AudioFrame(pcm_s16le=index.to_bytes(2, "little", signed=True) * 1_280)
        for index in range(count)
    )


def test_capture_writes_hashed_protected_wavs_and_manifest(tmp_path: Path) -> None:
    sources: list[FrameSource] = []
    hardened: list[Path] = []

    def source_factory() -> FrameSource:
        source = FrameSource(frames())
        sources.append(source)
        return source

    destination = (tmp_path / "corpus").resolve()
    manifest = capture_wakeword_corpus(
        plan(),
        destination,
        UUID("12345678-1234-5678-1234-567812345678"),
        source_factory,
        lambda _sample: True,
        hardener=hardened.append,
    )

    assert [sample.audio_file for sample in manifest.samples] == [
        "positive-01.wav",
        "negative-01.wav",
    ]
    assert all(
        sample.consent_record_id == "12345678-1234-5678-1234-567812345678"
        for sample in manifest.samples
    )
    assert [source.limits for source in sources] == [[20], [20]]
    assert hardened.count(destination / "manifest.json") == 2
    for sample in manifest.samples:
        path = destination / sample.audio_file
        assert hardened.count(path) == 2
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sample.audio_sha256
        with wave.open(str(path), "rb") as captured:
            assert captured.getnchannels() == 1
            assert captured.getsampwidth() == 2
            assert captured.getframerate() == 16_000
            assert captured.getnframes() == 25_600
    document = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert document == manifest.model_dump(mode="json")


def test_capture_cleans_up_only_owned_output_on_readiness_denial(tmp_path: Path) -> None:
    parent_sentinel = tmp_path / "keep.txt"
    parent_sentinel.write_text("keep", encoding="utf-8")
    destination = (tmp_path / "corpus").resolve()
    calls = 0

    def source_factory() -> FrameSource:
        nonlocal calls
        calls += 1
        return FrameSource(frames())

    with pytest.raises(WakeWordCaptureError, match="not confirmed"):
        capture_wakeword_corpus(
            plan(),
            destination,
            UUID(int=1),
            source_factory,
            lambda _sample: False,
            hardener=lambda _path: None,
        )

    assert calls == 0
    assert not destination.exists()
    assert parent_sentinel.read_text(encoding="utf-8") == "keep"


def test_capture_cleans_up_short_audio(tmp_path: Path) -> None:
    destination = (tmp_path / "corpus").resolve()

    with pytest.raises(WakeWordCaptureError, match="required frames"):
        capture_wakeword_corpus(
            plan(),
            destination,
            UUID(int=2),
            lambda: FrameSource(frames(19)),
            lambda _sample: True,
            hardener=lambda _path: None,
        )

    assert not destination.exists()


def test_capture_cleans_up_partial_file_when_hardening_fails(tmp_path: Path) -> None:
    destination = (tmp_path / "corpus").resolve()

    def reject_hardening(_path: Path) -> None:
        raise OSError("simulated ACL failure")

    with pytest.raises(WakeWordCaptureError, match="WAV could not be written"):
        capture_wakeword_corpus(
            plan(),
            destination,
            UUID(int=4),
            lambda: FrameSource(frames()),
            lambda _sample: True,
            hardener=reject_hardening,
        )

    assert not destination.exists()


def test_capture_rejects_existing_destination_before_source_creation(tmp_path: Path) -> None:
    destination = tmp_path / "corpus"
    destination.mkdir()
    calls = 0

    def source_factory() -> FrameSource:
        nonlocal calls
        calls += 1
        return FrameSource(frames())

    with pytest.raises(WakeWordCaptureError, match="new absolute path"):
        capture_wakeword_corpus(
            plan(),
            destination.resolve(),
            UUID(int=3),
            source_factory,
            lambda _sample: True,
            hardener=lambda _path: None,
        )

    assert calls == 0


@pytest.mark.parametrize(
    "samples",
    [
        (
            WakeWordCaptureSample(sample_id="a", target_present=True),
            WakeWordCaptureSample(sample_id="b", target_present=True),
        ),
        (
            WakeWordCaptureSample(sample_id="same", target_present=True),
            WakeWordCaptureSample(sample_id="same", target_present=False),
        ),
    ],
)
def test_capture_plan_rejects_unbalanced_or_duplicate_samples(
    samples: tuple[WakeWordCaptureSample, WakeWordCaptureSample],
) -> None:
    with pytest.raises(ValidationError):
        WakeWordCapturePlan(corpus_id="invalid", samples=samples)


def test_capture_plan_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_text(
        '{"schema_version":"1.0","corpus_id":"a","corpus_id":"b",'
        '"locale":"ko-KR","duration_ms":1600,"samples":[]}',
        encoding="utf-8",
    )

    with pytest.raises(WakeWordCaptureError, match="duplicate"):
        load_wakeword_capture_plan(path)


def test_console_capture_consent_requires_exact_fresh_phrase() -> None:
    output = io.StringIO()
    provider = ConsoleWakeWordCaptureConsent(
        input_stream=TtyInput("동의 abc123\n"),
        output_stream=output,
        token_factory=lambda: "abc123",
    )

    assert provider.request(plan()) is not None
    assert "원음 WAV는 삭제하지 않고" in output.getvalue()

    with pytest.raises(WakeWordCaptureError, match="fresh"):
        provider.request(plan())


def test_console_capture_consent_rejects_non_tty_and_enter() -> None:
    assert (
        ConsoleWakeWordCaptureConsent(
            input_stream=io.StringIO("동의 abc123\n"),
            token_factory=lambda: "abc123",
        ).request(plan())
        is None
    )
    assert (
        ConsoleWakeWordCaptureConsent(
            input_stream=TtyInput("\n"),
            token_factory=lambda: "abc123",
        ).request(plan())
        is None
    )


def test_console_readiness_requires_exact_korean_acknowledgement() -> None:
    output = io.StringIO()
    readiness = ConsoleWakeWordCaptureReadiness(
        input_stream=TtyInput("준비\n아니오\n"),
        output_stream=output,
    )

    assert readiness(plan().samples[0])
    assert not readiness(plan().samples[1])
    assert "헤이 자비스" in output.getvalue()
